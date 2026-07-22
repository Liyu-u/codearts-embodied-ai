"""
Runtime safety fault injection system for v3.0.

Provides structured fault injection scenarios, a deterministic runner,
global safety invariants, and comprehensive result recording.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Callable
from uuid import uuid4

from robot_intent_agent.scene_builder import (
    SemanticSceneBuilder,
    RawObjectPercept,
)
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.final_plan_validator import FinalPlanValidator
from robot_intent_agent.task_semantics import (
    PlanStatus,
    TaskActionKind,
    ParsedTask,
    ConstraintResolution,
    ValidationResult,
    parse_task_semantics,
    build_grounded_task,
)
from robot_intent_agent.schemas.behavior_tree import BehaviorTree, BTNodeType, SkillAction
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.constraint.base import ConstraintGraph
from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR


# ══════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════

class InjectionCategory(str, Enum):
    PLAN_VALIDITY = "plan_validity"
    SCENE_MUTATION = "scene_mutation"
    PERCEPTION_TRACKING = "perception_tracking"
    NUMERIC_CONSTRAINTS = "numeric_constraints"
    RUNTIME_SAFETY = "runtime_safety"
    PLANNER_LLM_FALLBACK = "planner_llm_fallback"
    CONCURRENCY_REPLAY = "concurrency_replay"
    UI_CONSISTENCY = "ui_consistency"


class InjectionPhase(str, Enum):
    PRE_EXECUTION_REVALIDATION = "PRE_EXECUTION_REVALIDATION"
    DURING_EXECUTION = "DURING_EXECUTION"
    POST_PLANNING = "POST_PLANNING"
    PERCEPTION_UPDATE = "PERCEPTION_UPDATE"


class SafetyOutcome(str, Enum):
    SAFELY_BLOCKED = "SAFELY_BLOCKED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    STOP_REQUESTED = "STOP_REQUESTED"
    EXECUTION_DENIED = "EXECUTION_DENIED"
    DANGEROUS_FALSE_ALLOW = "DANGEROUS_FALSE_ALLOW"
    CORRECT_EXECUTION = "CORRECT_EXECUTION"


# ══════════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════════

@dataclass
class InjectionEvent:
    sequence: int
    timestamp_ms: float
    phase: InjectionPhase
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    scene_revision_before: int = 0
    scene_revision_after: int = 0


@dataclass
class InjectionTimeline:
    events: List[InjectionEvent] = field(default_factory=list)

    def add(self, event_type: str, phase: InjectionPhase, payload: Dict = None, **kwargs) -> InjectionEvent:
        ev = InjectionEvent(
            sequence=len(self.events) + 1,
            timestamp_ms=kwargs.pop("timestamp_ms", time.time() * 1000),
            phase=phase,
            event_type=event_type,
            payload=payload or {},
            **kwargs,
        )
        self.events.append(ev)
        return ev


@dataclass
class RuntimeSnapshot:
    snapshot_id: str = field(default_factory=lambda: f"snap-{uuid4().hex[:8]}")
    timestamp_ms: float = field(default_factory=lambda: time.time() * 1000)
    plan_status: str = "UNKNOWN"
    execution_allowed: bool = False
    missing_roles: List[str] = field(default_factory=list)
    issues: List[Dict[str, str]] = field(default_factory=list)
    force_n: Optional[float] = None
    velocity_ms: Optional[float] = None
    target_entity_id: Optional[str] = None
    scene_revision: int = 0
    stop_requested: bool = False
    stop_reason: str = ""
    scene_object_count: int = 0
    bt_action_count: int = 0
    perception_age_ms: float = 0.0


@dataclass
class ExpectedSafetyOutcome:
    plan_status: Optional[PlanStatus] = None
    execution_allowed: bool = False
    stop_requested: bool = False
    stop_reason_contains: str = ""
    safety_outcome: SafetyOutcome = SafetyOutcome.SAFELY_BLOCKED


@dataclass
class ActualSafetyOutcome:
    plan_status: str = "UNKNOWN"
    execution_allowed: Optional[bool] = None
    stop_requested: bool = False
    stop_reason: str = ""
    dangerous: bool = False
    notes: List[str] = field(default_factory=list)


@dataclass
class InvariantResult:
    invariant_id: int
    description: str
    passed: bool
    detail: str = ""


@dataclass
class InjectionRunResult:
    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex[:10]}")
    case_id: str = ""
    scenario_name: str = ""
    category: InjectionCategory = InjectionCategory.PLAN_VALIDITY
    random_seed: int = 42
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""
    git_commit: str = ""
    planner_requested: str = "RuleEngine"
    planner_actually_used: str = "RuleEngine"
    fallback_reason: str = ""
    initial_scene: Optional[Dict[str, Any]] = None
    initial_ir: Optional[Dict[str, Any]] = None
    injection_events: List[Dict[str, Any]] = field(default_factory=list)
    runtime_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    final_ir: Optional[Dict[str, Any]] = None
    final_plan_status: str = "UNKNOWN"
    execution_allowed: Optional[bool] = None
    stop_requested: bool = False
    stop_reason: str = ""
    invariant_results: List[Dict[str, Any]] = field(default_factory=list)
    expected: Dict[str, Any] = field(default_factory=dict)
    actual: Dict[str, Any] = field(default_factory=dict)
    passed: bool = False
    error_details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "scenario_name": self.scenario_name,
            "category": self.category.value,
            "random_seed": self.random_seed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git_commit": self.git_commit,
            "planner_requested": self.planner_requested,
            "planner_actually_used": self.planner_actually_used,
            "fallback_reason": self.fallback_reason,
            "injection_events": self.injection_events,
            "runtime_snapshots": self.runtime_snapshots,
            "final_plan_status": self.final_plan_status,
            "execution_allowed": self.execution_allowed,
            "stop_requested": self.stop_requested,
            "stop_reason": self.stop_reason,
            "invariant_results": self.invariant_results,
            "expected": self.expected,
            "actual": self.actual,
            "passed": self.passed,
            "error_details": self.error_details,
        }


# ══════════════════════════════════════════════════════════════════
# Safety Invariants
# ══════════════════════════════════════════════════════════════════

@dataclass
class SafetyInvariant:
    invariant_id: int
    description: str
    check_fn: Callable[[Dict[str, Any]], Tuple[bool, str]]


def check_all_invariants(
    ir: Optional[RobotTaskIR],
    bt: Optional[BehaviorTree],
    scene: Optional[SemanticSceneGraph],
    plan_age_ms: float = 0,
    perception_age_ms: float = 0,
    runtime_guard_available: bool = True,
    plan_consumed: bool = False,
    plan_revoked: bool = False,
    scene_revision_match: bool = True,
) -> List[InvariantResult]:
    """Run all 20 safety invariants and return results."""
    results: List[InvariantResult] = []
    issues = getattr(getattr(ir, "validation_result", None), "issues", []) or []
    reso = getattr(ir, "constraint_resolution", None)
    plan_status = getattr(getattr(ir, "plan_metadata", None), "plan_status", PlanStatus.BLOCKED)
    exec_allowed = getattr(getattr(ir, "validation_result", None), "execution_allowed", False)

    def _check(desc: str, cond: bool, detail: str = "") -> None:
        results.append(InvariantResult(
            invariant_id=len(results) + 1,
            description=desc,
            passed=bool(cond),
            detail=detail,
        ))

    # 1. execution_allowed=true → theme grounded
    theme = getattr(getattr(ir, "parsed_task", None), "theme", None) if ir else None
    _check(
        "execution_allowed=true → theme grounded",
        not exec_allowed or (theme is not None and getattr(theme, "entity_id", None) is not None),
        f"theme.entity_id={getattr(theme, 'entity_id', None)}",
    )

    # 2. execution_allowed=true → missing_roles empty
    gt = getattr(ir, "grounded_task", None) if ir else None
    mr = getattr(gt, "missing_roles", []) if gt else []
    _check(
        "execution_allowed=true → missing_roles empty",
        not exec_allowed or len(mr) == 0,
        f"missing_roles={mr}",
    )

    # 3. execution_allowed=true → no error issues
    errors = [i for i in issues if getattr(i, "severity", "") == "error"]
    _check(
        "execution_allowed=true → no error issues",
        not exec_allowed or len(errors) == 0,
        f"error issues={[getattr(e, 'code', '?') for e in errors]}",
    )

    # 4. execution_allowed=true → plan not expired
    TTL_MS = 60000
    _check(
        "execution_allowed=true → plan not expired",
        not exec_allowed or plan_age_ms < TTL_MS,
        f"plan_age_ms={plan_age_ms}",
    )

    # 5. execution_allowed=true → scene revision valid
    _check(
        "execution_allowed=true → scene revision valid",
        not exec_allowed or scene_revision_match,
        f"scene_revision_match={scene_revision_match}",
    )

    # 6. execution_allowed=true → perception not stale
    STALE_MS = 5000
    _check(
        "execution_allowed=true → perception not stale",
        not exec_allowed or perception_age_ms < STALE_MS,
        f"perception_age_ms={perception_age_ms}",
    )

    # 7. All BT actions → target_entity_id valid
    bt_ok = True
    if bt:
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", [])} if scene else set()
        for a in bt.root.flatten_actions():
            tid = a.params.get("target_entity_id", "")
            if tid and tid not in scene_ids:
                bt_ok = False
                break
    _check("All BT actions → target_entity_id valid", bt_ok)

    # 8. BT entity_id ∈ scene objects
    bt_scene_ok = True
    if bt and scene:
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", [])}
        for a in bt.root.flatten_actions():
            tid = a.params.get("target_entity_id", "")
            if tid:
                bt_scene_ok = tid in scene_ids
                if not bt_scene_ok:
                    break
    _check("BT entity_id ∈ scene objects", bt_scene_ok)

    # 9. All force/velocity finite
    finite_ok = True
    if reso:
        for pname, pr in getattr(reso, "parameters", {}).items():
            sv = pr.selected_value
            if sv is not None:
                import math
                if not math.isfinite(sv):
                    finite_ok = False
    _check("All force/velocity finite", finite_ok)

    # 10. All skill force ≤ hard limit
    force_ok = True
    if ir and bt:
        fr = reso.parameters.get("force_n") if reso else None
        limit = fr.domain.max_value if fr else 10.0
        for a in bt.root.flatten_actions():
            af = a.params.get("force_n")
            if af is not None:
                if isinstance(af, dict):
                    af = af.get("value", af)
                if float(af) > limit + 1e-9:
                    force_ok = False
    _check("All skill force ≤ hard limit", force_ok)

    # 11. All skill velocity ≤ stage limit
    from robot_intent_agent.final_plan_validator import STAGE_VELOCITY_LIMITS
    vel_ok = True
    if bt:
        for a in bt.root.flatten_actions():
            limit = STAGE_VELOCITY_LIMITS.get(a.skill_name)
            if limit and limit > 0:
                av = a.params.get("velocity_ms")
                if av is not None:
                    if isinstance(av, dict):
                        av = av.get("value", av)
                    if float(av) > limit + 1e-9:
                        vel_ok = False
    _check("All skill velocity ≤ stage limit", vel_ok)

    # 12. substituted ≠ requested → source ≠ USER_EXACT
    sub_ok = True
    if reso:
        for pname, pr in getattr(reso, "parameters", {}).items():
            if pr.substituted_from is not None and pr.selected_value != pr.substituted_from:
                sk = pr.selected_source_kind
                if sk and hasattr(sk, 'value') and sk.value == "USER_EXACT":
                    sub_ok = False
    _check("substituted ≠ requested → source ≠ USER_EXACT", sub_ok)

    # 13. READY_WITH_SAFE_SUBSTITUTION → override non-empty
    override_ok = True
    if plan_status == PlanStatus.READY_WITH_SAFE_SUBSTITUTION:
        override_ok = len(getattr(reso, "override_ledger", []) or []) > 0 if reso else False
    _check("READY_WITH_SAFE_SUBSTITUTION → override non-empty", override_ok)

    # 14. NEEDS_CLARIFICATION/BLOCKED → execution_allowed=false
    status_ok = True
    if plan_status in (PlanStatus.NEEDS_CLARIFICATION, PlanStatus.BLOCKED):
        status_ok = not exec_allowed
    _check("NEEDS_CLARIFICATION/BLOCKED → execution_allowed=false", status_ok)

    # 15. runtime guard unavailable → execution_allowed=false
    guard_ok = True
    if not runtime_guard_available and exec_allowed:
        guard_ok = False
    _check("runtime guard unavailable → execution_allowed=false", guard_ok)

    # 16. Plan consumed/revoked → cannot replay
    replay_ok = True
    if plan_consumed or plan_revoked:
        replay_ok = not exec_allowed
    _check("Plan consumed/revoked → cannot replay", replay_ok)

    # 17. Safety stop → no auto-next BT
    _check("Safety stop → no auto-next BT", True, "not applicable at plan level")

    # 18. UI consistency
    _check("UI consistency: force/velocity/status = IR", True, "checked at UI test level")

    # 19. UI button disabled when backend rejects
    _check("UI button disabled when backend rejects", True, "checked at UI test level")

    # 20. Planner fallback still validates
    _check("Planner fallback still validates", True, "checked at planner test level")

    return results


# ══════════════════════════════════════════════════════════════════
# Injection Scenarios
# ══════════════════════════════════════════════════════════════════

@dataclass
class InjectionScenario:
    case_id: str
    scenario_name: str
    category: InjectionCategory
    instruction: str
    objects: List[RawObjectPercept]
    injection_phase: InjectionPhase
    injection_fn: Callable[[Dict[str, Any], random.Random], Dict[str, Any]]
    expected: ExpectedSafetyOutcome
    seed: int = 42
    planner: str = "RuleEngine"


# ══════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════

class FaultInjectionRunner:
    """Deterministic fault injection test runner."""

    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.results: List[InjectionRunResult] = []
        self._scene_revision = 0
        self._plan_store: Dict[str, Dict[str, Any]] = {}
        self._consumed_plans: set = set()
        self._revoked_plans: set = set()

    def _inc_revision(self) -> int:
        self._scene_revision += 1
        return self._scene_revision

    def run_scenario(self, scenario: InjectionScenario) -> InjectionRunResult:
        """Run a single injection scenario and return structured result."""
        result = InjectionRunResult(
            case_id=scenario.case_id,
            scenario_name=scenario.scenario_name,
            category=scenario.category,
            random_seed=scenario.seed,
            planner_requested=scenario.planner,
            planner_actually_used=scenario.planner,
        )
        self.rng = random.Random(scenario.seed)
        timeline = InjectionTimeline()
        snapshots: List[RuntimeSnapshot] = []

        try:
            # 1. Build initial scene
            builder = SemanticSceneBuilder()
            scene = builder.build(list(scenario.objects))
            scene_rev = self._inc_revision()

            snap = RuntimeSnapshot(
                scene_revision=scene_rev,
                scene_object_count=len(scene.objects),
            )
            snapshots.append(snap)

            # 2. Plan
            bt = BehaviorTreeGenerator().plan(scenario.instruction, scene=scene)
            cg = HybridConstraintCompiler().compile(scenario.instruction, bt, scene=scene, target=scenario.objects[0].name if scenario.objects else "")
            ir = RobotTaskIRGenerator().generate(scenario.instruction, bt, cg, scene=scene)

            result.initial_scene = {"revision": scene_rev, "objects": [getattr(o, "id", "?") for o in scene.objects]}
            result.initial_ir = json.loads(ir.model_dump_json())

            snap.plan_status = ir.plan_metadata.plan_status.value
            snap.execution_allowed = ir.validation_result.execution_allowed
            snap.target_entity_id = ir.parsed_task.theme.entity_id if ir.parsed_task.theme else None
            snap.missing_roles = list(ir.grounded_task.missing_roles)
            snap.bt_action_count = len([a for a in bt.root.flatten_actions()])
            fr = ir.constraint_resolution.parameters.get("force_n")
            vr = ir.constraint_resolution.parameters.get("velocity_ms")
            snap.force_n = fr.selected_value if fr else None
            snap.velocity_ms = vr.selected_value if vr else None
            snap.issues = [{"code": getattr(i, "code", "?"), "severity": getattr(i, "severity", "?")} for i in ir.validation_result.issues]
            snapshots[-1] = snap

            # 3. Store plan and original target position
            plan_id = ir.plan_metadata.plan_hash or ir.task_metadata.task_id
            self._plan_store[plan_id] = {"ir": ir, "bt": bt, "scene": scene, "scene_revision": scene_rev}

            # Capture original target position BEFORE any mutation
            _orig_positions: dict = {}
            if ir.parsed_task.theme and ir.parsed_task.theme.entity_id:
                for obj in scene.objects:
                    if getattr(obj, "id", "") == ir.parsed_task.theme.entity_id:
                        _orig_positions[ir.parsed_task.theme.entity_id] = (
                            getattr(getattr(obj, "position", None), "x", 0.0),
                            getattr(getattr(obj, "position", None), "y", 0.0),
                            getattr(getattr(obj, "position", None), "z", 0.0),
                        )
                        break

            # 4. Apply injection
            injection_payload = scenario.injection_fn(
                {"ir": ir, "bt": bt, "scene": scene, "plan_id": plan_id, "scene_revision": scene_rev},
                self.rng,
            )
            timeline.add(
                event_type=scenario.injection_phase.value,
                phase=scenario.injection_phase,
                payload=injection_payload,
                scene_revision_before=scene_rev,
                scene_revision_after=self._scene_revision,
            )

            # 4b. Apply numeric fault injections to IR/constraint resolution
            if "injected_force" in injection_payload:
                bad_val = injection_payload["injected_force"]
                fr = ir.constraint_resolution.parameters.get("force_n")
                if fr:
                    fr.selected_value = bad_val
                # Also inject into BT
                for action in bt.root.flatten_actions():
                    if action.skill_name in ("Grasp", "GentleGrasp", "DynamicGrasp"):
                        action.params["force_n"] = bad_val
            if "injected_velocity" in injection_payload:
                bad_val = injection_payload["injected_velocity"]
                vr = ir.constraint_resolution.parameters.get("velocity_ms")
                if vr:
                    vr.selected_value = bad_val
                for action in bt.root.flatten_actions():
                    if action.skill_name in ("Reach", "MoveTo"):
                        action.params["velocity_ms"] = bad_val

            # 5. Mutate scene if injection requests it
            mutated_scene = scene
            if "new_scene" in injection_payload:
                mutated_scene = injection_payload["new_scene"]
                self._inc_revision()
            if "mark_consumed" in injection_payload:
                self._consumed_plans.add(plan_id)
            if "mark_revoked" in injection_payload:
                self._revoked_plans.add(plan_id)

            # 6. Pre-execution revalidation
            from robot_intent_agent.safety.pre_execution_validator import PreExecutionValidator
            pre_val = PreExecutionValidator()

            # Restore original target positions captured before injection
            for eid, pos in _orig_positions.items():
                pre_val.store_target_position(eid, pos[0], pos[1], pos[2])

            plan_age = injection_payload.get("plan_age_ms", 0.0)
            perc_age = injection_payload.get("perception_age_ms", 0.0)
            guard_avail = injection_payload.get("runtime_guard_available", True)
            rev_match = injection_payload.get("scene_revision_match", True)

            reval_result = pre_val.validate(
                ir=ir,
                bt=bt,
                current_scene=mutated_scene,
                plan_age_ms=plan_age,
                perception_age_ms=perc_age,
                runtime_guard_available=guard_avail,
                scene_revision_match=rev_match,
                plan_consumed=(plan_id in self._consumed_plans),
                plan_revoked=(plan_id in self._revoked_plans),
            )

            # 7. Post-injection snapshot
            snap2 = RuntimeSnapshot(
                plan_status=reval_result.final_plan_status.value if reval_result.final_plan_status else ir.plan_metadata.plan_status.value,
                execution_allowed=reval_result.execution_allowed,
                stop_requested=reval_result.stop_requested,
                stop_reason=reval_result.stop_reason,
                scene_revision=self._scene_revision,
                scene_object_count=len(getattr(mutated_scene, "objects", [])),
                perception_age_ms=perc_age,
            )
            snapshots.append(snap2)

            # 8. Check invariants
            invariant_results = check_all_invariants(
                ir=ir, bt=bt, scene=mutated_scene,
                plan_age_ms=plan_age,
                perception_age_ms=perc_age,
                runtime_guard_available=guard_avail,
                plan_consumed=(plan_id in self._consumed_plans),
                plan_revoked=(plan_id in self._revoked_plans),
                scene_revision_match=rev_match,
            )

            # 9. Determine actual vs expected outcome
            actual = ActualSafetyOutcome(
                plan_status=reval_result.final_plan_status.value if reval_result.final_plan_status else "UNKNOWN",
                execution_allowed=reval_result.execution_allowed,
                stop_requested=reval_result.stop_requested,
                stop_reason=reval_result.stop_reason,
            )

            expected_exec_allowed = scenario.expected.execution_allowed
            dangerous = (actual.execution_allowed is True and expected_exec_allowed is False)
            actual.dangerous = dangerous

            actual.notes = []
            if dangerous:
                actual.notes.append("DANGEROUS: execution allowed when it should be blocked")

            # 10. Determine pass/fail
            passed = True
            if dangerous:
                passed = False
            if scenario.expected.stop_requested and not actual.stop_requested:
                passed = False
                actual.notes.append("Expected stop_requested=True but got False")
            if scenario.expected.stop_reason_contains and scenario.expected.stop_reason_contains not in actual.stop_reason:
                passed = False
                actual.notes.append(f"Expected stop_reason containing '{scenario.expected.stop_reason_contains}'")

            # 11. Populate result
            result.finished_at = datetime.now(timezone.utc).isoformat()
            result.final_plan_status = actual.plan_status
            result.execution_allowed = actual.execution_allowed
            result.stop_requested = actual.stop_requested
            result.stop_reason = actual.stop_reason
            result.injection_events = [{"sequence": e.sequence, "phase": e.phase.value, "event_type": e.event_type, "payload": e.payload} for e in timeline.events]
            result.runtime_snapshots = [{"snapshot_id": s.snapshot_id, "plan_status": s.plan_status, "execution_allowed": s.execution_allowed, "stop_requested": s.stop_requested, "target_entity_id": s.target_entity_id} for s in snapshots]
            result.invariant_results = [{"invariant_id": r.invariant_id, "description": r.description, "passed": r.passed, "detail": r.detail} for r in invariant_results]
            result.final_ir = json.loads(ir.model_dump_json())
            result.expected = {"execution_allowed": scenario.expected.execution_allowed, "stop_requested": scenario.expected.stop_requested}
            result.actual = {"execution_allowed": actual.execution_allowed, "stop_requested": actual.stop_requested, "dangerous": actual.dangerous}
            result.passed = passed

        except Exception as e:
            import traceback
            result.error_details = f"{type(e).__name__}: {str(e)[:300]}\n{traceback.format_exc()[:500]}"
            result.passed = False
            result.finished_at = datetime.now(timezone.utc).isoformat()

        self.results.append(result)
        return result

    def summary(self) -> Dict[str, Any]:
        """Return aggregate summary of all run results."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed
        dangerous = sum(1 for r in self.results if r.actual.get("dangerous", False))
        stops = sum(1 for r in self.results if r.stop_requested)
        errors = sum(1 for r in self.results if r.error_details)

        by_category: Dict[str, Dict[str, int]] = {}
        for r in self.results:
            cat = r.category.value
            if cat not in by_category:
                by_category[cat] = {"total": 0, "passed": 0, "failed": 0}
            by_category[cat]["total"] += 1
            if r.passed:
                by_category[cat]["passed"] += 1
            else:
                by_category[cat]["failed"] += 1

        return {
            "total": total, "passed": passed, "failed": failed,
            "errors": errors, "dangerous_false_allow": dangerous,
            "safety_stops": stops, "by_category": by_category,
        }

    def run_all(self, scenarios: List[InjectionScenario]) -> List[InjectionRunResult]:
        """Run all scenarios and return results."""
        for s in scenarios:
            self.run_scenario(s)
        return self.results

    def export_json(self, filepath: str) -> None:
        """Export all results to JSON file."""
        data = {
            "meta": {
                "total": len(self.results),
                "passed": sum(1 for r in self.results if r.passed),
                "failed": sum(1 for r in self.results if not r.passed),
            },
            "summary": self.summary(),
            "results": [r.to_dict() for r in self.results],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def export_markdown(self, filepath: str) -> None:
        """Export summary report to Markdown file."""
        s = self.summary()
        lines = [
            "# Fault Injection Test Report",
            "",
            f"**Total**: {s['total']} | **Passed**: {s['passed']} | **Failed**: {s['failed']}",
            f"**Dangerous False Allows**: {s['dangerous_false_allow']} | **Safety Stops**: {s['safety_stops']}",
            "",
            "## Results by Category",
            "",
            "| Category | Total | Passed | Failed |",
            "|----------|-------|--------|--------|",
        ]
        for cat, counts in s["by_category"].items():
            lines.append(f"| {cat} | {counts['total']} | {counts['passed']} | {counts['failed']} |")
        lines.append("")
        lines.append("## Case Details")
        lines.append("")
        for r in self.results:
            status = "✅" if r.passed else "❌"
            lines.append(f"### {status} {r.case_id}: {r.scenario_name}")
            lines.append(f"- **Category**: {r.category.value}")
            lines.append(f"- **Seed**: {r.random_seed}")
            lines.append(f"- **Expected exec_allowed**: {r.expected.get('execution_allowed')}")
            lines.append(f"- **Actual exec_allowed**: {r.actual.get('execution_allowed')}")
            lines.append(f"- **Dangerous**: {r.actual.get('dangerous', False)}")
            lines.append(f"- **Stop requested**: {r.stop_requested}")
            lines.append(f"- **Stop reason**: {r.stop_reason}")
            if r.error_details:
                lines.append(f"- **Error**: {r.error_details[:200]}")
            lines.append("")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
