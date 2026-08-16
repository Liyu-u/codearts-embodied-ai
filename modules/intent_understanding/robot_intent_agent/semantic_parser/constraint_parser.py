"""Evidence-preserving numeric constraint extraction.

The parser emits one semantic atom per user expression.  It deliberately
recognizes the operator before falling back to an exact value, so phrases such
as ``劲儿别超过 3N`` cannot be emitted as both MAX and EXACT.
"""

from __future__ import annotations

import re
from typing import List

from robot_intent_agent.schemas.semantic_task_graph import EvidenceSpan, SemanticConstraint


_NUMBER = r"\d+(?:\.\d+)?"
_FORCE_UNIT = r"(?:N|牛顿?|newtons?)"
_VELOCITY_UNIT = r"(?:m\s*/\s*s|米\s*/?\s*秒|米每秒)"


def _constraint(
    text: str,
    match: re.Match[str],
    parameter: str,
    operator: str,
    value: float | None = None,
    min_value: float | None = None,
    max_value: float | None = None,
    hard: bool = True,
    rule_id: str = "constraint.numeric",
) -> SemanticConstraint:
    unit = "N" if parameter == "force_n" else "m/s"
    evidence = EvidenceSpan(
        value=match.group(0), source_text=text, start=match.start(), end=match.end(),
        confidence=1.0 if hard else 0.9, rule_id=rule_id,
    )
    return SemanticConstraint(
        constraint_id=f"constraint-{parameter}-{match.start()}-{operator.lower()}",
        parameter=parameter,
        operator=operator,
        value=value,
        min_value=min_value,
        max_value=max_value,
        unit=unit,
        evidence_span=match.group(0),
        evidence=[evidence],
        hard=hard,
    )


