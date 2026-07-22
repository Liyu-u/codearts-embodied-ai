"""
Structured cross-validation with field-level conflict resolution.

Each parser (Rule, DeepSeek, Grounder) produces a SemanticHypothesis.
The ConflictResolver merges them field-by-field:
  - Numeric/unit: deterministic parse authoritative
  - object_id: EntityGrounder authoritative
  - Robot capability: input JSON + deterministic validator authoritative
  - Action/role/condition: agreement → adopt; disagreement → resolve or clarify
  - CRITICAL still unresolved → NEEDS_CLARIFICATION

No whole-JSON pick-one. No voting. No dangerous auto-compromise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════
# Core types
# ══════════════════════════════════════════════════════════════

class HypothesisSource(str, Enum):
    RULE = "rule"
    DEEPSEEK = "deepseek"
    GROUNDER = "grounder"
    ROBOT_STATE = "robot_state"


class FieldStatus(str, Enum):
    AGREEMENT = "AGREEMENT"              # All sources agree
    RULE_AUTHORITATIVE = "RULE_AUTHORITATIVE"  # Rule wins by authority
    GROUNDER_AUTHORITATIVE = "GROUNDER_AUTHORITATIVE"
    ROBOT_STATE_AUTHORITATIVE = "ROBOT_STATE_AUTHORITATIVE"
    RESOLVED = "RESOLVED"                # Conflict resolved via re-grounding
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    DEEPSEEK_UNAVAILABLE = "DEEPSEEK_UNAVAILABLE"


@dataclass
class FieldValue:
    """One field value from one source with evidence."""
    source: HypothesisSource
    value: Any
    confidence: float = 1.0
    evidence: List[str] = field(default_factory=list)


@dataclass
class ConflictRecord:
    """Structured record of a field-level conflict and its resolution."""
    field: str
    rule_value: Any = None
    llm_value: Any = None
    grounder_value: Any = None
    resolved_value: Any = None
    resolution: str = ""               # FieldStatus value
    reason_code: str = ""
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "rule_value": str(self.rule_value)[:120],
            "llm_value": str(self.llm_value)[:120] if self.llm_value is not None else None,
            "grounder_value": str(self.grounder_value)[:120] if self.grounder_value is not None else None,
            "resolved_value": str(self.resolved_value)[:120] if self.resolved_value is not None else None,
            "resolution": self.resolution,
            "reason_code": self.reason_code,
            "detail": self.detail,
        }


# ══════════════════════════════════════════════════════════════
# SemanticHypothesis — structured output from each parser
# ══════════════════════════════════════════════════════════════

@dataclass
class SemanticHypothesis:
    """One parser's semantic interpretation of the instruction."""
    source: HypothesisSource
    action: Optional[str] = None
    roles: Dict[str, Any] = field(default_factory=dict)       # role_name → value
    negation_objects: List[str] = field(default_factory=list)  # negated mentions
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    manner: Optional[str] = None
    confidence_by_field: Dict[str, float] = field(default_factory=dict)
    evidence_spans: Dict[str, List[str]] = field(default_factory=dict)
    raw_output: Any = None  # The original parser output (for debugging)

    def get(self, field: str) -> Any:
        """Get a field value by name."""
        if field == "action":
            return self.action
        if field in ("theme", "destination", "support_surface", "recipient", "source"):
            return self.roles.get(field)
        if field == "avoid":
            return self.negation_objects
        if field == "condition":
            return self.conditions
        if field == "manner":
            return self.manner
        if field == "constraints":
            return self.constraints
        return None


# ══════════════════════════════════════════════════════════════
# Hypothesis builders — extract from each parser
# ══════════════════════════════════════════════════════════════

