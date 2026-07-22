"""
Phase 7: Enhanced FinalPlanValidator tests — role conflict, negation propagation,
condition completeness, entity strictness, error codes.

3+ expressions generalized per regression case:
  B23, B33, B35, B41, B43, B44, B45, B48, B49, B86, B87, B88
"""

from __future__ import annotations

import pytest

from robot_intent_agent.final_plan_validator import (
    FinalPlanValidator,
    ErrorCategory,
    ErrorCode,
    STATUS_PRIORITY,
    _issue,
)
from robot_intent_agent.task_semantics import (
    ParsedTask, TaskActionKind, PlanStatus, SemanticEntityRef,
    ConstraintResolution, ValidationResult, ValidationIssue,
    MotionState,
)
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree, BTNode, BTNodeType, SkillAction,
)
from robot_intent_agent.constraint.base import ConstraintGraph
from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder


def _scene(objs):
    return SemanticSceneBuilder().build(objs)


def _bt(skills=None, target="", target_eid=""):
    """Build a minimal BehaviorTree for testing."""
    acts = skills or ["Reach", "Grasp"]
    children = []
    for s in acts:
        children.append(BTNode(
            type=BTNodeType.ACTION,
            name=f"{s}({target})",
            skill=SkillAction(skill_name=s, target=target,
                            params={"target": target, "target_entity_id": target_eid}),
        ))
    return BehaviorTree(
        task_id="task-test",
        root=BTNode(type=BTNodeType.SEQUENCE, name="TestTask", children=children),
        metadata={"action": "GRASP", "target": target, "planner": "RuleEngine"},
    )


def _pt(action="GRASP", theme_eid=None, theme_mention="", dest_eid=None,
        ss_eid=None, recip_eid=None, avoid_list=None, notes=None, manner=None):
    """Build a minimal ParsedTask."""
    theme = None
    if theme_eid or theme_mention:
        theme = SemanticEntityRef(mention=theme_mention or "物体", entity_id=theme_eid,
                                  role="theme", source="scene" if theme_eid else "nl")
    dest = None
    if dest_eid:
        dest = SemanticEntityRef(mention="目标位置", entity_id=dest_eid, role="destination", source="scene")
    ss = None
    if ss_eid:
        ss = SemanticEntityRef(mention="支撑面", entity_id=ss_eid, role="support_surface", source="scene")
    recip = None
    if recip_eid:
        recip = SemanticEntityRef(mention="接收者", entity_id=recip_eid, role="recipient",
                                  source="scene" if recip_eid != "user" else "nl")
    obstacles = []
    if avoid_list:
        for a in avoid_list:
            if isinstance(a, tuple):
                obstacles.append(SemanticEntityRef(mention=a[0], entity_id=a[1], role="obstacle", source="scene"))
            else:
                obstacles.append(SemanticEntityRef(mention=a, entity_id=a, role="obstacle", source="scene"))

    return ParsedTask(
        instruction="test instruction",
        action=TaskActionKind[action] if action in TaskActionKind._value2member_map_ else TaskActionKind.CUSTOM,
        theme=theme, destination=dest, support_surface=ss, recipient=recip,
        obstacle=obstacles, manner=manner, motion_state=MotionState(),
        notes=notes or [],
    )


def _cg(avoid_objects=None):
    """Build a minimal ConstraintGraph with optional collision_avoid nodes."""
    from robot_intent_agent.constraint.base import ConstraintGraph as CG, ConstraintNode, ConstraintPriority
    cg = CG()
    if avoid_objects:
        for obj in avoid_objects:
            cg.add(ConstraintNode(
                constraint_type="collision_avoid",
                expression=f"avoid({obj})",
                params={"obstacle": obj},
                priority=ConstraintPriority.HARD,
            ))
    return cg


# ══════════════════════════════════════════════════════════════
# Role non-conflict (B23, B35, B44)
# ══════════════════════════════════════════════════════════════

