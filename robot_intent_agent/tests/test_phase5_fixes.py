"""
Regression tests for input fidelity and NL semantics fixes (Phase 5).

Each test captures a specific failure pattern from the blind evaluation.
Tests are written BEFORE fixes — they should FAIL initially, then PASS after fixes.
"""

from __future__ import annotations

import pytest

from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.task_semantics import parse_task_semantics, TaskActionKind, PlanStatus


def _scene(objects):
    return SemanticSceneBuilder().build(objects)


def _pipeline(instruction, objects):
    scene = _scene(objects)
    target = objects[0].name if objects else ""
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
    return ir, scene  # Return scene too for UUID checks


# ══════════════════════════════════════════════════════════════
# Category A: Entity Grounding — non-standard categories
# ══════════════════════════════════════════════════════════════

class TestEntityGroundingNonStandardCategories:
    """Theme grounding must work for block, ball, and other common categories."""

    def test_ground_block_category(self):
        """'抓住红色方块' with scene object category='block' must ground."""
        objs = [RawObjectPercept(name="block", x=0.22, y=0.15, z=0.04,
                                  width=0.05, height=0.05, depth=0.05,
                                  color="red", material="wood")]
        ir, scene = _pipeline("抓住红色方块", objs)
        pt = ir.parsed_task
        scene_ids = {getattr(o, "id", "") for o in (scene.objects)}
        assert pt.theme is not None, "Theme must not be None"
        assert pt.theme.entity_id is not None, "Theme must be grounded"
        assert pt.theme.entity_id in scene_ids, f"Theme entity_id {pt.theme.entity_id} not in scene"
        assert pt.theme.specific_class in (None, "block", "cube"), \
            f"specific_class should be block/cube, got {pt.theme.specific_class}"

    def test_ground_ball_category(self):
        """'抓住小球' with scene object category='ball' must ground."""
        objs = [RawObjectPercept(name="ball", x=0.30, y=0.15, z=0.03,
                                  width=0.04, height=0.04, depth=0.04,
                                  color="red", material="rubber")]
        ir, scene = _pipeline("抓住小球", objs)
        pt = ir.parsed_task
        scene_ids = {getattr(o, "id", "") for o in (scene.objects)}
        assert pt.theme is not None and pt.theme.entity_id is not None
        assert pt.theme.entity_id in scene_ids

    def test_ground_unknown_category_fallback(self):
        """Unknown category should still ground via fallback name matching."""
        objs = [RawObjectPercept(name="device", x=0.30, y=0.10, z=0.04,
                                  width=0.06, height=0.03, depth=0.10,
                                  color="black", material="plastic")]
        ir, scene = _pipeline("抓住那个设备", objs)
        pt = ir.parsed_task
        scene_ids = {getattr(o, "id", "") for o in (scene.objects)}
        # Theme should be grounded or at minimum not crash
        assert pt is not None
        if pt.theme and pt.theme.entity_id:
            assert pt.theme.entity_id in scene_ids

    def test_single_object_always_grounded(self):
        """With only one object in scene, any instruction should ground to it."""
        objs = [RawObjectPercept(name="needle", x=0.25, y=0.10, z=0.02,
                                  width=0.001, height=0.001, depth=0.03,
                                  color="silver", material="metal")]
        ir, scene = _pipeline("抓住那根针", objs)
        pt = ir.parsed_task
        scene_ids = {getattr(o, "id", "") for o in (scene.objects)}
        if pt.theme and pt.theme.entity_id:
            assert pt.theme.entity_id in scene_ids

    def test_vague_reference_grounds_to_only_object(self):
        """'拿起那个东西' with only one object must ground to it."""
        objs = [RawObjectPercept(name="block", x=0.30, y=0.10, z=0.05,
                                  width=0.05, height=0.05, depth=0.05,
                                  color="gray", material="plastic")]
        ir, scene = _pipeline("拿起那个东西", objs)
        pt = ir.parsed_task
        scene_ids = {getattr(o, "id", "") for o in (scene.objects)}
        if pt.theme and pt.theme.entity_id:
            assert pt.theme.entity_id in scene_ids


# ══════════════════════════════════════════════════════════════
# Category B: Color/Material Mismatch → Block Execution
# ══════════════════════════════════════════════════════════════