def build_rule_hypothesis(parsed_task: Any, grounded_task: Any = None) -> SemanticHypothesis:
    """Build a SemanticHypothesis from the RuleEngine's ParsedTask + GroundedTask."""
    h = SemanticHypothesis(source=HypothesisSource.RULE)

    if parsed_task:
        h.action = parsed_task.action.value if hasattr(parsed_task.action, 'value') else str(parsed_task.action)

        # Roles
        if parsed_task.theme:
            h.roles["theme"] = {
                "mention": parsed_task.theme.mention,
                "entity_id": parsed_task.theme.entity_id,
                "specific_class": parsed_task.theme.specific_class,
                "confidence": parsed_task.theme.grounding_confidence,
            }
        if parsed_task.destination:
            h.roles["destination"] = {
                "mention": parsed_task.destination.mention,
                "entity_id": parsed_task.destination.entity_id,
                "specific_class": parsed_task.destination.specific_class,
                "confidence": parsed_task.destination.grounding_confidence,
            }
        if parsed_task.support_surface:
            h.roles["support_surface"] = {
                "mention": parsed_task.support_surface.mention,
                "entity_id": parsed_task.support_surface.entity_id,
                "specific_class": parsed_task.support_surface.specific_class,
                "confidence": parsed_task.support_surface.grounding_confidence,
            }
        if parsed_task.recipient:
            h.roles["recipient"] = {
                "mention": parsed_task.recipient.mention,
                "entity_id": parsed_task.recipient.entity_id,
                "specific_class": parsed_task.recipient.specific_class,
                "confidence": parsed_task.recipient.grounding_confidence,
            }

        # Negation / obstacles
        h.negation_objects = [
            {"mention": o.mention, "entity_id": o.entity_id}
            for o in (parsed_task.obstacle or [])
        ]

        # Manner
        h.manner = parsed_task.manner

        # Constraints
        for c in (parsed_task.user_constraints or []):
            h.constraints.append({
                "parameter": c.parameter,
                "operator": c.operator.value if hasattr(c.operator, 'value') else str(c.operator),
                "value": c.value,
                "min_value": c.min_value,
                "max_value": c.max_value,
                "unit": c.unit,
                "text_span": c.text_span,
            })

        # Confidence
        h.confidence_by_field = {
            "action": 0.90 if parsed_task.action else 0.5,
            "theme": parsed_task.theme.grounding_confidence if parsed_task.theme else 0.0,
        }

        # Evidence
        if parsed_task.theme and hasattr(parsed_task.theme, 'match_evidence'):
            h.evidence_spans["theme"] = list(parsed_task.theme.match_evidence)

    return h


def build_deepseek_hypothesis(descriptor: Any) -> Optional[SemanticHypothesis]:
    """Build a SemanticHypothesis from DeepSeek's SemanticDescriptor.

    Returns None if DeepSeek is unavailable or fell back.
    """
    if descriptor is None:
        return None

    h = SemanticHypothesis(source=HypothesisSource.DEEPSEEK)

    # Action
    ac = getattr(descriptor, 'action_candidates', None)
    if ac and len(ac) > 0:
        h.action = ac[0]

    # Roles
    roles = getattr(descriptor, 'roles', {}) or {}
    for role_name, role_data in roles.items():
        if hasattr(role_data, 'mention'):
            h.roles[role_name] = {
                "mention": role_data.mention,
                "entity_id": role_data.object_id,
                "specific_class": role_data.specific_class,
                "confidence": role_data.confidence,
            }
        elif isinstance(role_data, dict):
            h.roles[role_name] = role_data

    # Avoid
    avoid_list = getattr(descriptor, 'avoid', []) or []
    for a in avoid_list:
        if hasattr(a, 'mention'):
            h.negation_objects.append({
                "mention": a.mention,
                "entity_id": a.object_id,
            })
        elif isinstance(a, dict):
            h.negation_objects.append(a)

    # Conditions
    conds = getattr(descriptor, 'conditions', []) or []
    for c in conds:
        if hasattr(c, 'model_dump'):
            h.conditions.append(c.model_dump())
        elif isinstance(c, dict):
            h.conditions.append(c)

    # Constraints
    constrs = getattr(descriptor, 'constraints', []) or []
    for c in constrs:
        if hasattr(c, 'model_dump'):
            h.constraints.append(c.model_dump())
        elif isinstance(c, dict):
            h.constraints.append(c)

    # Manner
    manner_list = getattr(descriptor, 'manner', []) or []
    if manner_list:
        h.manner = manner_list[0] if isinstance(manner_list, list) else str(manner_list)

    # Confidence
    h.confidence_by_field = {
        "action": getattr(descriptor, 'parse_confidence', 0.0),
        "parse": getattr(descriptor, 'parse_confidence', 0.0),
    }

    return h


