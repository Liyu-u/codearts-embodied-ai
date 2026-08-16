"""
Logical AST for negation, condition, sequence, and scope understanding.

Parses natural language instruction into structured logical forms that drive:
  - Entity role assignment (theme vs avoid)
  - Conditional BT generation (IF_ELSE, UNLESS)
  - Sequential action ordering (BEFORE, AFTER, SEQUENCE)
  - Manner/motion constraint extraction
  - Robot state precondition evaluation

All types are Pydantic models for serialization into RobotTaskIR.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════
# Logical node types
# ══════════════════════════════════════════════════════════════

class LogicalOp(str, Enum):
    """Logical operation type."""
    NEGATION = "NEGATION"           # "不要/别/禁止/避开/千万别碰"
    CONDITION_IF_ELSE = "IF_ELSE"   # "如果A就B，否则C"
    CONDITION_UNLESS = "UNLESS"     # "除非A，否则B"
    SEQUENCE_BEFORE = "BEFORE"      # "先A再B"
    SEQUENCE_AFTER = "AFTER"        # "A以后B"
    SEQUENCE_SIMUL = "SIMULTANEOUS" # "A同时B"
    SEQUENCE_AND = "AND"           # "A并且B"
    WAIT_UNTIL = "WAIT_UNTIL"      # "等待/直到A"


class PredicateKind(str, Enum):
    """Well-known robot state predicates for deterministic evaluation."""
    GRIPPER_EMPTY = "GRIPPER_EMPTY"
    GRIPPER_HAS_OBJECT = "GRIPPER_HAS_OBJECT"
    GRIPPER_OPEN = "GRIPPER_OPEN"
    IS_HOMED = "IS_HOMED"
    VISIBLE = "VISIBLE"
    OBJECT_MOVING = "OBJECT_MOVING"
    OBJECT_STABLE = "OBJECT_STABLE"
    CAPABILITY_AVAILABLE = "CAPABILITY_AVAILABLE"
    OBJECT_EXISTS = "OBJECT_EXISTS"


# ══════════════════════════════════════════════════════════════
# Logical AST nodes (Pydantic models)
# ══════════════════════════════════════════════════════════════

class LogicalPredicate(BaseModel):
    """A single evaluable predicate."""
    predicate: str = Field(..., description="PredicateKind value or freeform predicate name")
    subject_ref: Optional[str] = Field(default=None, description="Entity reference (mention or object_id)")
    negated: bool = Field(default=False, description="Whether this predicate is negated")
    raw_text: str = Field(default="", description="Original text span")


class LogicalAction(BaseModel):
    """A single action step within a logical structure."""
    action: str = Field(default="", description="Action kind (GRASP, FETCH, PLACE, etc.)")
    theme_ref: Optional[str] = Field(default=None, description="Theme entity reference")
    destination_ref: Optional[str] = Field(default=None)
    manner: Optional[str] = Field(default=None)
    raw_text: str = Field(default="")


class NegationNode(BaseModel):
    """Negation: '不要碰X', '别碰X', '避开X', '千万别碰X'."""
    type: str = Field(default=LogicalOp.NEGATION.value)
    negated_refs: List[str] = Field(default_factory=list, description="Entity references that are negated/avoided")
    manner_hint: Optional[str] = Field(default=None, description="'别用力' → gentle")
    raw_text: str = Field(default="")


class ConditionNode(BaseModel):
    """IF_ELSE or UNLESS structure."""
    type: str = Field(..., description="IF_ELSE | UNLESS")
    condition: LogicalPredicate = Field(...)
    then_branch: List[LogicalAction] = Field(default_factory=list)
    else_branch: List[LogicalAction] = Field(default_factory=list)
    raw_text: str = Field(default="")


class SequenceNode(BaseModel):
    """Sequential or simultaneous action ordering."""
    type: str = Field(..., description="BEFORE | AFTER | SIMULTANEOUS | AND")
    steps: List[LogicalAction] = Field(default_factory=list, min_length=2)
    raw_text: str = Field(default="")


class WaitUntilNode(BaseModel):
    """Wait for a condition before proceeding."""
    type: str = Field(default=LogicalOp.WAIT_UNTIL.value)
    condition: LogicalPredicate = Field(...)
    then_action: Optional[LogicalAction] = Field(default=None)
    raw_text: str = Field(default="")


class LogicalAST(BaseModel):
    """Complete logical parse of an instruction.

    An instruction may contain multiple logical structures (e.g., negation + sequence).
    """
    instruction: str = Field(...)
    negations: List[NegationNode] = Field(default_factory=list)
    conditions: List[ConditionNode] = Field(default_factory=list)
    sequences: List[SequenceNode] = Field(default_factory=list)
    wait_until: List[WaitUntilNode] = Field(default_factory=list)
    manner_overrides: List[str] = Field(default_factory=list, description="Manner hints: gentle, fast, careful")
    has_unsupported_structure: bool = Field(default=False)
    unsupported_reason: str = Field(default="")


# ══════════════════════════════════════════════════════════════
# NL Parser — regex-based extraction
# ══════════════════════════════════════════════════════════════

# ── Negation patterns ────────────────────────────────────────

_NEGATION_VERB_PATTERNS = [
    # "不要碰X", "别碰X", "禁止碰X", "不能碰X", "不想碰X", "不想让你碰X"
    (re.compile(r"(不要碰|别碰|禁止碰|不能碰|不想碰|不想让你碰|千万别碰)\s*(\S{1,8})"), "touch"),
    # "不要X", "别X" (verb-modifier)
    (re.compile(r"(不要|别)(用力|使劲|太用力|太使劲)"), "manner_gentle"),
    # "避开X", "绕过X", "绕开X", "躲开X"
    (re.compile(r"(避开|绕过|绕开|躲开)\s*(\S{1,8})"), "avoid"),
    # "禁止接触X"
    (re.compile(r"禁止接触\s*(\S{1,8})"), "touch"),
    # "除了X不要碰" / "除了X"
    (re.compile(r"除了\s*(\S{1,8})\s*(?:不要碰|别碰|以外)"), "except"),
    # "但我不想让你碰X" / "但我不想碰X"
    (re.compile(r"(?:但|但是|可是)\s*(?:我)?\s*(?:不想让你碰|不想碰)\s*(\S{1,8})"), "touch"),
]

_NEGATION_STANDALONE = [
    # "别碰任何东西", "不要碰任何东西", "不要碰其他东西"
    re.compile(r"(别碰|不要碰|千万别碰|不能碰)(任何|其他|别的)\S*"),
    # "什么都别碰", "其他东西都别动"
    re.compile(r"(什么|其他|别的)\S*(?:别碰|别动|不要碰)"),
    # "anything else"
    re.compile(r"don'?t\s+touch\s+anything\s*else", re.IGNORECASE),
    re.compile(r"anything\s+else", re.IGNORECASE),
]

# ── Manner patterns ──────────────────────────────────────────

_MANNER_EXPANDED: Dict[str, List[str]] = {
    "gentle": [
        "轻一点", "轻轻", "小心", "慢慢", "柔和", "温柔",
        "别用力", "不要用力", "别使劲", "不要太用力",
        "轻拿轻放", "轻轻地", "别太用力",
    ],
    "fast": [
        "快点", "快一点", "迅速", "赶快", "赶紧", "马上", "立刻",
        "尽快", "快速", "加速",
    ],
    "careful": [
        "小心一点", "当心", "注意", "谨慎",
    ],
}

# ── Condition patterns ───────────────────────────────────────

_CONDITION_PATTERNS = [
    # "如果A就B" / "如果A，否则C"
    (re.compile(r"如果\s*(.+?)\s*(?:就|则|那么)\s*(.+?)(?:，|。|$)"), "IF_THEN"),
    (re.compile(r"如果\s*(.+?)\s*[,，]\s*否则\s*(.+?)(?:，|。|$)"), "IF_ELSE"),
    (re.compile(r"如果\s*(.+?)\s*(?:就|则)\s*(.+?)\s*[,，]\s*否则\s*(.+?)(?:，|。|$)"), "IF_THEN_ELSE"),
    # "除非A，否则B"
    (re.compile(r"除非\s*(.+?)\s*[,，]\s*否则\s*(.+?)(?:，|。|$)"), "UNLESS"),
    (re.compile(r"除非\s*(.+?)\s*[,，]\s*(?:不然|要不)\s*(.+?)(?:，|。|$)"), "UNLESS"),
    # "只有A才B"
    (re.compile(r"只有\s*(.+?)\s*才\s*(.+?)(?:，|。|$)"), "ONLY_IF"),
]

# ── Sequence patterns ────────────────────────────────────────

_SEQUENCE_PATTERNS = [
    # "先A再B" / "先A然后B"
    (re.compile(r"先\s*(.+?)\s*(?:再|然后|之后)\s*(.+?)(?:，|。|$)"), "BEFORE"),
    # "首先A然后B" / "首先A接着B"
    (re.compile(r"首先\s*(.+?)\s*(?:然后|接着|之后)\s*(.+?)(?:，|。|$)"), "BEFORE"),
    # "A然后B" / "A以后B"
    (re.compile(r"(.+?)\s*(?:然后|之后)\s*(.+?)(?:，|。|$)"), "AFTER"),
    # "A同时B" / "A并且B"
    (re.compile(r"(.+?)\s*(?:同时|并且|并)\s*(.+?)(?:，|。|$)"), "SIMULTANEOUS"),
    # "A以后再B" / "A以后B"
    (re.compile(r"(.+?)\s*以后\s*(?:再\s*)?(.+?)(?:，|。|$)"), "AFTER"),
    # "确认A，然后B"
    (re.compile(r"(?:确认|检查|验证)\s*(.+?)\s*[,，]\s*(?:然后|再)\s*(.+?)(?:，|。|$)"), "CHECK_THEN"),
]

# ── Wait patterns ────────────────────────────────────────────

_WAIT_PATTERNS = [
    (re.compile(r"(?:等待|等到|直到)\s*(.+?)\s*(?:，|。|$)"), "WAIT_UNTIL"),
    (re.compile(r"等\s*(.+?)\s*(?:以后|之后再|再)"), "WAIT_THEN"),
]

# ── Robot state predicate detection ──────────────────────────

_ROBOT_STATE_PREDICATES: Dict[str, Tuple[str, PredicateKind]] = {
    "夹爪是空的": ("gripper", PredicateKind.GRIPPER_EMPTY),
    "夹爪为空": ("gripper", PredicateKind.GRIPPER_EMPTY),
    "夹爪空": ("gripper", PredicateKind.GRIPPER_EMPTY),
    "手是空的": ("gripper", PredicateKind.GRIPPER_EMPTY),
    "夹爪里有东西": ("gripper", PredicateKind.GRIPPER_HAS_OBJECT),
    "夹爪已抓取": ("gripper", PredicateKind.GRIPPER_HAS_OBJECT),
    "夹爪打开": ("gripper", PredicateKind.GRIPPER_OPEN),
    "夹爪张开": ("gripper", PredicateKind.GRIPPER_OPEN),
    "已归位": ("robot", PredicateKind.IS_HOMED),
    "已回原点": ("robot", PredicateKind.IS_HOMED),
    "正在移动": ("object", PredicateKind.OBJECT_MOVING),
    "移动中的": ("object", PredicateKind.OBJECT_MOVING),
    "运动中的": ("object", PredicateKind.OBJECT_MOVING),
    "静止": ("object", PredicateKind.OBJECT_STABLE),
    "不动": ("object", PredicateKind.OBJECT_STABLE),
    "可见": ("perception", PredicateKind.VISIBLE),
    "能看到": ("perception", PredicateKind.VISIBLE),
    "看到": ("perception", PredicateKind.VISIBLE),
}


# ══════════════════════════════════════════════════════════════
# Parser
# ══════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """Normalize text for pattern matching."""
    import unicodedata
    t = unicodedata.normalize("NFKC", text)
    t = t.replace("，", ",").replace("。", ".").replace("：", ":")
    t = t.replace("－", "-").replace("—", "-").replace("～", "-")
    return t


def parse_logical_ast(instruction: str, robot_state: Optional[Dict[str, Any]] = None) -> LogicalAST:
    """Parse an instruction into a LogicalAST.

    Args:
        instruction: Natural language instruction
        robot_state: Optional dict with keys like gripper_empty, is_homed, etc.
                     Used for deterministic predicate evaluation.

    Returns:
        LogicalAST with all detected structures.
    """
    text = _normalize(instruction)
    ast = LogicalAST(instruction=instruction)

    # ── 1. Extract negations ──
    ast.negations = _extract_negations(text)

    # ── 2. Extract manner hints ──
    ast.manner_overrides = _extract_manner_hints(text)

    # ── 3. Extract conditions (IF_ELSE, UNLESS) ──
    ast.conditions = _extract_conditions(text)

    # ── 4. Extract sequences ──
    ast.sequences = _extract_sequences(text)

    # ── 5. Extract wait-until ──
    ast.wait_until = _extract_wait_until(text)

    # ── 6. Evaluate predicates against robot state ──
    if robot_state:
        _evaluate_predicates(ast, robot_state)

    return ast


def _extract_negations(text: str) -> List[NegationNode]:
    """Extract negation structures from text."""
    negations: List[NegationNode] = []

    for pattern, neg_type in _NEGATION_VERB_PATTERNS:
        for match in pattern.finditer(text):
            if neg_type == "manner_gentle":
                negations.append(NegationNode(
                    negated_refs=[],
                    manner_hint="gentle",
                    raw_text=match.group(0),
                ))
            elif neg_type in ("touch", "avoid"):
                obj_text = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else match.group(1).strip()
                # Clean trailing particles
                obj_text = re.sub(r"[的了呢吗啊]$", "", obj_text)
                if obj_text and len(obj_text) >= 1:
                    negations.append(NegationNode(
                        negated_refs=[obj_text],
                        raw_text=match.group(0),
                    ))
            elif neg_type == "except":
                obj_text = match.group(1).strip()
                obj_text = re.sub(r"[的了呢吗啊]$", "", obj_text)
                if obj_text:
                    negations.append(NegationNode(
                        negated_refs=[obj_text],
                        raw_text=match.group(0),
                    ))

    # Standalone negation patterns ("别碰任何东西", etc.)
    for pattern in _NEGATION_STANDALONE:
        if pattern.search(text):
            negations.append(NegationNode(
                negated_refs=["*"],  # Wildcard: everything except theme
                raw_text=pattern.search(text).group(0),
            ))
            break  # One wildcard negation is enough

    return negations


def _extract_manner_hints(text: str) -> List[str]:
    """Extract manner hints from text."""
    hints: List[str] = []
    for manner, patterns in _MANNER_EXPANDED.items():
        if any(p in text for p in patterns):
            hints.append(manner)
    return hints


def _extract_conditions(text: str) -> List[ConditionNode]:
    """Extract IF_ELSE and UNLESS condition structures."""
    conditions: List[ConditionNode] = []

    for pattern, cond_type in _CONDITION_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue

        if cond_type == "IF_THEN":
            cond_text = m.group(1).strip()
            then_text = m.group(2).strip()
            pred = _parse_predicate(cond_text)
            then_actions = _parse_actions(then_text)
            conditions.append(ConditionNode(
                type=LogicalOp.CONDITION_IF_ELSE.value,
                condition=pred,
                then_branch=then_actions,
                else_branch=[],
                raw_text=m.group(0),
            ))

        elif cond_type == "IF_ELSE":
            cond_text = m.group(1).strip()
            else_text = m.group(2).strip()
            pred = _parse_predicate(cond_text)
            else_actions = _parse_actions(else_text)
            conditions.append(ConditionNode(
                type=LogicalOp.CONDITION_IF_ELSE.value,
                condition=pred,
                then_branch=[],  # THEN not explicitly stated in this pattern
                else_branch=else_actions,
                raw_text=m.group(0),
            ))

        elif cond_type == "IF_THEN_ELSE":
            cond_text = m.group(1).strip()
            then_text = m.group(2).strip()
            else_text = m.group(3).strip()
            pred = _parse_predicate(cond_text)
            then_actions = _parse_actions(then_text)
            else_actions = _parse_actions(else_text)
            conditions.append(ConditionNode(
                type=LogicalOp.CONDITION_IF_ELSE.value,
                condition=pred,
                then_branch=then_actions,
                else_branch=else_actions,
                raw_text=m.group(0),
            ))

        elif cond_type in ("UNLESS",):
            cond_text = m.group(1).strip()
            otherwise_text = m.group(2).strip()
            pred = _parse_predicate(cond_text)
            # UNLESS X, otherwise Y → condition=NOT X, then=Y
            pred.negated = not pred.negated
            otherwise_actions = _parse_actions(otherwise_text)
            conditions.append(ConditionNode(
                type=LogicalOp.CONDITION_UNLESS.value,
                condition=pred,
                then_branch=[],
                else_branch=otherwise_actions,
                raw_text=m.group(0),
            ))

        elif cond_type == "ONLY_IF":
            cond_text = m.group(1).strip()
            then_text = m.group(2).strip()
            pred = _parse_predicate(cond_text)
            then_actions = _parse_actions(then_text)
            conditions.append(ConditionNode(
                type=LogicalOp.CONDITION_IF_ELSE.value,
                condition=pred,
                then_branch=then_actions,
                else_branch=[],
                raw_text=m.group(0),
            ))

    return conditions


def _extract_sequences(text: str) -> List[SequenceNode]:
    """Extract sequential action structures."""
    sequences: List[SequenceNode] = []

    for pattern, seq_type in _SEQUENCE_PATTERNS:
        for match in pattern.finditer(text):
            step1_text = match.group(1).strip()
            step2_text = match.group(2).strip() if match.lastindex and match.lastindex >= 2 else ""

            actions = []
            a1 = _parse_actions(step1_text)
            a2 = _parse_actions(step2_text)
            actions.extend(a1)
            actions.extend(a2)

            if len(actions) >= 2:
                seq_op = {
                    "BEFORE": LogicalOp.SEQUENCE_BEFORE.value,
                    "AFTER": LogicalOp.SEQUENCE_AFTER.value,
                    "SIMULTANEOUS": LogicalOp.SEQUENCE_SIMUL.value,
                    "CHECK_THEN": LogicalOp.SEQUENCE_BEFORE.value,
                }.get(seq_type, LogicalOp.SEQUENCE_BEFORE.value)

                sequences.append(SequenceNode(
                    type=seq_op,
                    steps=actions,
                    raw_text=match.group(0),
                ))

    return sequences


def _extract_wait_until(text: str) -> List[WaitUntilNode]:
    """Extract wait-until structures."""
    waits: List[WaitUntilNode] = []

    for pattern, wait_type in _WAIT_PATTERNS:
        for match in pattern.finditer(text):
            cond_text = match.group(1).strip()
            pred = _parse_predicate(cond_text)
            waits.append(WaitUntilNode(
                condition=pred,
                raw_text=match.group(0),
            ))

    return waits


def _parse_predicate(text: str) -> LogicalPredicate:
    """Parse a condition text into a LogicalPredicate.

    Examples:
        "红色药瓶可见" → VISIBLE, subject_ref="红色药瓶"
        "夹爪是空的" → GRIPPER_EMPTY
        "看到红色药瓶" → VISIBLE, subject_ref="红色药瓶"
    """
    # Check against known robot state predicates
    for pattern_text, (domain, kind) in _ROBOT_STATE_PREDICATES.items():
        if pattern_text in text:
            return LogicalPredicate(
                predicate=kind.value,
                subject_ref=text,
                raw_text=text,
            )

    # "X可见" or "看到X" → VISIBLE
    m = re.search(r"(.+?)可见", text)
    if m:
        return LogicalPredicate(
            predicate=PredicateKind.VISIBLE.value,
            subject_ref=m.group(1).strip(),
            raw_text=text,
        )
    m = re.search(r"(?:看到|看见)\s*(.+)", text)
    if m:
        return LogicalPredicate(
            predicate=PredicateKind.VISIBLE.value,
            subject_ref=m.group(1).strip(),
            raw_text=text,
        )

    # "X在移动" / "X静止"
    m = re.search(r"(.+?)(?:在移动|正在移动|运动中)", text)
    if m:
        return LogicalPredicate(
            predicate=PredicateKind.OBJECT_MOVING.value,
            subject_ref=m.group(1).strip(),
            raw_text=text,
        )
    m = re.search(r"(.+?)(?:静止|不动)", text)
    if m:
        return LogicalPredicate(
            predicate=PredicateKind.OBJECT_STABLE.value,
            subject_ref=m.group(1).strip(),
            raw_text=text,
        )

    # Generic predicate
    return LogicalPredicate(
        predicate=text[:40],
        raw_text=text,
    )


def _parse_actions(text: str) -> List[LogicalAction]:
    """Parse action text into LogicalAction list.

    Examples:
        "拿红色药瓶" → [LogicalAction(action="FETCH", theme_ref="红色药瓶")]
        "抓住杯子放到桌子上" → [LogicalAction(action="GRASP", theme_ref="杯子"),
                              LogicalAction(action="PLACE", theme_ref="杯子", destination_ref="桌子上")]
    """
    actions: List[LogicalAction] = []

    # Simple single action detection
    action_patterns = [
        (re.compile(r"(?:拿|取|抓|fetch)\s*(\S{1,8})"), "FETCH"),
        (re.compile(r"(?:抓住|抓取|grasp|grab)\s*(\S{1,8})"), "GRASP"),
        (re.compile(r"(?:放到|放在|摆到|置于|place)\s*(\S{1,8})"), "PLACE"),
        (re.compile(r"(?:递给|交给|handover|give)\s*(\S{1,8})"), "HANDOVER"),
        (re.compile(r"(?:移动|搬运|move)\s*(\S{1,8})"), "TRANSFER"),
    ]

    for pattern, action_kind in action_patterns:
        for m in pattern.finditer(text):
            theme = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else ""
            actions.append(LogicalAction(
                action=action_kind,
                theme_ref=theme,
                raw_text=m.group(0),
            ))

    if not actions:
        # Fallback: treat entire text as one action
        actions.append(LogicalAction(
            action="CUSTOM",
            raw_text=text,
        ))

    return actions


def _evaluate_predicates(ast: LogicalAST, robot_state: Dict[str, Any]) -> None:
    """Evaluate predicates against robot state and annotate AST.

    Modifies ast in place, setting condition evaluation results.
    """
    for cond in ast.conditions:
        pred = cond.condition
        if pred.predicate == PredicateKind.GRIPPER_EMPTY.value:
            # Check robot_state for gripper status
            if robot_state.get("gripper_empty") is True or robot_state.get("gripper_has_object") is False:
                setattr(pred, '__evaluated__', True)
            elif robot_state.get("gripper_empty") is False or robot_state.get("gripper_has_object") is True:
                setattr(pred, '__evaluated__', False)
            else:
                setattr(pred, '__evaluated__', None)  # Unknown

        elif pred.predicate == PredicateKind.GRIPPER_HAS_OBJECT.value:
            if robot_state.get("gripper_has_object") is True:
                setattr(pred, '__evaluated__', True)
            elif robot_state.get("gripper_has_object") is False:
                setattr(pred, '__evaluated__', False)
            else:
                setattr(pred, '__evaluated__', None)

        elif pred.predicate == PredicateKind.IS_HOMED.value:
            if "is_homed" in robot_state:
                setattr(pred, '__evaluated__', robot_state["is_homed"])
            else:
                setattr(pred, '__evaluated__', None)

        elif pred.predicate == PredicateKind.VISIBLE.value:
            # Object visibility can't be determined without perception
            setattr(pred, '__evaluated__', None)


# ══════════════════════════════════════════════════════════════
# Merger: combine LogicalAST with existing task semantics
# ══════════════════════════════════════════════════════════════

def merge_ast_negations(ast: LogicalAST) -> Tuple[List[str], Optional[str]]:
    """Extract negated entity references and manner hints from AST.

    Returns:
        (negated_refs, manner_override)
    """
    negated_refs: List[str] = []
    manner_override: Optional[str] = None

    for neg in ast.negations:
        if neg.manner_hint:
            manner_override = neg.manner_hint
        negated_refs.extend(neg.negated_refs)

    # Manner from AST takes priority
    if ast.manner_overrides and not manner_override:
        manner_override = ast.manner_overrides[0]

    return negated_refs, manner_override


def ast_has_conditional(ast: LogicalAST) -> bool:
    """Check if AST contains any conditional/sequential structure that needs handling."""
    return bool(ast.conditions) or bool(ast.wait_until) or bool(ast.sequences)


def ast_get_unsupported_reason(ast: LogicalAST) -> Optional[str]:
    """Get the reason why a conditional structure can't be executed."""
    for cond in ast.conditions:
        if cond.type == LogicalOp.CONDITION_IF_ELSE.value:
            if not cond.else_branch:
                return f"IF_ELSE missing else branch: '{cond.raw_text}'"
        if cond.type == LogicalOp.CONDITION_UNLESS.value:
            if not cond.else_branch:
                return f"UNLESS missing otherwise clause: '{cond.raw_text}'"
    for wait_node in ast.wait_until:
        if not wait_node.then_action:
            return f"WAIT_UNTIL missing then-action: '{wait_node.raw_text}'"
    return None
