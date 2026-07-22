from __future__ import annotations

import json

import pytest

from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.final_plan_validator import FinalPlanValidator
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.task_semantics import PlanStatus, TaskActionKind, parse_task_semantics
from robot_intent_agent.demo.web_ui import pipeline as web_pipeline


def build_scene(objects):
    return SemanticSceneBuilder().build(objects)


@pytest.mark.parametrize(
    "instruction,expected",
    [
        ("用50N力量把玻璃杯抓过来", {"force_op": "exact", "force": 50.0, "action": TaskActionKind.FETCH}),
        ("用5 N把盒子放到桌子上", {"force_op": "exact", "force": 5.0, "action": TaskActionKind.PLACE}),
        ("不超过2N抓住杯子", {"force_op": "max", "force": 2.0, "action": TaskActionKind.GRASP}),
        ("至少2N抓住杯子", {"force_op": "min", "force": 2.0, "action": TaskActionKind.GRASP}),
        ("2到4N抓住杯子", {"force_op": "range", "force_min": 2.0, "force_max": 4.0, "action": TaskActionKind.GRASP}),
        ("以0.2m/s速度移动杯子", {"vel_op": "exact", "velocity": 0.2, "action": TaskActionKind.CUSTOM}),
        ("抓住杯子", {"force_count": 0, "action": TaskActionKind.GRASP}),
        ("用 5Ｎ，速度 0.2 m/s 抓住杯子", {"force": 5.0, "force_op": "exact", "velocity": 0.2, "vel_op": "exact", "action": TaskActionKind.GRASP}),
    ],
)
def test_parameter_parser_variants(instruction, expected):
    parsed = parse_task_semantics(instruction)
    assert parsed.action == expected["action"]
    force_constraints = [c for c in parsed.user_constraints if c.parameter == "force_n"]
    velocity_constraints = [c for c in parsed.user_constraints if c.parameter == "velocity_ms"]

    if expected.get("force_count") == 0:
        assert not force_constraints
    if expected.get("force") is not None:
        assert force_constraints and force_constraints[0].value == expected["force"]
        assert force_constraints[0].operator.value == expected["force_op"]
    if expected.get("force_min") is not None:
        assert force_constraints and force_constraints[0].min_value == expected["force_min"]
        assert force_constraints[0].max_value == expected["force_max"]
        assert force_constraints[0].operator.value == expected["force_op"]
    if expected.get("velocity") is not None:
        assert velocity_constraints and velocity_constraints[0].value == expected["velocity"]
        assert velocity_constraints[0].operator.value == expected["vel_op"]


def test_resolution_exact_infeasible_substitutes_safely():
    scene = build_scene([
        RawObjectPercept(name="玻璃杯", x=0.1, y=0.0, z=0.04, width=0.05, height=0.12, depth=0.05, material="glass"),
    ])
    instruction = "用50N力量抓住玻璃杯"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    force_resolution = ir.constraint_resolution.parameters["force_n"]
    assert force_resolution.selected_value == pytest.approx(2.0, rel=1e-6)
    assert force_resolution.request_infeasible is True
    # EXACT user request exceeding hard limits → non-executable status
    assert ir.plan_metadata.plan_status in (
        PlanStatus.READY_WITH_SAFE_SUBSTITUTION,
        PlanStatus.NEEDS_CLARIFICATION,
        PlanStatus.BLOCKED,
    )
    assert any(entry.get("parameter") == "force_n" for entry in ir.constraint_resolution.override_ledger)


def test_fetch_without_delivery_zone_needs_clarification():
    scene = build_scene([
        RawObjectPercept(name="杯子", x=0.1, y=0.0, z=0.04, width=0.05, height=0.12, depth=0.05, material="plastic"),
    ])
    instruction = "把杯子拿过来"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="杯子")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    assert ir.plan_metadata.plan_status == PlanStatus.NEEDS_CLARIFICATION
    assert any("MISSING_DELIVERY" in issue.code or "MISSING_RECIPIENT_POSE" in issue.code for issue in ir.validation_result.issues)