class TestRoleNonConflict:
    """theme≠dest, theme∉avoid, destination∉avoid, recipient≠theme."""

    def test_theme_equals_destination_blocked(self):
        """theme==destination → THEME_DESTINATION_ROLE_SWAP (B23 generalization)"""
        pt = _pt(action="PLACE", theme_eid="obj-1", ss_eid="obj-1")  # same ID!
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.THEME_DESTINATION_ROLE_SWAP in i.code for i in result.issues), \
            f"Issues: {[(i.code, i.message[:60]) for i in result.issues]}"
        assert not result.execution_allowed

    def test_theme_in_avoid_blocked(self):
        """Theme also in avoid → THEME_IN_AVOID (B44 generalization)"""
        pt = _pt(action="FETCH", theme_eid="obj-red", avoid_list=[("红色方块", "obj-red")])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj-red", target_eid="obj-red"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.THEME_IN_AVOID in i.code for i in result.issues)
        assert not result.execution_allowed

    def test_destination_in_avoid_blocked(self):
        """Destination also in avoid → DESTINATION_IN_AVOID"""
        pt = _pt(action="PLACE", theme_eid="obj-1", dest_eid="obj-table",
                avoid_list=[("桌子", "obj-table")])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj-1", target_eid="obj-1"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.DESTINATION_IN_AVOID in i.code for i in result.issues)

    def test_recipient_is_theme_error(self):
        """Recipient cannot be the theme."""
        pt = _pt(action="HANDOVER", theme_eid="obj-cup", recip_eid="obj-cup")
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj-cup", target_eid="obj-cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.RECIPIENT_IS_THEME in i.code for i in result.issues)

    def test_distinct_roles_no_conflict(self):
        """Different entities for different roles → no conflict."""
        pt = _pt(action="PLACE", theme_eid="obj-1", ss_eid="obj-2",
                avoid_list=[("box", "obj-3")])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj-1", target_eid="obj-1"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not any("ROLE_SWAP" in i.code or "_IN_AVOID" in i.code for i in result.issues)

    def test_support_surface_fixed_ok(self):
        """Support surface with 'fixed' affordance → valid, no error."""
        objs = [RawObjectPercept(name="table", x=0, y=0, z=0, width=0.5, height=0.03, depth=0.3,
                                 color="brown", material="wood")]
        scene = _scene(objs)
        table_id = scene.objects[0].id
        pt = _pt(action="PLACE", theme_eid="obj-cup", ss_eid=table_id)
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-cup"), _cg(), scene,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not any(ErrorCode.SUPPORT_SURFACE_INFEASIBLE in i.code for i in result.issues)


# ══════════════════════════════════════════════════════════════
# Role completeness (B23, B86)
# ══════════════════════════════════════════════════════════════

class TestRoleCompleteness:
    """Every action must have required roles grounded."""

    def test_place_without_support_surface_blocked(self):
        """PLACE without support_surface → NEEDS_CLARIFICATION (B86 generalization)"""
        pt = _pt(action="PLACE", theme_eid="obj-cup")  # no support_surface
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any("SUPPORT_SURFACE" in i.code or "DESTINATION" in i.code
                   for i in result.issues), \
            f"Issues: {[(i.code, i.message[:60]) for i in result.issues]}"

    def test_fetch_without_recipient_needs_clarification(self):
        """FETCH without recipient → needs clarification."""
        pt = _pt(action="FETCH", theme_eid="obj-box")  # no recipient
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="box", target_eid="obj-box"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        # FETCH without recipient should have issues
        assert len(result.issues) > 0

    def test_grasp_with_theme_ready(self):
        """GRASP with grounded theme → READY."""
        pt = _pt(action="GRASP", theme_eid="obj-cup")
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        # No critical errors expected
        critical = [i for i in result.issues if i.severity == "error"]
        assert len(critical) == 0, f"Unexpected critical errors: {[(i.code,) for i in critical]}"


# ══════════════════════════════════════════════════════════════
# Negation propagation (B33, B41, B87, B88)
# ══════════════════════════════════════════════════════════════

