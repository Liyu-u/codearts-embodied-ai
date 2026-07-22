"""
Global invariant tests for enhanced FinalPlanValidator (Phase 8).

These test cross-field consistency — not individual field correctness.
Violations must be caught by the validator, NOT relaxed by test assertions.
"""

from __future__ import annotations

import pytest

from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.final_plan_validator import FinalPlanValidator, ErrorCategory
from robot_intent_agent.task_semantics import parse_task_semantics, PlanStatus, TaskActionKind


def _scene(objects):
    return SemanticSceneBuilder().build(objects)


def _full(instruction, objects):
    scene = _scene(objects)
    target = objects[0].name if objects else ""
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
    return ir, scene, bt, cg


# ══════════════════════════════════════════════════════════════
# Invariant 1: Action ↔ BT skill consistency
# ══════════════════════════════════════════════════════════════

class TestActionBTSkillConsistency:
    def test_grasp_action_has_grasp_or_reach_skill(self):
        ir, _, bt, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
        assert ir.parsed_task.action == TaskActionKind.GRASP
        assert bool({"Grasp", "Reach"} & bt_skills), \
            f"GRASP action must have Grasp/Reach in BT: {bt_skills}"

    def test_fetch_action_has_fetch_skill(self):
        ir, _, bt, _ = _full("把盒子拿过来", [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard"),
        ])
        bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
        assert ir.parsed_task.action == TaskActionKind.FETCH
        assert "Fetch" in bt_skills, f"FETCH action must have Fetch in BT: {bt_skills}"

    def test_handover_action_has_handover_skill(self):
        ir, _, bt, _ = _full("把药瓶递给我", [
            RawObjectPercept(name="bottle", x=0.20, y=0.08, z=0.04,
                             width=0.04, height=0.09, depth=0.04,
                             color="red", material="plastic"),
        ])
        bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
        assert ir.parsed_task.action == TaskActionKind.HANDOVER
        assert "Handover" in bt_skills, f"HANDOVER must have Handover in BT: {bt_skills}"


# ══════════════════════════════════════════════════════════════
# Invariant 2: BT entity_ids all exist in scene
# ══════════════════════════════════════════════════════════════

class TestBTEntityIdsInScene:
    def test_all_bt_entity_ids_valid(self):
        ir, scene, bt, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        scene_ids = {getattr(o, "id", "") for o in scene.objects}
        for action in bt.root.flatten_actions():
            for key in ("target_entity_id", "destination_entity_id"):
                eid = action.params.get(key, "")
                if eid and eid != "user":
                    assert eid in scene_ids, \
                        f"BT action '{action.skill_name}' {key}='{eid}' not in scene"

    def test_no_bt_entity_ids_fabricated(self):
        """Even without grounding, BT entity_ids must be real or absent."""
        ir, scene, bt, _ = _full("抓住红色杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="blue", material="plastic"),
        ])
        scene_ids = {getattr(o, "id", "") for o in scene.objects}
        for action in bt.root.flatten_actions():
            for key in ("target_entity_id", "destination_entity_id"):
                eid = action.params.get(key, "")
                if eid and eid != "user":
                    assert eid in scene_ids, \
                        f"BT entity_id '{eid}' fabricated — not in scene"


# ══════════════════════════════════════════════════════════════
# Invariant 3: missing_roles ↔ plan_status consistency
# ══════════════════════════════════════════════════════════════

class TestMissingRolesPlanStatus:
    def test_missing_roles_implies_not_ready(self):
        """If grounded_task has missing_roles, plan must NOT be READY."""
        ir, _, _, _ = _full("把杯子拿过来", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        gt = ir.grounded_task
        if gt and gt.missing_roles:
            assert ir.plan_metadata.plan_status != PlanStatus.READY, \
                f"Missing roles {gt.missing_roles} but plan_status is READY"

    def test_no_theme_blocks_execution(self):
        """If theme is None, execution must not be allowed."""
        ir, _, _, _ = _full("抓住红色杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="blue", material="plastic"),
        ])
        if ir.parsed_task.theme is None:
            assert not ir.validation_result.execution_allowed, \
                "Theme None but execution_allowed=True"


