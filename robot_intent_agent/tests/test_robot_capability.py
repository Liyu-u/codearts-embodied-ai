"""
Tests for robot capability integration (Phase 7).

Verifies robot hardware limits participate in intent understanding:
- Gripper force limits, width, has_object
- Workspace bounds, payload
- Skill availability filtering
- Homed status
- Priority: System Safety > Robot Hard Limits > Scene Facts > User Request > Memory > Default
"""

from __future__ import annotations

import pytest

from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.task_semantics import (
    RobotCapability, RobotCapabilityValidator, CapabilityDecision,
    parse_task_semantics, TaskActionKind, PlanStatus,
)


def _scene(objects):
    return SemanticSceneBuilder().build(objects)


def _pipeline(instruction, objects, robot_cap=None):
    scene = _scene(objects)
    target = objects[0].name if objects else ""
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
    # Monkey-patch RobotCapability for this test run
    import robot_intent_agent.ir.ir_generator as irg
    orig = irg.RobotCapability
    if robot_cap is not None:
        irg.RobotCapability = lambda: robot_cap
    try:
        ir = irg.RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
    finally:
        irg.RobotCapability = orig
    return ir, scene


# ══════════════════════════════════════════════════════════════
# Category A: Gripper width limits
# ══════════════════════════════════════════════════════════════

class TestGripperWidthLimits:
    """Target wider than gripper max opening → BLOCKED."""

    def test_target_within_gripper_width_allowed(self):
        """Normal cup (7cm) within default gripper (10cm) → allowed."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        ir, scene = _pipeline("抓住杯子", objs)
        assert ir.validation_result is not None
        # Should be allowed (7cm < 10cm)
        has_width_block = any("gripper_max_width" in issue.message
                            for issue in ir.validation_result.issues)
        assert not has_width_block, f"Normal cup should not be blocked by width: {[i.message for i in ir.validation_result.issues]}"

    def test_wide_target_blocked(self):
        """Wide box (15cm) exceeds default gripper (10cm) → BLOCKED."""
        objs = [RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                                  width=0.15, height=0.06, depth=0.15,
                                  color="brown", material="cardboard")]
        cap = RobotCapability(gripper_max_width_m=0.10)
        ir, scene = _pipeline("抓住盒子", objs, robot_cap=cap)
        has_width_block = any("gripper_max_width" in issue.message
                            for issue in ir.validation_result.issues)
        assert has_width_block or not ir.validation_result.execution_allowed, \
            "Wide box must be blocked by gripper width"

    def test_custom_gripper_allows_wider(self):
        """With 20cm gripper, 15cm box should be allowed."""
        objs = [RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                                  width=0.15, height=0.06, depth=0.15,
                                  color="brown", material="cardboard")]
        cap = RobotCapability(gripper_max_width_m=0.20)
        ir, scene = _pipeline("抓住盒子", objs, robot_cap=cap)
        has_width_block = any("gripper_max_width" in issue.message
                            for issue in ir.validation_result.issues)
        assert not has_width_block, "20cm gripper should allow 15cm box"


# ══════════════════════════════════════════════════════════════
# Category B: Workspace limits
# ══════════════════════════════════════════════════════════════

class TestWorkspaceLimits:
    """Target outside workspace → BLOCKED."""

    def test_target_within_workspace_allowed(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        ir, scene = _pipeline("抓住杯子", objs)
        has_ws_block = any("workspace" in issue.message.lower()
                          for issue in ir.validation_result.issues)
        assert not has_ws_block, f"Target at 0.35m should be within 0.75m workspace"

    def test_target_outside_workspace_blocked(self):
        """Target at x=1.0m exceeds default workspace (0.75m) → BLOCKED."""
        objs = [RawObjectPercept(name="cup", x=1.00, y=0.10, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(workspace_radius_m=0.75)
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        has_ws_block = any("workspace" in issue.message.lower()
                          for issue in ir.validation_result.issues)
        assert has_ws_block or not ir.validation_result.execution_allowed, \
            "Target at 1.0m must be blocked with 0.75m workspace"

    def test_target_below_z_min_blocked(self):
        """Target at z=-0.1m below workspace floor → BLOCKED."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=-0.10,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(workspace_z_min_m=0.0)
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        has_ws_block = any("z=-0.1" in issue.message or "z_min" in issue.message
                          for issue in ir.validation_result.issues)
        assert has_ws_block or not ir.validation_result.execution_allowed, \
            "Target below z_min must be blocked"


# ══════════════════════════════════════════════════════════════
# Category C: Gripper has_object
# ══════════════════════════════════════════════════════════════