class TestNegationPropagation:
    """Avoid objects must propagate through CG and BT."""

    def test_avoid_without_cg_blocked(self):
        """ParsedTask has obstacles but CG has no collision_avoid → BLOCKED (B33/B41)"""
        pt = _pt(action="FETCH", theme_eid="obj-box", avoid_list=[("玻璃杯", "obj-glass")])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="box", target_eid="obj-box"),
                           _cg(), None,  # CG has no collision_avoid
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.NEGATION_NOT_PROPAGATED in i.code for i in result.issues)

    def test_avoid_without_bt_path_blocked(self):
        """Obstacles but BT has no PlanPath → BLOCKED (B87/B88)"""
        pt = _pt(action="FETCH", theme_eid="obj-box",
                avoid_list=[("精密仪器", "obj-device")])
        v = FinalPlanValidator()
        result = v.validate(pt,
                           _bt(skills=["Reach", "Grasp"], target="box", target_eid="obj-box"),
                           _cg(avoid_objects=["obj-device"]), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.NEGATION_NOT_PROPAGATED in i.code for i in result.issues)

    def test_avoid_fully_propagated_no_error(self):
        """Obstacles in both CG + BT PlanPath → no NEGATION_NOT_PROPAGATED."""
        pt = _pt(action="FETCH", theme_eid="obj-box",
                avoid_list=[("玻璃杯", "obj-glass")])
        # BT with PlanPath and avoid_obstacles in params
        children = []
        pp_node = BTNode(
            type=BTNodeType.ACTION, name="PlanPath",
            skill=SkillAction(skill_name="PlanPath", target="box",
                            params={"target": "box", "target_entity_id": "obj-box",
                                    "avoid_obstacles": ["obj-glass"]}),
        )
        children.append(pp_node)
        children.append(BTNode(
            type=BTNodeType.ACTION, name="Reach(box)",
            skill=SkillAction(skill_name="Reach", target="box",
                            params={"target": "box", "target_entity_id": "obj-box"}),
        ))
        bt = BehaviorTree(
            task_id="task-test",
            root=BTNode(type=BTNodeType.SEQUENCE, name="TestTask", children=children),
            metadata={"action": "FETCH", "target": "box", "planner": "RuleEngine"},
        )
        v = FinalPlanValidator()
        result = v.validate(pt, bt, _cg(avoid_objects=["obj-glass"]), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not any(ErrorCode.NEGATION_NOT_PROPAGATED in i.code for i in result.issues)

    def test_multiple_avoids_all_must_propagate(self):
        """Multiple avoids — each must be in BT/CG."""
        pt = _pt(action="FETCH", theme_eid="obj-box",
                avoid_list=[("玻璃杯", "obj-glass"), ("桌子", "obj-table"), ("设备", "obj-device")])
        v = FinalPlanValidator()
        result = v.validate(pt,
                           _bt(skills=["Reach", "Grasp"], target="box", target_eid="obj-box"),
                           _cg(avoid_objects=["obj-glass"]), None,  # Only one in CG
                           ConstraintResolution(plan_status=PlanStatus.READY))
        # At least one NEGATION_NOT_PROPAGATED expected
        negation_issues = [i for i in result.issues if ErrorCode.NEGATION_NOT_PROPAGATED in i.code]
        assert len(negation_issues) >= 1


# ══════════════════════════════════════════════════════════════
# Condition completeness (B43, B45, B48, B49)
# ══════════════════════════════════════════════════════════════

class TestConditionCompleteness:
    """IF_ELSE/UNLESS must preserve branches; state evaluation."""

    def test_unsupported_conditional_flag(self):
        """unsupported_conditional in notes → CONDITIONAL_BRANCH_LOST (B48 generalization)"""
        pt = _pt(action="FETCH", theme_eid="obj-1",
                notes=["unsupported_conditional:IF_ELSE missing else branch"])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj", target_eid="obj-1"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.CONDITIONAL_BRANCH_LOST in i.code for i in result.issues)

    def test_conditional_detected_unknown_state(self):
        """Condition detected but state unevaluated → CONDITION_STATE_UNKNOWN (B45/B49 generalization)"""
        pt = _pt(action="GRASP", theme_eid="obj-1",
                notes=["conditional_detected:1 conditions, 0 sequences"])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj", target_eid="obj-1"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.CONDITION_STATE_UNKNOWN in i.code for i in result.issues)

    def test_unless_missing_otherwise_branch(self):
        """UNLESS without otherwise clause → CONDITIONAL_BRANCH_LOST"""
        pt = _pt(action="GRASP", theme_eid="obj-1",
                notes=["unsupported_conditional:UNLESS missing otherwise clause: '除非夹爪是空的'"])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj", target_eid="obj-1"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.CONDITIONAL_BRANCH_LOST in i.code for i in result.issues)

    def test_no_condition_no_error(self):
        """No conditional structure → no CONDITIONAL errors."""
        pt = _pt(action="GRASP", theme_eid="obj-1")
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="obj", target_eid="obj-1"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not any("CONDITIONAL" in i.code for i in result.issues)


# ══════════════════════════════════════════════════════════════
# Entity reference strictness (B23, B35)
# ══════════════════════════════════════════════════════════════

class TestEntityReferenceStrictness:
    """Entity IDs must be valid; names insufficient for ambiguous objects."""

    def test_entity_id_not_in_scene(self):
        """BT references entity_id not in scene → ENTITY_ID_NOT_IN_SCENE"""
        pt = _pt(action="GRASP", theme_eid="obj-fake")
        objs = [RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.075,
                                 width=0.07, height=0.1, depth=0.07,
                                 color="red", material="plastic")]
        scene = _scene(objs)
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-fake"), _cg(), scene,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.ENTITY_ID_NOT_IN_SCENE in i.code for i in result.issues)

    def test_ambiguous_name_with_multiple_objects(self):
        """Using name when multiple objects share it → AMBIGUOUS_GROUNDING_FORCED"""
        objs = [
            RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.075,
                            width=0.07, height=0.1, depth=0.07, color="red", material="plastic"),
            RawObjectPercept(name="cup", x=0.3, y=-0.1, z=0.075,
                            width=0.07, height=0.1, depth=0.07, color="blue", material="plastic"),
        ]
        scene = _scene(objs)
        # BT uses name "cup" with no entity_id
        bt = _bt(target="cup", target_eid="")  # no entity_id!
        v = FinalPlanValidator()
        result = v.validate(_pt(action="GRASP", theme_eid=scene.objects[0].id),
                           bt, _cg(), scene,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        # Should flag ambiguous name usage
        assert any(ErrorCode.AMBIGUOUS_GROUNDING_FORCED in i.code or
                   "ambiguous" in i.message.lower()
                   for i in result.issues), \
            f"Issues: {[(i.code, i.message[:80]) for i in result.issues]}"

    def test_unique_name_ok_with_entity_id(self):
        """Unique name with valid entity_id → no entity errors."""
        objs = [RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.075,
                                 width=0.07, height=0.1, depth=0.07,
                                 color="red", material="plastic")]
        scene = _scene(objs)
        cup_id = scene.objects[0].id
        pt = _pt(action="GRASP", theme_eid=cup_id)
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid=cup_id), _cg(), scene,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        entity_issues = [i for i in result.issues
                        if ErrorCode.ENTITY_ID_NOT_IN_SCENE in i.code
                        or ErrorCode.AMBIGUOUS_GROUNDING_FORCED in i.code]
        assert len(entity_issues) == 0, f"Unexpected: {entity_issues}"


