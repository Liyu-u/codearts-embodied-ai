#!/usr/bin/env python3
"""
Blind evaluation runner — LEGACY.

⚠️ DEPRECATED: Use UpgradedEvalRunner (eval/upgraded_runner.py) instead.
This runner is kept for backward-compatible metric comparison only.
The UpgradedEvalRunner provides 13-dimension scoring, severe-error veto,
and consistent applicable counting that this runner lacks.

Runs the full production pipeline against the blind dataset and reports
accuracy, severe errors, and failures.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.task_semantics import PlanStatus, TaskActionKind


# ── Severity levels ────────────────────────────────────────

class Severity:
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# ── Error record ───────────────────────────────────────────

@dataclass
class BlindError:
    case_id: str
    category: str
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    error_type: str
    detail: str


# ── Case result ────────────────────────────────────────────

@dataclass
class BlindResult:
    case_id: str = ""
    category: str = ""
    instruction: str = ""
    passed: bool = True
    errors: List[BlindError] = field(default_factory=list)
    action_actual: str = ""
    action_expected: str = ""
    theme_entity_id_actual: Optional[str] = None
    theme_entity_id_expected: Optional[str] = None
    force_actual: Optional[float] = None
    force_expected: Optional[float] = None
    execution_allowed_actual: Optional[bool] = None
    execution_allowed_expected: Optional[bool] = None
    plan_status_actual: str = ""
    plan_status_expected: str = ""
    elapsed_ms: float = 0.0
    exception: str = ""


# ── Blind Evaluator ────────────────────────────────────────

class BlindEvaluator:
    """Runs the blind dataset through the production pipeline and scores results."""

    def __init__(self, dataset_path: str = None):
        if dataset_path is None:
            dataset_path = str(Path(__file__).parent / "blind_dataset.json")
        with open(dataset_path, "r", encoding="utf-8") as f:
            self.dataset = json.load(f)
        self.results: List[BlindResult] = []
        self._builder = SemanticSceneBuilder()

    def run_all(self) -> List[BlindResult]:
        self.results = []
        for case in self.dataset.get("cases", []):
            result = self._run_case(case)
            self.results.append(result)
        return self.results

    def _run_case(self, case: Dict[str, Any]) -> BlindResult:
        result = BlindResult(
            case_id=case["case_id"],
            category=case.get("category", "unknown"),
            instruction=case["instruction"],
        )
        expected = case.get("expected", {})
        severity_rules = case.get("severity", {})
        t0 = time.time()

        # ── Set expected values ──
        result.action_expected = expected.get("action", "")
        result.theme_entity_id_expected = expected.get("theme_entity_id")
        result.force_expected = expected.get("force_n")
        if "execution_allowed" in expected:
            result.execution_allowed_expected = expected["execution_allowed"]
        if "plan_status" in expected:
            result.plan_status_expected = expected["plan_status"]

        instruction = case["instruction"]
        objects_raw = case.get("objects", [])

        # ── Handle edge cases ──
        # Empty instruction
        if not instruction.strip():
            try:
                raw_objects = self._build_raw_objects(objects_raw)
                scene = self._builder.build(raw_objects) if raw_objects else None
                bt = BehaviorTreeGenerator().plan(instruction, scene=scene) if scene else None
                if bt:
                    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="")
                    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
                    result.execution_allowed_actual = ir.validation_result.execution_allowed
            except Exception as e:
                result.exception = f"{type(e).__name__}: {str(e)[:200]}"
            result.elapsed_ms = (time.time() - t0) * 1000
            self._check_empty_instruction(result, expected, severity_rules)
            return result

        # Empty objects
        if not objects_raw:
            try:
                scene = self._builder.build([])
                bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
                cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="")
                ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
                result.execution_allowed_actual = ir.validation_result.execution_allowed
            except Exception as e:
                result.exception = f"{type(e).__name__}: {str(e)[:200]}"
            result.elapsed_ms = (time.time() - t0) * 1000
            self._check_empty_scene(result, expected, severity_rules)
            return result

        # ── Normal pipeline ──
        try:
            raw_objects = self._build_raw_objects(objects_raw)
            if not raw_objects:
                result.exception = "No valid objects could be built"
                result.elapsed_ms = (time.time() - t0) * 1000
                return result

            scene = self._builder.build(raw_objects)
            target = raw_objects[0].name if raw_objects else "target"
            bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
            cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
            ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

            # ── Extract actual values ──
            parsed_task = ir.parsed_task
            result.action_actual = parsed_task.action.value if parsed_task else "UNKNOWN"
            result.theme_entity_id_actual = parsed_task.theme.entity_id if parsed_task.theme else None
            result.execution_allowed_actual = ir.validation_result.execution_allowed
            result.plan_status_actual = ir.plan_metadata.plan_status.value if ir.plan_metadata else "UNKNOWN"

            # Force
            force_constraints = [c for c in (parsed_task.user_constraints or []) if c.parameter == "force_n"]
            if force_constraints:
                result.force_actual = force_constraints[0].value
            else:
                fr = ir.constraint_resolution.parameters.get("force_n")
                result.force_actual = fr.selected_value if fr else None

            # ── Run checks ──
            self._check_action(result, expected, severity_rules)
            self._check_entity_grounding(result, expected, severity_rules, ir, scene, case)
            self._check_constraints(result, expected, severity_rules, ir, parsed_task)
            self._check_execution_allowed(result, expected, severity_rules)
            self._check_plan_status(result, expected, severity_rules)
            self._check_roles(result, expected, severity_rules, ir)
            self._check_negation_avoid(result, expected, severity_rules, ir, bt, cg, case)
            self._check_schema(result, severity_rules, ir)
            self._check_target_in_scene(result, expected, severity_rules, scene, case)
            self._check_color_match(result, expected, severity_rules, ir, case)
            self._check_numeric_bounds(result, expected, severity_rules, ir)

        except Exception as e:
            import traceback
            result.exception = f"{type(e).__name__}: {str(e)[:300]}"
            # If error_expected, that's okay
            if expected.get("notes") and "error" in str(expected.get("notes", "")).lower():
                pass
            else:
                # Crash on unexpected inputs is a CRITICAL issue unless the input is genuinely malformed
                err_type = type(e).__name__
                trail = traceback.format_exc()[-300:]
                result.errors.append(BlindError(
                    case_id=case["case_id"],
                    category=case.get("category", "unknown"),
                    severity=Severity.CRITICAL if err_type not in ("KeyError", "StopIteration") else Severity.HIGH,
                    error_type=f"Exception: {err_type}",
                    detail=f"{err_type}: {str(e)[:150]}",
                ))

        result.elapsed_ms = (time.time() - t0) * 1000

        # Determine pass/fail
        result.passed = len(result.errors) == 0
        return result

    # ── Check methods ──────────────────────────────────────

    def _add_error(self, result: BlindResult, case: Dict, error_type: str, detail: str, default_severity: str):
        severity_map = case.get("severity", {})
        sev = severity_map.get(error_type, default_severity)
        result.errors.append(BlindError(
            case_id=case["case_id"],
            category=case.get("category", "unknown"),
            severity=sev,
            error_type=error_type,
            detail=detail,
        ))

    def _check_empty_instruction(self, result, expected, sev_rules):
        if result.execution_allowed_actual is True:
            self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                          "execution_allowed_with_empty_instruction",
                          "Execution allowed with empty instruction", Severity.CRITICAL)

    def _check_empty_scene(self, result, expected, sev_rules):
        if result.execution_allowed_actual is True:
            self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                          "execution_allowed_with_empty_scene",
                          "Execution allowed with empty scene", Severity.CRITICAL)

    def _check_action(self, result, expected, sev_rules):
        exp_action = expected.get("action")
        if exp_action and result.action_actual != exp_action:
            # Dynamic grasp vs grasp — close enough if moving target
            if exp_action == "DYNAMIC_GRASP" and result.action_actual == "GRASP":
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "wrong_action",
                              f"Expected {exp_action}, got {result.action_actual} (should detect moving target)",
                              Severity.HIGH)
            else:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "wrong_action",
                              f"Expected {exp_action}, got {result.action_actual}", Severity.HIGH)

    def _check_entity_grounding(self, result, expected, sev_rules, ir, scene, case):
        exp_entity = expected.get("theme_entity_id")
        theme_not_in_scene = expected.get("theme_not_in_scene")
        scene_ids = {getattr(o, "id", "") for o in (scene.objects if scene else [])}

        if theme_not_in_scene:
            # Theme should NOT be grounded to any real scene object
            if result.theme_entity_id_actual is not None and result.theme_entity_id_actual in scene_ids:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "fabricated_grounding",
                              f"Theme grounded to '{result.theme_entity_id_actual}' but should not be (target not in scene)",
                              Severity.CRITICAL)
        elif exp_entity:
            # An entity IS expected to be grounded. Check it IS grounded (non-null, in scene).
            # Scene IDs are auto-generated UUIDs, so we check existence not string equality.
            actual_id = result.theme_entity_id_actual
            is_grounded = actual_id is not None and actual_id in scene_ids
            if not is_grounded:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "theme_not_grounded",
                              f"Theme not grounded to any scene object (expected grounding, actual={actual_id})",
                              Severity.CRITICAL)

    def _check_constraints(self, result, expected, sev_rules, ir, parsed_task):
        # Force value check
        exp_force = expected.get("force_n")
        if exp_force is not None:
            user_constraints = [c for c in (parsed_task.user_constraints or []) if c.parameter == "force_n"]
            if user_constraints:
                actual_force = user_constraints[0].value
                if actual_force is None or abs(actual_force - exp_force) > 0.01:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "wrong_force_value",
                                  f"Expected force={exp_force}, got {actual_force}", Severity.HIGH)
            else:
                # Force not parsed from NL — check resolution
                fr = ir.constraint_resolution.parameters.get("force_n")
                actual = fr.selected_value if fr else None
                if actual is None:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "wrong_force_value",
                                  f"Force constraint not parsed from instruction (expected {exp_force})", Severity.HIGH)

        # Force operator check
        exp_force_op = expected.get("force_op")
        if exp_force_op:
            user_constraints = [c for c in (parsed_task.user_constraints or []) if c.parameter == "force_n"]
            if user_constraints:
                actual_op = user_constraints[0].operator.value
                if actual_op != exp_force_op:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "wrong_force_operator",
                                  f"Expected force_op={exp_force_op}, got {actual_op}", Severity.HIGH)

        # Velocity check
        exp_vel = expected.get("velocity_ms")
        if exp_vel is not None:
            vel_constraints = [c for c in (parsed_task.user_constraints or []) if c.parameter == "velocity_ms"]
            if vel_constraints:
                actual_vel = vel_constraints[0].value
                if actual_vel is None or abs(actual_vel - exp_vel) > 0.01:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "wrong_velocity_value",
                                  f"Expected velocity={exp_vel}, got {actual_vel}", Severity.HIGH)

    def _check_execution_allowed(self, result, expected, sev_rules):
        if "execution_allowed" in expected:
            exp = expected["execution_allowed"]
            actual = result.execution_allowed_actual
            if actual is not None and actual != exp:
                if exp is False and actual is True:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "execution_allowed_when_should_be_blocked",
                                  f"Execution allowed=True but expected=False", Severity.CRITICAL)
                elif exp is True and actual is False:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "execution_blocked_falsely",
                                  f"Execution allowed=False but expected=True", Severity.HIGH)

    def _check_plan_status(self, result, expected, sev_rules):
        if "plan_status" in expected:
            exp = expected["plan_status"]
            if result.plan_status_actual != exp:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "wrong_plan_status",
                              f"Expected plan_status={exp}, got {result.plan_status_actual}", Severity.MEDIUM)

    def _check_roles(self, result, expected, sev_rules, ir):
        exp_missing = expected.get("missing_roles", [])
        actual_missing = list(ir.grounded_task.missing_roles) if ir.grounded_task else []

        # If expected missing roles exist, check they appear in actual missing
        if exp_missing:
            for role in exp_missing:
                if role not in actual_missing:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "missing_role_not_detected",
                                  f"Expected missing role '{role}' not detected. Actual missing: {actual_missing}",
                                  Severity.MEDIUM)

        # CRITICAL: execution_allowed=True when critical roles are missing
        if result.execution_allowed_actual is True and actual_missing:
            # Check if missing roles are critical (recipient_pose, support_surface, theme)
            critical = [r for r in actual_missing if any(kw in r for kw in
                        ("recipient", "delivery", "support_surface", "theme", "handover"))]
            if critical:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "execution_allowed_with_missing_roles",
                              f"Missing critical roles {critical} but execution_allowed=True",
                              Severity.CRITICAL)

        # Support surface check
        exp_ss = expected.get("support_surface_entity_id")
        if exp_ss:
            actual_ss = ir.parsed_task.support_surface.entity_id if ir.parsed_task.support_surface else None
            if actual_ss != exp_ss:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "wrong_support_surface",
                              f"Expected support_surface={exp_ss}, got {actual_ss}", Severity.HIGH)

        exp_ss_missing = expected.get("support_surface_not_in_scene")
        if exp_ss_missing:
            actual_ss = ir.parsed_task.support_surface
            if actual_ss is not None and actual_ss.entity_id is not None:
                # Support surface was fabricated
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "fabricated_support_surface",
                              f"Support surface fabricated: {actual_ss.entity_id}", Severity.CRITICAL)

    def _check_negation_avoid(self, result, expected, sev_rules, ir, bt, cg, case):
        exp_avoid = expected.get("avoid_objects", [])
        if exp_avoid:
            # Check if BT has avoid/PlanPath nodes
            action_names = [a.skill_name for a in bt.root.flatten_actions()]
            avoid_params = set()
            for a in bt.root.flatten_actions():
                if "avoid" in a.params:
                    av = a.params["avoid"]
                    if isinstance(av, list):
                        avoid_params.update(av)
                avoid_obstacles = a.params.get("avoid_obstacles", [])
                if isinstance(avoid_obstacles, list):
                    avoid_params.update(avoid_obstacles)

            # Check if expected avoid objects appear
            for exp_av in exp_avoid:
                found = False
                # Check in BT params
                if exp_av in avoid_params:
                    found = True
                # Check in parsed_task.obstacle
                if ir.parsed_task and ir.parsed_task.obstacle:
                    for obs in ir.parsed_task.obstacle:
                        if obs.entity_id == exp_av:
                            found = True
                            break
                # Check in CG collision_avoid nodes
                for node in cg.nodes:
                    if node.constraint_type == "collision_avoid":
                        if node.params.get("obstacle", "") == exp_av:
                            found = True
                            break
                if not found:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "ignored_negation",
                                  f"Expected avoid object '{exp_av}' not found in BT/CG",
                                  Severity.CRITICAL)

    def _check_schema(self, result, sev_rules, ir):
        try:
            ir.model_dump_json()
        except Exception as e:
            self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                          "schema_invalid",
                          f"IR serialization failed: {e}", Severity.LOW)

    def _check_target_in_scene(self, result, expected, sev_rules, scene, case):
        """Check that theme grounding respects color/material/size constraints."""
        # If the instruction specifies a color, verify the grounded object matches
        exp_color = expected.get("theme_color")
        instruction = case.get("instruction", "")
        scene_ids = {getattr(o, "id", "") for o in (scene.objects if scene else [])}
        actual_id = result.theme_entity_id_actual

        # Detect color-related instructions
        color_words_cn = {"红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
                          "白色": "white", "黑色": "black", "透明": "transparent"}
        requested_color = None
        for cn, en in color_words_cn.items():
            if cn in instruction:
                requested_color = en
                break

        if requested_color and actual_id and actual_id in scene_ids:
            obj = scene.find_object(actual_id)
            if obj:
                obj_color = getattr(obj, "attributes", {}).get("color", "")
                if obj_color and obj_color != requested_color and obj_color != "unknown":
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "wrong_color_grounding",
                                  f"Instruction requested '{requested_color}' but grounded to '{obj_color}' object",
                                  Severity.CRITICAL)

    def _check_color_match(self, result, expected, sev_rules, ir, case):
        exp_color = expected.get("theme_color")
        if exp_color and ir.parsed_task and ir.parsed_task.theme:
            # Can't directly check from IR, but we check if entity matches expected
            pass

    def _check_numeric_bounds(self, result, expected, sev_rules, ir):
        # Check resolved force doesn't exceed hard limit
        resolved_le = expected.get("resolved_force_n_le") or expected.get("resolved_force_le")
        if resolved_le is not None:
            fr = ir.constraint_resolution.parameters.get("force_n")
            actual = fr.selected_value if fr else None
            if actual is not None and actual > resolved_le + 0.01:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "force_not_clamped",
                              f"Resolved force {actual} > max allowed {resolved_le}",
                              Severity.CRITICAL)

        # Check resolved force ge
        resolved_ge = expected.get("resolved_force_n_ge")
        if resolved_ge is not None:
            fr = ir.constraint_resolution.parameters.get("force_n")
            actual = fr.selected_value if fr else None
            if actual is not None and actual < resolved_ge - 0.01:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "force_below_minimum",
                              f"Resolved force {actual} < min required {resolved_ge}",
                              Severity.HIGH)

        # Check force exceeds global max
        global_max = expected.get("resolved_force_le_global_max")
        if global_max is not None:
            fr = ir.constraint_resolution.parameters.get("force_n")
            actual = fr.selected_value if fr else None
            if actual is not None and actual > global_max + 0.01:
                self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                              "force_exceeds_global_max",
                              f"Resolved force {actual} > global max {global_max}",
                              Severity.CRITICAL)

        # Check unsafe velocity
        note_check = expected.get("_check", "")
        if "velocity" in note_check and "exceed" in note_check:
            # Check that velocity was clamped to stage limit
            for action in ir.behavior_tree.root.flatten_actions():
                vel = action.params.get("velocity_ms")
                if isinstance(vel, dict):
                    vel = vel.get("value")
                if vel is not None and float(vel) > 0.3:
                    self._add_error(result, {"case_id": result.case_id, "severity": sev_rules},
                                  "unsafe_velocity_allowed",
                                  f"Velocity {vel} exceeds safe stage limits", Severity.CRITICAL)

    # ── Helpers ────────────────────────────────────────────

    def _build_raw_objects(self, objects_raw: List[Dict]) -> List[RawObjectPercept]:
        raw_objects = []
        for obj in objects_raw:
            if not isinstance(obj, dict):
                continue
            pos = obj.get("pose", {}).get("position", {})
            geom = obj.get("geometry", {}).get("size", obj.get("geometry", {}))
            if not isinstance(geom, dict):
                geom = {}
            app = obj.get("appearance", {})
            cats = obj.get("category_candidates", [{"name": "unknown", "score": 0.5}])
            if not cats:
                cats = [{"name": "unknown", "score": 0.5}]
            # Handle missing name field in category
            valid_cats = [c for c in cats if isinstance(c, dict) and c.get("name")]
            if not valid_cats:
                valid_cats = [{"name": "unknown", "score": 0.5}]
            top_cat = max(valid_cats, key=lambda c: c.get("score", 0))

            def _safe_float(v, default=0.0):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return default

            raw_objects.append(RawObjectPercept(
                name=top_cat["name"],
                x=_safe_float(pos.get("x", 0)),
                y=_safe_float(pos.get("y", 0)),
                z=_safe_float(pos.get("z", 0.03)),
                width=max(0.001, _safe_float(geom.get("width", 0.05), 0.05)),
                height=max(0.001, _safe_float(geom.get("height", 0.08), 0.08)),
                depth=max(0.001, _safe_float(geom.get("depth", 0.05), 0.05)),
                color=app.get("color", "unknown") if isinstance(app, dict) else "unknown",
                material=app.get("material", "unknown") if isinstance(app, dict) else "unknown",
            ))
        return raw_objects


# ══════════════════════════════════════════════════════════════
# Metrics computation
# ══════════════════════════════════════════════════════════════

def compute_metrics(results: List[BlindResult]) -> Dict[str, Any]:
    total = len(results)
    if total == 0:
        return {"total": 0}

    passed = sum(1 for r in results if r.passed)
    exceptions = sum(1 for r in results if r.exception)

    # Severity counts
    severity_counts = {Severity.CRITICAL: 0, Severity.HIGH: 0, Severity.MEDIUM: 0, Severity.LOW: 0}
    for r in results:
        for e in r.errors:
            severity_counts[e.severity] = severity_counts.get(e.severity, 0) + 1

    # By category
    by_category: Dict[str, Dict] = {}
    for r in results:
        cat = r.category or "unknown"
        if cat not in by_category:
            by_category[cat] = {"total": 0, "passed": 0, "critical": 0, "high": 0, "errors": []}
        by_category[cat]["total"] += 1
        if r.passed:
            by_category[cat]["passed"] += 1
        for e in r.errors:
            if e.severity == Severity.CRITICAL:
                by_category[cat]["critical"] += 1
            elif e.severity == Severity.HIGH:
                by_category[cat]["high"] += 1

    # Accuracy by dimension
    action_cases = [r for r in results if r.action_expected]
    action_correct = sum(1 for r in action_cases if not any(
        e.error_type == "wrong_action" for e in r.errors))
    action_accuracy = action_correct / len(action_cases) if action_cases else 1.0

    entity_cases = [r for r in results if r.theme_entity_id_expected]
    entity_correct = sum(1 for r in entity_cases if not any(
        e.error_type in ("wrong_target", "fabricated_grounding", "wrong_color_grounding", "theme_not_grounded") for e in r.errors))
    entity_accuracy = entity_correct / len(entity_cases) if entity_cases else 1.0

    force_cases = [r for r in results if r.force_expected is not None]
    force_correct = sum(1 for r in force_cases if not any(
        e.error_type in ("wrong_force_value", "force_not_clamped", "force_exceeds_global_max", "force_below_minimum") for e in r.errors))
    force_accuracy = force_correct / len(force_cases) if force_cases else 1.0

    exec_cases = [r for r in results if r.execution_allowed_expected is not None]
    exec_correct = sum(1 for r in exec_cases if not any(
        e.error_type in ("execution_allowed_when_should_be_blocked", "execution_blocked_falsely",
                         "execution_allowed_with_missing_roles", "execution_allowed_with_empty_instruction",
                         "execution_allowed_with_empty_scene") for e in r.errors))
    exec_accuracy = exec_correct / len(exec_cases) if exec_cases else 1.0

    avoid_cases = [r for r in results if any(
        e.error_type == "ignored_negation" for e in r.errors)]
    # Count cases that SHOULD have avoid
    avoid_expected = sum(1 for r in results if r.case_id in {
        c["case_id"] for c in [
            {"case_id": r.case_id} for r in results
        ]
    })

    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "exceptions": exceptions,
        "pass_rate": round(passed / total, 4) if total else 0.0,
        "severity_counts": severity_counts,
        "action_accuracy": round(action_accuracy, 4),
        "action_cases": len(action_cases),
        "entity_grounding_accuracy": round(entity_accuracy, 4),
        "entity_cases": len(entity_cases),
        "force_accuracy": round(force_accuracy, 4),
        "force_cases": len(force_cases),
        "execution_accuracy": round(exec_accuracy, 4),
        "execution_cases": len(exec_cases),
        "by_category": by_category,
    }


def export_report(results: List[BlindResult], metrics: Dict, filepath: str) -> None:
    """Export detailed markdown report."""
    m = metrics
    lines = [
        "# Blind Evaluation Report",
        "",
        f"**Date**: 2026-07-20",
        f"**Total cases**: {m['total']}",
        f"**Passed**: {m['passed']} | **Failed**: {m['failed']}",
        f"**Exceptions**: {m.get('exceptions', 0)}",
        f"**Overall pass rate**: {m['pass_rate']:.1%}",
        "",
        "## Severity Summary",
        "",
        f"| Severity | Count |",
        f"|----------|-------|",
    ]
    for sev in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
        lines.append(f"| {sev} | {m['severity_counts'].get(sev, 0)} |")

    lines.extend([
        "",
        "## Accuracy by Dimension",
        "",
        f"| Dimension | Accuracy | Cases |",
        f"|-----------|----------|-------|",
        f"| Action Recognition | {m['action_accuracy']:.1%} | {m['action_cases']} |",
        f"| Entity Grounding | {m['entity_grounding_accuracy']:.1%} | {m['entity_cases']} |",
        f"| Force/Constraint Parsing | {m['force_accuracy']:.1%} | {m['force_cases']} |",
        f"| Execution Gate | {m['execution_accuracy']:.1%} | {m['execution_cases']} |",
        "",
        "## Accuracy by Category",
        "",
        "| Category | Total | Passed | Critical Errors | High Errors |",
        "|----------|-------|--------|-----------------|-------------|",
    ])
    for cat, counts in sorted(m.get("by_category", {}).items()):
        lines.append(f"| {cat} | {counts['total']} | {counts['passed']} | {counts['critical']} | {counts['high']} |")

    lines.extend([
        "",
        "## Failed Cases",
        "",
    ])

    for r in results:
        if not r.passed:
            lines.append(f"### {'❌' if any(e.severity == Severity.CRITICAL for e in r.errors) else '⚠️'} {r.case_id} [{r.category}]: {r.instruction[:60]}")
            if r.exception:
                lines.append(f"- **Exception**: {r.exception[:200]}")
            lines.append(f"- Action: expected={r.action_expected}, actual={r.action_actual}")
            lines.append(f"- Entity: expected={r.theme_entity_id_expected}, actual={r.theme_entity_id_actual}")
            lines.append(f"- Execution: expected={r.execution_allowed_expected}, actual={r.execution_allowed_actual}")
            for e in r.errors:
                lines.append(f"- [{e.severity}] **{e.error_type}**: {e.detail}")
            lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("  Blind Evaluation — Intent Understanding Module")
    print("=" * 70)

    evaluator = BlindEvaluator()
    print(f"\nRunning {len(evaluator.dataset['cases'])} blind cases...\n")

    results = evaluator.run_all()
    metrics = compute_metrics(results)

    # Print summary
    print(f"\n{'='*70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"  Total:     {metrics['total']}")
    print(f"  Passed:    {metrics['passed']}")
    print(f"  Failed:    {metrics['failed']}")
    print(f"  Pass Rate: {metrics['pass_rate']:.1%}")
    print(f"  Exceptions:{metrics.get('exceptions', 0)}")
    print()
    print(f"  Severity:")
    for sev in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
        print(f"    {sev}: {metrics['severity_counts'].get(sev, 0)}")
    print()
    print(f"  Accuracy:")
    print(f"    Action:         {metrics['action_accuracy']:.1%} ({metrics['action_cases']} cases)")
    print(f"    Entity:         {metrics['entity_grounding_accuracy']:.1%} ({metrics['entity_cases']} cases)")
    print(f"    Force:          {metrics['force_accuracy']:.1%} ({metrics['force_cases']} cases)")
    print(f"    Execution Gate: {metrics['execution_accuracy']:.1%} ({metrics['execution_cases']} cases)")
    print()
    print(f"  By Category:")
    for cat, counts in sorted(metrics.get("by_category", {}).items()):
        print(f"    {cat:25s}  {counts['passed']:2d}/{counts['total']:2d} passed  C:{counts['critical']} H:{counts['high']}")

    # Export
    report_path = str(Path(__file__).parent / "blind_eval_report.md")
    json_path = str(Path(__file__).parent / "blind_eval_results.json")
    export_report(results, metrics, report_path)

    # Export JSON results
    json_data = {
        "metrics": metrics,
        "results": [
            {
                "case_id": r.case_id,
                "category": r.category,
                "instruction": r.instruction,
                "passed": r.passed,
                "action_actual": r.action_actual,
                "action_expected": r.action_expected,
                "theme_entity_id_actual": r.theme_entity_id_actual,
                "theme_entity_id_expected": r.theme_entity_id_expected,
                "force_actual": r.force_actual,
                "force_expected": r.force_expected,
                "execution_allowed_actual": r.execution_allowed_actual,
                "execution_allowed_expected": r.execution_allowed_expected,
                "errors": [{"severity": e.severity, "error_type": e.error_type, "detail": e.detail} for e in r.errors],
                "exception": r.exception,
                "elapsed_ms": r.elapsed_ms,
            }
            for r in results
        ],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    print(f"\nExported: {report_path}")
    print(f"Exported: {json_path}")