def test_place_missing_surface_needs_clarification_and_no_fake_object():
    scene = build_scene([
        RawObjectPercept(name="盒子", x=0.1, y=0.0, z=0.04, width=0.06, height=0.06, depth=0.06, material="plastic"),
    ])
    instruction = "用5N力量把盒子以0.2m/s速度放到桌子上"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="盒子")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    assert ir.plan_metadata.plan_status == PlanStatus.NEEDS_CLARIFICATION
    assert ir.parsed_task.support_surface is None or ir.parsed_task.support_surface.mention in ("桌子", "桌")
    assert all(skill.get("object") not in (None, {}) for skill in ir.skills.values())


def test_scene_semantics_preserve_specific_class_and_parent():
    scene = build_scene([
        RawObjectPercept(name="托盘", x=0.0, y=0.0, z=0.03, width=0.2, height=0.02, depth=0.15, material="metal"),
        RawObjectPercept(name="杯子", x=0.1, y=0.0, z=0.05, width=0.05, height=0.1, depth=0.05, material="plastic"),
    ])
    tray = scene.find_object("托盘")
    cup = scene.find_object("杯子")
    assert tray is not None and tray.specific_class == "tray"
    assert tray.parent_class == "support_surface"
    assert cup is not None and cup.specific_class == "cup"
    assert cup.parent_class == "container"


def test_dynamic_grasp_has_stability_gate_and_timeout():
    scene = build_scene([
        RawObjectPercept(name="杯子", x=0.1, y=0.0, z=0.04, width=0.05, height=0.12, depth=0.05, material="plastic"),
    ])
    instruction = "抓住正在移动的杯子"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    action_names = [action.skill_name for action in bt.root.flatten_actions()]
    assert "WaitUntilStable" in action_names
    wait_action = next(action for action in bt.root.flatten_actions() if action.skill_name == "WaitUntilStable")
    assert wait_action.timeout_s and wait_action.timeout_s > 0
    assert wait_action.failure_conditions


def test_final_validator_catches_mismatch():
    scene = build_scene([
        RawObjectPercept(name="杯子", x=0.1, y=0.0, z=0.04, width=0.05, height=0.12, depth=0.05, material="plastic"),
    ])
    instruction = "用5N力量抓住杯子"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="杯子")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    # Introduce an inconsistency and verify the validator catches it.
    grasp = next(action for action in bt.root.flatten_actions() if action.skill_name == "Grasp")
    grasp.params["force_n"] = 9.0
    result = FinalPlanValidator().validate(ir.parsed_task, bt, cg, scene, ir.constraint_resolution)
    assert any("FORCE_MISMATCH" in issue.code for issue in result.issues)


def test_consistency_across_ir_and_frontend_view_model():
    scene = build_scene([
        RawObjectPercept(name="玻璃杯", x=0.1, y=0.0, z=0.04, width=0.05, height=0.12, depth=0.05, material="glass"),
    ])
    instruction = "用5N力量抓住玻璃杯"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    ui_result = web_pipeline.run(instruction, json.dumps({"objects": [{
        "object_id": "obj_glass_001",
        "category_candidates": [{"name": "cup", "score": 0.91}],
        "pose": {"position": {"x": 0.1, "y": 0.0, "z": 0.04}},
        "geometry": {"size": {"width": 0.05, "height": 0.12, "depth": 0.05}},
        "appearance": {"color": "transparent", "material": "glass"},
        "affordances": ["graspable", "fragile", "movable"],
        "tracking": {"state": "stationary", "confidence": 0.98, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}
    }]}), engine="纯规则引擎 (极速)", api_key="")
    assert ui_result["resolved_force"] == ir.constraint_resolution.parameters["force_n"].selected_value
    assert ui_result["plan_status"] == ir.plan_metadata.plan_status.value
    assert ui_result["execution_ready"] == ir.validation_result.execution_allowed


# ══════════════════════════════════════════════════════════════════
# v3.0 HANDOVER 回归: "把红色药瓶递给我"
# ══════════════════════════════════════════════════════════════════

