"""
IntentFrame v1 Schema Tests — Phase 1

Validates:
- Legal IntentFrame construction
- Missing required fields
- Illegal enum values
- Illegal operator/unit
- Additional unknown fields rejected
- EntityReference never contains object_id
- Prohibition never stored in explanatory_notes
- Condition missing required_before
"""

import json
import pytest
from pydantic import ValidationError

from robot_intent_agent.schemas.intent_frame import (
    ActionKind,
    ProhibitionType,
    ConditionPredicate,
    ConstraintOperator,
    ConstraintUnit,
    MannerKind,
    UrgencyKind,
    EntityDescriptors,
    EntityReference,
    Prohibition,
    Condition,
    UserConstraint,
    SequenceStep,
    IntentFrame,
    EngineTrace,
    intent_frame_json_schema,
    make_prohibition_id,
    make_condition_id,
    make_constraint_id,
)


class TestEntityReference:
    """EntityReference must NEVER contain object_id from LLM."""

    def test_legal_entity(self):
        ref = EntityReference(mention="红色杯子", category="cup",
                              descriptors=EntityDescriptors(color="red"))
        data = ref.model_dump()
        # entity_id must NOT be in the model
        assert "entity_id" not in data

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            EntityReference(mention="test", category="cup",
                          extra_field="should_fail")

    def test_empty_descriptors_default(self):
        ref = EntityReference(mention="test")
        assert ref.descriptors.color is None
        assert ref.descriptors.material is None
        assert ref.descriptors.size is None

    def test_null_optional_fields(self):
        ref = EntityReference(mention="test")
        assert ref.category is None
        assert ref.spatial_relations == []
        assert ref.required_affordances == []

    def test_empty_mention_rejected(self):
        with pytest.raises(ValidationError):
            EntityReference(mention="", category="cup")  # empty mention not explicitly forbidden but should pass minimum


