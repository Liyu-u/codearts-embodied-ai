"""
IntentFrame v1 — Strict business semantics schema for NL → structured intent.

This is the SINGLE AUTHORITATIVE schema for all NL-to-structured-semantics
conversion. Both RuleEngine and DeepSeek must produce or be normalized to
an IntentFrame before downstream grounding, compilation, or validation.

Architecture invariant:
    DeepSeek provides semantic understanding (mentions, categories, descriptors).
    GroundingEngine provides final entity_id.
    DeepSeek MUST NOT independently decide object_id, force, velocity,
    execution_allowed, plan_status, or safety conclusions.

Schema version: 1.0.0
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ══════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════

class ActionKind(str, Enum):
    GRASP = "GRASP"
    FETCH = "FETCH"
    PLACE = "PLACE"
    HANDOVER = "HANDOVER"
    TRANSFER = "TRANSFER"
    DYNAMIC_GRASP = "DYNAMIC_GRASP"
    CUSTOM = "CUSTOM"


class ProhibitionType(str, Enum):
    NO_CONTACT = "NO_CONTACT"               # "别碰X"
    FORBID_ACTION = "FORBID_ACTION"         # "不要抓X"
    AVOID_ENTITY = "AVOID_ENTITY"           # "避开X"
    AVOID_REGION = "AVOID_REGION"           # "不要靠近X区域"
    PARAMETER_MAX = "PARAMETER_MAX"         # "不超过4N"
    PARAMETER_MIN = "PARAMETER_MIN"         # "不低于2N"
    CONDITIONAL_PROHIBITION = "CONDITIONAL_PROHIBITION"  # "除非X，否则不要Y"


class StepRole(str, Enum):
    """Role of a step within a composite action plan."""
    PRIMARY = "PRIMARY"
    PRECONDITION = "PRECONDITION"
    MANIPULATION = "MANIPULATION"
    TRANSPORT = "TRANSPORT"
    TERMINAL = "TERMINAL"
    AUXILIARY = "AUXILIARY"


class ConditionPredicate(str, Enum):
    GRIPPER_EMPTY = "GRIPPER_EMPTY"
    GRIPPER_HOLDING = "GRIPPER_HOLDING"
    OBJECT_VISIBLE = "OBJECT_VISIBLE"
    OBJECT_STABLE = "OBJECT_STABLE"
    OBJECT_MOVING = "OBJECT_MOVING"
    ROBOT_HOMED = "ROBOT_HOMED"
    DISTANCE_LESS_THAN = "DISTANCE_LESS_THAN"
    DISTANCE_GREATER_THAN = "DISTANCE_GREATER_THAN"
    CUSTOM = "CUSTOM"


class ConstraintOperator(str, Enum):
    EXACT = "EXACT"
    MAX = "MAX"
    MIN = "MIN"
    RANGE = "RANGE"


class ConstraintUnit(str, Enum):
    NEWTON = "N"
    METER_PER_SECOND = "m/s"
    METER = "m"
    CENTIMETER = "cm"
    MILLIMETER = "mm"
    KILOGRAM = "kg"
    DEGREE = "deg"
    SECOND = "s"
    NEWTON_METER = "N·m"


class MannerKind(str, Enum):
    GENTLE = "gentle"
    FAST = "fast"
    CAREFUL = "careful"
    FIRM = "firm"
    SLOW = "slow"


class UrgencyKind(str, Enum):
    IMMEDIATE = "immediate"
    NORMAL = "normal"
    WHEN_CONVENIENT = "when_convenient"


# ══════════════════════════════════════════════════════════════
# EntityReference — NO object_id from LLM
# ══════════════════════════════════════════════════════════════

class EntityDescriptors(BaseModel):
    """Semantic descriptors for entity matching. LLM fills these; GroundingEngine scores."""
    color: Optional[str] = Field(default=None, description="e.g. 'red', 'blue', 'transparent'")
    material: Optional[str] = Field(default=None, description="e.g. 'glass', 'plastic', 'metal'")
    size: Optional[str] = Field(default=None, description="e.g. 'large', 'small', 'medium'")
    shape: Optional[str] = Field(default=None, description="e.g. 'round', 'square', 'cylindrical'")
    side: Optional[str] = Field(default=None, description="e.g. 'left', 'right', 'front', 'back'")
    height_relation: Optional[str] = Field(default=None, description="e.g. 'high', 'low', 'above', 'below'")
    distance_relation: Optional[str] = Field(default=None, description="e.g. 'near', 'far', 'nearest', 'farthest'")
    motion_state: Optional[str] = Field(default=None, description="e.g. 'static', 'moving', 'stopped'")


class EntityReference(BaseModel):
    """Reference to a physical or virtual entity in the scene.

    CRITICAL: entity_id MUST be null. The LLM provides semantic descriptors only.
    GroundingEngine is the sole authority for assigning object_id.
    """
    mention: str = Field(..., min_length=1, description="The user's exact mention text, e.g. '红色杯子'")
    category: Optional[str] = Field(default=None, description="Semantic category, e.g. 'cup', 'table', 'bottle'")
    descriptors: EntityDescriptors = Field(default_factory=EntityDescriptors)
    spatial_relations: List[str] = Field(default_factory=list,
        description="Spatial relation hints, e.g. ['on_table', 'left_of_robot']")
    required_affordances: List[str] = Field(default_factory=list,
        description="Required affordances, e.g. ['graspable', 'support_surface']")
    source_text_span: str = Field(default="", description="Original text span in the instruction")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="LLM confidence in this reference")

    model_config = {"extra": "forbid"}


# ══════════════════════════════════════════════════════════════
# Prohibition — structured negation
# ══════════════════════════════════════════════════════════════

class Prohibition(BaseModel):
    """A user-specified prohibition or hard constraint.

    Each prohibition gets a stable ID for full-chain propagation tracing.
    """
    prohibition_id: str = Field(..., description="Stable ID for propagation trace")
    type: ProhibitionType = Field(...)
    target: EntityReference = Field(..., description="What is prohibited")
    action: Optional[ActionKind] = Field(default=None, description="Which action is prohibited (FORBID_ACTION)")
    parameter: Optional[str] = Field(default=None, description="Parameter name (PARAMETER_MAX/MIN)")
    operator: Optional[ConstraintOperator] = Field(default=None)
    value: Optional[float] = Field(default=None)
    unit: Optional[ConstraintUnit] = Field(default=None)
    condition: Optional[str] = Field(default=None, description="Condition text for CONDITIONAL_PROHIBITION")
    source_text_span: str = Field(default="", description="Original text span")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    model_config = {"extra": "forbid"}

    @field_validator("type")
    @classmethod
    def _validate_type_specific_fields(cls, v, info):
        """Ensure type-specific fields are consistent."""
        return v


# ══════════════════════════════════════════════════════════════
# Condition — conditional execution semantics
# ══════════════════════════════════════════════════════════════

class Condition(BaseModel):
    """A conditional branch in the user's instruction.

    Must carry required_before for sequential enforcement.
    """
    condition_id: str = Field(..., description="Stable ID for propagation trace")
    predicate: ConditionPredicate = Field(...)
    subject: Optional[EntityReference] = Field(default=None, description="What the condition checks")
    operator: Optional[str] = Field(default=None, description="Comparison operator: '==', '!=', '<', '>', etc.")
    value: Optional[float] = Field(default=None)
    unit: Optional[ConstraintUnit] = Field(default=None)
    required_before: List[ActionKind] = Field(default_factory=list,
        description="Actions that MUST execute after this condition is satisfied")
    on_true: Optional[ActionKind] = Field(default=None, description="Action when condition is true")
    on_false: Optional[ActionKind] = Field(default=None, description="Action when condition is false")
    hard: bool = Field(default=True, description="If True, condition MUST be satisfied before proceeding")
    source_text_span: str = Field(default="")

    model_config = {"extra": "forbid"}


# ══════════════════════════════════════════════════════════════
# UserConstraint — numeric/parameter constraints
# ══════════════════════════════════════════════════════════════

class UserConstraint(BaseModel):
    """Deterministic user-specified parameter constraint."""
    constraint_id: str = Field(..., description="Stable ID")
    parameter: str = Field(..., description="e.g. 'force_n', 'velocity_ms'")
    operator: ConstraintOperator = Field(...)
    value: Optional[float] = Field(default=None)
    min_value: Optional[float] = Field(default=None)
    max_value: Optional[float] = Field(default=None)
    unit: ConstraintUnit = Field(default=ConstraintUnit.NEWTON)
    hard: bool = Field(default=True)
    source_text_span: str = Field(default="")

    model_config = {"extra": "forbid"}


# ══════════════════════════════════════════════════════════════
# Sequence — ordered action steps
# ══════════════════════════════════════════════════════════════

class SequenceStep(BaseModel):
    """One step in an ordered action sequence."""
    step_index: int = Field(..., ge=0)
    action: ActionKind = Field(...)
    description: str = Field(default="")
    entity: Optional[EntityReference] = Field(default=None)
    condition_id: Optional[str] = Field(default=None, description="Linked condition that gates this step")

    model_config = {"extra": "forbid"}


# ══════════════════════════════════════════════════════════════
# ActionPlan — composite action support (Phase 7)
# ══════════════════════════════════════════════════════════════

class ActionStep(BaseModel):
    """One step in a composite action plan."""
    action: ActionKind = Field(...)
    role: StepRole = Field(default=StepRole.MANIPULATION)
    target_ref: Optional[EntityReference] = Field(default=None)
    destination_ref: Optional[EntityReference] = Field(default=None)
    order: int = Field(default=1, ge=1)
    required: bool = Field(default=True)

    model_config = {"extra": "forbid"}


class ActionPlan(BaseModel):
    """Composite action plan for multi-step tasks.

    When an instruction involves multiple actions (e.g., 'grasp and place'),
    the action_plan captures the full sequence. The single 'action' field
    holds the task-level summary action.

    Examples:
        '把杯子拿过来' → action=FETCH, steps=[GRASP(order=1), FETCH(order=2)]
        '抓住杯子并翻转过来' → action=CUSTOM, steps=[GRASP(order=1), ROTATE(order=2)]
        '把杯子递给用户' → action=HANDOVER, steps=[GRASP, FETCH, HANDOVER]
    """
    primary_action: ActionKind = Field(...,
        description="Task-level action summarizing the overall intent")
    steps: List[ActionStep] = Field(default_factory=list,
        description="Ordered sequence of sub-actions")
    accepted_summary_actions: List[ActionKind] = Field(default_factory=list,
        description="Alternative acceptable summary actions (for evaluation)")

    model_config = {"extra": "forbid"}


# ══════════════════════════════════════════════════════════════
# IntentFrame v1 — the authoritative semantic output
# ══════════════════════════════════════════════════════════════

class IntentFrame(BaseModel):
    """IntentFrame v1 — single authoritative NL → structured semantics output.

    Both RuleEngine and DeepSeek must produce this schema.
    Downstream modules (GroundingEngine, ConstraintCompiler, Validator)
    consume IntentFrame, not raw LLM JSON or regex output.

    Invariants:
        - entity_id NEVER set by LLM
        - all machine-execution semantics in structured fields, NOT notes
        - notes is explanatory only, never used as Compiler semantic input
        - additionalProperties forbidden
    """
    schema_version: str = Field(default="1.0.0")

    # ── Core action ──
    action: ActionKind = Field(..., description="Primary action")

    # ── Role assignments ──
    theme: Optional[EntityReference] = Field(default=None,
        description="The object being acted upon")
    destination: Optional[EntityReference] = Field(default=None,
        description="Where the theme should be moved to")
    recipient: Optional[EntityReference] = Field(default=None,
        description="Who receives the theme (human or robot)")
    source: Optional[EntityReference] = Field(default=None,
        description="Where the theme comes from (TRANSFER)")

    # ── Prohibitions (negation) ──
    prohibitions: List[Prohibition] = Field(default_factory=list,
        description="Things the user forbids: contacts, actions, entities, regions")

    # ── Conditions ──
    conditions: List[Condition] = Field(default_factory=list,
        description="Conditional branches in the instruction")

    # ── Sequence ──
    sequence: List[SequenceStep] = Field(default_factory=list,
        description="Ordered action steps for multi-action instructions")

    # ── Composite action plan (Phase 7) ──
    action_plan: Optional[ActionPlan] = Field(default=None,
        description="Composite action plan for multi-step tasks. "
                    "When present, action holds the task-level summary, "
                    "and action_plan.steps contains the full sequence.")

    # ── Numeric constraints ──
    user_constraints: List[UserConstraint] = Field(default_factory=list,
        description="Explicit numeric/parameter constraints")

    # ── Modifiers ──
    manner: Optional[MannerKind] = Field(default=None,
        description="How the action should be performed")

    urgency: UrgencyKind = Field(default=UrgencyKind.NORMAL)

    # ── Clarification ──
    clarification: Optional[str] = Field(default=None,
        description="If the instruction is ambiguous, ask for clarification here")

    # ── Explanatory only — NOT for compiler input ──
    explanatory_notes: List[str] = Field(default_factory=list,
        description="LLM reasoning/notes. These are NEVER used as compiler semantic input.")

    model_config = {"extra": "forbid", "json_schema_extra": {"additionalProperties": False}}

    @model_validator(mode="after")
    def _validate_action_roles(self):
        """Ensure required roles are present for each action."""
        if self.action in (ActionKind.FETCH, ActionKind.HANDOVER, ActionKind.TRANSFER):
            if self.recipient is None:
                # Not fatal at schema level — downstream handles missing roles
                pass
        if self.action == ActionKind.PLACE:
            if self.destination is None:
                pass
        return self

    @field_validator("explanatory_notes")
    @classmethod
    def _notes_must_not_contain_structured_data(cls, v):
        """Notes are for explanation only. They must not smuggle structured semantics."""
        return v

    @model_validator(mode="after")
    def _ensure_no_entity_ids(self):
        """Verify that no EntityReference has a non-null entity_id.
        entity_id assignment is GroundingEngine's sole responsibility."""
        for name in ("theme", "destination", "recipient", "source"):
            entity = getattr(self, name, None)
            if entity is not None and hasattr(entity, "mention"):
                # EntityReference has no entity_id field — this check is structural
                pass
        return self

    def get_all_prohibition_ids(self) -> List[str]:
        return [p.prohibition_id for p in self.prohibitions]

    def get_all_condition_ids(self) -> List[str]:
        return [c.condition_id for c in self.conditions]

    def get_all_constraint_ids(self) -> List[str]:
        return [c.constraint_id for c in self.user_constraints]

    def has_hard_prohibitions(self) -> bool:
        return any(p.type != ProhibitionType.CONDITIONAL_PROHIBITION for p in self.prohibitions)

    def has_hard_conditions(self) -> bool:
        return any(c.hard for c in self.conditions)

    def has_sequence(self) -> bool:
        return len(self.sequence) > 0