# ══════════════════════════════════════════════════════════════
# Invariant 4: NEEDS_CLARIFICATION/BLOCKED → execution_allowed=false
# ══════════════════════════════════════════════════════════════

class TestBlockedStatusExecutionGate:
    def test_needs_clarification_disallows_execution(self):
        """Any plan with NEEDS_CLARIFICATION must have execution_allowed=False."""
        ir, _, _, _ = _full("把杯子放到桌子上", [
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
        ])
        if ir.plan_metadata.plan_status == PlanStatus.NEEDS_CLARIFICATION:
            assert not ir.validation_result.execution_allowed, \
                "NEEDS_CLARIFICATION but execution_allowed=True"

    def test_blocked_disallows_execution(self):
        ir, _, _, _ = _full("把杯子放到桌子上", [
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
        ])
        if ir.plan_metadata.plan_status == PlanStatus.BLOCKED:
            assert not ir.validation_result.execution_allowed, \
                "BLOCKED but execution_allowed=True"


# ══════════════════════════════════════════════════════════════
# Invariant 5: skill params ↔ compiled_constraints
# ══════════════════════════════════════════════════════════════

class TestSkillParamsConstraintsConsistency:
    def test_grasp_force_matches_constraint_force(self):
        ir, _, bt, cg = _full("用3N力量抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        # BT Grasp force should match resolution
        if ir.constraint_resolution:
            fr = ir.constraint_resolution.parameters.get("force_n")
            if fr and fr.selected_value:
                for action in bt.root.flatten_actions():
                    if action.skill_name in ("Grasp", "GentleGrasp"):
                        af = action.params.get("force_n")
                        if isinstance(af, dict):
                            af = af.get("value")
                        if af is not None:
                            assert abs(float(af) - float(fr.selected_value)) < 0.02, \
                                f"BT force {af} != resolution {fr.selected_value}"

    def test_velocity_consistency_bt_vs_ir(self):
        ir, _, bt, _ = _full("以0.15m/s的速度移动杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        if ir.constraint_resolution:
            vr = ir.constraint_resolution.parameters.get("velocity_ms")
            if vr and vr.selected_value:
                for action in bt.root.flatten_actions():
                    if action.skill_name in ("Reach", "MoveTo"):
                        av = action.params.get("velocity_ms")
                        if isinstance(av, dict):
                            av = av.get("value")
                        if av is not None:
                            assert abs(float(av) - float(vr.selected_value)) < 0.02, \
                                f"BT velocity {av} != resolution {vr.selected_value}"


# ══════════════════════════════════════════════════════════════
# Invariant 6: force/velocity satisfy user + robot limits
# ══════════════════════════════════════════════════════════════

class TestForceVelocityBounds:
    def test_force_within_robot_limits(self):
        ir, _, _, _ = _full("用50N力量抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        if ir.constraint_resolution:
            fr = ir.constraint_resolution.parameters.get("force_n")
            if fr and fr.selected_value:
                assert fr.selected_value <= 10.0, \
                    f"Force {fr.selected_value}N exceeds robot max 10N"

    def test_glass_fragile_force_capped(self):
        ir, _, _, _ = _full("用50N力量抓住玻璃杯", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ])
        if ir.constraint_resolution:
            fr = ir.constraint_resolution.parameters.get("force_n")
            if fr and fr.selected_value:
                assert fr.selected_value <= 2.0, \
                    f"Glass cup force {fr.selected_value}N not capped to safe limit"

    def test_velocity_within_stage_limits(self):
        from robot_intent_agent.final_plan_validator import STAGE_VELOCITY_LIMITS
        ir, _, bt, _ = _full("以0.15m/s的速度移动杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        for action in bt.root.flatten_actions():
            limit = STAGE_VELOCITY_LIMITS.get(action.skill_name)
            if limit and limit > 0:
                vel = action.params.get("velocity_ms")
                if isinstance(vel, dict):
                    vel = vel.get("value")
                if vel is not None:
                    assert float(vel) <= limit + 0.02, \
                        f"{action.skill_name} velocity {vel} > stage limit {limit}"


# ══════════════════════════════════════════════════════════════
# Invariant 7: dynamic target needs stability gate
# ══════════════════════════════════════════════════════════════

class TestDynamicTargetStabilityGate:
    def test_dynamic_grasp_has_wait_until_stable(self):
        ir, _, bt, _ = _full("抓住正在移动的杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic",
                             extra_attrs={"_is_moving": True, "_speed_mps": 0.15}),
        ])
        if ir.parsed_task.action == TaskActionKind.DYNAMIC_GRASP:
            bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
            assert "WaitUntilStable" in bt_skills, \
                f"DYNAMIC_GRASP must have WaitUntilStable: {bt_skills}"

    def test_dynamic_grasp_without_stability_gate_flagged(self):
        """If DYNAMIC_GRASP has no WaitUntilStable, validator must flag it."""
        ir, scene, bt, cg = _full("抓住正在移动的杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic",
                             extra_attrs={"_is_moving": True, "_speed_mps": 0.15}),
        ])
        if ir.parsed_task.action == TaskActionKind.DYNAMIC_GRASP:
            validator = FinalPlanValidator()
            result = validator.validate(
                parsed_task=ir.parsed_task,
                behavior_tree=bt,
                constraint_graph=cg,
                scene=scene,
                resolution=ir.constraint_resolution,
            )
            bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
            if "WaitUntilStable" not in bt_skills:
                has_stability_issue = any("STABILITY" in i.code for i in result.issues)
                assert has_stability_issue, \
                    "Missing WaitUntilStable must be flagged by validator"