# ══════════════════════════════════════════════════════════════
# Error codes — all 10+ new codes covered
# ══════════════════════════════════════════════════════════════

class TestErrorCodes:
    """All new error codes must be defined and usable."""

    REQUIRED_CODES = {
        ErrorCode.THEME_DESTINATION_ROLE_SWAP,
        ErrorCode.THEME_IN_AVOID,
        ErrorCode.DESTINATION_IN_AVOID,
        ErrorCode.RECIPIENT_IS_THEME,
        ErrorCode.SUPPORT_SURFACE_NOT_GROUNDED,
        ErrorCode.SUPPORT_SURFACE_INFEASIBLE,
        ErrorCode.ROLE_MENTION_NOT_GROUNDED,
        ErrorCode.NEGATION_NOT_PROPAGATED,
        ErrorCode.CONDITIONAL_BRANCH_LOST,
        ErrorCode.CONDITION_STATE_UNKNOWN,
        ErrorCode.COMPOSITE_ACTION_LOST,
        ErrorCode.ENTITY_ID_NOT_IN_SCENE,
        ErrorCode.AMBIGUOUS_GROUNDING_FORCED,
        ErrorCode.SCORER_PIPELINE_DISAGREEMENT,
    }

    def test_all_error_codes_defined(self):
        assert len(self.REQUIRED_CODES) == 14

    def test_each_code_is_string(self):
        for code in self.REQUIRED_CODES:
            assert isinstance(code, str) and len(code) > 0

    def test_codes_start_with_category(self):
        for code in self.REQUIRED_CODES:
            assert "_" in code, f"Code '{code}' should have underscore-separated category"


# ══════════════════════════════════════════════════════════════
# Status priority
# ══════════════════════════════════════════════════════════════

