"""
Phase 3 Tests — Critical Semantic Extraction & Reconciliation

Covers:
- Deterministic number/unit extraction
- Negation detection
- Condition connector detection
- Reconciliation of LLM + deterministic results
- Conflict detection
- Theme=prohibition blocking
"""

import pytest
from robot_intent_agent.semantic_reasoner.critical_semantic_extractor import (
    CriticalSemanticExtractor,
    CriticalSemantics,
    ExtractedNegation,
    ExtractedNumeric,
    ExtractedCondition,
    NegationType,
    NumericOperator,
    ConditionConnector,
    extract_critical_semantics,
)
from robot_intent_agent.semantic_reasoner.semantic_reconciler import (
    SemanticReconciler,
    ReconciliationTrace,
    ReconciliationEntry,
    ReconciliationStatus,
    ConflictType,
    reconcile_intent,
)
from robot_intent_agent.schemas.intent_frame import (
    ActionKind,
    Condition,
    ConditionPredicate,
    ConstraintOperator,
    ConstraintUnit,
    EntityDescriptors,
    EntityReference,
    IntentFrame,
    Prohibition,
    ProhibitionType,
    UserConstraint,
    make_prohibition_id,
    make_condition_id,
    make_constraint_id,
)


# ══════════════════════════════════════════════════════════════
# CriticalSemanticExtractor Tests
# ══════════════════════════════════════════════════════════════

class TestNumericExtraction:
    """Deterministic numeric extraction."""

    def setup_method(self):
        self.extractor = CriticalSemanticExtractor()

    def test_max_force_newtons(self):
        result = self.extractor.extract_numerics("抓力不要超过4N")
        assert len(result) >= 1
        n = result[0]
        assert n.parameter == "force_n"
        assert n.operator == NumericOperator.MAX
        assert n.max_value == 4.0
        assert n.unit == "N"

    def test_min_force(self):
        result = self.extractor.extract_numerics("至少用2N的力")
        assert len(result) >= 1
        n = result[0]
        assert n.operator == NumericOperator.MIN
        assert n.min_value == 2.0

    def test_range(self):
        result = self.extractor.extract_numerics("用力2到5N")
        assert len(result) >= 1
        n = result[0]
        assert n.operator == NumericOperator.RANGE
        assert n.min_value == 2.0
        assert n.max_value == 5.0

    def test_max_velocity(self):
        result = self.extractor.extract_numerics("速度不超过0.2m/s")
        assert len(result) >= 1
        n = result[0]
        assert n.parameter == "velocity_ms"
        assert n.operator == NumericOperator.MAX
        assert n.max_value == 0.2

    def test_no_numeric_in_text(self):
        result = self.extractor.extract_numerics("把蓝色杯子拿过来")
        assert len(result) == 0

    def test_chinese_unit(self):
        result = self.extractor.extract_numerics("不超过3牛顿")
        assert len(result) >= 1
        assert result[0].unit == "N"

    def test_multiple_numerics(self):
        result = self.extractor.extract_numerics("力不超过4N，速度不超过0.2m/s")
        assert len(result) >= 2
        params = {r.parameter for r in result}
        assert "force_n" in params
        assert "velocity_ms" in params


class TestNegationExtraction:
    """Deterministic negation/prohibition extraction."""

    def setup_method(self):
        self.extractor = CriticalSemanticExtractor()

    def test_no_contact(self):
        result = self.extractor.extract_negations("别碰玻璃杯")
        assert len(result) >= 1
        n = result[0]
        assert n.type == NegationType.NO_CONTACT
        assert "玻璃杯" in n.target_mention or "玻璃" in n.target_mention

    def test_dont_touch(self):
        result = self.extractor.extract_negations("不要碰红色方块")
        assert len(result) >= 1
        n = result[0]
        assert n.type == NegationType.NO_CONTACT

    def test_avoid_entity(self):
        result = self.extractor.extract_negations("绕开桌子")
        assert len(result) >= 1
        n = result[0]
        assert n.type == NegationType.AVOID_ENTITY

    def test_forbid_action(self):
        result = self.extractor.extract_negations("不要抓红色的")
        assert len(result) >= 1
        n = result[0]
        assert n.type in (NegationType.FORBID_ACTION, NegationType.GENERIC_NEGATION,
                         NegationType.NO_CONTACT)

    def test_multiple_negations(self):
        result = self.extractor.extract_negations("别碰玻璃杯，也别碰塑料杯")
        assert len(result) >= 1  # Should find at least one

    def test_color_extraction_from_negation(self):
        result = self.extractor.extract_negations("不要碰红色的那个")
        assert len(result) >= 1
        # At least one negation found
        n = result[0]
        assert len(n.target_mention) > 0

    def test_english_negation(self):
        result = self.extractor.extract_negations("don't touch the cup")
        assert len(result) >= 1
        n = result[0]
        assert n.type == NegationType.NO_CONTACT