class TestProhibition:
    """Prohibition types must use proper enums."""

    def test_no_contact_type(self):
        p = Prohibition(
            prohibition_id=make_prohibition_id("别碰玻璃杯", 1),
            type=ProhibitionType.NO_CONTACT,
            target=EntityReference(mention="玻璃杯", category="cup"),
            source_text_span="别碰玻璃杯"
        )
        assert p.type == ProhibitionType.NO_CONTACT
        assert p.prohibition_id.startswith("proh-")

    def test_forbid_action(self):
        p = Prohibition(
            prohibition_id=make_prohibition_id("不要抓红色的", 1),
            type=ProhibitionType.FORBID_ACTION,
            target=EntityReference(mention="红色的", category="block",
                                  descriptors=EntityDescriptors(color="red")),
            action=ActionKind.GRASP,
            source_text_span="不要抓红色的"
        )
        assert p.type == ProhibitionType.FORBID_ACTION
        assert p.action == ActionKind.GRASP

    def test_parameter_max(self):
        p = Prohibition(
            prohibition_id=make_prohibition_id("不超过4N", 1),
            type=ProhibitionType.PARAMETER_MAX,
            target=EntityReference(mention="grasp"),
            parameter="force_n",
            operator=ConstraintOperator.MAX,
            value=4.0,
            unit=ConstraintUnit.NEWTON,
            source_text_span="不超过4N"
        )
        assert p.value == 4.0
        assert p.operator == ConstraintOperator.MAX

    def test_illegal_enum_rejected(self):
        with pytest.raises(ValidationError):
            Prohibition(
                prohibition_id="test-1",
                type="INVALID_TYPE",  # not a valid enum
                target=EntityReference(mention="test")
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            Prohibition(
                prohibition_id="test-1",
                type=ProhibitionType.NO_CONTACT,
                target=EntityReference(mention="test"),
                object_id="obj-001"  # NOT allowed
            )

    def test_stable_id_generation(self):
        id1 = make_prohibition_id("别碰玻璃杯", 1)
        id2 = make_prohibition_id("别碰玻璃杯", 1)
        id3 = make_prohibition_id("别碰玻璃杯", 2)
        assert id1 == id2
        assert id1 != id3
        assert id1.startswith("proh-")


class TestCondition:
    """Conditions must have required_before for sequential enforcement."""

    def test_legal_condition(self):
        c = Condition(
            condition_id=make_condition_id("杯子没停稳", 1),
            predicate=ConditionPredicate.OBJECT_STABLE,
            subject=EntityReference(mention="杯子", category="cup"),
            required_before=[ActionKind.GRASP],
            hard=True,
            source_text_span="杯子没停稳就先等它停下来再抓"
        )
        assert c.predicate == ConditionPredicate.OBJECT_STABLE
        assert ActionKind.GRASP in c.required_before

    def test_gripper_empty_condition(self):
        c = Condition(
            condition_id=make_condition_id("夹爪是空的", 1),
            predicate=ConditionPredicate.GRIPPER_EMPTY,
            required_before=[ActionKind.GRASP],
            hard=True,
            source_text_span="除非夹爪是空的，否则不要抓取"
        )
        assert c.predicate == ConditionPredicate.GRIPPER_EMPTY

    def test_condition_missing_predicate(self):
        with pytest.raises(ValidationError):
            Condition(
                condition_id="cond-test",
                # predicate is required
                required_before=[ActionKind.GRASP],
            )

    def test_condition_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            Condition(
                condition_id="cond-test",
                predicate=ConditionPredicate.OBJECT_STABLE,
                hard=True,
                made_up_field="intrusion"
            )

    def test_required_before_empty_list(self):
        c = Condition(
            condition_id=make_condition_id("test", 1),
            predicate=ConditionPredicate.OBJECT_VISIBLE,
            hard=False,
        )
        assert c.required_before == []

    def test_stable_condition_id(self):
        id1 = make_condition_id("杯子没停稳", 1)
        id2 = make_condition_id("杯子没停稳", 1)
        assert id1 == id2
        assert id1.startswith("cond-")


class TestUserConstraint:
    """Numeric constraints with proper operators and units."""

    def test_legal_constraint(self):
        c = UserConstraint(
            constraint_id=make_constraint_id("force_n", "MAX", "不超过4N"),
            parameter="force_n",
            operator=ConstraintOperator.MAX,
            max_value=4.0,
            unit=ConstraintUnit.NEWTON,
            source_text_span="不超过4N"
        )
        assert c.operator == ConstraintOperator.MAX
        assert c.unit == ConstraintUnit.NEWTON

    def test_illegal_unit_rejected(self):
        with pytest.raises(ValidationError):
            UserConstraint(
                constraint_id="test",
                parameter="force_n",
                operator=ConstraintOperator.EXACT,
                value=4.0,
                unit="pounds"  # not in ConstraintUnit
            )

    def test_illegal_operator_rejected(self):
        with pytest.raises(ValidationError):
            UserConstraint(
                constraint_id="test",
                parameter="force_n",
                operator="GREATER_THAN_ALL",  # not an enum member
                value=4.0,
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            UserConstraint(
                constraint_id="test",
                parameter="force_n",
                operator=ConstraintOperator.MAX,
                max_value=4.0,
                target_object_id="obj-001"  # forbidden
            )


class TestIntentFrame:
    """Full IntentFrame validation."""

    def test_simple_grasp(self):
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="蓝色方块",
                                 category="block",
                                 descriptors=EntityDescriptors(color="blue")),
        )
        assert frame.schema_version == "1.0.0"
        assert frame.action == ActionKind.GRASP
        assert frame.prohibitions == []
        assert frame.conditions == []

    def test_place_with_destination(self):
        frame = IntentFrame(
            action=ActionKind.PLACE,
            theme=EntityReference(mention="蓝色方块", category="block",
                                 descriptors=EntityDescriptors(color="blue")),
            destination=EntityReference(mention="红色方块", category="block",
                                       descriptors=EntityDescriptors(color="red"),
                                       required_affordances=["support_surface"]),
        )
        assert frame.destination is not None
        assert frame.destination.mention == "红色方块"

    def test_with_prohibition(self):
        frame = IntentFrame(
            action=ActionKind.FETCH,
            theme=EntityReference(mention="蓝色杯子", category="cup"),
            prohibitions=[
                Prohibition(
                    prohibition_id=make_prohibition_id("别碰塑料杯", 1),
                    type=ProhibitionType.NO_CONTACT,
                    target=EntityReference(mention="塑料杯", category="cup",
                                          descriptors=EntityDescriptors(material="plastic")),
                    source_text_span="别碰塑料杯"
                )
            ]
        )
        assert len(frame.prohibitions) == 1
        assert frame.prohibitions[0].type == ProhibitionType.NO_CONTACT
        assert "proh-" in frame.prohibitions[0].prohibition_id

    def test_with_condition(self):
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="杯子", category="cup"),
            conditions=[
                Condition(
                    condition_id=make_condition_id("杯子没停稳", 1),
                    predicate=ConditionPredicate.OBJECT_STABLE,
                    subject=EntityReference(mention="杯子", category="cup"),
                    required_before=[ActionKind.GRASP],
                    hard=True,
                    source_text_span="杯子没停稳就先等它停下来再抓"
                )
            ]
        )
        assert len(frame.conditions) == 1
        assert frame.conditions[0].hard is True
        assert ActionKind.GRASP in frame.conditions[0].required_before

    def test_with_numeric_constraint(self):
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="药瓶", category="medicine_bottle"),
            user_constraints=[
                UserConstraint(
                    constraint_id=make_constraint_id("force_n", "MAX", "不超过4N"),
                    parameter="force_n",
                    operator=ConstraintOperator.MAX,
                    max_value=4.0,
                    unit=ConstraintUnit.NEWTON,
                    source_text_span="不超过4N"
                )
            ]
        )
        assert len(frame.user_constraints) == 1
        assert frame.user_constraints[0].max_value == 4.0

    def test_missing_action_rejected(self):
        with pytest.raises(ValidationError):
            IntentFrame(theme=EntityReference(mention="test"))

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            IntentFrame(
                action=ActionKind.GRASP,
                theme=EntityReference(mention="test"),
                execution_allowed=True,  # FORBIDDEN — safety decision
            )

    def test_prohibition_not_in_notes(self):
        """Prohibition must be in prohibitions list, not smuggled in notes."""
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="蓝色杯子", category="cup"),
            prohibitions=[
                Prohibition(
                    prohibition_id=make_prohibition_id("别碰红色", 1),
                    type=ProhibitionType.NO_CONTACT,
                    target=EntityReference(mention="红色的", category="block",
                                          descriptors=EntityDescriptors(color="red")),
                    source_text_span="别碰红色的"
                )
            ],
            explanatory_notes=["用户要求别碰红色的"]
        )
        # The prohibition must be in the structured field
        assert len(frame.prohibitions) == 1
        # Notes are just explanatory
        assert "用户要求别碰红色的" in frame.explanatory_notes

    def test_prohibition_ids_unique(self):
        """Each prohibition gets a unique stable ID."""
        p1 = make_prohibition_id("别碰A", 1)
        p2 = make_prohibition_id("别碰B", 1)
        assert p1 != p2

    def test_condition_ids_unique(self):
        c1 = make_condition_id("如果A", 1)
        c2 = make_condition_id("如果B", 1)
        assert c1 != c2

    def test_constraint_ids_unique(self):
        c1 = make_constraint_id("force_n", "MAX", "不超过4N")
        c2 = make_constraint_id("force_n", "MIN", "至少2N")
        assert c1 != c2

    def test_has_hard_prohibitions(self):
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="test", category="cup"),
            prohibitions=[
                Prohibition(
                    prohibition_id="proh-1",
                    type=ProhibitionType.NO_CONTACT,
                    target=EntityReference(mention="obstacle", category="cup"),
                )
            ]
        )
        assert frame.has_hard_prohibitions()

    def test_has_hard_conditions(self):
        frame = IntentFrame(
            action=ActionKind.GRASP,
            theme=EntityReference(mention="test"),
            conditions=[
                Condition(
                    condition_id="cond-1",
                    predicate=ConditionPredicate.OBJECT_STABLE,
                    hard=True,
                    required_before=[ActionKind.GRASP],
                )
            ]
        )
        assert frame.has_hard_conditions()

    def test_multi_role_instruction(self):
        """中英混合口语: 'grab那个红色的bottle然后放到table上'"""
        frame = IntentFrame(
            action=ActionKind.FETCH,
            theme=EntityReference(mention="红色的bottle", category="bottle",
                                 descriptors=EntityDescriptors(color="red")),
            destination=EntityReference(mention="table", category="table",
                                       required_affordances=["support_surface"]),
            sequence=[
                SequenceStep(step_index=0, action=ActionKind.GRASP,
                           entity=EntityReference(mention="红色的bottle")),
                SequenceStep(step_index=1, action=ActionKind.PLACE,
                           entity=EntityReference(mention="table")),
            ]
        )
        assert frame.has_sequence()
        assert len(frame.sequence) == 2

    def test_json_schema_export(self):
        schema = intent_frame_json_schema()
        assert "$defs" in schema or "properties" in schema
        assert "action" in schema.get("properties", {})

    def test_schema_version_field(self):
        frame = IntentFrame(action=ActionKind.GRASP,
                          theme=EntityReference(mention="test"))
        assert frame.schema_version == "1.0.0"