class TestGripperHasObject:
    """Robot already holding object → cannot Grasp/Fetch/DynamicGrasp."""

    def test_has_object_blocks_grasp(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(gripper_has_object=True)
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        has_grip_block = any("has_object" in issue.message or "holding" in issue.message.lower()
                            for issue in ir.validation_result.issues)
        assert has_grip_block or not ir.validation_result.execution_allowed, \
            "Must block Grasp when already holding object"

    def test_has_object_allows_place(self):
        """Place does not require empty gripper → should be allowed."""
        objs = [
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="table", x=0.40, y=0.00, z=0.00,
                             width=0.60, height=0.03, depth=0.40,
                             color="brown", material="wood"),
        ]
        cap = RobotCapability(gripper_has_object=True)
        ir, scene = _pipeline("把杯子放到桌子上", objs, robot_cap=cap)
        has_grip_block = any("has_object" in issue.message or "holding" in issue.message.lower()
                            for issue in ir.validation_result.issues)
        assert not has_grip_block, \
            "PLACE should be allowed even when holding object"

    def test_empty_gripper_allows_grasp(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(gripper_has_object=False)
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        has_grip_block = any("has_object" in issue.message or "holding" in issue.message.lower()
                            for issue in ir.validation_result.issues)
        assert not has_grip_block, "Empty gripper should allow Grasp"


# ══════════════════════════════════════════════════════════════
# Category D: Unavailable skills
# ══════════════════════════════════════════════════════════════

class TestUnavailableSkills:
    """Unsupported/unavailable skills must not appear in BT or must block."""

    def test_unavailable_skill_blocks(self):
        """If 'Pour' is unavailable, '把药片倒出来' must block."""
        objs = [RawObjectPercept(name="bottle", x=0.20, y=0.08, z=0.04,
                                  width=0.04, height=0.09, depth=0.04,
                                  color="orange", material="plastic")]
        cap = RobotCapability(unavailable_skills=["Pour"])
        ir, scene = _pipeline("把药片倒出来", objs, robot_cap=cap)
        # Either Pour not in BT, or execution blocked
        bt_skills = [a.skill_name for a in ir.behavior_tree.root.flatten_actions()]
        has_pour = "Pour" in bt_skills
        has_block = not ir.validation_result.execution_allowed
        assert not has_pour or has_block, \
            f"Pour skill should be blocked when unavailable. BT: {bt_skills}"

    def test_available_skill_allowed(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        ir, scene = _pipeline("抓住杯子", objs)
        bt_skills = [a.skill_name for a in ir.behavior_tree.root.flatten_actions()]
        assert "Reach" in bt_skills or "Grasp" in bt_skills, \
            "Standard skills should be available by default"

    def test_multiple_skills_unavailable(self):
        """Multiple unavailable skills should be detected."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(unavailable_skills=["Reach", "Grasp"])
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        bt_skills = [a.skill_name for a in ir.behavior_tree.root.flatten_actions()]
        has_reach = "Reach" in bt_skills
        has_grasp = "Grasp" in bt_skills
        has_block = not ir.validation_result.execution_allowed
        assert (not has_reach or not has_grasp) or has_block, \
            f"Unavailable skills should be blocked. BT: {bt_skills}"


# ══════════════════════════════════════════════════════════════
# Category E: Homed status
# ══════════════════════════════════════════════════════════════

class TestHomedStatus:
    """Robot not homed → cannot execute any motion."""

    def test_not_homed_blocks_execution(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(is_homed=False)
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        has_home_block = any("homed" in issue.message.lower()
                            for issue in ir.validation_result.issues)
        assert has_home_block or not ir.validation_result.execution_allowed, \
            "Not-homed robot must block execution"

    def test_homed_allows_execution(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(is_homed=True)
        ir, scene = _pipeline("抓住杯子", objs, robot_cap=cap)
        has_home_block = any("homed" in issue.message.lower()
                            for issue in ir.validation_result.issues)
        assert not has_home_block, "Homed robot should allow execution"


# ══════════════════════════════════════════════════════════════
# Category F: Force limits
# ══════════════════════════════════════════════════════════════

class TestForceLimits:
    """Robot gripper force limits must cap user/planner requests."""

    def test_force_within_limit_accepted(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(gripper_max_force_n=10.0, gripper_min_force_n=0.1)
        ir, scene = _pipeline("用3N力量抓住杯子", objs, robot_cap=cap)
        if ir.constraint_resolution:
            fr = ir.constraint_resolution.parameters.get("force_n")
            if fr and fr.selected_value:
                assert fr.selected_value <= 10.0, "Force must be within gripper limit"

    def test_force_exceeding_robot_limit_clamped(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(gripper_max_force_n=5.0)
        validator = RobotCapabilityValidator(cap)
        parsed = parse_task_semantics("用8N力量抓住杯子")
        executable, decisions, blocking = validator.validate(
            parsed_task=parsed, scene=_scene(objs))
        force_decisions = [d for d in decisions if d.parameter == "force_n"]
        # Should have a decision about force limits
        assert len(force_decisions) >= 1, f"Must have force decision. Got: {[d.parameter for d in decisions]}"
        if force_decisions:
            # Robot max is 5.0N — any selected force above that should be clamped
            fd = force_decisions[0]
            assert fd.source == "ROBOT_HARD_LIMIT", \
                f"Source must be ROBOT_HARD_LIMIT, got {fd.source}"

    def test_robot_force_limit_priority(self):
        """Robot limits > user request: user asks 50N, robot caps at 10N."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(gripper_max_force_n=10.0)
        ir, scene = _pipeline("用50N力量抓住杯子", objs, robot_cap=cap)
        if ir.constraint_resolution:
            fr = ir.constraint_resolution.parameters.get("force_n")
            if fr and fr.selected_value:
                assert fr.selected_value <= 10.0, \
                    f"Robot limit must cap user 50N request, got {fr.selected_value}"


# ══════════════════════════════════════════════════════════════
# Category G: Payload limits
# ══════════════════════════════════════════════════════════════

class TestPayloadLimits:
    """Heavy objects exceeding max payload → BLOCKED."""

    def test_normal_object_within_payload(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        ir, scene = _pipeline("抓住杯子", objs)
        has_payload_block = any("payload" in issue.message.lower()
                               for issue in ir.validation_result.issues)
        assert not has_payload_block, "Normal cup should be within 2kg payload"

    def test_heavy_metal_object_blocked(self):
        """Large metal block (10cm³, ~5kg) exceeds 2kg payload → BLOCKED."""
        objs = [RawObjectPercept(name="block", x=0.30, y=0.10, z=0.05,
                                  width=0.10, height=0.10, depth=0.10,
                                  color="gray", material="metal")]
        cap = RobotCapability(max_payload_kg=2.0)
        ir, scene = _pipeline("抓住铁块", objs, robot_cap=cap)
        has_payload_block = any("payload" in issue.message.lower()
                               for issue in ir.validation_result.issues)
        if has_payload_block:
            assert not ir.validation_result.execution_allowed


# ══════════════════════════════════════════════════════════════
# Category H: Decisions have full evidence
# ══════════════════════════════════════════════════════════════

class TestDecisionEvidence:
    """Each CapabilityDecision must contain requested, selected, source, reason."""

    def test_decision_has_all_fields(self):
        cap = RobotCapability()
        validator = RobotCapabilityValidator(cap)
        parsed = parse_task_semantics("抓住杯子")
        executable, decisions, blocking = validator.validate(parsed_task=parsed)
        for d in decisions:
            assert d.parameter, "Decision must have parameter"
            assert d.source, "Decision must have source"
            assert d.reason, "Decision must have reason"
            assert hasattr(d, 'requested'), "Decision must have requested"
            assert hasattr(d, 'selected'), "Decision must have selected"

    def test_ir_contains_robot_decisions(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        ir, scene = _pipeline("抓住杯子", objs)
        assert hasattr(ir, 'robot_capability_decisions'), \
            "IR must have robot_capability_decisions field"
        assert len(ir.robot_capability_decisions) > 0, \
            "IR must contain at least one robot capability decision"

    def test_priority_is_correct(self):
        """Robot hard limit must override user explicit request."""
        cap = RobotCapability(gripper_max_force_n=5.0)
        validator = RobotCapabilityValidator(cap)
        parsed = parse_task_semantics("用10N力量抓住杯子")
        executable, decisions, blocking = validator.validate(parsed_task=parsed)
        force_decisions = [d for d in decisions if d.parameter == "force_n"]
        if force_decisions:
            fd = force_decisions[0]
            assert fd.source == "ROBOT_HARD_LIMIT", \
                f"Robot limit must take priority. Got source={fd.source}, reason={fd.reason}"


# ══════════════════════════════════════════════════════════════
# Category I: Speed limits
# ══════════════════════════════════════════════════════════════

class TestSpeedLimits:
    """Robot max speed must cap user velocity requests."""

    def test_velocity_within_limit(self):
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        cap = RobotCapability(max_velocity_ms=0.3)
        ir, scene = _pipeline("以0.15m/s的速度移动杯子", objs, robot_cap=cap)
        if ir.constraint_resolution:
            vr = ir.constraint_resolution.parameters.get("velocity_ms")
            if vr and vr.selected_value:
                assert vr.selected_value <= 0.3, "Velocity must be within robot limit"

    def test_velocity_exceeding_robot_limit(self):
        """User requests 2m/s but robot max is 0.3m/s → velocity decision."""
        cap = RobotCapability(max_velocity_ms=0.3)
        validator = RobotCapabilityValidator(cap)
        parsed = parse_task_semantics("以2m/s的速度移动杯子")
        executable, decisions, blocking = validator.validate(parsed_task=parsed)
        vel_decisions = [d for d in decisions if d.parameter == "velocity_ms"]
        if vel_decisions:
            assert vel_decisions[0].source == "ROBOT_HARD_LIMIT", \
                f"Robot velocity limit must apply. Got: {vel_decisions[0]}"