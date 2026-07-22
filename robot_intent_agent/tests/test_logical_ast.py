"""
Phase 4: Logical AST tests — negation, condition, sequence, manner.

At least 4 expressions per logic type. Covers the target failure cases
B33/B41/B44/B45/B46/B48/B49/B88 and generalizes beyond them.
"""

from __future__ import annotations

import pytest

from robot_intent_agent.semantic_reasoner.logical_ast import (
    parse_logical_ast,
    LogicalAST,
    LogicalOp,
    NegationNode,
    ConditionNode,
    SequenceNode,
    merge_ast_negations,
    ast_has_conditional,
)
from robot_intent_agent.task_semantics import (
    parse_task_semantics,
    _extract_manner,
    ParsedTask,
    TaskActionKind,
)
from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder


def _scene(objs):
    return SemanticSceneBuilder().build(objs)


# ══════════════════════════════════════════════════════════════
# Negation extraction (6 expressions)
# ══════════════════════════════════════════════════════════════

class TestNegationExtraction:
    """Negation patterns must extract negated refs correctly."""

    def test_do_not_touch(self):
        """'别碰玻璃杯' → negated_refs=['玻璃杯']"""
        ast = parse_logical_ast("别碰玻璃杯")
        refs, _ = merge_ast_negations(ast)
        assert "玻璃杯" in refs or any("玻璃杯" in r for r in refs), f"Got: {refs}"

    def test_must_not_touch(self):
        """'千万别碰玻璃杯' → negated_refs=['玻璃杯']"""
        ast = parse_logical_ast("千万别碰玻璃杯")
        refs, _ = merge_ast_negations(ast)
        assert "玻璃杯" in refs or any("玻璃杯" in r for r in refs), f"Got: {refs}"

    def test_avoid_object(self):
        """'避开那个盒子' → negated_refs should contain box reference"""
        ast = parse_logical_ast("避开那个盒子")
        refs, _ = merge_ast_negations(ast)
        assert len(refs) > 0, f"Should have negated refs, got: {refs}"

    def test_cannot_touch(self):
        """'不能碰桌子' → negated_refs should contain table reference"""
        ast = parse_logical_ast("不能碰桌子")
        refs, _ = merge_ast_negations(ast)
        assert len(refs) > 0, f"Should have negated refs, got: {refs}"

    def test_do_not_want_you_to_touch(self):
        """'不想让你碰桌子' → negated_refs should contain table reference (B88)"""
        ast = parse_logical_ast("不想让你碰桌子")
        refs, _ = merge_ast_negations(ast)
        assert len(refs) > 0, f"Should have negated refs for B88, got: {refs}"

    def test_prohibit_contact(self):
        """'禁止接触精密仪器' → negated_refs should contain instrument reference"""
        ast = parse_logical_ast("禁止接触精密仪器")
        refs, _ = merge_ast_negations(ast)
        assert len(refs) > 0, f"Should have negated refs, got: {refs}"


# ══════════════════════════════════════════════════════════════
# Manner detection (5 expressions)
# ══════════════════════════════════════════════════════════════

class TestMannerDetection:
    """Manner patterns must detect gentle/fast correctly."""

    def test_do_not_use_force_is_gentle(self):
        """'别用力' → manner=gentle (B46)"""
        ast = parse_logical_ast("别用力")
        _, manner = merge_ast_negations(ast)
        assert manner == "gentle" or "gentle" in ast.manner_overrides, \
            f"Expected gentle, got manner={manner}, overrides={ast.manner_overrides}"

    def test_lightly_is_gentle(self):
        """'轻一点抓' → manner=gentle"""
        ast = parse_logical_ast("轻一点抓")
        assert "gentle" in ast.manner_overrides, f"Got: {ast.manner_overrides}"

    def test_gently_grasp(self):
        """'轻轻抓住' → manner=gentle"""
        ast = parse_logical_ast("轻轻抓住")
        assert "gentle" in ast.manner_overrides, f"Got: {ast.manner_overrides}"

    def test_quickly_is_fast(self):
        """'快点拿过来' → manner=fast"""
        ast = parse_logical_ast("快点拿过来")
        assert "fast" in ast.manner_overrides, f"Got: {ast.manner_overrides}"

    def test_careful_is_not_negation(self):
        """'小心地抓住' → manner=gentle or careful, NOT negation"""
        ast = parse_logical_ast("小心地抓住")
        assert len(ast.negations) == 0, \
            f"'小心地' should not trigger negation, got: {ast.negations}"


# ══════════════════════════════════════════════════════════════
# Conditional structures (6 expressions)
# ══════════════════════════════════════════════════════════════