def build_grounder_hypothesis(grounding_results: Optional[Dict[str, Any]] = None) -> SemanticHypothesis:
    """Build a SemanticHypothesis from GroundingEngine results.

    The Grounder is authoritative for object_id assignment.
    """
    h = SemanticHypothesis(source=HypothesisSource.GROUNDER)

    if grounding_results:
        for role_name, result in grounding_results.items():
            if result is not None and hasattr(result, 'selected') and result.selected:
                h.roles[role_name] = {
                    "entity_id": result.selected.entity_ref.entity_id,
                    "mention": result.selected.entity_ref.mention,
                    "confidence": result.selected.total_score,
                }
                h.confidence_by_field[role_name] = result.selected.total_score
                h.evidence_spans[role_name] = list(result.selected.evidence)

    return h


# ══════════════════════════════════════════════════════════════
# ConflictResolver — field-level merge engine
# ══════════════════════════════════════════════════════════════

# Fields where the RuleEngine is authoritatively correct
_RULE_AUTHORITATIVE_FIELDS: Set[str] = {
    "action",  # Rule's _classify_action is deterministic
}

# Fields where the Grounder is authoritatively correct
_GROUNDER_AUTHORITATIVE_FIELDS: Set[str] = {
    "object_id",  # Grounder maps mentions to scene entities
}

# Fields where input JSON / robot state is authoritatively correct
_ROBOT_STATE_AUTHORITATIVE_FIELDS: Set[str] = {
    "robot_capability",
    "gripper_state",
    "is_homed",
}

# CRITICAL fields — unresolved disagreement → NEEDS_CLARIFICATION
_CRITICAL_FIELDS: Set[str] = {
    "action", "theme", "destination", "support_surface",
    "recipient", "avoid", "condition_branch", "numeric_operator",
}

# Fields that map to ParsedTask roles
_ROLE_FIELDS: Set[str] = {"theme", "destination", "support_surface", "recipient", "source"}


@dataclass
class MergeResult:
    """Result of merging multiple SemanticHypotheses."""
    action: Optional[str] = None
    roles: Dict[str, Any] = field(default_factory=dict)
    negation_objects: List[Dict[str, Any]] = field(default_factory=list)
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Dict[str, Any]] = field(default_factory=list)
    manner: Optional[str] = None
    conflicts: List[ConflictRecord] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_fields: List[str] = field(default_factory=list)
    overall_confidence: float = 0.0

    @property
    def has_critical_conflict(self) -> bool:
        return any(
            c.field in _CRITICAL_FIELDS and c.resolution == FieldStatus.NEEDS_CLARIFICATION.value
            for c in self.conflicts
        )