class TestStatusPriority:
    """BLOCKED > NEEDS_CLARIFICATION > READY_WITH_SAFE_SUBSTITUTION > READY."""

    def test_blocked_highest_priority(self):
        assert STATUS_PRIORITY[PlanStatus.BLOCKED] < STATUS_PRIORITY[PlanStatus.NEEDS_CLARIFICATION]
        assert STATUS_PRIORITY[PlanStatus.BLOCKED] < STATUS_PRIORITY[PlanStatus.READY]

    def test_needs_clarification_above_ready(self):
        assert STATUS_PRIORITY[PlanStatus.NEEDS_CLARIFICATION] < STATUS_PRIORITY[PlanStatus.READY]

    def test_ready_lowest_priority(self):
        priorities = list(STATUS_PRIORITY.values())
        assert STATUS_PRIORITY[PlanStatus.READY] == max(priorities)

    def test_negation_issue_forces_blocked(self):
        """NEGATION_NOT_PROPAGATED → status=BLOCKED regardless of resolution."""
        pt = _pt(action="FETCH", theme_eid="obj-box", avoid_list=[("玻璃杯", "obj-glass")])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="box", target_eid="obj-box"),
                           _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert result.status == PlanStatus.BLOCKED
        assert not result.execution_allowed


# ══════════════════════════════════════════════════════════════
# Composite action (B48, B70)
# ══════════════════════════════════════════════════════════════

class TestCompositeAction:
    """CUSTOM action must not lose composite semantics."""

    def test_custom_action_warns(self):
        """CUSTOM action → COMPOSITE_ACTION_LOST warning."""
        pt = _pt(action="CUSTOM", theme_eid="obj-cup")
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.COMPOSITE_ACTION_LOST in i.code for i in result.issues)

    def test_custom_action_blocks_execution(self):
        """CUSTOM action must NOT be execution_allowed=True."""
        pt = _pt(action="CUSTOM", theme_eid="obj-cup")
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not result.execution_allowed

    def test_regular_action_no_composite_error(self):
        """GRASP action with proper pipeline → no COMPOSITE_ACTION_LOST."""
        pt = _pt(action="GRASP", theme_eid="obj-cup")
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup", target_eid="obj-cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not any(ErrorCode.COMPOSITE_ACTION_LOST in i.code for i in result.issues)


# ══════════════════════════════════════════════════════════════
# Ambiguous grounding (B18, B35)
# ══════════════════════════════════════════════════════════════

class TestAmbiguousGrounding:
    """Clarification-needed notes must trigger errors."""

    def test_clarification_needed_theme(self):
        """clarification_needed:theme → AMBIGUOUS_GROUNDING_FORCED"""
        pt = _pt(action="FETCH", theme_eid=None,
                notes=["clarification_needed:theme=你指的是左边的杯子还是右边的杯子？"])
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="cup"), _cg(), None,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert any(ErrorCode.AMBIGUOUS_GROUNDING_FORCED in i.code for i in result.issues)

    def test_ungrounded_theme_blocked(self):
        """Theme with entity_id=None when scene objects exist → error."""
        pt = _pt(action="GRASP", theme_eid=None, theme_mention="杯子")
        objs = [RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.075,
                                 width=0.07, height=0.1, depth=0.07,
                                 color="red", material="plastic")]
        scene = _scene(objs)
        v = FinalPlanValidator()
        result = v.validate(pt, _bt(target="杯子"), _cg(), scene,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        # Theme not grounded → should have issues
        assert len(result.issues) > 0

    def test_theme_grounded_no_ambiguity_error(self):
        """Clear grounding → no AMBIGUOUS_GROUNDING_FORCED."""
        objs = [RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.075,
                                 width=0.07, height=0.1, depth=0.07,
                                 color="red", material="plastic")]
        scene = _scene(objs)
        pt = _pt(action="GRASP", theme_eid=scene.objects[0].id, theme_mention="杯子")
        v = FinalPlanValidator()
        result = v.validate(pt,
                           _bt(target="杯子", target_eid=scene.objects[0].id),
                           _cg(), scene,
                           ConstraintResolution(plan_status=PlanStatus.READY))
        assert not any(ErrorCode.AMBIGUOUS_GROUNDING_FORCED in i.code for i in result.issues)
        assert not any(ErrorCode.ROLE_MENTION_NOT_GROUNDED in i.code for i in result.issues)