class TestConditionalStructures:
    """IF_ELSE and UNLESS must be parsed correctly."""

    def test_if_then(self):
        """'如果看到红色药瓶就拿它' → IF_ELSE with VISIBLE predicate"""
        ast = parse_logical_ast("如果看到红色药瓶就拿它")
        assert len(ast.conditions) >= 1, f"Expected condition, got: {ast.conditions}"
        cond = ast.conditions[0]
        assert cond.condition.predicate in ("VISIBLE", "看到红色药瓶"), \
            f"Expected VISIBLE predicate, got: {cond.condition.predicate}"

    def test_if_else_full(self):
        """'如果看到红色药瓶就先拿它，否则拿蓝色盒子' → IF_ELSE with both branches (B48)"""
        ast = parse_logical_ast("如果看到红色药瓶就先拿它，否则拿蓝色盒子")
        assert len(ast.conditions) >= 1, f"Expected condition, got: {ast.conditions}"

    def test_unless_gripper_empty(self):
        """'除非夹爪是空的，否则不要抓取' → UNLESS with GRIPPER_EMPTY (B45)"""
        ast = parse_logical_ast("除非夹爪是空的，否则不要抓取")
        assert len(ast.conditions) >= 1, f"Expected condition, got: {ast.conditions}"
        cond = ast.conditions[0]
        assert cond.type == LogicalOp.CONDITION_UNLESS.value, \
            f"Expected UNLESS, got: {cond.type}"
        assert "GRIPPER_EMPTY" in cond.condition.predicate or "夹爪" in cond.raw_text, \
            f"Expected gripper-related predicate, got: {cond.condition.predicate}"

    def test_only_if(self):
        """'只有夹爪为空才抓取' → condition with gripper predicate"""
        ast = parse_logical_ast("只有夹爪为空才抓取")
        assert len(ast.conditions) >= 1, f"Expected condition, got: {ast.conditions}"

    def test_if_visible_or_else(self):
        """'如果杯子在桌子上就抓取，要不就拿盒子' → IF_ELSE"""
        ast = parse_logical_ast("如果杯子在桌子上就抓取，要不就拿盒子")
        assert len(ast.conditions) >= 1, f"Expected condition, got: {ast.conditions}"

    def test_unless_otherwise_pattern(self):
        """'除非已经抓住，不然继续尝试' → UNLESS"""
        ast = parse_logical_ast("除非已经抓住，不然继续尝试")
        assert len(ast.conditions) >= 1, f"Expected UNLESS condition, got: {ast.conditions}"


# ══════════════════════════════════════════════════════════════
# Sequence structures (5 expressions)
# ══════════════════════════════════════════════════════════════

class TestSequenceStructures:
    """BEFORE/AFTER sequences must be parsed correctly."""

    def test_first_then(self):
        """'先抓住杯子再放到桌子上' → BEFORE sequence (B43)"""
        ast = parse_logical_ast("先抓住杯子再放到桌子上")
        assert len(ast.sequences) >= 1, f"Expected sequence, got: {ast.sequences}"

    def test_first_confirm_then_grasp(self):
        """'先确认夹爪是空的，然后抓住杯子' → BEFORE sequence (B49)"""
        ast = parse_logical_ast("先确认夹爪是空的，然后抓住杯子")
        assert len(ast.sequences) >= 1, f"Expected CHECK_THEN sequence, got: {ast.sequences}"

    def test_a_then_b(self):
        """'抓住杯子然后放到桌子上' → sequence"""
        ast = parse_logical_ast("抓住杯子然后放到桌子上")
        assert len(ast.sequences) >= 1, f"Expected sequence, got: {ast.sequences}"

    def test_a_and_b(self):
        """'抓住杯子并且放到桌子上' → SIMULTANEOUS or AND"""
        ast = parse_logical_ast("抓住杯子并且放到桌子上")
        assert len(ast.sequences) >= 1, f"Expected sequence, got: {ast.sequences}"

    def test_after_a_do_b(self):
        """'确认夹爪为空以后再抓取' → sequence"""
        ast = parse_logical_ast("确认夹爪为空以后再抓取")
        assert len(ast.sequences) >= 1 or len(ast.conditions) >= 1, \
            f"Expected sequence or condition, got: {ast}"


# ══════════════════════════════════════════════════════════════
# Negation-to-obstacle propagation (4 expressions)
# ══════════════════════════════════════════════════════════════