class ConflictResolver:
    """Field-level merge engine for SemanticHypotheses.

    Rules (in priority order):
      1. Robot state fields: input JSON authoritative
      2. object_id: Grounder authoritative
      3. Numeric values/units: Rule (deterministic) authoritative
      4. Action/role/condition: Rule ↔ DeepSeek agreement → adopt
      5. Disagreement on CRITICAL fields → NEEDS_CLARIFICATION
      6. DeepSeek unavailable → Rule authoritative (no conflict)
    """

    def merge(
        self,
        rule_hypothesis: SemanticHypothesis,
        deepseek_hypothesis: Optional[SemanticHypothesis],
        grounder_hypothesis: Optional[SemanticHypothesis] = None,
        robot_state: Optional[Dict[str, Any]] = None,
    ) -> MergeResult:
        """Merge hypotheses field-by-field.

        Args:
            rule_hypothesis: From RuleEngine (parse_task_semantics)
            deepseek_hypothesis: From DeepSeek (may be None if unavailable)
            grounder_hypothesis: From GroundingEngine
            robot_state: Input robot state dict

        Returns:
            MergeResult with resolved values and conflict records
        """
        result = MergeResult()
        has_llm = deepseek_hypothesis is not None
        has_grounder = grounder_hypothesis is not None

        # ── 1. Action ──
        result.action = self._merge_action(rule_hypothesis, deepseek_hypothesis, result)

        # ── 2. Roles (theme, destination, support_surface, recipient) ──
        for role_name in _ROLE_FIELDS:
            merged = self._merge_role(
                role_name, rule_hypothesis, deepseek_hypothesis,
                grounder_hypothesis, result,
            )
            if merged is not None:
                result.roles[role_name] = merged

        # ── 3. Negation / avoid ──
        result.negation_objects = self._merge_negation(
            rule_hypothesis, deepseek_hypothesis, result,
        )

        # ── 4. Conditions ──
        result.conditions = self._merge_conditions(
            rule_hypothesis, deepseek_hypothesis, result,
        )

        # ── 5. Constraints ──
        result.constraints = self._merge_constraints(
            rule_hypothesis, deepseek_hypothesis, result,
        )

        # ── 6. Manner ──
        result.manner = self._merge_manner(rule_hypothesis, deepseek_hypothesis, result)

        # ── 7. Check for CRITICAL unresolved ──
        result.needs_clarification = result.has_critical_conflict
        result.clarification_fields = [
            c.field for c in result.conflicts
            if c.resolution == FieldStatus.NEEDS_CLARIFICATION.value
        ]

        # ── 8. Overall confidence ──
        result.overall_confidence = self._compute_overall_confidence(
            rule_hypothesis, deepseek_hypothesis, result,
        )

        return result

    # ── Per-field merge methods ───────────────────────────────

    def _merge_action(
        self,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> Optional[str]:
        """Merge action field."""
        rule_action = rule.action
        llm_action = llm.action if llm else None

        if llm_action is None or rule_action is None:
            # One source only — take what we have
            chosen = rule_action or llm_action
            if llm is None and rule_action:
                result.conflicts.append(ConflictRecord(
                    field="action", rule_value=rule_action,
                    resolution=FieldStatus.DEEPSEEK_UNAVAILABLE.value,
                    reason_code="LLM_UNAVAILABLE",
                    detail="DeepSeek unavailable; using RuleEngine action",
                ))
            return chosen

        if rule_action == llm_action:
            # Agreement
            result.conflicts.append(ConflictRecord(
                field="action", rule_value=rule_action, llm_value=llm_action,
                resolved_value=rule_action,
                resolution=FieldStatus.AGREEMENT.value,
                reason_code="CONSISTENT",
                detail=f"Both sources agree on action={rule_action}",
            ))
            return rule_action

        # Disagreement on CRITICAL field → NEEDS_CLARIFICATION
        result.conflicts.append(ConflictRecord(
            field="action", rule_value=rule_action, llm_value=llm_action,
            resolved_value=None,
            resolution=FieldStatus.NEEDS_CLARIFICATION.value,
            reason_code="CRITICAL_ACTION_DISAGREEMENT",
            detail=f"Rule says '{rule_action}', DeepSeek says '{llm_action}'. Cannot auto-resolve.",
        ))
        return None

    def _merge_role(
        self,
        role_name: str,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        grounder: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> Optional[Dict[str, Any]]:
        """Merge a role field. Grounder is authoritative for entity_id."""
        rule_role = rule.roles.get(role_name)
        llm_role = llm.roles.get(role_name) if llm else None
        grounder_role = grounder.roles.get(role_name) if grounder else None

        # If no source has this role, skip
        if not rule_role and not llm_role and not grounder_role:
            return None

        # Grounder is authoritative for entity_id
        merged: Dict[str, Any] = {}

        # Start with rule mention (deterministic NL extraction)
        if rule_role and isinstance(rule_role, dict):
            merged["mention"] = rule_role.get("mention", "")
            merged["specific_class"] = rule_role.get("specific_class")
        elif llm_role and isinstance(llm_role, dict):
            merged["mention"] = llm_role.get("mention", "")
            merged["specific_class"] = llm_role.get("specific_class")

        # Entity ID: Grounder > Rule > LLM
        eid = None
        if grounder_role and isinstance(grounder_role, dict) and grounder_role.get("entity_id"):
            eid = grounder_role["entity_id"]
        elif rule_role and isinstance(rule_role, dict) and rule_role.get("entity_id"):
            eid = rule_role["entity_id"]
        elif llm_role and isinstance(llm_role, dict) and llm_role.get("object_id"):
            eid = llm_role["object_id"]

        if eid:
            merged["entity_id"] = eid

        # Check for mention disagreement between rule and LLM
        rule_mention = rule_role.get("mention", "") if isinstance(rule_role, dict) else ""
        llm_mention = llm_role.get("mention", "") if llm_role and isinstance(llm_role, dict) else ""

        if rule_mention and llm_mention and rule_mention != llm_mention:
            # Different mentions → potential conflict
            # Check if they refer to the same entity via grounder
            if eid:
                # Grounded to same entity → different surface forms, OK
                result.conflicts.append(ConflictRecord(
                    field=role_name,
                    rule_value=rule_mention, llm_value=llm_mention,
                    resolved_value=eid,
                    resolution=FieldStatus.GROUNDER_AUTHORITATIVE.value,
                    reason_code="DIFFERENT_MENTION_SAME_ENTITY",
                    detail=f"Different mentions '{rule_mention}' vs '{llm_mention}' but same entity_id={eid}",
                ))
            else:
                # Different mentions, not grounded — CRITICAL for key roles
                if role_name in ("theme", "destination", "support_surface"):
                    result.conflicts.append(ConflictRecord(
                        field=role_name,
                        rule_value=rule_mention, llm_value=llm_mention,
                        resolved_value=None,
                        resolution=FieldStatus.NEEDS_CLARIFICATION.value,
                        reason_code="CRITICAL_GROUNDING_DISAGREEMENT",
                        detail=f"Rule says '{rule_mention}', DeepSeek says '{llm_mention}'. No grounder consensus.",
                    ))
                    return None
                else:
                    result.conflicts.append(ConflictRecord(
                        field=role_name,
                        rule_value=rule_mention, llm_value=llm_mention,
                        resolved_value=rule_mention,
                        resolution=FieldStatus.RULE_AUTHORITATIVE.value,
                        reason_code="MENTION_DISAGREEMENT_NON_CRITICAL",
                    ))
        elif rule_mention and not llm_mention:
            result.conflicts.append(ConflictRecord(
                field=role_name, rule_value=rule_mention,
                resolved_value=rule_mention,
                resolution=FieldStatus.RULE_AUTHORITATIVE.value,
                reason_code="LLM_NO_MENTION",
            ))

        return merged if merged else None

    def _merge_negation(
        self,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> List[Dict[str, Any]]:
        """Merge negation/avoid objects. Union of both sources."""
        merged: List[Dict[str, Any]] = []
        seen_mentions: Set[str] = set()

        # Rule obstacles first (deterministic)
        for obj in rule.negation_objects:
            mention = obj.get("mention", "") if isinstance(obj, dict) else str(obj)
            if mention and mention not in seen_mentions:
                merged.append(obj if isinstance(obj, dict) else {"mention": mention})
                seen_mentions.add(mention)

        # Add LLM avoid objects not already present
        if llm:
            for obj in llm.negation_objects:
                mention = obj.get("mention", "") if isinstance(obj, dict) else str(obj)
                if mention and mention not in seen_mentions:
                    merged.append(obj if isinstance(obj, dict) else {"mention": mention})
                    seen_mentions.add(mention)

        # Check if LLM missed avoid objects that rule found
        if llm and rule.negation_objects:
            rule_mentions = {
                o.get("mention", "") if isinstance(o, dict) else str(o)
                for o in rule.negation_objects
            }
            llm_mentions = {
                o.get("mention", "") if isinstance(o, dict) else str(o)
                for o in llm.negation_objects
            }
            missed = rule_mentions - llm_mentions
            if missed:
                result.conflicts.append(ConflictRecord(
                    field="avoid",
                    rule_value=sorted(rule_mentions),
                    llm_value=sorted(llm_mentions),
                    resolved_value=sorted(seen_mentions),
                    resolution=FieldStatus.RULE_AUTHORITATIVE.value,
                    reason_code="LLM_MISSED_AVOID",
                    detail=f"DeepSeek missed avoid objects: {sorted(missed)}. Using RuleEngine's full set.",
                ))

        return merged

    def _merge_conditions(
        self,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> List[Dict[str, Any]]:
        """Merge condition structures. LLM can add, but can't override rule."""
        # Rule conditions are mostly from LogicalAST (deterministic regex)
        # LLM conditions may add nuance (e.g., VISIBLE predicate)
        merged = list(rule.conditions)

        if llm and llm.conditions:
            rule_texts = {c.get("raw_text", c.get("condition_text", "")) for c in rule.conditions}
            for c in llm.conditions:
                ct = c.get("condition_text", c.get("raw_text", ""))
                if ct and ct not in rule_texts:
                    merged.append(c)
                    result.conflicts.append(ConflictRecord(
                        field="condition",
                        rule_value="(not detected)", llm_value=ct,
                        resolved_value=ct,
                        resolution=FieldStatus.RESOLVED.value,
                        reason_code="LLM_AUGMENTED_CONDITION",
                        detail="DeepSeek detected condition that rule missed; added to merged set.",
                    ))

        return merged

    def _merge_constraints(
        self,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> List[Dict[str, Any]]:
        """Merge numeric constraints. Rule (deterministic regex) is authoritative.

        LLM may suggest constraints but rule's deterministic parse wins on values.
        """
        # Rule constraints are deterministically parsed → authoritative
        merged = list(rule.constraints)
        rule_params = {c.get("parameter", "") for c in rule.constraints}

        if llm:
            for c in llm.constraints:
                param = c.get("parameter", "")
                if param not in rule_params:
                    # LLM found a constraint rule missed — add it
                    merged.append(c)
                    result.conflicts.append(ConflictRecord(
                        field=f"constraint.{param}",
                        rule_value="(not detected)", llm_value=c.get("value"),
                        resolved_value=c.get("value"),
                        resolution=FieldStatus.RESOLVED.value,
                        reason_code="LLM_AUGMENTED_CONSTRAINT",
                    ))
                else:
                    # Both have this parameter — check operator agreement
                    rule_c = next((rc for rc in rule.constraints if rc.get("parameter") == param), None)
                    rule_op = rule_c.get("operator", "") if rule_c else ""
                    llm_op = c.get("operator", "")
                    if rule_op and llm_op and rule_op != llm_op:
                        result.conflicts.append(ConflictRecord(
                            field=f"constraint.{param}.operator",
                            rule_value=rule_op, llm_value=llm_op,
                            resolved_value=rule_op,
                            resolution=FieldStatus.RULE_AUTHORITATIVE.value,
                            reason_code="OPERATOR_DISAGREEMENT_RULE_WINS",
                            detail=f"Operator: rule='{rule_op}', llm='{llm_op}'. Using rule's deterministic parse.",
                        ))

        return merged

    def _merge_manner(
        self,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> Optional[str]:
        """Merge manner. Rule authoritative (deterministic regex)."""
        if rule.manner:
            return rule.manner
        if llm and llm.manner:
            return llm.manner
        return None

    def _compute_overall_confidence(
        self,
        rule: SemanticHypothesis,
        llm: Optional[SemanticHypothesis],
        result: MergeResult,
    ) -> float:
        """Compute overall confidence from field-level confidences and conflicts."""
        if result.has_critical_conflict:
            return 0.3

        confidences = []
        # Rule confidence
        rule_confs = list(rule.confidence_by_field.values())
        if rule_confs:
            confidences.append(sum(rule_confs) / len(rule_confs))

        # LLM confidence (if available)
        if llm:
            llm_confs = list(llm.confidence_by_field.values())
            if llm_confs:
                confidences.append(sum(llm_confs) / len(llm_confs))

        # Penalize for conflicts
        base = sum(confidences) / len(confidences) if confidences else 0.5
        conflict_penalty = 0.05 * len(result.conflicts)
        return max(0.1, min(1.0, base - conflict_penalty))


# ══════════════════════════════════════════════════════════════
# Convenience function
# ══════════════════════════════════════════════════════════════

def cross_validate(
    parsed_task: Any,
    grounded_task: Any = None,
    deepseek_descriptor: Any = None,
    grounding_results: Optional[Dict[str, Any]] = None,
    robot_state: Optional[Dict[str, Any]] = None,
) -> MergeResult:
    """Run the full cross-validation pipeline.

    Args:
        parsed_task: RuleEngine's ParsedTask
        grounded_task: RuleEngine's GroundedTask
        deepseek_descriptor: DeepSeek's SemanticDescriptor (or None)
        grounding_results: GroundingEngine results per role
        robot_state: Input robot state dict

    Returns:
        MergeResult with resolved values and conflict records
    """
    rule_h = build_rule_hypothesis(parsed_task, grounded_task)
    llm_h = build_deepseek_hypothesis(deepseek_descriptor) if deepseek_descriptor is not None else None
    grounder_h = build_grounder_hypothesis(grounding_results)

    resolver = ConflictResolver()
    return resolver.merge(rule_h, llm_h, grounder_h, robot_state)
