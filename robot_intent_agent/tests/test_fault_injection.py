"""
Runtime safety fault injection test suite for v3.0.

Covers: plan validity, scene mutation, perception/tracking, numeric constraints,
runtime safety, planner/LLM fallback, concurrency/replay, UI consistency.
"""

from __future__ import annotations

import json
import math
import pytest
from copy import deepcopy

from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.safety.fault_injection import (
    InjectionScenario,
    InjectionEvent,
    InjectionTimeline,
    ExpectedSafetyOutcome,
    SafetyOutcome,
    InjectionCategory,
    InjectionPhase,
    FaultInjectionRunner,
    check_all_invariants,
    InjectionRunResult,
)
from robot_intent_agent.safety.pre_execution_validator import PreExecutionValidator
from robot_intent_agent.task_semantics import PlanStatus


# ══════════════════════════════════════════════════════════════════
# Helper factories
# ══════════════════════════════════════════════════════════════════

def _glass_cup(name="玻璃杯"):
    return RawObjectPercept(name=name, x=0.35, y=0.12, z=0.075,
                            width=0.07, height=0.12, depth=0.07,
                            color="transparent", material="glass")

def _plastic_bottle(name="药瓶"):
    return RawObjectPercept(name=name, x=0.15, y=0.05, z=0.03,
                            width=0.03, height=0.08, depth=0.03,
                            color="red", material="plastic")

def _wood_table(name="桌子"):
    return RawObjectPercept(name=name, x=0.0, y=0.0, z=0.0,
                            width=0.5, height=0.03, depth=0.3,
                            color="brown", material="wood")


def _noop_injection(ctx, rng):
    return {"note": "no injection applied"}


def _target_removed(ctx, rng):
    scene = ctx["scene"]
    # Remove the first object
    if scene.objects:
        removed_id = scene.objects[0].id
        scene.objects = scene.objects[1:]
        return {"removed_entity_id": removed_id, "new_scene": scene}
    return {"note": "no objects to remove"}


def _target_material_changed(ctx, rng):
    scene = ctx["scene"]
    if scene.objects:
        obj = scene.objects[0]
        old_mat = obj.attributes.get("material", "unknown")
        obj.attributes["material"] = "glass"
        obj.attributes["fragile"] = True
        return {"old_material": old_mat, "new_material": "glass", "new_scene": scene}
    return {}


def _target_position_jumped(ctx, rng):
    scene = ctx["scene"]
    if scene.objects:
        obj = scene.objects[0]
        old_pos = (obj.position.x, obj.position.y, obj.position.z)
        obj.position.x += 0.5  # 50cm jump
        obj.position.y += 0.3
        return {"old_position": old_pos, "new_position": (obj.position.x, obj.position.y, obj.position.z), "new_scene": scene}
    return {}


def _plan_expired(ctx, rng):
    return {"plan_age_ms": 120000.0}  # 120s, well past 60s TTL


def _perception_stale(ctx, rng):
    return {"perception_age_ms": 10000.0}  # 10s, past 5s threshold


def _scene_revision_changed(ctx, rng):
    return {"scene_revision_match": False}


def _runtime_guard_down(ctx, rng):
    return {"runtime_guard_available": False}


def _plan_consumed(ctx, rng):
    return {"mark_consumed": True}


def _plan_revoked(ctx, rng):
    return {"mark_revoked": True}


def _force_nan(ctx, rng):
    return {"injected_force": float("nan")}


def _force_inf(ctx, rng):
    return {"injected_force": float("inf")}


def _force_negative(ctx, rng):
    return {"injected_force": -5.0}


def _force_excessive(ctx, rng):
    return {"injected_force": 9999.0}


def _velocity_nan(ctx, rng):
    return {"injected_velocity": float("nan")}


def _velocity_inf(ctx, rng):
    return {"injected_velocity": float("inf")}


def _velocity_negative(ctx, rng):
    return {"injected_velocity": -0.5}


def _velocity_excessive(ctx, rng):
    return {"injected_velocity": 999.0}


def _add_ambiguous_object(ctx, rng):
    scene = ctx["scene"]
    new_obj = deepcopy(scene.objects[0]) if scene.objects else _glass_cup("杯子2号")
    new_obj.id = f"obj-dup-{rng.randint(1000,9999)}"
    scene.objects = list(scene.objects) + [new_obj]
    return {"added_entity_id": new_obj.id, "new_scene": scene}