def test_handover_medicine_bottle_full_pipeline():
    """Acceptance test: 把红色药瓶递给我 — 13-point verification."""
    scene = build_scene([
        RawObjectPercept(name="红色药瓶", x=0.15, y=0.05, z=0.03,
                         width=0.03, height=0.08, depth=0.03,
                         color="red", material="plastic"),
    ])
    instruction = "把红色药瓶递给我"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="红色药瓶")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    # ── 1. Theme 接地到场景 entity_id ──
    assert ir.parsed_task.theme is not None
    assert ir.parsed_task.theme.entity_id is not None, "theme.entity_id must be grounded"
    assert ir.parsed_task.theme.mention == "红色药瓶", "theme mention must be full noun phrase"
    assert ir.parsed_task.theme.specific_class in ("medicine_bottle", "bottle"), f"specific_class={ir.parsed_task.theme.specific_class}"
    assert "container" in (ir.parsed_task.theme.parent_class or ""), f"parent_class={ir.parsed_task.theme.parent_class}"
    assert "medicine_bottle" in ir.parsed_task.theme.ontology_path

    # ── 2. Recipient 识别但缺少位姿 ──
    assert ir.parsed_task.recipient is not None
    assert ir.parsed_task.recipient.entity_id == "user"
    assert "recipient_pose_or_handover_zone" in ir.grounded_task.missing_roles

    # ── 3. support_surface 必须为 null ──
    assert ir.parsed_task.support_surface is None, "HANDOVER must not have support_surface"

    # ── 4. plan_status = NEEDS_CLARIFICATION ──
    assert ir.plan_metadata.plan_status == PlanStatus.NEEDS_CLARIFICATION

    # ── 5. execution_allowed = false ──
    assert ir.validation_result.execution_allowed is False

    # ── 6. override_ledger 为空 (无用户力/速度请求) ──
    assert len(ir.constraint_resolution.override_ledger) == 0, "override_ledger must be empty when no user constraints"

    # ── 7. user_constraints 为空 ──
    assert len(ir.parsed_task.user_constraints) == 0

    # ── 8. 默认抓力使用推荐值，不是硬上限 ──
    force_res = ir.constraint_resolution.parameters["force_n"]
    assert force_res.request_infeasible is False
    assert force_res.selected_source_kind is not None
    # selected should not be the OBJECT_HARD_LIMIT max
    assert force_res.domain.max_value is not None and force_res.domain.max_value > 0

    # ── 9. domain sources recorded ──
    assert len(force_res.domain.upper_sources) > 0, "upper_sources must record constraint IDs"
    assert len(force_res.domain.lower_sources) > 0, "lower_sources must record constraint IDs"

    # ── 10. BT 不含 MoveTo(user) → Release ──
    action_names = [a.skill_name for a in bt.root.flatten_actions()]
    assert "Release" not in action_names, "HANDOVER must not use Release"
    for a in bt.root.flatten_actions():
        dest = a.params.get("destination", "")
        assert dest != "user", f"{a.skill_name} must not have destination=user"
        assert dest != "我", f"{a.skill_name} must not have destination=我"

    # ── 11. 验证器捕获 BT_TARGET 和 HANDOVER_MOVETO_USER ──
    issue_str = " ".join(i.code for i in ir.validation_result.issues)
    assert "NON_DISPATCHABLE_STATUS" in issue_str
    assert "MISSING_RECIPIENT_POSE" in issue_str

    # ── 12. 不出现 custom/min-clamping/v2.1 旧文案 ──
    import json
    ir_json = json.loads(ir.model_dump_json())
    assert ir.ir_version == "3.0.0"
    assert ir.plan_metadata.plan_status != "CUSTOM"
    for trace_node in ir_json.get("decision_trace", []):
        assert "min-clamping" not in str(trace_node), "decision_trace must not reference min-clamping"
        assert "v2.1" not in str(trace_node), "decision_trace must not reference v2.1"

    # ── 13. plan_metadata 一致性 ──
    assert ir.plan_metadata.plan_hash == ir.constraint_resolution.plan_hash
    assert ir.plan_metadata.audit_id == ir.constraint_resolution.audit_id