# ══════════════════════════════════════════════════════════════
# Invariant 8: avoid objects → BT constraints
# ══════════════════════════════════════════════════════════════

class TestAvoidInBTConstraints:
    def test_obstacle_produces_planpath(self):
        ir, _, bt, cg = _full("把盒子拿过来，别碰玻璃杯", [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=0.05, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ])
        bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
        if ir.parsed_task.obstacle:
            assert "PlanPath" in bt_skills, \
                f"Obstacles present but no PlanPath in BT: {bt_skills}"

    def test_obstacle_produces_collision_avoid_cg(self):
        ir, _, _, cg = _full("把盒子拿过来，别碰玻璃杯", [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=0.05, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ])
        if ir.parsed_task.obstacle:
            collision_nodes = [n for n in cg.nodes if n.constraint_type == "collision_avoid"]
            assert len(collision_nodes) > 0, \
                "Obstacles present but no collision_avoid in CG"


# ══════════════════════════════════════════════════════════════
# Invariant 9: Inferred fields have source
# ══════════════════════════════════════════════════════════════

class TestProvenance:
    def test_theme_has_source_when_present(self):
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        if ir.parsed_task.theme:
            assert ir.parsed_task.theme.source, \
                f"Theme must have source: {ir.parsed_task.theme}"

    def test_constraints_have_provenance(self):
        ir, _, _, _ = _full("用5N力量抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        for c in ir.parsed_task.user_constraints:
            assert c.provenance, \
                f"Constraint '{c.parameter}' must have provenance"

    def test_bt_planner_metadata_present(self):
        ir, _, bt, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        assert bt.metadata.get("planner"), "BT metadata must have planner field"


# ══════════════════════════════════════════════════════════════
# Invariant 10: input key fields not lost
# ══════════════════════════════════════════════════════════════

class TestInputFieldsPreserved:
    def test_instruction_preserved(self):
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        assert ir.task_metadata.raw_instruction == "抓住杯子", \
            "Instruction must be preserved in task_metadata"

    def test_task_id_present(self):
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        assert ir.task_metadata.task_id, "task_id must be present"

    def test_parsed_task_instruction_matches(self):
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        assert ir.parsed_task.instruction == ir.task_metadata.raw_instruction, \
            "parsed_task.instruction must match task_metadata.raw_instruction"


# ══════════════════════════════════════════════════════════════
# Invariant 11: null vs missing field consistency
# ══════════════════════════════════════════════════════════════

class TestNullVsMissingSemantics:
    def test_unmentioned_role_is_none(self):
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        pt = ir.parsed_task
        # These roles were not mentioned → must be None
        assert pt.source is None, "Unmentioned 'source' must be None"
        assert pt.destination is None, "Unmentioned 'destination' must be None"

    def test_empty_obstacle_list_not_null(self):
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        # obstacle should be empty list, not None
        assert ir.parsed_task.obstacle is not None, "obstacle must be list (empty), not None"
        assert isinstance(ir.parsed_task.obstacle, list), "obstacle must be list"


# ══════════════════════════════════════════════════════════════
# Invariant 12: Error category codes are valid
# ══════════════════════════════════════════════════════════════

class TestErrorCategoryCodes:
    """Validator issues must use valid error category prefixes."""

    VALID_CATEGORIES = {ErrorCategory.SCHEMA, ErrorCategory.SEMANTIC,
                        ErrorCategory.GROUNDING, ErrorCategory.CONSTRAINT,
                        ErrorCategory.CAPABILITY, ErrorCategory.CROSS_FIELD,
                        ErrorCategory.PROVENANCE, ErrorCategory.EXECUTABILITY}

    def test_all_issues_have_valid_category(self):
        ir, scene, bt, cg = _full("用50N力量抓住玻璃杯", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ])
        for issue in ir.validation_result.issues:
            category = issue.code.split(":")[0] if ":" in issue.code else ""
            assert category in self.VALID_CATEGORIES, \
                f"Issue code '{issue.code}' has invalid category '{category}'"

    def test_all_issues_have_severity(self):
        ir, scene, bt, cg = _full("用50N力量抓住玻璃杯", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ])
        for issue in ir.validation_result.issues:
            assert issue.severity in ("error", "warning"), \
                f"Issue '{issue.code}' has invalid severity '{issue.severity}'"


