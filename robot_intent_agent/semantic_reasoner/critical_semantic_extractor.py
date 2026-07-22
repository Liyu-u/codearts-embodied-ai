"""
Critical Semantic Extractor — Deterministic, high-confidence semantic extraction.

This module ONLY extracts semantics that can be determined with HIGH confidence
through pattern matching. It does NOT do full NL understanding — that's DeepSeek's job.

Design principle:
    Deterministic extraction is authoritative for:
    - Explicit numeric values and units
    - Explicit operators (max/min/exact)
    - Explicit negation words (别碰, 不要碰, etc.)
    - Explicit prohibition actions
    - Explicit condition connectors (如果, 除非, 先...再, etc.)
    - Explicit sequence words (第一步, 然后, 之后, etc.)

    DeepSeek provides the rich semantic understanding (roles, categories, etc.)
    The Reconciler merges them, with deterministic extraction authoritative for
    numeric/operator/unit conflicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════

class NegationType(str, Enum):
    NO_CONTACT = "NO_CONTACT"           # 别碰X, 不要碰X
    FORBID_ACTION = "FORBID_ACTION"     # 不要抓X, 不能拿X
    AVOID_ENTITY = "AVOID_ENTITY"       # 避开X, 绕开X
    AVOID_REGION = "AVOID_REGION"       # 不要靠近X
    GENERIC_NEGATION = "GENERIC_NEGATION"  # 不要X (ambiguous target)


class ConditionConnector(str, Enum):
    IF_ELSE = "IF_ELSE"          # 如果...否则...
    UNLESS = "UNLESS"            # 除非...否则...
    BEFORE = "BEFORE"            # 先...再...
    AFTER = "AFTER"              # ...之后...
    WAIT_UNTIL = "WAIT_UNTIL"    # 等待...直到...
    SEQUENCE = "SEQUENCE"        # 第一步...第二步...


class NumericOperator(str, Enum):
    EXACT = "EXACT"
    MAX = "MAX"
    MIN = "MIN"
    RANGE = "RANGE"


# ══════════════════════════════════════════════════════════════
# Data structures
# ══════════════════════════════════════════════════════════════

@dataclass
class ExtractedNegation:
    """Deterministically extracted negation/prohibition."""
    text_span: str
    type: NegationType
    target_mention: str
    target_description: Dict[str, str] = field(default_factory=dict)  # color, material, etc.
    prohibited_action: Optional[str] = None  # e.g., "抓", "拿"
    confidence: float = 1.0  # Deterministic = high confidence
    span_start: int = 0
    span_end: int = 0


@dataclass
class ExtractedNumeric:
    """Deterministically extracted numeric constraint."""
    text_span: str
    parameter: str  # force_n, velocity_ms, etc.
    operator: NumericOperator
    value: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: str = ""
    confidence: float = 1.0
    span_start: int = 0
    span_end: int = 0


@dataclass
class ExtractedCondition:
    """Deterministically extracted condition/sequence."""
    text_span: str
    connector: ConditionConnector
    condition_text: str = ""
    action_text: str = ""
    else_text: str = ""
    steps: List[str] = field(default_factory=list)
    confidence: float = 1.0
    span_start: int = 0
    span_end: int = 0


@dataclass
class CriticalSemantics:
    """Aggregated deterministic extraction results."""
    negations: List[ExtractedNegation] = field(default_factory=list)
    numerics: List[ExtractedNumeric] = field(default_factory=list)
    conditions: List[ExtractedCondition] = field(default_factory=list)
    raw_text: str = ""

    @property
    def has_prohibitions(self) -> bool:
        return len(self.negations) > 0

    @property
    def has_numerics(self) -> bool:
        return len(self.numerics) > 0

    @property
    def has_conditions(self) -> bool:
        return len(self.conditions) > 0

    @property
    def all_prohibition_mentions(self) -> List[str]:
        return [n.target_mention for n in self.negations if n.target_mention]


# ══════════════════════════════════════════════════════════════
# Extractor
# ══════════════════════════════════════════════════════════════

class CriticalSemanticExtractor:
    """Deterministic extraction of high-confidence semantic elements.

    This is NOT a replacement for DeepSeek — it only extracts what can be
    reliably determined via pattern matching. The SemanticReconciler merges
    these results with DeepSeek's richer semantic understanding.
    """

    # ── Negation patterns — generalized lexicon (Phase 9) ──
    # Structured prohibition cues grouped by canonical type.
    # Each cue produces a regex that extracts the prohibition target.

    # Hard negation prefixes (Chinese)
    _NEGATION_PREFIXES_CN = r"不要|别|禁止|不得|不可|不能|勿|请勿|避免|不准|不许|严禁|切勿"
    # Soft/spatial avoidance prefixes
    _AVOIDANCE_PREFIXES_CN = r"避开|绕开|绕过|躲开|远离|小心别"
    # Contact verbs
    _CONTACT_VERBS_CN = r"碰|触碰|接触|撞|蹭|擦到|靠近|碰到|摸|挨"
    # Action verbs (grasp-related)
    _ACTION_VERBS_CN = r"抓|拿|取|碰|握|夹|端|捏|搬"

    NEGATION_PATTERNS = [
        # ── NO_CONTACT: {negation_prefix} + {contact_verb} + target ──
        (NegationType.NO_CONTACT,
         re.compile(rf"(?:{_NEGATION_PREFIXES_CN})\s*(?:{_CONTACT_VERBS_CN})\s*(\S{{1,8}})")),
        # ── AVOID_ENTITY: {avoidance_prefix} + target ──
        (NegationType.AVOID_ENTITY,
         re.compile(rf"(?:{_AVOIDANCE_PREFIXES_CN})\s*(\S{{1,8}})")),
        # ── FORBID_ACTION: {negation_prefix} + {action_verb} + target ──
        (NegationType.FORBID_ACTION,
         re.compile(rf"(?:{_NEGATION_PREFIXES_CN})\s*({_ACTION_VERBS_CN})\s*(\S{{1,8}})")),
        # ── AVOID_REGION: {negation_prefix} + 靠近/接近 + region ──
        (NegationType.AVOID_REGION,
         re.compile(rf"(?:{_NEGATION_PREFIXES_CN})\s*(?:靠近|接近|进入)\s*(\S{{1,8}})")),
        # ── GENERIC: bare {negation_prefix} + target (catch-all) ──
        (NegationType.GENERIC_NEGATION,
         re.compile(rf"(?:{_NEGATION_PREFIXES_CN})\s*(\S{{1,8}})")),
        # ── English patterns ──
        (NegationType.NO_CONTACT,
         re.compile(r"(?:don'?t\s+touch|do\s+not\s+touch|never\s+touch|avoid\s+touching)\s+(\S{1,15})", re.IGNORECASE)),
        (NegationType.AVOID_ENTITY,
         re.compile(r"(?:avoid|stay\s+away\s+from|keep\s+distance\s+from)\s+(\S{1,15})", re.IGNORECASE)),
    ]

    # ── Numeric patterns ──
    NUMERIC_PATTERNS = [
        # MAX: "不超过4N", "最多3N", "不大于5N", "不要超过4N"
        (NumericOperator.MAX,
         re.compile(r"(?:不超过|不要超过|不能超过|最多|至多|<=|小于等于|不大于)\s*(\d+(?:\.\d+)?)\s*(N|牛顿|m/s|kg|cm|mm|m)")),
        # MIN: "至少2N", "不低于1N", "不小于3N", "至少用2N的力"
        (NumericOperator.MIN,
         re.compile(r"(?:至少|不低于|>=|大于等于|不小于)[^\d]{0,5}(\d+(?:\.\d+)?)\s*(N|牛顿|m/s|kg|cm|mm|m)")),
        # RANGE: "2到5N", "1-3N"
        (NumericOperator.RANGE,
         re.compile(r"(\d+(?:\.\d+)?)\s*(?:到|至|-)\s*(\d+(?:\.\d+)?)\s*(N|牛顿|m/s|kg|cm|mm|m)")),
        # EXACT: "用3N", "以2m/s"
        (NumericOperator.EXACT,
         re.compile(r"(?:用|以|力度|力量|速度)\s*(\d+(?:\.\d+)?)\s*(N|牛顿|m/s)")),
    ]

    # ── Condition patterns ──
    CONDITION_PATTERNS = [
        (ConditionConnector.IF_ELSE,
         re.compile(r"如果\s*(.+?)\s*(?:否则|要不|就)\s*(.+?)(?:$|，|。)", re.DOTALL)),
        (ConditionConnector.UNLESS,
         re.compile(r"除非\s*(.+?)\s*否则\s*(.+?)(?:$|，|。)", re.DOTALL)),
        (ConditionConnector.BEFORE,
         re.compile(r"先\s*(.+?)\s*(?:再|然后|之后)\s*(.+?)(?:$|，|。)", re.DOTALL)),
        (ConditionConnector.AFTER,
         re.compile(r"(.+?)\s*(?:之后|以后|然后)\s*再\s*(.+?)(?:$|，|。)", re.DOTALL)),
        (ConditionConnector.WAIT_UNTIL,
         re.compile(r"(?:等待|等到|直到)\s*(.+?)\s*(?:再|才)\s*(.+?)(?:$|，|。)", re.DOTALL)),
    ]

    # ── Sequence patterns ──
    SEQUENCE_PATTERNS = [
        re.compile(r"第[一二三四五六七八九十\d]\s*步[：:]\s*(.+?)(?=第[一二三四五六七八九十\d]\s*步|$)", re.DOTALL),
        re.compile(r"首先\s*(.+?)\s*(?:然后|接着|之后)\s*(.+?)(?:$|，|。)", re.DOTALL),
    ]

    # ── Unit normalization ──
    UNIT_MAP = {
        "N": "N", "牛顿": "N",
        "m/s": "m/s",
        "kg": "kg",
        "cm": "cm", "mm": "mm", "m": "m",
    }

    PARAMETER_FOR_UNIT = {
        "N": "force_n", "牛顿": "force_n",
        "m/s": "velocity_ms",
        "kg": "mass_kg",
        "cm": "distance_cm", "mm": "distance_mm", "m": "distance_m",
    }

    def __init__(self):
        pass

    # ── Public API ──────────────────────────────────────────

    def extract(self, text: str) -> CriticalSemantics:
        """Extract all deterministically recognizable semantic elements."""
        return CriticalSemantics(
            negations=self.extract_negations(text),
            numerics=self.extract_numerics(text),
            conditions=self.extract_conditions(text),
            raw_text=text,
        )

    def extract_negations(self, text: str) -> List[ExtractedNegation]:
        """Extract negation/prohibition mentions."""
        results: List[ExtractedNegation] = []
        covered_spans: set = set()

        for neg_type, pattern in self.NEGATION_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                # Avoid double-counting: prefer specific matches
                if any(s[0] <= span[0] < s[1] or s[0] < span[1] <= s[1] for s in covered_spans):
                    continue
                covered_spans.add(span)

                groups = match.groups()
                if neg_type == NegationType.FORBID_ACTION and len(groups) >= 2:
                    prohibited_action = groups[0]
                    target = groups[1]
                else:
                    prohibited_action = None
                    target = groups[0] if groups else match.group(1) if match.lastindex else ""

                # Clean target: strip trailing particles
                target = re.sub(r"[的了呢吗啊哦]$", "", target.strip())

                if not target or len(target) < 1:
                    continue

                # Infer target description from color/material keywords
                description = {}
                color_map = {"红": "red", "红色": "red", "蓝色的": "blue", "蓝色": "blue",
                           "蓝": "blue", "绿": "green", "绿色": "green", "黄色": "yellow",
                           "黄": "yellow", "白": "white", "白色": "white", "黑": "black",
                           "黑色": "black", "透明": "transparent"}
                for cn, en in color_map.items():
                    if cn in target:
                        description["color"] = en
                        break
                material_map = {"玻璃": "glass", "塑料": "plastic", "金属": "metal",
                              "木头": "wood", "陶瓷": "ceramic", "橡胶": "rubber"}
                for cn, en in material_map.items():
                    if cn in target:
                        description["material"] = en
                        break

                results.append(ExtractedNegation(
                    text_span=match.group(0),
                    type=neg_type,
                    target_mention=target,
                    target_description=description,
                    prohibited_action=prohibited_action,
                    span_start=match.start(),
                    span_end=match.end(),
                ))

        return results

    def extract_numerics(self, text: str) -> List[ExtractedNumeric]:
        """Extract numeric constraints with operators and units."""
        results: List[ExtractedNumeric] = []
        covered_spans: set = set()

        for operator, pattern in self.NUMERIC_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span in covered_spans:
                    continue
                covered_spans.add(span)

                groups = match.groups()
                raw_unit = groups[-1] if groups else ""
                unit = self.UNIT_MAP.get(raw_unit, raw_unit)
                parameter = self.PARAMETER_FOR_UNIT.get(raw_unit, "unknown")

                if operator == NumericOperator.RANGE and len(groups) >= 3:
                    min_val = float(groups[0])
                    max_val = float(groups[1])
                    results.append(ExtractedNumeric(
                        text_span=match.group(0),
                        parameter=parameter,
                        operator=operator,
                        min_value=min_val, max_value=max_val,
                        unit=unit,
                        span_start=match.start(), span_end=match.end(),
                    ))
                elif operator == NumericOperator.MAX:
                    val = float(groups[0])
                    results.append(ExtractedNumeric(
                        text_span=match.group(0),
                        parameter=parameter,
                        operator=operator,
                        value=val, max_value=val,
                        unit=unit,
                        span_start=match.start(), span_end=match.end(),
                    ))
                elif operator == NumericOperator.MIN:
                    val = float(groups[0])
                    results.append(ExtractedNumeric(
                        text_span=match.group(0),
                        parameter=parameter,
                        operator=operator,
                        value=val, min_value=val,
                        unit=unit,
                        span_start=match.start(), span_end=match.end(),
                    ))
                elif operator == NumericOperator.EXACT:
                    val = float(groups[0])
                    results.append(ExtractedNumeric(
                        text_span=match.group(0),
                        parameter=parameter,
                        operator=operator,
                        value=val,
                        unit=unit,
                        span_start=match.start(), span_end=match.end(),
                    ))

        return results

    def extract_conditions(self, text: str) -> List[ExtractedCondition]:
        """Extract conditional/sequential structures."""
        results: List[ExtractedCondition] = []
        covered_spans: set = set()

        for connector, pattern in self.CONDITION_PATTERNS:
            for match in pattern.finditer(text):
                span = (match.start(), match.end())
                if span in covered_spans:
                    continue
                covered_spans.add(span)

                groups = match.groups()
                if connector == ConditionConnector.BEFORE:
                    results.append(ExtractedCondition(
                        text_span=match.group(0),
                        connector=connector,
                        condition_text=groups[0].strip() if len(groups) >= 1 else "",
                        action_text=groups[1].strip() if len(groups) >= 2 else "",
                        span_start=match.start(), span_end=match.end(),
                    ))
                elif connector == ConditionConnector.WAIT_UNTIL:
                    results.append(ExtractedCondition(
                        text_span=match.group(0),
                        connector=connector,
                        condition_text=groups[0].strip() if len(groups) >= 1 else "",
                        action_text=groups[1].strip() if len(groups) >= 2 else "",
                        span_start=match.start(), span_end=match.end(),
                    ))
                elif len(groups) >= 2:
                    results.append(ExtractedCondition(
                        text_span=match.group(0),
                        connector=connector,
                        condition_text=groups[0].strip(),
                        action_text=groups[1].strip(),
                        span_start=match.start(), span_end=match.end(),
                    ))

        # Sequence patterns: 第一步/第二步 or 首先...然后...
        for pattern in self.SEQUENCE_PATTERNS:
            matches = list(pattern.finditer(text))
            if matches:
                steps = []
                for m in matches:
                    g = m.groups()
                    if g:
                        steps.append(g[0].strip())
                if steps:
                    results.append(ExtractedCondition(
                        text_span=text,
                        connector=ConditionConnector.SEQUENCE,
                        steps=steps,
                        span_start=matches[0].start(),
                        span_end=matches[-1].end(),
                    ))

        return results


# ══════════════════════════════════════════════════════════════
# Factory
# ══════════════════════════════════════════════════════════════

_critical_extractor: Optional[CriticalSemanticExtractor] = None


def get_critical_extractor() -> CriticalSemanticExtractor:
    """Get or create the singleton CriticalSemanticExtractor."""
    global _critical_extractor
    if _critical_extractor is None:
        _critical_extractor = CriticalSemanticExtractor()
    return _critical_extractor


def extract_critical_semantics(text: str) -> CriticalSemantics:
    """Convenience function for deterministic extraction."""
    return get_critical_extractor().extract(text)