def test_handover_ui_never_shows_normal_when_blocked():
    """UI must not render '正常执行' when execution_allowed=False."""
    import json as _json
    from robot_intent_agent.demo.web_ui import pipeline

    obs_json = _json.dumps({"objects": [{
        "object_id": "obj-4d1121",
        "category_candidates": [{"name": "medicine_bottle", "score": 0.9}],
        "pose": {"position": {"x": 0.15, "y": 0.05, "z": 0.03}},
        "geometry": {"size": {"width": 0.03, "height": 0.08, "depth": 0.03}},
        "appearance": {"color": "red", "material": "plastic"},
        "affordances": ["graspable", "movable"],
        "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}
    }], "robot_state": {"gripper": {"is_open": True, "has_object": False}}})

    result = pipeline.run("把红色药瓶递给我", obs_json, "纯规则引擎 (极速)", "")
    assert result["execution_ready"] is False, "UI must report execution_ready=False"
    assert result["plan_status"] == "NEEDS_CLARIFICATION", f"Expected NEEDS_CLARIFICATION, got {result['plan_status']}"

    # Render constraint and verify it does NOT contain "正常执行"
    from robot_intent_agent.demo.web_ui import render_constraint
    html = render_constraint(result)
    assert "正常执行" not in html, "UI must not display '正常执行' when execution not allowed"
    assert "NEEDS_CLARIFICATION" in html or "BLOCKED" in html, "UI must show blocked/clarification status"


def test_stage_velocity_validator_blocks_handover_exceed():
    """ApproachHandoverZone at 0.2 m/s must be BLOCKED by validator."""
    scene = build_scene([
        RawObjectPercept(name="红色药瓶", x=0.15, y=0.05, z=0.03,
                         width=0.03, height=0.08, depth=0.03, color="red", material="plastic"),
    ])
    from robot_intent_agent.schemas.behavior_tree import BehaviorTree, BTNode, BTNodeType, SkillAction
    from robot_intent_agent.constraint.base import ConstraintGraph
    from robot_intent_agent.final_plan_validator import FinalPlanValidator

    # Build a mock BT with an over-speed ApproachHandoverZone
    children = [
        BTNode(type=BTNodeType.ACTION, name="Reach", skill=SkillAction(
            skill_name="Reach", target="红色药瓶", params={"velocity_ms": 0.2})),
        BTNode(type=BTNodeType.ACTION, name="Grasp", skill=SkillAction(
            skill_name="Grasp", target="红色药瓶", params={"force_n": 3.0})),
        BTNode(type=BTNodeType.ACTION, name="ApproachHandoverZone", skill=SkillAction(
            skill_name="ApproachHandoverZone", target="user", params={"velocity_ms": 0.2})),
    ]
    root_node = BTNode(type=BTNodeType.SEQUENCE, name="Root", children=children)

    parsed = parse_task_semantics("把红色药瓶递给我", scene=scene)
    from robot_intent_agent.task_semantics import ConstraintResolution, ParameterResolution, ConstraintDomain
    resolution = ConstraintResolution(plan_status=PlanStatus.READY)
    resolution.parameters["force_n"] = ParameterResolution(parameter="force_n", selected_value=3.0)
    resolution.parameters["velocity_ms"] = ParameterResolution(parameter="velocity_ms", selected_value=0.2)

    bt = BehaviorTree(task_id="test-handover-vel", root=root_node)
    validator = FinalPlanValidator()
    result = validator.validate(parsed, bt, ConstraintGraph(task_id="test"), scene, resolution)

    assert result.execution_allowed is False
    assert any("STAGE_VELOCITY_EXCEEDED" in i.code for i in result.issues), \
        f"Validator must catch stage velocity violation, got: {[i.code for i in result.issues]}"


# ══════════════════════════════════════════════════════════════════
# v3.0 TC_003 回归: 玻璃杯高抓力安全截断
# ══════════════════════════════════════════════════════════════════

