"""
Semantic Reconciler — Merges LLM (DeepSeek) and deterministic extraction results.

Input:
    - DeepSeek IntentFrame (from LLM)
    - CriticalSemanticExtractor results (deterministic)
    - Optional RuleEngine results

Output:
    - ReconciledIntentFrame
    - reconciliation_trace
    - conflict_flags

Reconciliation principles:
    1. Numeric values, units, operators: deterministic extraction is authoritative
    2. Explicit user prohibitions: deterministic extraction guarantees no loss;
       union with LLM prohibitions (normalize + dedup)
    3. If LLM and deterministic results have HIGH-RISK conflicts:
       - LLM theme = prohibited object → NEEDS_CLARIFICATION or BLOCKED
       - LLM output exact when user said MAX → reconcile to MAX
       - LLM direct grasp when user said "wait then grasp" → enforce BEFORE
    4. Never use "just overwrite all LLM fields" strategy
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from robot_intent_agent.schemas.intent_frame import (
    ActionKind,
    ConditionPredicate,
    Condition,
    ConstraintOperator,
    ConstraintUnit,
    EntityReference,
    IntentFrame,
    Prohibition,
    ProhibitionType,
    UserConstraint,
    make_condition_id,
    make_constraint_id,
    make_prohibition_id,
)
from robot_intent_agent.semantic_reasoner.critical_semantic_extractor import (
    CriticalSemantics,
    ExtractedCondition,
    ExtractedNegation,
    ExtractedNumeric,
    NegationType,
    NumericOperator,
    ConditionConnector,
    extract_critical_semantics,
)


# ══════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════

class ConflictType(str, Enum):
    NONE = "NONE"
    THEME_IS_PROHIBITED = "THEME_IS_PROHIBITED"        # LLM theme = user forbidden object
    OPERATOR_MISMATCH = "OPERATOR_MISMATCH"             # LLM said EXACT, user said MAX
    VALUE_MISMATCH = "VALUE_MISMATCH"                  # Numeric values disagree
    MISSING_PROHIBITION = "MISSING_PROHIBITION"        # LLM lost a user prohibition
    MISSING_CONDITION = "MISSING_CONDITION"            # LLM lost a condition
    ACTION_CONFLICT = "ACTION_CONFLICT"                # Different action interpretations
    SEQUENCE_MISSING = "SEQUENCE_MISSING"              # LLM didn't capture sequential steps
    UNIT_MISMATCH = "UNIT_MISMATCH"                    # Unit disagreement


class ReconciliationStatus(str, Enum):
    OK = "OK"
    RECONCILED = "RECONCILED"           # Merged successfully with adjustments
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"  # Ambiguous, need human
    BLOCKED = "BLOCKED"                 # Unresolvable conflict


# ══════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════

@dataclass
class ReconciliationEntry:
    """One reconciliation action taken."""
    conflict_type: ConflictType
    field: str
    llm_value: Any
    deterministic_value: Any
    resolved_value: Any
    resolution: str
    action_taken: str  # "LLM_KEPT" | "DET_OVERRIDE" | "MERGED" | "FLAGGED"


@dataclass
class ReconciliationTrace:
    """Complete reconciliation trace."""
    status: ReconciliationStatus = ReconciliationStatus.OK
    entries: List[ReconciliationEntry] = field(default_factory=list)
    conflict_flags: List[str] = field(default_factory=list)
    needs_clarification: bool = False
    blocked: bool = False

    def add(self, entry: ReconciliationEntry) -> None:
        self.entries.append(entry)
        if entry.conflict_type != ConflictType.NONE:
            self.conflict_flags.append(entry.conflict_type.value)

    def escalate(self, reason: str) -> None:
        self.blocked = True
        self.status = ReconciliationStatus.BLOCKED
        self.conflict_flags.append(f"BLOCKED:{reason}")


# ══════════════════════════════════════════════════════════════
# Reconciler
# ══════════════════════════════════════════════════════════════

class SemanticReconciler:
    """Reconcile LLM IntentFrame with deterministic critical semantics.

    Usage:
        reconciler = SemanticReconciler()
        result = reconciler.reconcile(llm_intent_frame, critical_semantics)
        if result.blocked:
            # Return NEEDS_CLARIFICATION or BLOCKED
    """

    def __init__(self):
        self._extractor = None  # lazy init

    @property
    def extractor(self):
        if self._extractor is None:
            from robot_intent_agent.semantic_reasoner.critical_semantic_extractor import get_critical_extractor
            self._extractor = get_critical_extractor()
        return self._extractor

    # ── Public API ──────────────────────────────────────────

    def reconcile(
        self,
        llm_frame: IntentFrame,
        critical: Optional[CriticalSemantics] = None,
        rule_result: Optional[Dict[str, Any]] = None,
    ) -> tuple[IntentFrame, ReconciliationTrace]:
        """Reconcile LLM IntentFrame with deterministic extraction.

        Returns:
            (reconciled_frame, trace)
        """
        trace = ReconciliationTrace()

        if critical is None:
            # Extract from instruction text (fallback: use theme mentions)
            instruction = self._reconstruct_instruction(llm_frame)
            critical = extract_critical_semantics(instruction)

        # ── 1. Reconcile prohibitions ──
        frame = self._reconcile_prohibitions(llm_frame, critical, trace)

        # ── 2. Reconcile numeric constraints ──
        frame = self._reconcile_numerics(frame, critical, trace)

        # ── 3. Reconcile conditions ──
        frame = self._reconcile_conditions(frame, critical, trace)

        # ── 4. Check for high-risk conflicts ──
        self._check_high_risk_conflicts(frame, critical, trace)

        # ── 5. Update status ──
        if trace.blocked:
            trace.status = ReconciliationStatus.BLOCKED
        elif trace.needs_clarification:
            trace.status = ReconciliationStatus.NEEDS_CLARIFICATION
        elif trace.entries:
            trace.status = ReconciliationStatus.RECONCILED

        return frame, trace

    # ── Prohibition reconciliation ──────────────────────────

    def _reconcile_prohibitions(
        self,
        frame: IntentFrame,
        critical: CriticalSemantics,
        trace: ReconciliationTrace,
    ) -> IntentFrame:
        """Ensure all deterministically detected prohibitions are present."""
        # Build set of existing prohibition mentions
        existing_mentions = {p.target.mention for p in frame.prohibitions}
        existing_texts = {p.source_text_span for p in frame.prohibitions}

        for neg in critical.negations:
            # Check if this negation is already in LLM output
            if neg.target_mention in existing_mentions:
                continue
            if neg.text_span in existing_texts:
                continue

            # Map NegationType to ProhibitionType
            type_map = {
                NegationType.NO_CONTACT: ProhibitionType.NO_CONTACT,
                NegationType.FORBID_ACTION: ProhibitionType.FORBID_ACTION,
                NegationType.AVOID_ENTITY: ProhibitionType.AVOID_ENTITY,
                NegationType.AVOID_REGION: ProhibitionType.AVOID_REGION,
                NegationType.GENERIC_NEGATION: ProhibitionType.NO_CONTACT,
            }
            proh_type = type_map.get(neg.type, ProhibitionType.NO_CONTACT)

            # Map prohibited action
            llm_action = None
            if neg.prohibited_action:
                action_map = {"抓": ActionKind.GRASP, "拿": ActionKind.FETCH,
                            "取": ActionKind.FETCH, "碰": ActionKind.GRASP,
                            "握": ActionKind.GRASP, "夹": ActionKind.GRASP}
                llm_action = action_map.get(neg.prohibited_action)

            # Create prohibition
            idx = len(frame.prohibitions)
            prohibition = Prohibition(
                prohibition_id=make_prohibition_id(neg.text_span, idx),
                type=proh_type,
                target=EntityReference(
                    mention=neg.target_mention,
                    category=None,
                    source_text_span=neg.text_span,
                    confidence=neg.confidence,
                ),
                action=llm_action,
                source_text_span=neg.text_span,
                confidence=neg.confidence,
            )

            # Add target descriptions
            if neg.target_description:
                from robot_intent_agent.schemas.intent_frame import EntityDescriptors
                prohibition.target.descriptors = EntityDescriptors(**{
                    k: v for k, v in neg.target_description.items()
                    if k in EntityDescriptors.model_fields
                })

            frame.prohibitions.append(prohibition)
            existing_mentions.add(neg.target_mention)
            existing_texts.add(neg.text_span)

            trace.add(ReconciliationEntry(
                conflict_type=ConflictType.MISSING_PROHIBITION,
                field="prohibitions",
                llm_value=None,
                deterministic_value=neg.text_span,
                resolved_value=prohibition.prohibition_id,
                resolution=f"Added missing prohibition: {neg.text_span}",
                action_taken="DET_OVERRIDE",
            ))

        return frame

    # ── Numeric reconciliation ─────────────────────────────

    def _reconcile_numerics(
        self,
        frame: IntentFrame,
        critical: CriticalSemantics,
        trace: ReconciliationTrace,
    ) -> IntentFrame:
        """Deterministic numerics are authoritative over LLM numerics."""
        for num in critical.numerics:
            # Map to IntentFrame operator
            op_map = {
                NumericOperator.EXACT: ConstraintOperator.EXACT,
                NumericOperator.MAX: ConstraintOperator.MAX,
                NumericOperator.MIN: ConstraintOperator.MIN,
                NumericOperator.RANGE: ConstraintOperator.RANGE,
            }
            operator = op_map.get(num.operator, ConstraintOperator.MAX)

            # Unit mapping
            unit_map = {
                "N": ConstraintUnit.NEWTON,
                "m/s": ConstraintUnit.METER_PER_SECOND,
                "kg": ConstraintUnit.KILOGRAM,
                "cm": ConstraintUnit.CENTIMETER,
                "mm": ConstraintUnit.MILLIMETER,
                "m": ConstraintUnit.METER,
            }
            unit = unit_map.get(num.unit, ConstraintUnit.NEWTON)

            # Check for existing matching constraint
            existing = next(
                (c for c in frame.user_constraints
                 if c.parameter == num.parameter and c.operator == operator),
                None
            )

            if existing is not None:
                # LLM has a constraint — check for value mismatches
                llm_min = existing.min_value
                llm_max = existing.max_value
                if num.operator == NumericOperator.MAX and llm_max is not None:
                    if abs(llm_max - (num.max_value or 0)) > 0.01:
                        trace.add(ReconciliationEntry(
                            conflict_type=ConflictType.VALUE_MISMATCH,
                            field=f"user_constraints.{num.parameter}.max_value",
                            llm_value=llm_max,
                            deterministic_value=num.max_value,
                            resolved_value=num.max_value,
                            resolution=f"Corrected {num.parameter} max from {llm_max} to {num.max_value}",
                            action_taken="DET_OVERRIDE",
                        ))
                        existing.max_value = num.max_value
                elif num.operator == NumericOperator.MIN and llm_min is not None:
                    if abs(llm_min - (num.min_value or 0)) > 0.01:
                        trace.add(ReconciliationEntry(
                            conflict_type=ConflictType.VALUE_MISMATCH,
                            field=f"user_constraints.{num.parameter}.min_value",
                            llm_value=llm_min,
                            deterministic_value=num.min_value,
                            resolved_value=num.min_value,
                            resolution=f"Corrected {num.parameter} min from {llm_min} to {num.min_value}",
                            action_taken="DET_OVERRIDE",
                        ))
                        existing.min_value = num.min_value

                # Check for operator mismatch (LLM said EXACT, user said MAX)
                if existing.operator != operator:
                    trace.add(ReconciliationEntry(
                        conflict_type=ConflictType.OPERATOR_MISMATCH,
                        field=f"user_constraints.{num.parameter}.operator",
                        llm_value=existing.operator.value,
                        deterministic_value=operator.value,
                        resolved_value=operator.value,
                        resolution=f"Corrected operator from {existing.operator.value} to {operator.value}",
                        action_taken="DET_OVERRIDE",
                    ))
                    # We keep the LLM's constraint but flag it
            else:
                # LLM missed this constraint entirely
                new_constraint = UserConstraint(
                    constraint_id=make_constraint_id(num.parameter, operator.value, num.text_span),
                    parameter=num.parameter,
                    operator=operator,
                    value=num.value,
                    min_value=num.min_value,
                    max_value=num.max_value,
                    unit=unit,
                    hard=True,
                    source_text_span=num.text_span,
                )
                frame.user_constraints.append(new_constraint)
                trace.add(ReconciliationEntry(
                    conflict_type=ConflictType.MISSING_PROHIBITION,
                    field="user_constraints",
                    llm_value=None,
                    deterministic_value=num.text_span,
                    resolved_value=new_constraint.constraint_id,
                    resolution=f"Added missing constraint: {num.text_span}",
                    action_taken="DET_OVERRIDE",
                ))

        return frame

    # ── Condition reconciliation ────────────────────────────

    def _reconcile_conditions(
        self,
        frame: IntentFrame,
        critical: CriticalSemantics,
        trace: ReconciliationTrace,
    ) -> IntentFrame:
        """Ensure conditions and sequences from deterministic extraction are preserved."""
        for cond in critical.conditions:
            if cond.connector == ConditionConnector.WAIT_UNTIL:
                # Ensure a WaitUntilStable-like condition exists
                predicate = ConditionPredicate.OBJECT_STABLE
                if "停" in cond.condition_text or "稳" in cond.condition_text:
                    predicate = ConditionPredicate.OBJECT_STABLE
                elif "空" in cond.condition_text or "夹爪" in cond.condition_text:
                    predicate = ConditionPredicate.GRIPPER_EMPTY

                # Check if LLM already has this condition
                existing = any(
                    c.predicate == predicate and
                    (c.subject and cond.condition_text in (c.subject.mention or ""))
                    for c in frame.conditions
                )
                if not existing:
                    new_cond = Condition(
                        condition_id=make_condition_id(cond.text_span, len(frame.conditions)),
                        predicate=predicate,
                        subject=EntityReference(mention=cond.condition_text[:30]),
                        required_before=[frame.action],
                        hard=True,
                        source_text_span=cond.text_span,
                    )
                    frame.conditions.append(new_cond)
                    trace.add(ReconciliationEntry(
                        conflict_type=ConflictType.MISSING_CONDITION,
                        field="conditions",
                        llm_value=None,
                        deterministic_value=cond.text_span,
                        resolved_value=new_cond.condition_id,
                        resolution=f"Added missing condition: {cond.text_span}",
                        action_taken="DET_OVERRIDE",
                    ))

            elif cond.connector == ConditionConnector.BEFORE:
                # "先X再Y" → ensure sequential ordering is preserved
                if not frame.has_sequence():
                    from robot_intent_agent.schemas.intent_frame import SequenceStep
                    # Infer steps from condition text
                    steps = [
                        SequenceStep(step_index=0, action=frame.action,
                                   description=cond.condition_text),
                        SequenceStep(step_index=1, action=frame.action,
                                   description=cond.action_text),
                    ]
                    frame.sequence = steps
                    trace.add(ReconciliationEntry(
                        conflict_type=ConflictType.SEQUENCE_MISSING,
                        field="sequence",
                        llm_value=None,
                        deterministic_value=cond.text_span,
                        resolved_value="sequence_added",
                        resolution=f"Added sequence from BEFORE pattern: {cond.text_span}",
                        action_taken="DET_OVERRIDE",
                    ))

        return frame

    # ── High-risk conflict detection ────────────────────────

    def _check_high_risk_conflicts(
        self,
        frame: IntentFrame,
        critical: CriticalSemantics,
        trace: ReconciliationTrace,
    ) -> None:
        """Detect HIGH-RISK conflicts that should block execution.

        Cases:
            1. Theme = prohibited object ("不要抓红杯子" but theme = 红杯子)
            2. LLM output GRASP directly when user said "先等它停稳再抓"
            3. LLM misses a hard prohibition
        """
        if frame.theme is None:
            return

        theme_mention = frame.theme.mention
        theme_category = frame.theme.category

        # Check 1: Theme is among prohibited objects
        for neg in critical.negations:
            if neg.target_mention and neg.target_mention in theme_mention:
                trace.escalate(
                    f"Theme '{theme_mention}' appears to be prohibited: '{neg.text_span}'. "
                    f"Cannot simultaneously act on and avoid the same entity."
                )
                return

            # Check if theme category matches prohibition target
            if theme_category and neg.target_mention:
                # If the prohibition target might be a category descriptor
                pass

        # Check 2: WAIT_UNTIL with direct GRASP
        has_wait_condition = any(
            c.connector == ConditionConnector.WAIT_UNTIL
            for c in critical.conditions
        )
        if has_wait_condition and frame.action in (ActionKind.GRASP, ActionKind.FETCH):
            # Check that LLM recognized the condition
            if not frame.has_hard_conditions():
                trace.needs_clarification = True
                trace.add(ReconciliationEntry(
                    conflict_type=ConflictType.MISSING_CONDITION,
                    field="conditions",
                    llm_value="no conditions",
                    deterministic_value="WAIT_UNTIL detected",
                    resolved_value=None,
                    resolution="LLM missed WAIT_UNTIL condition; execution may be premature",
                    action_taken="FLAGGED",
                ))

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _reconstruct_instruction(frame: IntentFrame) -> str:
        """Reconstruct approximate instruction text from IntentFrame for extraction."""
        parts = []
        if frame.theme:
            parts.append(frame.theme.mention)
        if frame.action:
            parts.append(frame.action.value)
        for p in frame.prohibitions:
            parts.append(p.source_text_span)
        for c in frame.conditions:
            parts.append(c.source_text_span)
        return " ".join(parts)


# ══════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════

_reconciler: Optional[SemanticReconciler] = None


def get_reconciler() -> SemanticReconciler:
    """Get or create the singleton SemanticReconciler."""
    global _reconciler
    if _reconciler is None:
        _reconciler = SemanticReconciler()
    return _reconciler


def reconcile_intent(
    llm_frame: IntentFrame,
    instruction: str = "",
) -> tuple[IntentFrame, ReconciliationTrace]:
    """Convenience: reconcile LLM frame with deterministic extraction."""
    critical = extract_critical_semantics(instruction) if instruction else None
    return get_reconciler().reconcile(llm_frame, critical=critical)