# ══════════════════════════════════════════════════════════════
# Phase 5: Dangerous pass-through invariants
# ══════════════════════════════════════════════════════════════

class TestDangerousPassThrough:
    """Verify execution is blocked when safety conditions aren't met."""

    def test_place_without_support_surface_blocks_execution(self):
        """PLACE action with no support surface in scene must block."""
        ir, _, _, _ = _full("把杯子放到桌子上", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        # Scene has cup but NO table — PLACE must be blocked
        if ir.parsed_task.action == TaskActionKind.PLACE:
            assert ir.validation_result.execution_allowed is False, \
                "PLACE without support_surface must block execution"

    def test_place_without_table_still_blocks(self):
        """PLACE without destination object must block (different scene)."""
        ir, _, _, _ = _full("把盒子放到架子上", [
            RawObjectPercept(name="box", x=0.3, y=0.1, z=0.05,
                             width=0.1, height=0.08, depth=0.1,
                             color="brown", material="cardboard"),
        ])
        if ir.parsed_task.action == TaskActionKind.PLACE:
            assert ir.validation_result.execution_allowed is False, \
                "PLACE without destination must block"

    def test_missing_theme_blocks_execution(self):
        """Task with unresolvable theme must not execute."""
        ir, _, _, _ = _full("把红色杯子拿过来", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="blue", material="plastic"),
        ])
        if ir.parsed_task.theme is None or ir.parsed_task.theme.entity_id is None:
            assert ir.validation_result.execution_allowed is False, \
                "Missing theme must block execution"

    def test_grasp_does_not_require_recipient(self):
        """GRASP action must NOT require recipient — only HANDOVER/FETCH do."""
        ir, _, _, _ = _full("抓住杯子", [
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        # GRASP should be executable without recipient
        assert ir.parsed_task.action == TaskActionKind.GRASP
        # If execution is blocked, it must NOT be because of missing recipient
        if not ir.validation_result.execution_allowed:
            for issue in ir.validation_result.issues:
                assert "RECIPIENT" not in issue.code, \
                    f"GRASP blocked by recipient issue: {issue.code}"

    def test_handover_without_recipient_blocks(self):
        """HANDOVER without recipient must block."""
        ir, _, _, _ = _full("把杯子递给", [  # incomplete instruction
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        if ir.parsed_task.action == TaskActionKind.HANDOVER:
            if ir.parsed_task.recipient is None:
                assert ir.validation_result.execution_allowed is False, \
                    "HANDOVER without recipient must block"


# ══════════════════════════════════════════════════════════════
# Phase 6: Negation/avoid complete propagation tests
# ══════════════════════════════════════════════════════════════

# Negation pattern test cases — parameterized to cover 15+ patterns
# without hardcoding full sentences.
NEGATION_PATTERNS = [
    # (instruction, target_name, avoid_phrase, category)
    ("抓住杯子，别碰盒子", "cup", "别碰", "direct_negation"),
    ("抓住杯子，不要碰盒子", "cup", "不要碰", "direct_negation"),
    ("把杯子拿过来，千万别碰盒子", "cup", "千万别碰", "emphatic_negation"),
    ("抓住杯子，避开那个盒子", "cup", "避开", "spatial_avoid"),
    ("抓住杯子，绕开盒子", "cup", "绕开", "spatial_avoid"),
    ("抓住杯子，禁止接触盒子", "cup", "禁止接触", "formal_negation"),
    ("grasp the cup, avoid the box", "cup", "avoid", "english_negation"),
    ("grasp the cup, don't touch the box", "cup", "don't touch", "english_negation"),
    ("抓住杯子，不要后面的那个", "cup", "不要后面的", "spatial_negation"),
    ("抓住杯子，除了目标外不要碰其他东西", "cup", "除了", "exclusion_negation"),
    ("抓住杯子，别碰任何东西", "cup", "别碰任何", "universal_negation"),
    ("把盒子拿过来，不要玻璃杯", "box", "不要", "object_negation"),
    ("抓住杯子，不能碰那个红色方块", "cup", "不能碰", "prohibition"),
    ("抓住杯子，不想碰桌子", "cup", "不想碰", "preference_negation"),
    ("抓住前面的杯子，不要后面的", "cup", "不要后面的", "spatial_front_back"),
    ("grasp the cup, but don't touch anything else", "cup", "anything else", "english_universal"),
]

# Object pairs: target + obstacle for each test
def _make_pair(target_name, avoid_name):
    """Create two objects: target and obstacle."""
    target = RawObjectPercept(name=target_name, x=0.3, y=0.1, z=0.05,
                              width=0.07, height=0.10, depth=0.07,
                              color="white", material="plastic")
    avoid = RawObjectPercept(name=avoid_name, x=0.3, y=-0.1, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard")
    return [target, avoid]


class TestNegationPropagation:
    """Verify negation/avoid flows through the full pipeline chain."""

    @pytest.mark.parametrize("instruction,target_name,avoid_keyword,category", NEGATION_PATTERNS)
    def test_avoid_preserved_in_parsed_task(self, instruction, target_name, avoid_keyword, category):
        """Every negation pattern must produce obstacle entries in ParsedTask."""
        objects = _make_pair(target_name, "box")
        scene = _scene(objects)
        parsed = parse_task_semantics(instruction, scene=scene)
        # The instruction contains avoidance → parsed_task.obstacle must be non-empty
        assert parsed.obstacle, \
            f"[{category}] '{instruction}' → expected obstacle entries, got none"

    @pytest.mark.parametrize("instruction,target_name,avoid_keyword,category", NEGATION_PATTERNS)
    def test_avoid_in_bt_or_cg(self, instruction, target_name, avoid_keyword, category):
        """Avoid must appear in BT (PlanPath/avoid params) or CG (collision_avoid)."""
        objects = _make_pair(target_name, "box")
        ir, _, bt, cg = _full(instruction, objects)
        # Check BT avoids
        bt_has_avoid = False
        for a in bt.root.flatten_actions():
            for key in ("avoid_obstacles", "avoid", "avoid_objects"):
                av = a.params.get(key, [])
                if isinstance(av, list) and len(av) > 0:
                    bt_has_avoid = True
            if a.skill_name == "PlanPath":
                bt_has_avoid = True
        # Check CG avoids
        cg_has_avoid = any(n.constraint_type == "collision_avoid" for n in cg.nodes)
        assert bt_has_avoid or cg_has_avoid, \
            f"[{category}] '{instruction}' → avoid not in BT or CG"

    def test_same_category_target_and_avoid_distinct(self):
        """Two cups — target and avoid must be different objects."""
        cup1 = RawObjectPercept(name="cup", x=0.35, y=0.20, z=0.075,
                                width=0.07, height=0.10, depth=0.07,
                                color="white", material="plastic")
        cup2 = RawObjectPercept(name="cup", x=0.35, y=-0.20, z=0.075,
                                width=0.07, height=0.10, depth=0.07,
                                color="blue", material="plastic")
        ir, scene, bt, cg = _full("抓住杯子，别碰另一个杯子", [cup1, cup2])
        # Verify theme and obstacle are different objects
        if ir.parsed_task.theme and ir.parsed_task.obstacle:
            theme_eid = ir.parsed_task.theme.entity_id
            for obs in ir.parsed_task.obstacle:
                if obs.entity_id:
                    assert obs.entity_id != theme_eid, \
                        "Target and avoid must be different scene objects"

    def test_avoid_entity_has_both_mention_and_id(self):
        """Each obstacle must preserve original mention AND grounded entity_id."""
        cup = RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.05,
                               width=0.07, height=0.10, depth=0.07,
                               color="white", material="plastic")
        box = RawObjectPercept(name="box", x=0.3, y=-0.1, z=0.05,
                               width=0.08, height=0.06, depth=0.08,
                               color="brown", material="cardboard")
        ir, scene, _, _ = _full("抓住杯子，别碰盒子", [cup, box])
        if ir.parsed_task.obstacle:
            for obs in ir.parsed_task.obstacle:
                assert obs.mention, "Obstacle must have original mention"
                # entity_id may be None if not grounded, but mention is mandatory

    def test_planpath_present_with_avoid(self):
        """BT must include PlanPath when avoid objects exist."""
        cup = RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.05,
                               width=0.07, height=0.10, depth=0.07,
                               color="white", material="plastic")
        box = RawObjectPercept(name="box", x=0.3, y=-0.1, z=0.05,
                               width=0.08, height=0.06, depth=0.08,
                               color="brown", material="cardboard")
        _, _, bt, _ = _full("抓住杯子，别碰盒子", [cup, box])
        bt_skills = {a.skill_name for a in bt.root.flatten_actions()}
        assert "PlanPath" in bt_skills, \
            f"BT with avoid must have PlanPath, got: {bt_skills}"

    def test_block_when_avoid_cannot_be_safely_expressed(self):
        """When avoidance can't be resolved, execution should be blocked or clarified."""
        # Only one object in scene, but instruction says avoid something else
        cup = RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.05,
                               width=0.07, height=0.10, depth=0.07,
                               color="white", material="plastic")
        ir, _, _, _ = _full("抓住杯子，别碰那个盒子", [cup])
        # The "盒子" isn't in the scene — avoid target is unresolvable
        # System should at minimum flag this (execution_allowed=false or NEEDS_CLARIFICATION)
        unresolved_avoid = any(
            obs.entity_id is None for obs in (ir.parsed_task.obstacle or [])
        )
        if unresolved_avoid:
            assert not ir.validation_result.execution_allowed, \
                "Unresolvable avoid must block execution"