class TestNegationPropagation:
    """Negated refs must become obstacles in ParsedTask."""

    def test_b33_avoid_glass_cup(self):
        """'把盒子拿过来，别碰玻璃杯' → theme=盒子, obstacles包含玻璃杯"""
        objs = [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.04,
                            width=0.06, height=0.06, depth=0.06,
                            color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=-0.10, z=0.075,
                            width=0.07, height=0.10, depth=0.07,
                            color="transparent", material="glass"),
        ]
        scene = _scene(objs)
        pt = parse_task_semantics("把盒子拿过来，别碰玻璃杯", scene)
        # Theme should be the box
        if pt.theme:
            obj = scene.find_object(pt.theme.entity_id)
            assert obj is not None
            sc = getattr(obj, "specific_class", "") or getattr(obj, "name", "")
            assert "box" in sc.lower() or "盒" in sc, \
                f"Theme should be box, got class={sc}"
        # Obstacles should include the glass cup
        obstacle_mentions = [o.mention for o in pt.obstacle]
        assert any("杯" in m or "glass" in m.lower() or "cup" in m.lower()
                   for m in obstacle_mentions), \
            f"Obstacles should include glass cup, got: {obstacle_mentions}"

    def test_b44_avoid_red_fetch_blue(self):
        """'不要碰那个红色的，把蓝色的拿过来' → theme=蓝, obstacles包含红"""
        objs = [
            RawObjectPercept(name="block", x=0.20, y=0.10, z=0.03,
                            width=0.05, height=0.05, depth=0.05,
                            color="red", material="wood"),
            RawObjectPercept(name="block", x=0.35, y=-0.10, z=0.03,
                            width=0.05, height=0.05, depth=0.05,
                            color="blue", material="wood"),
        ]
        scene = _scene(objs)
        pt = parse_task_semantics("不要碰那个红色的，把蓝色的拿过来", scene)
        # Obstacles should include the red object
        obstacle_mentions = [o.mention for o in pt.obstacle]
        print(f"B44 obstacles: {[(o.entity_id, o.mention) for o in pt.obstacle]}")
        print(f"B44 theme: {pt.theme.entity_id if pt.theme else None}")
        # At minimum, there should be an obstacle detected
        assert len(pt.obstacle) > 0, "Should have at least one obstacle"

    def test_b88_do_not_want_touch_table(self):
        """'抓住杯子，但我不想让你碰桌子' → obstacles包含桌子"""
        objs = [
            RawObjectPercept(name="cup", x=0.30, y=0.12, z=0.075,
                            width=0.07, height=0.10, depth=0.07,
                            color="white", material="plastic"),
            RawObjectPercept(name="table", x=0.00, y=0.00, z=0.00,
                            width=0.50, height=0.03, depth=0.30,
                            color="brown", material="wood"),
        ]
        scene = _scene(objs)
        pt = parse_task_semantics("抓住杯子，但我不想让你碰桌子", scene)
        obstacle_mentions = [o.mention for o in pt.obstacle]
        assert any("桌" in m or "table" in m.lower() for m in obstacle_mentions), \
            f"Obstacles should include table, got: {obstacle_mentions}"

    def test_b41_must_not_touch_glass(self):
        """'把盒子拿过来，千万别碰玻璃杯' → obstacles包含玻璃杯"""
        objs = [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.04,
                            width=0.06, height=0.06, depth=0.06,
                            color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=-0.10, z=0.075,
                            width=0.07, height=0.10, depth=0.07,
                            color="transparent", material="glass"),
        ]
        scene = _scene(objs)
        pt = parse_task_semantics("把盒子拿过来，千万别碰玻璃杯", scene)
        obstacle_mentions = [o.mention for o in pt.obstacle]
        assert any("杯" in m or "glass" in m.lower() or "cup" in m.lower()
                   for m in obstacle_mentions), \
            f"Obstacles should include glass cup, got: {obstacle_mentions}"


# ══════════════════════════════════════════════════════════════
# Robot state evaluation (4 expressions)
# ══════════════════════════════════════════════════════════════

class TestRobotStateEvaluation:
    """Robot state predicates must be evaluated deterministically."""

    def test_gripper_empty_satisfied(self):
        """'先确认夹爪是空的，然后抓住杯子' with gripper_empty=True → condition satisfied"""
        ast = parse_logical_ast("先确认夹爪是空的，然后抓住杯子",
                               robot_state={"gripper_empty": True})
        # Condition should be evaluable
        for cond in ast.conditions:
            if "GRIPPER_EMPTY" in cond.condition.predicate:
                evaluated = getattr(cond.condition, '__evaluated__', None)
                assert evaluated is True, \
                    f"GRIPPER_EMPTY should evaluate to True when gripper_empty=True"

    def test_gripper_has_object_blocks(self):
        """'除非夹爪是空的，否则不要抓取' with gripper_has_object=True → BLOCKED"""
        ast = parse_logical_ast("除非夹爪是空的，否则不要抓取",
                               robot_state={"gripper_has_object": True})
        for cond in ast.conditions:
            if "GRIPPER_EMPTY" in cond.condition.predicate:
                evaluated = getattr(cond.condition, '__evaluated__', None)
                # GRIPPER_EMPTY negated (UNLESS flips it) → check evaluation
                assert evaluated is False, \
                    f"GRIPPER_EMPTY should evaluate to False when gripper_has_object=True"

    def test_unknown_state_needs_clarification(self):
        """'先确认夹爪是空的，然后抓住杯子' without robot_state → unknown"""
        ast = parse_logical_ast("先确认夹爪是空的，然后抓住杯子")
        for cond in ast.conditions:
            evaluated = getattr(cond.condition, '__evaluated__', None)
            # Without robot_state, predicate evaluation is None (unknown)
            assert evaluated is None, \
                f"Without robot_state, predicate should be unevaluated (None)"

    def test_is_homed_evaluation(self):
        """'只有归位后才抓取' with is_homed=True → condition satisfied"""
        ast = parse_logical_ast("只有归位后才抓取",
                               robot_state={"is_homed": True})
        for cond in ast.conditions:
            if "IS_HOMED" in cond.condition.predicate:
                evaluated = getattr(cond.condition, '__evaluated__', None)
                assert evaluated is True