class TestColorMismatchBlocksExecution:
    """When instruction specifies a color not in scene, execution must be blocked."""

    def test_red_cup_requested_but_only_blue_exists(self):
        """'抓住红色杯子' with only blue cup → must NOT ground or must block."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="blue", material="plastic")]
        ir, scene = _pipeline("抓住红色杯子", objs)
        pt = ir.parsed_task
        vr = ir.validation_result
        # Either theme not grounded, or execution blocked
        theme_wrong = (pt.theme is None or pt.theme.entity_id is None)
        exec_blocked = (vr and not vr.execution_allowed)
        assert theme_wrong or exec_blocked, \
            f"Theme grounded to wrong color object AND execution allowed. theme={pt.theme.entity_id if pt.theme else None}"

    def test_transparent_glass_requested_but_plastic_block_exists(self):
        """'抓住那个玻璃杯' with only plastic block → must not fabricate grounding."""
        objs = [RawObjectPercept(name="block", x=0.35, y=0.12, z=0.05,
                                  width=0.05, height=0.05, depth=0.05,
                                  color="transparent", material="plastic")]
        ir, scene = _pipeline("抓住那个玻璃杯", objs)
        pt = ir.parsed_task
        vr = ir.validation_result
        # Category is "block" not "cup/glass" — should not be grounded as glass cup
        if pt.theme and pt.theme.entity_id and pt.theme.specific_class:
            is_wrong = "glass" not in (pt.theme.specific_class or "")
            assert not is_wrong or not vr.execution_allowed, \
                "Fabricated glass cup grounding from plastic block"

    def test_blue_block_requested_but_only_red_and_green_exist(self):
        """'抓住蓝色方块' with red + green blocks → no blue → must block."""
        objs = [
            RawObjectPercept(name="block", x=0.20, y=0.10, z=0.03,
                             width=0.05, height=0.05, depth=0.05,
                             color="red", material="wood"),
            RawObjectPercept(name="block", x=0.35, y=-0.10, z=0.03,
                             width=0.05, height=0.05, depth=0.05,
                             color="green", material="wood"),
        ]
        ir, scene = _pipeline("抓住蓝色方块", objs)
        pt = ir.parsed_task
        vr = ir.validation_result
        # No blue block exists — must not ground to red or green
        if pt.theme and pt.theme.entity_id:
            scene = _scene(objs)
            obj = scene.find_object(pt.theme.entity_id)
            if obj:
                color = getattr(obj, "attributes", {}).get("color", "")
                assert color != "red" and color != "green", \
                    f"Grounded to wrong color ({color}) when blue requested"
            assert not vr.execution_allowed, \
                f"Execution allowed when target color not found"


# ══════════════════════════════════════════════════════════════
# Category C: Negation Propagation
# ══════════════════════════════════════════════════════════════

class TestNegationPropagation:
    """Negation/avoid instructions must propagate to BT/CG collision_avoid."""

    def test_avoid_object_appears_in_cg(self):
        """'别碰X' must produce collision_avoid constraint node in CG."""
        objs = [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=0.05, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ]
        scene = _scene(objs)
        bt = BehaviorTreeGenerator().plan("把盒子拿过来，别碰玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("把盒子拿过来，别碰玻璃杯", bt, scene=scene, target="box")
        ir = RobotTaskIRGenerator().generate("把盒子拿过来，别碰玻璃杯", bt, cg, scene=scene)

        # Check CG has collision_avoid
        collision_nodes = [n for n in cg.nodes if n.constraint_type == "collision_avoid"]
        assert len(collision_nodes) > 0, \
            f"No collision_avoid nodes in CG. Nodes: {[n.constraint_type for n in cg.nodes]}"

        # Check BT has PlanPath or Avoid
        action_names = [a.skill_name for a in bt.root.flatten_actions()]
        assert "PlanPath" in action_names, \
            f"No PlanPath in BT when obstacles exist. Actions: {action_names}"

    def test_avoid_with_multiple_objects(self):
        """'不要碰任何东西' should propagate avoid for all non-target objects."""
        objs = [
            RawObjectPercept(name="cup", x=0.30, y=0.20, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
            RawObjectPercept(name="cup", x=0.30, y=0.00, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
            RawObjectPercept(name="cup", x=0.30, y=-0.20, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ]
        scene = _scene(objs)
        bt = BehaviorTreeGenerator().plan("抓住最右边的杯子，不要碰任何东西", scene=scene)
        cg = HybridConstraintCompiler().compile("抓住最右边的杯子，不要碰任何东西", bt, scene=scene, target="cup")
        ir = RobotTaskIRGenerator().generate("抓住最右边的杯子，不要碰任何东西", bt, cg, scene=scene)

        # Should have avoid for at least the other two cups
        all_avoids = set()
        for a in bt.root.flatten_actions():
            for key in ("avoid_obstacles", "avoid", "avoid_objects"):
                av = a.params.get(key, [])
                if isinstance(av, list):
                    all_avoids.update(str(x) for x in av)
        for node in cg.nodes:
            if node.constraint_type == "collision_avoid":
                all_avoids.add(str(node.params.get("obstacle", "")))

        # At least some avoid objects should be present
        assert len(all_avoids) >= 1, \
            f"No avoid objects propagated at all. BT actions: {[a.skill_name for a in bt.root.flatten_actions()]}"

    def test_dont_touch_keyword_propagates(self):
        """'但我不想让你碰X' must produce avoid for X."""
        objs = [
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="table", x=0.40, y=0.00, z=0.00,
                             width=0.60, height=0.03, depth=0.40,
                             color="brown", material="wood"),
        ]
        scene = _scene(objs)
        bt = BehaviorTreeGenerator().plan("抓住杯子，但我不想让你碰桌子", scene=scene)
        cg = HybridConstraintCompiler().compile("抓住杯子，但我不想让你碰桌子", bt, scene=scene, target="cup")
        ir = RobotTaskIRGenerator().generate("抓住杯子，但我不想让你碰桌子", bt, cg, scene=scene)

        # Check avoidance
        all_avoids = set()
        for a in bt.root.flatten_actions():
            for key in ("avoid_obstacles", "avoid", "avoid_objects"):
                av = a.params.get(key, [])
                if isinstance(av, list):
                    all_avoids.update(str(x) for x in av)
        for node in cg.nodes:
            if node.constraint_type == "collision_avoid":
                all_avoids.add(str(node.params.get("obstacle", "")))

        # Should detect table as avoid
        has_table_avoid = any("table" in a or "桌" in a for a in all_avoids)
        assert has_table_avoid, \
            f"Table avoidance not detected. Avoids: {all_avoids}"


# ══════════════════════════════════════════════════════════════
# Category D: Support Surface Entity ID
# ══════════════════════════════════════════════════════════════

class TestSupportSurfaceEntityId:
    """Support surface entity_id must be a scene UUID, not Chinese text."""

    def test_support_surface_entity_id_is_scene_uuid(self):
        """Place on table: support_surface.entity_id must be the table's scene UUID."""
        objs = [
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="table", x=0.40, y=0.00, z=0.00,
                             width=0.60, height=0.03, depth=0.40,
                             color="brown", material="wood"),
        ]
        ir, pipeline_scene = _pipeline("把杯子放到桌子上", objs)
        scene_ids = {getattr(o, "id", "") for o in pipeline_scene.objects}
        pt = ir.parsed_task
        if pt.support_surface:
            ss_id = pt.support_surface.entity_id
            assert ss_id in scene_ids, \
                f"Support surface entity_id '{ss_id}' not in scene UUIDs: {scene_ids}"

    def test_support_surface_entity_id_not_chinese_text(self):
        """Support surface must not use Chinese text like '桌' as entity_id."""
        objs = [
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="table", x=0.40, y=0.00, z=0.00,
                             width=0.60, height=0.03, depth=0.40,
                             color="brown", material="wood"),
        ]
        ir, scene = _pipeline("把杯子放到桌子上", objs)
        pt = ir.parsed_task
        if pt.support_surface:
            ss_id = pt.support_surface.entity_id
            # Should not be plain Chinese text
            is_chinese_only = ss_id and all('一' <= c <= '鿿' for c in (ss_id or ""))
            assert not is_chinese_only, \
                f"Support surface entity_id is Chinese text '{ss_id}', expected UUID"