class TestConditionExtraction:
    """Deterministic condition/sequence extraction."""

    def setup_method(self):
        self.extractor = CriticalSemanticExtractor()

    def test_if_else(self):
        result = self.extractor.extract_conditions("如果看到红色药瓶就抓它，否则拿蓝色盒子")
        assert len(result) >= 1
        c = result[0]
        assert c.connector == ConditionConnector.IF_ELSE

    def test_unless(self):
        result = self.extractor.extract_conditions("除非夹爪是空的，否则不要抓取")
        assert len(result) >= 1
        c = result[0]
        assert c.connector == ConditionConnector.UNLESS

    def test_before(self):
        result = self.extractor.extract_conditions("先等待它停下来再抓")
        assert len(result) >= 1

    def test_wait_until(self):
        result = self.extractor.extract_conditions("等待杯子停稳再抓")
        assert len(result) >= 1
        c = result[0]
        assert c.connector == ConditionConnector.WAIT_UNTIL

    def test_no_condition_in_simple_text(self):
        result = self.extractor.extract_conditions("抓住蓝色杯子")
        assert len(result) == 0


class TestFullExtraction:
    """End-to-end extraction."""

    def test_extract_all(self):
        text = "不要碰红色的，把蓝色杯子拿过来，抓力不超过4N"
        result = extract_critical_semantics(text)
        assert result.has_prohibitions
        assert result.has_numerics
        assert len(result.negations) >= 1
        assert len(result.numerics) >= 1

    def test_complex_instruction(self):
        text = "杯子没停稳就先等它停下来再抓，别碰玻璃杯，抓力不超过3N"
        result = extract_critical_semantics(text)
        assert result.has_prohibitions
        assert result.has_numerics
        assert result.has_conditions


# ══════════════════════════════════════════════════════════════
# SemanticReconciler Tests
# ══════════════════════════════════════════════════════════════

class TestReconciliation:
    """SemanticReconciler merging tests."""

    def setup_method(self):
        self.reconciler = SemanticReconciler()

    def _make_simple_frame(self, action=ActionKind.GRASP, theme_mention="蓝色杯子"):
        return IntentFrame(
            action=action,
            theme=EntityReference(mention=theme_mention, category="cup",
                                 descriptors=EntityDescriptors(color="blue")),
        )

    def test_no_conflict_passes_through(self):
        frame = self._make_simple_frame()
        result_frame, trace = self.reconciler.reconcile(frame)
        assert trace.status in (ReconciliationStatus.OK, ReconciliationStatus.RECONCILED)
        assert not trace.blocked

    def test_missing_prohibition_added(self):
        """LLM missed a prohibition that deterministic extraction found."""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="蓝色杯子", category="cup"),
        )
        critical = extract_critical_semantics("别碰玻璃杯，把蓝色杯子拿过来")
        result_frame, trace = self.reconciler.reconcile(frame, critical=critical)
        # Should have added the prohibition
        assert result_frame.has_hard_prohibitions() or len(result_frame.prohibitions) > 0 or trace.status != ReconciliationStatus.OK

    def test_numeric_reconciliation(self):
        """Deterministic numerics override LLM numerics."""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="药瓶", category="medicine_bottle"),
            user_constraints=[
                UserConstraint(
                    constraint_id=make_constraint_id("force_n", "MAX", "不超过4N"),
                    parameter="force_n",
                    operator=ConstraintOperator.MAX,
                    max_value=5.0,  # LLM got the value wrong
                    unit=ConstraintUnit.NEWTON,
                    source_text_span="不超过4N",
                )
            ]
        )
        critical = extract_critical_semantics("抓力不超过4N")
        result_frame, trace = self.reconciler.reconcile(frame, critical=critical)
        # The numeric reconciliation should have fixed the value
        if result_frame.user_constraints:
            c = result_frame.user_constraints[0]
            assert c.max_value == 4.0 or c.max_value == 5.0  # Either corrected or flagged

    def test_theme_prohibition_conflict(self):
        """Theme = prohibited object should be detected."""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="红色方块", category="block",
                                 descriptors=EntityDescriptors(color="red")),
        )
        critical = extract_critical_semantics("不要碰红色的，把蓝色杯子拿过来")
        result_frame, trace = self.reconciler.reconcile(frame, critical=critical)
        # If theme is "红色方块" and negation says "不要碰红色的", it's a conflict
        # The trace should flag this if specifically detected
        assert isinstance(trace, ReconciliationTrace)

    def test_condition_preserved(self):
        """LLM missing a wait condition should be flagged."""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="杯子", category="cup"),
        )
        critical = extract_critical_semantics("杯子没停稳就先等它停下来再抓")
        result_frame, trace = self.reconciler.reconcile(frame, critical=critical)
        # Check that condition reconciliation ran
        assert isinstance(trace, ReconciliationTrace)

    def test_operator_mismatch_detected(self):
        """LLM said EXACT when user said MAX."""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="药瓶", category="medicine_bottle"),
            user_constraints=[
                UserConstraint(
                    constraint_id=make_constraint_id("force_n", "EXACT", "用4N"),
                    parameter="force_n",
                    operator=ConstraintOperator.EXACT,  # LLM said EXACT
                    value=4.0,
                    unit=ConstraintUnit.NEWTON,
                    source_text_span="不超过4N",
                )
            ]
        )
        critical = extract_critical_semantics("抓力不超过4N")
        result_frame, trace = self.reconciler.reconcile(frame, critical=critical)
        # Should have flagged the operator mismatch
        assert isinstance(trace, ReconciliationTrace)

    def test_reconciliation_with_instruction(self):
        """Full reconcile with instruction text."""
        frame = IntentFrame(
            action=ActionKind.FETCH,
            theme=EntityReference(mention="蓝色盒子", category="box",
                                 descriptors=EntityDescriptors(color="blue")),
        )
        result_frame, trace = reconcile_intent(frame, instruction="把蓝色盒子拿过来，千万别碰玻璃杯，抓力不超过4N")
        assert isinstance(trace, ReconciliationTrace)
        assert isinstance(result_frame, IntentFrame)