def parse_constraints(instruction: str) -> List[SemanticConstraint]:
    text = (instruction or "").replace("Ｎ", "N")
    result: List[SemanticConstraint] = []
    occupied: List[tuple[int, int]] = []

    def add(match: re.Match[str], item: SemanticConstraint) -> None:
        span = (match.start(), match.end())
        # A scoped match owns its number.  Do not let a broad exact fallback
        # claim the same numeric span afterward.
        if any(not (span[1] <= left or span[0] >= right) for left, right in occupied):
            return
        occupied.append(span)
        result.append(item)

    # Canonical Chinese upper-bound forms are parsed before the permissive
    # exact-value fallback.  The operator is semantic evidence: "以内" and
    # "不要超过" mean MAX, never EXACT.
    canonical_max_patterns = (
        (rf"(?:\u7528\u529b|\u529b\u91cf|\u6293\u529b|\u5939\u6301\u529b)?\s*(?:\u4e0d\u8981\u8d85\u8fc7|\u4e0d\u8d85\u8fc7|\u4e0d\u5927\u4e8e|\u4e0d\u9ad8\u4e8e|\u81f3\u591a|\u6700\u591a)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}", "force_n"),
        (rf"(?:\u901f\u5ea6|\u901f\u7387)\s*(?:\u4fdd\u6301\u5728|\u63a7\u5236\u5728|\u7ef4\u6301\u5728)?\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}\s*(?:\u4ee5\u5185|\u4ee5\u4e0b|\u4e4b\u5185)", "velocity_ms"),
        (rf"(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}\s*(?:\u4ee5\u5185|\u4ee5\u4e0b|\u4e4b\u5185)", "velocity_ms"),
    )
    for pattern, parameter in canonical_max_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            add(match, _constraint(text, match, parameter, "MAX",
                                   value=float(match.group("v")),
                                   max_value=float(match.group("v")),
                                   rule_id="constraint.numeric.canonical_max"))

    # Ranges are checked first because they contain both a lower and upper
    # number and must remain one RANGE atom.
    range_patterns = (
        (rf"(?P<lo>{_NUMBER})\s*(?:到|至|~|～|-)\s*(?P<hi>{_NUMBER})\s*{_FORCE_UNIT}", "force_n"),
        (rf"(?:force|力|力量|抓力|夹持力)\s*(?P<lo>{_NUMBER})\s*(?:到|至|~|～|-)\s*(?P<hi>{_NUMBER})\s*{_FORCE_UNIT}", "force_n"),
        (rf"(?P<lo>{_NUMBER})\s*(?:到|至|~|～|-)\s*(?P<hi>{_NUMBER})\s*{_VELOCITY_UNIT}", "velocity_ms"),
    )
    for pattern, parameter in range_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            add(match, _constraint(text, match, parameter, "RANGE",
                                   min_value=float(match.group("lo")),
                                   max_value=float(match.group("hi"))))

    # Explicit upper/lower bounds.  Include operator-first and parameter-first
    # Chinese, colloquial, symbolic, and English forms.
    max_patterns = (
        (rf"(?:不超过|不大于|不高于|最多|至多|最大|<=|≤)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}(?:\s*(?:的)?\s*(?:力|力量|劲儿|力气|抓力|夹持力|force))?", "force_n"),
        (rf"(?:抓力|夹持力|握力|力量|劲儿|力气|force)\s*(?:别超过|不要超过|不得超过|不能超过|不应超过|不要|不得|不能|不应|不超过|不大于|最多|至多|最大|<=|≤)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}", "force_n"),
        (rf"(?:不超过|不大于|不高于|最多|至多|最大|<=|≤)\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}", "velocity_ms"),
        (rf"(?:速度|velocity)\s*(?:不超过|不大于|最多|至多|<=|≤)\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}", "velocity_ms"),
        (rf"(?:力|力量|劲儿|力气|抓力|夹持力)\s*(?:的)?\s*(?:上限|最大值|最大)\s*(?:是|为|=)?\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}", "force_n"),
    )
    min_patterns = (
        (rf"(?:至少|不低于|不小于|最少|>=|≥)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}(?:\s*(?:的)?\s*(?:力|力量|劲儿|力气|抓力|夹持力|force))?", "force_n"),
        (rf"(?:力|力量|抓力|夹持力|force)\s*(?:至少|不低于|不小于|最少|>=|≥)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}", "force_n"),
        (rf"(?:至少|不低于|不小于|最少|>=|≥)\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}", "velocity_ms"),
        (rf"(?:速度|velocity)\s*(?:至少|不低于|不小于|最少|>=|≥)\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}", "velocity_ms"),
    )
    for pattern, parameter in max_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            add(match, _constraint(text, match, parameter, "MAX",
                                   value=float(match.group("v")),
                                   max_value=float(match.group("v"))))
    # Strong colloquial upper bounds used by the legacy reasoning fixtures.
    absolute_max = rf"(?:\u7edd\u4e0d\u80fd\u8d85\u8fc7|\u7edd\u5bf9\u4e0d\u80fd\u8d85\u8fc7|\u7edd\u4e0d\u80fd\u5927\u4e8e|\u7edd\u5bf9\u4e0d\u80fd\u5927\u4e8e)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}"
    for match in re.finditer(absolute_max, text, re.IGNORECASE):
        add(match, _constraint(text, match, "force_n", "MAX",
                               value=float(match.group("v")),
                               max_value=float(match.group("v"))))
    for pattern, parameter in min_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            add(match, _constraint(text, match, parameter, "MIN",
                                   value=float(match.group("v")),
                                   min_value=float(match.group("v"))))

    # Exact values are deliberately last.  A number already covered by a
    # bound/range cannot become EXACT.
    exact_patterns = (
        (rf"(?:用|以|with|at)\s*(?P<v>{_NUMBER})\s*{_FORCE_UNIT}(?:\s*(?:的)?\s*(?:力|力量|劲儿|力气|抓力|夹持力|force))?", "force_n"),
        (rf"(?<![\d.])(?P<v>{_NUMBER})\s*{_FORCE_UNIT}\s*(?:的)?\s*(?:力|力量|劲儿|力气|抓力|夹持力|force)", "force_n"),
        (rf"(?:用|以|with|at)?\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}\s*(?:的)?\s*(?:速度|velocity)?", "velocity_ms"),
        (rf"(?:速度|velocity)\s*(?:为|是|=|at)?\s*(?P<v>{_NUMBER})\s*{_VELOCITY_UNIT}", "velocity_ms"),
    )
    for pattern, parameter in exact_patterns:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            add(match, _constraint(text, match, parameter, "EXACT",
                                   value=float(match.group("v")),
                                   min_value=float(match.group("v")),
                                   max_value=float(match.group("v"))))

    # Soft manner constraints remain non-hard recommendations.
    for pattern, parameter, value, unit in (
        (r"轻轻|轻一点|温柔|gentle|gently", "force_n", 3.0, "N"),
        (r"慢一点|慢些|slow|careful", "velocity_ms", 0.10, "m/s"),
        (r"快一点|快些|faster", "velocity_ms", 0.25, "m/s"),
    ):
        for match in re.finditer(pattern, text, re.IGNORECASE):
            # Manner spans do not overlap a user numeric atom in normal input.
            result.append(_constraint(text, match, parameter, "MAX", value=value,
                                      max_value=value, hard=False,
                                      rule_id="constraint.modifier"))

    unique = {}
    for item in result:
        key = (item.parameter, item.operator, item.value, item.min_value, item.max_value)
        unique[key] = item
    return sorted(unique.values(), key=lambda item: item.evidence[0].start if item.evidence else 0)