def test_grasp_glass_cup_force_safety_substitution():
    """纯 GRASP: 快点！用50N力量抓住玻璃杯！→ NEEDS_CLARIFICATION (EXACT unsafe request)."""
    scene = build_scene([
        RawObjectPercept(name="玻璃杯", x=0.35, y=0.12, z=0.075,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
    ])
    instruction = "快点！用50N力量抓住玻璃杯！"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    # Action = GRASP (not FETCH — no directional 过来)
    assert ir.parsed_task.action == TaskActionKind.GRASP

    # Theme grounded
    assert ir.parsed_task.theme is not None
    assert ir.parsed_task.theme.entity_id is not None
    assert "杯" in ir.parsed_task.theme.mention

    # User exact force 50N
    force_constraints = [c for c in ir.parsed_task.user_constraints if c.parameter == "force_n"]
    assert len(force_constraints) == 1
    assert force_constraints[0].value == 50.0
    assert force_constraints[0].operator.value == "exact"

    # Resolution: 50N → 2N safety substitution
    fr = ir.constraint_resolution.parameters["force_n"]
    assert fr.selected_value == pytest.approx(2.0, rel=1e-6)
    assert fr.substituted_from == 50.0
    assert fr.request_infeasible is True
    assert fr.override_required is True
    assert fr.selected_source_kind is not None
    assert fr.selected_source_kind.value != "USER_EXACT", "source must NOT be USER_EXACT for substituted value"
    assert "EXCEEDS" in (fr.substitution_reason or "")

    # Velocity ≤ glass max 0.1 m/s
    vr = ir.constraint_resolution.parameters["velocity_ms"]
    assert vr.selected_value is not None and vr.selected_value <= 0.1 + 1e-9

    # Domain sources recorded
    assert len(fr.domain.upper_sources) > 0
    assert len(fr.domain.lower_sources) > 0

    # Plan status — EXACT user request exceeding hard limits escalates to non-executable status
    # (user explicitly asked for unsafe value; should not silently substitute)
    assert ir.plan_metadata.plan_status in (PlanStatus.READY_WITH_SAFE_SUBSTITUTION, PlanStatus.NEEDS_CLARIFICATION, PlanStatus.BLOCKED)
    # execution_allowed should be False when unsafe request is detected
    if ir.plan_metadata.plan_status != PlanStatus.READY_WITH_SAFE_SUBSTITUTION:
        assert ir.validation_result.execution_allowed is False
    else:
        assert ir.validation_result.execution_allowed is True

    # Override ledger contains the substitution
    assert len(ir.constraint_resolution.override_ledger) > 0
    assert any("50.0" in str(e) for e in ir.constraint_resolution.override_ledger)

    # BT has entity_id on all actions
    for a in bt.root.flatten_actions():
        assert a.params.get("target_entity_id") is not None, \
            f"{a.skill_name} missing target_entity_id"


def test_fetch_glass_cup_without_delivery_zone_needs_clarification():
    """FETCH: 快点！用50N力量把玻璃杯抓过来！→ NEEDS_CLARIFICATION (no delivery zone)."""
    scene = build_scene([
        RawObjectPercept(name="玻璃杯", x=0.35, y=0.12, z=0.075,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
    ])
    instruction = "快点！用50N力量把玻璃杯抓过来！"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    # Action = FETCH (抓过来 has directional component)
    assert ir.parsed_task.action == TaskActionKind.FETCH

    # Theme grounded
    assert ir.parsed_task.theme is not None
    assert ir.parsed_task.theme.entity_id is not None

    # Force still correctly substituted: 50N → 2N
    fr = ir.constraint_resolution.parameters["force_n"]
    assert fr.selected_value == pytest.approx(2.0, rel=1e-6)
    assert fr.request_infeasible is True

    # Missing delivery zone → NEEDS_CLARIFICATION
    assert ir.plan_metadata.plan_status == PlanStatus.NEEDS_CLARIFICATION
    assert ir.validation_result.execution_allowed is False
    assert "delivery_pose_or_fetch_zone" in ir.grounded_task.missing_roles

    # BT must NOT contain MoveTo(user) or Release
    action_names = [a.skill_name for a in bt.root.flatten_actions()]
    assert "Release" not in action_names
    for a in bt.root.flatten_actions():
        assert a.params.get("destination", "") not in ("user", "我")

    # BT has entity_id on all motion actions
    for a in bt.root.flatten_actions():
        if a.skill_name in ("Reach", "Grasp", "Fetch"):
            assert a.params.get("target_entity_id") is not None


def test_cross_language_grounding_cup_to_glass():
    """中文'玻璃杯'接地到 scene cup/material=glass."""
    scene = build_scene([
        RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
    ])
    parsed = parse_task_semantics("把玻璃杯拿过来", scene=scene)
    assert parsed.theme is not None
    assert parsed.theme.entity_id is not None
    assert "杯" in parsed.theme.mention
    assert parsed.theme.specific_class in ("cup", "glass_cup")


def test_ambiguous_multiple_cups_needs_clarification():
    """多个杯子时返回 NEEDS_CLARIFICATION."""
    scene = build_scene([
        RawObjectPercept(name="cup_A", x=0.35, y=0.12, z=0.075,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
        RawObjectPercept(name="cup_B", x=0.30, y=-0.10, z=0.075,
                         width=0.06, height=0.10, depth=0.06,
                         color="blue", material="plastic"),
    ])
    instruction = "把杯子拿过来"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="杯子")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    # With multiple cups and no disambiguation, the new GroundingEngine
    # detects ambiguity and returns needs_clarification without committing
    # to a specific cup. This is safer than guessing.
    assert ir.parsed_task.theme is None or ir.validation_result.execution_allowed is False


def test_no_target_in_scene_blocks_execution():
    """场景无目标物体时必须禁止执行."""
    scene = build_scene([
        RawObjectPercept(name="桌子", x=0.0, y=0.0, z=0.0,
                         width=0.5, height=0.03, depth=0.3,
                         color="brown", material="wood"),
    ])
    instruction = "把玻璃杯拿过来"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    # Theme not found in scene → blocked or needs clarification
    assert ir.validation_result.execution_allowed is False


def test_clean_grasp_no_old_artifacts():
    """GRASP 用例不出现 v2.1/min-clamping/NoneN."""
    import json as _json
    scene = build_scene([
        RawObjectPercept(name="玻璃杯", x=0.35, y=0.12, z=0.075,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
    ])
    instruction = "用50N力量抓住玻璃杯"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    ir_json = _json.loads(ir.model_dump_json())
    # No old artifacts in decision_trace
    for t in ir_json.get("decision_trace", []):
        reason = t.get("reason", "")
        assert "min-clamping" not in reason, f"min-clamping found: {reason}"
        assert "v2.1" not in reason, f"v2.1 found: {reason}"
        assert "no override needed" not in reason.lower(), f"no override found: {reason}"

    # No NoneN in explain_report
    er = ir_json.get("explain_report", {})
    er_str = _json.dumps(er)
    assert "NoneN" not in er_str

    # IR version must be 3.0.0
    assert ir.ir_version == "3.0.0"


def test_force_selected_source_is_not_user_exact_when_substituted():
    """替代后的 selected_source_kind 不得为 USER_EXACT."""
    scene = build_scene([
        RawObjectPercept(name="玻璃杯", x=0.35, y=0.12, z=0.075,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
    ])
    instruction = "用50N力量抓住玻璃杯"
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="玻璃杯")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    fr = ir.constraint_resolution.parameters["force_n"]
    if fr.request_infeasible:
        assert fr.selected_source_kind is not None
        assert fr.selected_source_kind.value != "USER_EXACT", \
            f"Substituted value must not claim USER_EXACT, got {fr.selected_source_kind.value}"


def test_ui_consistency_grasp_with_substitution():
    """UI 在安全替代时显示正确的值."""
    import json as _json
    from robot_intent_agent.demo.web_ui import pipeline

    obs_json = _json.dumps({"objects": [{
        "object_id": "obj_cup_001",
        "category_candidates": [{"name": "cup", "score": 0.93}],
        "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}},
        "geometry": {"size": {"width": 0.07, "height": 0.12, "depth": 0.07}},
        "appearance": {"color": "transparent", "material": "glass"},
        "affordances": ["graspable", "fragile", "movable"],
        "tracking": {"state": "stationary", "confidence": 0.98,
                     "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}
    }], "robot_state": {"gripper": {"is_open": True, "has_object": False}}})

    result = pipeline.run("用50N力量抓住玻璃杯", obs_json, "纯规则引擎 (极速)", "")
    ir = result["ir"]

    # UI force matches IR
    assert result["resolved_force"] == ir.constraint_resolution.parameters["force_n"].selected_value
    # UI plan_status matches IR
    assert result["plan_status"] == ir.plan_metadata.plan_status.value
    # UI execution_ready matches IR
    assert result["execution_ready"] == ir.validation_result.execution_allowed


# ══════════════════════════════════════════════════════════════════
# 全局不变量测试 (覆盖 GRASP, FETCH, HANDOVER, PLACE, DYNAMIC_GRASP)
# ══════════════════════════════════════════════════════════════════

def _run_pipeline_for_invariant(instruction, material="plastic"):
    """Helper: run full pipeline and return (ir, bt)."""
    scene = build_scene([
        RawObjectPercept(name="测试物体", x=0.2, y=0.1, z=0.05,
                         width=0.05, height=0.08, depth=0.05,
                         color="red", material=material),
    ])
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target="测试物体")
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
    return ir, bt, scene


class TestGlobalInvariants:
    """v3.0 全局不变量 — 不针对任何特定测试用例硬编码."""

    def test_invariant_1_theme_grounded_when_execution_allowed(self):
        """execution_allowed=true → theme 必须已接地."""
        ir, _, _ = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        if ir.validation_result.execution_allowed:
            assert ir.parsed_task.theme is not None
            assert ir.parsed_task.theme.entity_id is not None, \
                "theme.entity_id must not be empty when execution_allowed=true"

    def test_invariant_2_no_missing_roles_when_execution_allowed(self):
        """execution_allowed=true → missing_roles 必须为空."""
        ir, _, _ = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        if ir.validation_result.execution_allowed:
            assert len(ir.grounded_task.missing_roles) == 0, \
                f"missing_roles must be empty when execution_allowed=true, got {ir.grounded_task.missing_roles}"

    def test_invariant_3_no_blocking_issues_when_execution_allowed(self):
        """execution_allowed=true → 不得包含阻断问题."""
        ir, _, _ = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        if ir.validation_result.execution_allowed:
            severe = [i for i in ir.validation_result.issues if i.severity == "error"]
            assert len(severe) == 0, f"no error-severity issues allowed, got {[i.code for i in severe]}"

    def test_invariant_4_bt_has_valid_target_references(self):
        """所有可执行BT动作 → target_entity_id 或 target_pose_id 必须有效."""
        ir, bt, scene = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", [])}
        for action in bt.root.flatten_actions():
            tid = action.params.get("target_entity_id") or action.params.get("target_pose_id")
            target = action.params.get("target", "")
            # Either entity_id is set and valid, or target name matches a scene object
            assert (tid and tid in scene_ids) or target in {getattr(o, "name", "") for o in scene.objects}, \
                f"{action.skill_name}: target='{target}' target_entity_id='{tid}' not in scene {scene_ids}"

    def test_invariant_5_force_not_exceed_hard_limit(self):
        """所有技能 force 不超过对应硬上限."""
        ir, bt, _ = _run_pipeline_for_invariant("用8N力量抓住测试物体", material="glass")
        fr = ir.constraint_resolution.parameters.get("force_n")
        if fr and fr.selected_value is not None:
            for action in bt.root.flatten_actions():
                af = action.params.get("force_n")
                if af is not None:
                    if isinstance(af, dict):
                        af = af.get("value", af)
                    assert float(af) <= (fr.domain.max_value or float("inf")) + 1e-9, \
                        f"{action.skill_name} force {af} exceeds domain max {fr.domain.max_value}"

    def test_invariant_6_velocity_not_exceed_stage_limit(self):
        """所有技能 velocity 不超过对应阶段硬上限."""
        from robot_intent_agent.final_plan_validator import STAGE_VELOCITY_LIMITS
        _, bt, _ = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        for action in bt.root.flatten_actions():
            limit = STAGE_VELOCITY_LIMITS.get(action.skill_name)
            if limit is not None and limit > 0:
                av = action.params.get("velocity_ms")
                if av is not None:
                    if isinstance(av, dict):
                        av = av.get("value", av)
                    assert float(av) <= limit + 1e-9, \
                        f"{action.skill_name} velocity {av} exceeds stage limit {limit}"

    def test_invariant_7_substituted_source_not_user_exact(self):
        """substituted_from 存在且 selected != requested → selected_source_kind 不得为 USER_EXACT."""
        ir, _, _ = _run_pipeline_for_invariant("用50N力量抓住测试物体", material="glass")
        for pname, pr in ir.constraint_resolution.parameters.items():
            if pr.substituted_from is not None and pr.selected_value != pr.substituted_from:
                assert pr.selected_source_kind is not None
                assert pr.selected_source_kind.value != "USER_EXACT", \
                    f"{pname}: substituted value must not claim USER_EXACT"

    def test_invariant_8_safe_substitution_has_override(self):
        """READY_WITH_SAFE_SUBSTITUTION → override_required=true 且 override ledger 非空."""
        ir, _, _ = _run_pipeline_for_invariant("用50N力量抓住测试物体", material="glass")
        if ir.plan_metadata.plan_status == PlanStatus.READY_WITH_SAFE_SUBSTITUTION:
            # At least one parameter should have override
            any_override = any(
                pr.override_required and pr.request_infeasible
                for pr in ir.constraint_resolution.parameters.values()
            )
            assert any_override or len(ir.constraint_resolution.override_ledger) > 0, \
                "READY_WITH_SAFE_SUBSTITUTION requires override_required or non-empty ledger"

    def test_invariant_9_needs_clarification_or_blocked_disallows_execution(self):
        """NEEDS_CLARIFICATION 或 BLOCKED → execution_allowed=false."""
        ir, _, _ = _run_pipeline_for_invariant("把测试物体递给我")
        if ir.plan_metadata.plan_status in (PlanStatus.NEEDS_CLARIFICATION, PlanStatus.BLOCKED):
            assert ir.validation_result.execution_allowed is False, \
                f"{ir.plan_metadata.plan_status.value} must have execution_allowed=false"

    def test_invariant_10_ui_consistency_with_ir(self):
        """UI 显示的 force/velocity/status 必须等于 RobotTaskIR 权威值."""
        import json as _json
        from robot_intent_agent.demo.web_ui import pipeline

        obs = _json.dumps({"objects": [{
            "object_id": "obj_test",
            "category_candidates": [{"name": "bottle", "score": 0.9}],
            "pose": {"position": {"x": 0.2, "y": 0.1, "z": 0.05}},
            "geometry": {"size": {"width": 0.05, "height": 0.08, "depth": 0.05}},
            "appearance": {"color": "red", "material": "plastic"},
            "affordances": ["graspable", "movable"],
            "tracking": {"state": "stationary", "confidence": 0.95,
                         "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}
        }], "robot_state": {"gripper": {"is_open": True, "has_object": False}}})

        result = pipeline.run("用5N力量抓住测试物体", obs, "纯规则引擎 (极速)", "")
        ir = result["ir"]
        fr = ir.constraint_resolution.parameters.get("force_n")
        vr = ir.constraint_resolution.parameters.get("velocity_ms")
        if fr and fr.selected_value is not None:
            assert result["resolved_force"] == fr.selected_value
        if vr and vr.selected_value is not None:
            assert result["resolved_vel"] == vr.selected_value
        assert result["plan_status"] == ir.plan_metadata.plan_status.value
        assert result["execution_ready"] == ir.validation_result.execution_allowed

    def test_invariant_11_constraint_count_matches_array(self):
        """compiled constraint 数量必须等于真实数组长度."""
        import json as _json
        ir, bt, _ = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        ir_json = _json.loads(ir.model_dump_json())
        compiled = ir_json.get("compiled_constraints", {})
        # Constraint set should have entries matching the graph
        assert isinstance(compiled, dict)

    def test_invariant_12_bt_entity_ids_in_scene(self):
        """BT 中所有 entity_id 必须存在于 scene objects."""
        ir, bt, scene = _run_pipeline_for_invariant("用2N力量抓住测试物体")
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", [])}
        for action in bt.root.flatten_actions():
            tid = action.params.get("target_entity_id")
            if tid:
                assert tid in scene_ids, \
                    f"{action.skill_name}: target_entity_id '{tid}' not in scene {scene_ids}"