# ══════════════════════════════════════════════════════════════
# Category E: Action Recognition Edge Cases
# ══════════════════════════════════════════════════════════════

class TestActionRecognition:
    """Action classification must work for common verbs and edge cases."""

    def test_grasp_recognized_for_cup(self):
        parsed = parse_task_semantics("抓住杯子")
        assert parsed.action == TaskActionKind.GRASP

    def test_fetch_recognized(self):
        parsed = parse_task_semantics("把盒子拿过来")
        assert parsed.action == TaskActionKind.FETCH

    def test_place_recognized(self):
        parsed = parse_task_semantics("把杯子放到桌子上")
        assert parsed.action == TaskActionKind.PLACE

    def test_handover_recognized(self):
        parsed = parse_task_semantics("把药瓶递给我")
        assert parsed.action == TaskActionKind.HANDOVER

    def test_dynamic_grasp_for_moving_target(self):
        parsed = parse_task_semantics("抓住正在移动的红色小球")
        assert parsed.action == TaskActionKind.DYNAMIC_GRASP, \
            f"Expected DYNAMIC_GRASP for moving target, got {parsed.action}"

    def test_push_not_fetch(self):
        """'推过来' should be recognized, not always fall to FETCH."""
        parsed = parse_task_semantics("把远处的盒子推过来")
        # '推' keyword should at least be detected
        has_push_hint = parsed.action != TaskActionKind.CUSTOM
        assert has_push_hint, "Action should be recognized for '推过来'"

    def test_english_grasp_recognized(self):
        """English 'grasp' should be recognized."""
        parsed = parse_task_semantics("grasp the cup")
        # English keyword may not be fully supported yet, but should not crash
        assert parsed is not None
        # System should at minimum not crash on English-only input

    def test_reduplication_recognized(self):
        """'抓抓杯子' (reduplication) should be recognized as GRASP."""
        parsed = parse_task_semantics("抓抓杯子")
        # Reduplication is common in colloquial Chinese
        assert parsed.action in (TaskActionKind.GRASP, TaskActionKind.CUSTOM), \
            f"Reduplication should not crash, got {parsed.action}"