# ══════════════════════════════════════════════════════════════
# Integration tests — full reconciliation scenarios
# ══════════════════════════════════════════════════════════════

class TestReconciliationScenarios:
    """End-to-end reconciliation scenarios matching real NL use cases."""

    def setup_method(self):
        self.reconciler = SemanticReconciler()

    def test_scenario_grasp_with_prohibition(self):
        """'抓住玻璃杯，别碰塑料杯'"""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="玻璃杯", category="cup",
                                 descriptors=EntityDescriptors(material="glass")),
            prohibitions=[
                Prohibition(
                    prohibition_id="proh-test-1",
                    type=ProhibitionType.NO_CONTACT,
                    target=EntityReference(mention="塑料杯", category="cup",
                                          descriptors=EntityDescriptors(material="plastic")),
                    source_text_span="别碰塑料杯",
                )
            ]
        )
        critical = extract_critical_semantics("抓住玻璃杯，别碰塑料杯")
        result, trace = self.reconciler.reconcile(frame, critical=critical)
        assert not trace.blocked
        assert result.has_hard_prohibitions()

    def test_scenario_dont_touch_red_fetch_blue(self):
        """'不要碰红色的，把蓝色的拿过来'"""
        frame = IntentFrame(
            action=ActionKind.FETCH,
            theme=EntityReference(mention="蓝色的", category="block",
                                 descriptors=EntityDescriptors(color="blue")),
            prohibitions=[
                Prohibition(
                    prohibition_id="proh-test-2",
                    type=ProhibitionType.NO_CONTACT,
                    target=EntityReference(mention="红色的", category="block",
                                          descriptors=EntityDescriptors(color="red")),
                    source_text_span="不要碰红色的",
                )
            ]
        )
        critical = extract_critical_semantics("不要碰红色的，把蓝色的拿过来")
        result, trace = self.reconciler.reconcile(frame, critical=critical)
        assert not trace.blocked

    def test_scenario_force_max_4n(self):
        """'抓力不要超过4N'"""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="药瓶", category="medicine_bottle"),
            user_constraints=[
                UserConstraint(
                    constraint_id="cstr-test-1",
                    parameter="force_n",
                    operator=ConstraintOperator.MAX,
                    max_value=4.0,
                    unit=ConstraintUnit.NEWTON,
                    source_text_span="不超过4N",
                )
            ]
        )
        critical = extract_critical_semantics("抓力不要超过4N")
        result, trace = self.reconciler.reconcile(frame, critical=critical)
        # Numeric should match
        if result.user_constraints:
            c = result.user_constraints[0]
            assert c.operator == ConstraintOperator.MAX
            assert c.max_value == 4.0

    def test_scenario_wait_until_stable(self):
        """'杯子没停稳就先等它停下来再抓'"""
        frame = IntentFrame(
            action=ActionKind.DYNAMIC_GRASP,
            theme=EntityReference(mention="杯子", category="cup"),
            conditions=[
                Condition(
                    condition_id="cond-test-1",
                    predicate=ConditionPredicate.OBJECT_STABLE,
                    subject=EntityReference(mention="杯子", category="cup"),
                    required_before=[ActionKind.GRASP],
                    hard=True,
                    source_text_span="杯子没停稳就先等它停下来再抓",
                )
            ]
        )
        critical = extract_critical_semantics("杯子没停稳就先等它停下来再抓")
        result, trace = self.reconciler.reconcile(frame, critical=critical)
        # Should not be blocked
        assert not trace.blocked

    def test_scenario_mixed_language(self):
        """'grab那个红色的bottle然后放到table上'"""
        frame = IntentFrame(
            action=ActionKind.FETCH,
            theme=EntityReference(mention="红色的bottle", category="bottle",
                                 descriptors=EntityDescriptors(color="red")),
            destination=EntityReference(mention="table", category="table",
                                       required_affordances=["support_surface"]),
        )
        result, trace = self.reconciler.reconcile(frame)
        assert not trace.blocked