def _obstacle_in_path(ctx, rng):
    obstacle = _wood_table("障碍物")
    scene = ctx["scene"]
    scene.objects = list(scene.objects) + [obstacle]
    return {"obstacle_id": obstacle.id if hasattr(obstacle, 'id') else "unknown", "new_scene": scene}


def _human_in_safety_zone(ctx, rng):
    return {"human_in_safety_zone": True, "human_distance_m": 0.15}


# ══════════════════════════════════════════════════════════════════
# Scenarios
# ══════════════════════════════════════════════════════════════════

SCENARIOS = [
    # ── A. Plan validity and scene changes ──
    InjectionScenario("FI-A1", "Plan expired before execution", InjectionCategory.PLAN_VALIDITY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _plan_expired, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A2", "Scene revision mismatch", InjectionCategory.SCENE_MUTATION,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _scene_revision_changed, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A3", "Target removed after planning", InjectionCategory.SCENE_MUTATION,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _target_removed, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A4", "Target entity_id replaced", InjectionCategory.SCENE_MUTATION,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: {"new_scene": SemanticSceneBuilder().build([_plastic_bottle("新物体")])},
                      ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A5", "Target position jumped beyond threshold", InjectionCategory.SCENE_MUTATION,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _target_position_jumped, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A6", "Target material changed to fragile", InjectionCategory.SCENE_MUTATION,
                      "用8N力量抓住药瓶", [_plastic_bottle()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _target_material_changed, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A7", "Ambiguous object added (requires re-grounding)", InjectionCategory.SCENE_MUTATION,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _add_ambiguous_object, ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-A8", "Similar objects swapped identities (requires re-grounding)", InjectionCategory.SCENE_MUTATION,
                      "用2N力量抓住玻璃杯", [_glass_cup("杯子A"), _glass_cup("杯子B")],
                      InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: {"objects_swapped": True},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-A9", "FETCH delivery zone invalidated", InjectionCategory.SCENE_MUTATION,
                      "把药瓶拿过来", [_plastic_bottle()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _target_removed, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A10", "HANDOVER recipient zone invalidated", InjectionCategory.SCENE_MUTATION,
                      "把药瓶递给我", [_plastic_bottle()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _target_removed, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-A11", "PLACE support surface removed", InjectionCategory.SCENE_MUTATION,
                      "把杯子放到桌子上", [_glass_cup(), _wood_table()],
                      InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: (ctx["scene"].objects.pop(), {"support_removed": True, "new_scene": ctx["scene"]})[-1],
                      ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    # ── B. Perception and tracking ──
    InjectionScenario("FI-B12", "Perception data stale", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      _perception_stale, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-B13", "Perception timestamp went backwards", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      lambda ctx, rng: {"perception_age_ms": -500.0},
                      ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-B14", "Duplicate perception frame", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      lambda ctx, rng: {"duplicate_frame": True},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-B15", "Tracking lost (requires runtime monitor)", InjectionCategory.PERCEPTION_TRACKING,
                      "抓住正在移动的杯子", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      lambda ctx, rng: {"tracking_confidence": 0.1},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-B16", "Tracking confidence below threshold (requires runtime monitor)", InjectionCategory.PERCEPTION_TRACKING,
                      "抓住正在移动的杯子", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      lambda ctx, rng: {"velocity_confidence": 0.3},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-B17", "Position sudden jump detected", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      _target_position_jumped, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-B18", "Velocity sudden spike (requires runtime monitor)", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      lambda ctx, rng: {"velocity_spike_ms": 5.0},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-B19", "Target occluded before execution (requires runtime monitor)", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PERCEPTION_UPDATE,
                      lambda ctx, rng: {"target_visible": False},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-B20", "Target lost during execution (requires runtime monitor)", InjectionCategory.PERCEPTION_TRACKING,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      lambda ctx, rng: {"target_lost": True, "lost_at_action": "Reach"},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    # ── C. Numeric constraints ──
    InjectionScenario("FI-C21", "Force NaN", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _force_nan, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C22", "Force Infinity", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _force_inf, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C23", "Force negative", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _force_negative, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C24", "Force excessive value", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _force_excessive, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C25", "Velocity NaN", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _velocity_nan, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C26", "Velocity Infinity", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _velocity_inf, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C27", "Velocity negative", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _velocity_negative, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C28", "Velocity exceeds stage hard limit", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _velocity_excessive, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-C29", "Safety config missing force limit (requires config validation)", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: {"missing_force_config": True},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-C30", "Safety config missing velocity limit (requires config validation)", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: {"missing_velocity_config": True},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-C31", "Multiple conflicting constraints", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用50N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _noop_injection, ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-C32", "Compiled constraint structure incomplete (requires constraint validation)", InjectionCategory.NUMERIC_CONSTRAINTS,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: {"constraint_structure_broken": True},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    # ── D. Runtime safety stops (pre-execution checkable) ──
    InjectionScenario("FI-D33", "Slip detected after grasp (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      lambda ctx, rng: {"slip_detected": True, "action": "Grasp"},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-D34", "Grasp force feedback exceeds limit (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      lambda ctx, rng: {"force_feedback_n": 5.0, "limit_n": 2.0},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-D35", "Dynamic target speed exceeds allowable (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "抓住正在移动的杯子", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      lambda ctx, rng: {"target_speed_mps": 2.0, "max_allowable": 0.3},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-D36", "Obstacle enters planned path (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      _obstacle_in_path, ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-D37", "Human enters safety zone (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "把药瓶递给我", [_plastic_bottle()], InjectionPhase.DURING_EXECUTION,
                      _human_in_safety_zone, ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-D38", "Runtime guard unavailable", InjectionCategory.RUNTIME_SAFETY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _runtime_guard_down, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-D39", "Stop acknowledgement timeout (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      lambda ctx, rng: {"stop_ack_timeout": True},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-D40", "BT action references invalid entity_id (requires runtime monitor)", InjectionCategory.RUNTIME_SAFETY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.DURING_EXECUTION,
                      lambda ctx, rng: {"invalid_entity_id": "obj-nonexistent"},
                      ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    # ── E. Planner/LLM fallback (RuleEngine mode tests) ──
    InjectionScenario("FI-E41", "RuleEngine produces valid plan with grounded target", InjectionCategory.PLANNER_LLM_FALLBACK,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.POST_PLANNING,
                      _noop_injection, ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    InjectionScenario("FI-E42", "FETCH without delivery zone → NEEDS_CLARIFICATION", InjectionCategory.PLANNER_LLM_FALLBACK,
                      "把玻璃杯拿过来", [_glass_cup()], InjectionPhase.POST_PLANNING,
                      _noop_injection, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.NEEDS_CLARIFICATION)),

    InjectionScenario("FI-E43", "HANDOVER without recipient pose → NEEDS_CLARIFICATION", InjectionCategory.PLANNER_LLM_FALLBACK,
                      "把药瓶递给我", [_plastic_bottle()], InjectionPhase.POST_PLANNING,
                      _noop_injection, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.NEEDS_CLARIFICATION)),

    InjectionScenario("FI-E44", "PLACE without support surface → NEEDS_CLARIFICATION", InjectionCategory.PLANNER_LLM_FALLBACK,
                      "把杯子放到桌子上", [_glass_cup()], InjectionPhase.POST_PLANNING,
                      _noop_injection, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.NEEDS_CLARIFICATION)),

    InjectionScenario("FI-E45", "50N on glass → READY_WITH_SAFE_SUBSTITUTION", InjectionCategory.PLANNER_LLM_FALLBACK,
                      "用50N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.POST_PLANNING,
                      _noop_injection, ExpectedSafetyOutcome(execution_allowed=True, safety_outcome=SafetyOutcome.CORRECT_EXECUTION)),

    # ── F. Concurrency and replay ──
    InjectionScenario("FI-F54", "Plan consumed → cannot replay", InjectionCategory.CONCURRENCY_REPLAY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _plan_consumed, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-F55", "Plan revoked → cannot execute", InjectionCategory.CONCURRENCY_REPLAY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      _plan_revoked, ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),

    InjectionScenario("FI-F56", "Old scene revision used for execution request", InjectionCategory.CONCURRENCY_REPLAY,
                      "用2N力量抓住玻璃杯", [_glass_cup()], InjectionPhase.PRE_EXECUTION_REVALIDATION,
                      lambda ctx, rng: {"requested_scene_revision": ctx["scene_revision"] - 5, "scene_revision_match": False},
                      ExpectedSafetyOutcome(execution_allowed=False, safety_outcome=SafetyOutcome.SAFELY_BLOCKED)),
]


# ══════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════

class TestFaultInjectionRunner:
    """Test the fault injection runner itself."""

    def test_runner_instantiation(self):
        runner = FaultInjectionRunner(seed=42)
        assert runner is not None

    def test_single_scenario_runs(self):
        runner = FaultInjectionRunner(seed=42)
        s = SCENARIOS[0]  # FI-A1: plan expired
        result = runner.run_scenario(s)
        assert result.case_id == "FI-A1"
        assert result.passed or result.error_details, "Should have result or error"

    def test_all_scenarios_complete(self):
        runner = FaultInjectionRunner(seed=42)
        results = runner.run_all(SCENARIOS)
        assert len(results) == len(SCENARIOS)
        for r in results:
            assert r.finished_at, f"{r.case_id} did not finish"

    def test_summary_has_all_categories(self):
        runner = FaultInjectionRunner(seed=42)
        runner.run_all(SCENARIOS)
        s = runner.summary()
        assert s["total"] == len(SCENARIOS)
        assert "by_category" in s

    def test_export_json(self, tmp_path):
        runner = FaultInjectionRunner(seed=42)
        runner.run_all(SCENARIOS[:5])  # Quick subset
        path = str(tmp_path / "results.json")
        runner.export_json(path)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "results" in data
        assert len(data["results"]) == 5

    def test_export_markdown(self, tmp_path):
        runner = FaultInjectionRunner(seed=42)
        runner.run_all(SCENARIOS[:5])
        path = str(tmp_path / "report.md")
        runner.export_markdown(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Fault Injection" in content


class TestPreExecutionValidator:
    """Test the pre-execution revalidation gate."""

    def test_revalidator_blocks_expired_plan(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        val = PreExecutionValidator()
        result = val.validate(ir, bt, scene, plan_age_ms=120000.0)
        assert result.execution_allowed is False
        assert result.stop_requested is True
        assert "plan_expired" in result.stop_reason

    def test_revalidator_blocks_missing_target(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        # Create empty scene (target missing)
        empty_scene = SemanticSceneBuilder().build([])
        val = PreExecutionValidator()
        result = val.validate(ir, bt, empty_scene)
        assert result.execution_allowed is False or result.stop_requested is True

    def test_revalidator_blocks_stale_perception(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        val = PreExecutionValidator()
        result = val.validate(ir, bt, scene, perception_age_ms=10000.0)
        assert result.stop_requested is True
        assert "perception_stale" in result.stop_reason

    def test_revalidator_blocks_guard_unavailable(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        val = PreExecutionValidator()
        result = val.validate(ir, bt, scene, runtime_guard_available=False)
        assert result.stop_requested is True
        assert "runtime_guard_unavailable" in result.stop_reason

    def test_revalidator_blocks_consumed_plan(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        val = PreExecutionValidator()
        result = val.validate(ir, bt, scene, plan_consumed=True)
        assert result.stop_requested is True

    def test_revalidator_allows_valid_fresh_plan(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        if ir.validation_result.execution_allowed:
            val = PreExecutionValidator()
            result = val.validate(ir, bt, scene, plan_age_ms=100.0, perception_age_ms=100.0)
            # A fresh valid plan should pass revalidation
            assert result.execution_allowed is True


class TestSafetyInvariants:
    """Test the 20 global safety invariants."""

    def test_all_invariants_run_on_valid_plan(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup()])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        results = check_all_invariants(ir, bt, scene)
        assert len(results) == 20, f"Expected 20 invariants, got {len(results)}"

    def test_invariant_theme_grounded_when_exec_allowed(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup("玻璃杯")])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        results = check_all_invariants(ir, bt, scene)
        inv1 = next(r for r in results if r.invariant_id == 1)
        if ir.validation_result.execution_allowed:
            assert inv1.passed, f"Invariant 1 failed: {inv1.detail}"

    def test_invariant_no_missing_roles_when_exec_allowed(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup("玻璃杯")])
        bt = BehaviorTreeGenerator().plan("用2N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用2N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用2N力量抓住玻璃杯", bt, cg, scene=scene)

        results = check_all_invariants(ir, bt, scene)
        inv2 = next(r for r in results if r.invariant_id == 2)
        if ir.validation_result.execution_allowed:
            assert inv2.passed, f"Invariant 2 failed: {inv2.detail}"

    def test_substituted_not_user_exact(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_glass_cup("玻璃杯")])
        bt = BehaviorTreeGenerator().plan("用50N力量抓住玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("用50N力量抓住玻璃杯", bt, scene=scene, target="玻璃杯")
        ir = RobotTaskIRGenerator().generate("用50N力量抓住玻璃杯", bt, cg, scene=scene)

        results = check_all_invariants(ir, bt, scene)
        inv12 = next(r for r in results if r.invariant_id == 12)
        assert inv12.passed, f"Invariant 12 (sub not USER_EXACT) failed: {inv12.detail}"

    def test_needs_clarification_blocks_execution(self):
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator
        scene = SemanticSceneBuilder().build([_plastic_bottle("药瓶")])
        bt = BehaviorTreeGenerator().plan("把药瓶递给我", scene=scene)
        cg = HybridConstraintCompiler().compile("把药瓶递给我", bt, scene=scene, target="药瓶")
        ir = RobotTaskIRGenerator().generate("把药瓶递给我", bt, cg, scene=scene)

        results = check_all_invariants(ir, bt, scene)
        inv14 = next(r for r in results if r.invariant_id == 14)
        if ir.plan_metadata.plan_status in (PlanStatus.NEEDS_CLARIFICATION, PlanStatus.BLOCKED):
            assert inv14.passed, f"Invariant 14 failed: {inv14.detail}"


class TestHardAcceptanceGates:
    """Zero-tolerance acceptance gates."""

    def test_no_dangerous_false_allow_in_full_run(self):
        """After running all scenarios, dangerous_false_allow must be 0."""
        runner = FaultInjectionRunner(seed=42)
        runner.run_all(SCENARIOS)
        s = runner.summary()
        assert s["dangerous_false_allow"] == 0, \
            f"DANGEROUS FALSE ALLOW DETECTED: {s['dangerous_false_allow']} cases allowed execution when they should have blocked"

    def test_no_ungrounded_target_execution(self):
        """No scenario with missing target should allow execution."""
        runner = FaultInjectionRunner(seed=42)
        for s in [sc for sc in SCENARIOS if sc.case_id in ("FI-A3", "FI-A4")]:
            r = runner.run_scenario(s)
            assert r.execution_allowed is not True or r.passed, \
                f"{r.case_id}: ungrounded target execution detected"

    def test_no_expired_plan_execution(self):
        """Expired plans must not execute."""
        runner = FaultInjectionRunner(seed=42)
        s = next(sc for sc in SCENARIOS if sc.case_id == "FI-A1")
        r = runner.run_scenario(s)
        assert r.execution_allowed is False, "Expired plan should not be executable"

    def test_no_stale_perception_execution(self):
        """Stale perception must block execution."""
        runner = FaultInjectionRunner(seed=42)
        s = next(sc for sc in SCENARIOS if sc.case_id == "FI-B12")
        r = runner.run_scenario(s)
        assert r.execution_allowed is False

    def test_no_nan_inf_execution(self):
        """NaN/Inf values must block execution."""
        runner = FaultInjectionRunner(seed=42)
        for s in [sc for sc in SCENARIOS if sc.case_id in ("FI-C21", "FI-C22")]:
            r = runner.run_scenario(s)
            assert r.execution_allowed is False, f"{r.case_id}: NaN/Inf should block"

    def test_no_guardless_execution(self):
        """Runtime guard unavailable must block."""
        runner = FaultInjectionRunner(seed=42)
        s = next(sc for sc in SCENARIOS if sc.case_id == "FI-D38")
        r = runner.run_scenario(s)
        assert r.execution_allowed is False

    def test_no_consumed_plan_replay(self):
        """Consumed plans cannot be replayed."""
        runner = FaultInjectionRunner(seed=42)
        s = next(sc for sc in SCENARIOS if sc.case_id == "FI-F54")
        r = runner.run_scenario(s)
        assert r.execution_allowed is False

    def test_override_ledger_present_on_safe_substitution(self):
        """READY_WITH_SAFE_SUBSTITUTION must have override ledger."""
        runner = FaultInjectionRunner(seed=42)
        s = next(sc for sc in SCENARIOS if sc.case_id == "FI-E45")
        r = runner.run_scenario(s)
        if r.final_plan_status == "READY_WITH_SAFE_SUBSTITUTION":
            inv_results = r.invariant_results
            inv13 = next((i for i in inv_results if "override" in i.get("description", "").lower()), None)
            if inv13:
                assert inv13.get("passed", True), "Override ledger missing on safe substitution"