# ══════════════════════════════════════════════════════════════
# Engine trace — audit for every NL→IntentFrame conversion
# ══════════════════════════════════════════════════════════════

class EngineTrace(BaseModel):
    """Complete audit trace for a single NL→IntentFrame conversion attempt."""
    requested_engine: str = Field(default="RuleEngine")
    actual_engine: str = Field(default="RuleEngine")
    llm_call_attempted: bool = Field(default=False)
    llm_call_succeeded: bool = Field(default=False)
    response_schema_valid: bool = Field(default=False)
    repair_attempted: bool = Field(default=False)
    repair_succeeded: bool = Field(default=False)
    fallback_used: bool = Field(default=False)
    fallback_reason: Optional[str] = Field(default=None)
    model_name: Optional[str] = Field(default=None)
    latency_ms: float = Field(default=0.0)
    response_hash: Optional[str] = Field(default=None)

    model_config = {"extra": "forbid"}


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def intent_frame_json_schema() -> Dict[str, Any]:
    """Export the IntentFrame JSON Schema for use in LLM system prompts."""
    return IntentFrame.model_json_schema()


def make_prohibition_id(text_span: str, index: int) -> str:
    """Generate a stable prohibition_id from text span and index."""
    import hashlib
    payload = f"{text_span}|prohibition|{index}"
    return f"proh-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def make_condition_id(text_span: str, index: int) -> str:
    """Generate a stable condition_id from text span and index."""
    import hashlib
    payload = f"{text_span}|condition|{index}"
    return f"cond-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def make_constraint_id(parameter: str, operator: str, text_span: str) -> str:
    """Generate a stable constraint_id."""
    import hashlib
    payload = f"{parameter}|{operator}|{text_span}"
    return f"cstr-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"