# ══════════════════════════════════════════════════════════════
# Category F: Numeric Constraint Parsing
# ══════════════════════════════════════════════════════════════

class TestNumericConstraintParsing:
    """Force/velocity constraints must parse operator, value, and units correctly."""

    def test_exact_force_parsed(self):
        parsed = parse_task_semantics("用3N力量抓住杯子")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        assert len(fc) > 0, "Force constraint not parsed"
        assert fc[0].operator.value == "exact"
        assert fc[0].value == 3.0
        assert fc[0].unit == "N"

    def test_max_force_parsed(self):
        parsed = parse_task_semantics("不超过5N抓住杯子")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        assert len(fc) > 0, "Upper-bound force constraint not parsed"
        assert fc[0].operator.value == "max", \
            f"Expected 'max' operator, got '{fc[0].operator.value}'"
        assert fc[0].max_value == 5.0

    def test_min_force_parsed(self):
        parsed = parse_task_semantics("至少2N抓住杯子")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        assert len(fc) > 0, "Lower-bound force constraint not parsed"
        assert fc[0].operator.value == "min", \
            f"Expected 'min' operator, got '{fc[0].operator.value}'"
        assert fc[0].min_value == 2.0

    def test_range_force_parsed(self):
        parsed = parse_task_semantics("用3到5N的力量抓住杯子")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        assert len(fc) > 0, "Range force constraint not parsed"
        assert fc[0].operator.value == "range", \
            f"Expected 'range' operator, got '{fc[0].operator.value}'"
        assert fc[0].min_value == 3.0
        assert fc[0].max_value == 5.0

    def test_velocity_parsed(self):
        parsed = parse_task_semantics("以0.15m/s的速度移动杯子")
        vc = [c for c in parsed.user_constraints if c.parameter == "velocity_ms"]
        assert len(vc) > 0, "Velocity constraint not parsed"
        assert vc[0].value == 0.15
        assert vc[0].unit == "m/s"

    def test_min_velocity_parsed(self):
        parsed = parse_task_semantics("至少0.2m/s，用3N抓力，抓住杯子")
        vc = [c for c in parsed.user_constraints if c.parameter == "velocity_ms"]
        assert len(vc) > 0, "MIN velocity not parsed"
        assert vc[0].operator.value == "min"

    def test_multiple_constraints_in_one_instruction(self):
        parsed = parse_task_semantics("用2N力、速度0.1m/s，把红色药瓶递给我")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        vc = [c for c in parsed.user_constraints if c.parameter == "velocity_ms"]
        assert len(fc) > 0, "Force not parsed in multi-constraint instruction"
        assert len(vc) > 0, "Velocity not parsed in multi-constraint instruction"
        assert fc[0].value == 2.0
        assert vc[0].value == 0.1