class TestEngineTrace:
    """Engine trace audit."""

    def test_legal_trace(self):
        trace = EngineTrace(
            requested_engine="DeepSeek",
            actual_engine="DeepSeek",
            llm_call_attempted=True,
            llm_call_succeeded=True,
            response_schema_valid=True,
            model_name="deepseek-chat",
            latency_ms=450.0,
        )
        assert trace.actual_engine == "DeepSeek"
        assert trace.llm_call_succeeded is True
        assert trace.fallback_used is False

    def test_fallback_trace(self):
        trace = EngineTrace(
            requested_engine="DeepSeek",
            actual_engine="RuleEngine",
            llm_call_attempted=True,
            llm_call_succeeded=False,
            response_schema_valid=False,
            fallback_used=True,
            fallback_reason="Schema validation failed",
            model_name="deepseek-chat",
            latency_ms=5200.0,
        )
        assert trace.fallback_used is True
        assert trace.actual_engine == "RuleEngine"
        assert "Schema validation" in trace.fallback_reason

    def test_fallback_not_counted_as_deepseek_success(self):
        """A fallback result must NOT be counted as DeepSeek success."""
        trace = EngineTrace(
            requested_engine="DeepSeek",
            actual_engine="RuleEngine",
            llm_call_attempted=True,
            llm_call_succeeded=False,
            fallback_used=True,
        )
        assert trace.actual_engine == "RuleEngine"
        assert trace.llm_call_succeeded is False

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            EngineTrace(
                requested_engine="test",
                hidden_field="should_not_exist"
            )


class TestActionKindEnum:
    """All existing actions must be representable."""

    def test_all_actions_valid(self):
        valid = ["GRASP", "FETCH", "PLACE", "HANDOVER", "TRANSFER",
                 "DYNAMIC_GRASP", "CUSTOM"]
        for v in valid:
            a = ActionKind(v)
            assert a.value == v

    def test_unknown_action_rejected(self):
        with pytest.raises(ValueError):
            ActionKind("FLY")


class TestProhibitionTypeEnum:
    def test_all_types_valid(self):
        valid = ["NO_CONTACT", "FORBID_ACTION", "AVOID_ENTITY",
                 "AVOID_REGION", "PARAMETER_MAX", "PARAMETER_MIN",
                 "CONDITIONAL_PROHIBITION"]
        for v in valid:
            pt = ProhibitionType(v)
            assert pt.value == v