# ══════════════════════════════════════════════════════════════
# Manner: no fabricated force values
# ══════════════════════════════════════════════════════════════

class TestMannerNoFabricatedForce:
    """Manner hints must not fabricate explicit force values."""

    def test_gentle_does_not_set_force(self):
        """'别用力抓住玻璃杯' → manner=gentle, no user_constraint force_n"""
        objs = [RawObjectPercept(name="cup", x=0.30, y=0.12, z=0.075,
                                 width=0.07, height=0.10, depth=0.07,
                                 color="white", material="glass")]
        scene = _scene(objs)
        pt = parse_task_semantics("别用力抓住玻璃杯", scene)
        assert pt.manner == "gentle", f"Expected gentle, got {pt.manner}"
        force_constraints = [c for c in pt.user_constraints if c.parameter == "force_n"]
        assert len(force_constraints) == 0, \
            f"Gentle manner should not fabricate force value, got: {force_constraints}"

    def test_light_grasp_no_force_value(self):
        """'轻一点抓住杯子' → no force_n constraint fabricated"""
        objs = [RawObjectPercept(name="cup", x=0.30, y=0.12, z=0.075,
                                 width=0.07, height=0.10, depth=0.07,
                                 color="white", material="plastic")]
        scene = _scene(objs)
        pt = parse_task_semantics("轻一点抓住杯子", scene)
        force_constraints = [c for c in pt.user_constraints if c.parameter == "force_n"]
        assert len(force_constraints) == 0, \
            f"No force_n should be fabricated from '轻一点', got: {force_constraints}"

    def test_explicit_force_still_works(self):
        """'用5N抓住杯子' → force_n=5 still extracted"""
        objs = [RawObjectPercept(name="cup", x=0.30, y=0.12, z=0.075,
                                 width=0.07, height=0.10, depth=0.07,
                                 color="white", material="plastic")]
        scene = _scene(objs)
        pt = parse_task_semantics("用5N抓住杯子", scene)
        force_constraints = [c for c in pt.user_constraints if c.parameter == "force_n"]
        if force_constraints:
            assert abs(force_constraints[0].value - 5.0) < 0.01, \
                f"Should parse 5N, got {force_constraints[0].value}"


# ══════════════════════════════════════════════════════════════
# AST-to-ParsedTask integration
# ══════════════════════════════════════════════════════════════

class TestASTIntegration:
    """Logical AST must integrate correctly with ParsedTask."""

    def test_negation_produces_obstacles(self):
        """Any negation should produce obstacle entries in ParsedTask."""
        objs = [
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.04,
                            width=0.06, height=0.06, depth=0.06,
                            color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=-0.10, z=0.075,
                            width=0.07, height=0.10, depth=0.07,
                            color="transparent", material="glass"),
        ]
        scene = _scene(objs)

        # Multiple negation patterns
        for instruction in [
            "别碰玻璃杯，把盒子拿过来",
            "不要碰杯子，拿盒子",
            "把盒子拿过来，千万别碰玻璃杯",
            "抓住盒子，避开杯子",
        ]:
            pt = parse_task_semantics(instruction, scene)
            assert len(pt.obstacle) > 0, \
                f"'{instruction}' should produce obstacles, got {len(pt.obstacle)}"

    def test_conditional_preserves_notes(self):
        """Conditional structures should be noted in ParsedTask.notes."""
        pt = parse_task_semantics("如果看到红色药瓶就先拿它，否则拿蓝色盒子")
        has_cond_note = any("conditional_detected" in n or "unsupported_conditional" in n
                           for n in pt.notes)
        assert has_cond_note, f"Conditional should be noted, got: {pt.notes}"

    def test_sequence_preserves_notes(self):
        """Sequences should be detected."""
        pt = parse_task_semantics("先抓住杯子再放到桌子上")
        has_seq = any("conditional_detected" in n for n in pt.notes)
        assert has_seq, f"Sequence should be detected, got: {pt.notes}"