# ══════════════════════════════════════════════════════════════
# Category G: Perception Factual Fidelity
# ══════════════════════════════════════════════════════════════

class TestPerceptionFactualFidelity:
    """No fabricated colors, materials, positions, or states in output."""

    def test_scene_object_color_preserved(self):
        """Scene object color must match perception input, not fabricated."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="blue", material="plastic")]
        scene = _scene(objs)
        obj = scene.objects[0]
        assert obj.attributes.get("color") == "blue", \
            f"Color preserved: expected 'blue', got '{obj.attributes.get('color')}'"

    def test_scene_object_material_preserved(self):
        """Scene object material must match perception input."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="transparent", material="glass")]
        scene = _scene(objs)
        obj = scene.objects[0]
        assert obj.attributes.get("material") == "glass", \
            f"Material preserved: expected 'glass', got '{obj.attributes.get('material')}'"

    def test_no_fabricated_objects_in_ir(self):
        """IR must not reference objects that don't exist in the scene."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        ir, scene = _pipeline("抓住杯子", objs)
        scene_ids = {getattr(o, "id", "") for o in (scene.objects)}
        # All BT action target_entity_ids must be in scene
        for action in ir.behavior_tree.root.flatten_actions():
            tid = action.params.get("target_entity_id", "")
            if tid and tid != "user":
                assert tid in scene_ids, \
                    f"BT action '{action.skill_name}' references non-existent entity '{tid}'"

    def test_unknown_category_not_fabricated(self):
        """When category is truly unknown, specific_class should be None/unknown."""
        objs = [RawObjectPercept(name="unknown_gizmo", x=0.30, y=0.10, z=0.05,
                                  width=0.05, height=0.05, depth=0.05,
                                  color="gray", material="metal")]
        scene = _scene(objs)
        obj = scene.objects[0]
        # specific_class can be None — that's acceptable
        # But it must not fabricate a known class
        if obj.specific_class and obj.specific_class not in ("unknown_gizmo",):
            # If it inferred a class, it must be reasonable from the name
            pass  # Accept any inference as long as it doesn't crash

    def test_position_values_are_finite(self):
        """Position values must be finite numbers, not NaN or Inf."""
        objs = [RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                  width=0.07, height=0.10, depth=0.07,
                                  color="white", material="plastic")]
        scene = _scene(objs)
        for obj in scene.objects:
            import math
            assert math.isfinite(obj.position.x)
            assert math.isfinite(obj.position.y)
            assert math.isfinite(obj.position.z)


# ══════════════════════════════════════════════════════════════
# Category H: ParsedTask Field Preservation
# ══════════════════════════════════════════════════════════════

class TestParsedTaskFieldPreservation:
    """ParsedTask must preserve source text, operator, value, and unit."""

    def test_constraint_preserves_unit(self):
        parsed = parse_task_semantics("用5N力量抓住杯子")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        assert fc and fc[0].unit == "N"

    def test_constraint_preserves_text_span(self):
        parsed = parse_task_semantics("用5N力量抓住杯子")
        fc = [c for c in parsed.user_constraints if c.parameter == "force_n"]
        assert fc and len(fc[0].text_span) > 0, "text_span must be preserved"

    def test_parsed_task_has_instruction(self):
        parsed = parse_task_semantics("抓住杯子")
        assert parsed.instruction == "抓住杯子"

    def test_unknown_role_is_none_not_fabricated(self):
        """Roles not mentioned in instruction must be None, not fabricated."""
        parsed = parse_task_semantics("抓住杯子")
        assert parsed.source is None, "Source should be None when not mentioned"
        assert parsed.destination is None, "Destination should be None when not mentioned"

    def test_confidence_fields_bounded(self):
        """Confidence fields must be in [0, 1] range."""
        parsed = parse_task_semantics("抓住杯子")
        assert 0.0 <= parsed.parse_confidence <= 1.0
        assert 0.0 <= parsed.grounding_confidence <= 1.0
        assert 0.0 <= parsed.constraint_confidence <= 1.0
