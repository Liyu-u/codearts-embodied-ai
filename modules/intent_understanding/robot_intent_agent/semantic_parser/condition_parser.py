"""Condition and temporal connector extraction."""

from __future__ import annotations

import re
from typing import List

from robot_intent_agent.schemas.semantic_task_graph import EvidenceSpan, SemanticCondition


def parse_conditions(instruction: str) -> List[SemanticCondition]:
    text = instruction or ""
    patterns = (
        (r"(?:先不动作|先别动作|先观察一会儿|保持当前状态|让系统)?\s*[,，、]?\s*(?:等到|等待|等|待)\s*((?:目标|场景|工位|传送过程|运动)(?:还有位移|完全)?)(?:静止|停止|稳定|不再变化|结束|完成)", "WAIT_UNTIL"),
        (r"((?:目标|场景|工位|传送过程))\s*(?:还有位移|未停止|在移动)\s*时\s*(?:暂缓操作|先不动作|等待)", "WAIT_UNTIL"),
        (r"(?:在|到)\s*((?:目标|场景|工位))\s*(?:稳住|静止|停止|稳定)以前\s*(?:不要开始|暂缓|延后)", "WAIT_UNTIL"),
        (r"保持等待?直到(.{1,30}?)(?:停止|稳定|完成|移动结束)", "WAIT_UNTIL"),
        (r"(?:等到|等待|等)(.{1,30}?)(?:停止|稳定|完成|移动结束)(?:.*?(?:再|然后|之后))?", "WAIT_UNTIL"),
        (r"直到(.{1,30}?)(?:停止|稳定|完成|移动结束)", "WAIT_UNTIL"),
        (r"如果(.{1,30}?)[，,].*?(?:就|则)", "IF"),
        (r"除非(.{1,30}?)[，,]", "UNLESS"),
    )
    patterns = patterns + (
        (r"(?:先不动作|先观察一会儿|保持当前状态|让系统)?\s*[,，、]?\s*(?:等到|等待|等|待)\s*((?:目标|场景|工位|传送过程|运动)(?:还有位移|完全)?)(?:静止|停止|稳定|不再变化|结束|完成)", "WAIT_UNTIL"),
        (r"((?:目标|场景|工位|传送过程))\s*(?:还有位移|未停止|在移动|稳住)\s*时\s*(?:暂缓操作|先不动作|等待)", "WAIT_UNTIL"),
        (r"(?:在|到)\s*((?:目标|场景|工位))\s*(?:稳住|静止|停止|稳定)以前\s*(?:不要开始|暂缓|延后)", "WAIT_UNTIL"),
        (r"(?:保持当前状态|暂时保持等待|继续保持不动|先暂停|待场景恢复稳定后继续|把后续动作延后到目标静止|等移动状态消失后再处理)", "WAIT_UNTIL"),
        # Open wording families for the fixed WAIT template.
        (r"(?:目标|场景|工位|传送过程|工件|运动状态)[^，。；,;]{0,16}(?:还在变化|未停止|在移动|不再晃动|运动结束|结束后)"
         r"[^，。；,;]{0,12}(?:保持等待|等待|再继续|继续|再处理)", "WAIT_UNTIL"),
        (r"(?:保持等待|等待|暂缓操作|先别动作|先不动作)[^，。；,;]{0,24}"
         r"(?:现场|场景|工位|目标|工件|运动状态)[^，。；,;]{0,16}"
         r"(?:不再变化|停止|静止|稳定|结束|完成)", "WAIT_UNTIL"),
        (r"(?:等|待)[^，。；,;]{0,24}(?:工位|工件|目标|现场|传送过程)[^，。；,;]{0,12}"
         r"(?:不再晃动|运动结束|停止|稳定|完成)后(?:再继续|继续|再处理)?", "WAIT_UNTIL"),
        (r"先让[^，。；,;]{0,20}运动状态[^，。；,;]{0,10}(?:消失|停止|结束)[^，。；,;]{0,10}(?:再|然后)", "WAIT_UNTIL"),
        (r"先不要执行动作[^，。；,;]{0,12}(?:等|待)[^，。；,;]{0,24}(?:停止|静止|稳定|停下|结束)", "WAIT_UNTIL"),
        (r"(?:把)?后续动作延后到[^，。；,;]{0,24}(?:停止位移|停止|静止|稳定|结束)", "WAIT_UNTIL"),
        (r"(?:继续等候|继续等待)[^，。；,;]{0,12}(?:到|直到)[^，。；,;]{0,20}(?:停止|静止|稳定|不再变化|结束)", "WAIT_UNTIL"),
        (r"先观察一会儿[^，。；,;]{0,12}(?:待|等)[^，。；,;]{0,20}(?:停下|停止|静止|稳定)", "WAIT_UNTIL"),
        (r"目标尚未稳住[^，。；,;]{0,12}(?:暂时不要开始|先不要开始|暂缓操作)", "WAIT_UNTIL"),
        (r"(?:待|等)[^。]{0,36}(?:停止运动|运动停止|运动结束)[^。]{0,12}(?:后继续|继续|再进行)", "WAIT_UNTIL"),
    )
    result: List[SemanticCondition] = []
    for pattern, predicate in patterns:
        for match in re.finditer(pattern, text):
            span = match.group(0)
            evidence = EvidenceSpan(value=span, source_text=text, start=match.start(),
                                    end=match.end(), confidence=0.95, rule_id="condition.connector")
            value = match.group(1).strip() if match.lastindex else span
            result.append(SemanticCondition(
                condition_id=f"condition-{match.start()}", predicate=predicate,
                value=value, evidence_span=span, evidence=[evidence],
            ))

    # Linear temporal commands are represented as graph ordering relations,
    # but retaining the connector as a condition makes the downstream audit
    # aware that the command is composite.  Do not turn ordinary “then” into
    # a runtime predicate; use a neutral SEQUENCE marker instead.
    sequence_match = re.search(
        r"(?:先|第一步|首先).{1,80}(?:然后|再|之后|接着|随后|第二步)", text
    )
    if sequence_match and not any(item.predicate == "SEQUENCE" for item in result):
        start, end = sequence_match.span()
        evidence = EvidenceSpan(value=sequence_match.group(0), source_text=text,
                                start=start, end=end, confidence=0.95,
                                rule_id="condition.sequence")
        result.append(SemanticCondition(
            condition_id=f"condition-sequence-{start}", predicate="SEQUENCE",
            value="ordered", evidence_span=sequence_match.group(0), evidence=[evidence],
        ))

    # English counterparts used by the same domain contract.
    english_sequence = re.search(r"\b(first|then|after that|next)\b.*\b(then|after that|next)\b", text, re.IGNORECASE)
    if english_sequence and not any(item.predicate == "SEQUENCE" for item in result):
        start, end = english_sequence.span()
        evidence = EvidenceSpan(value=english_sequence.group(0), source_text=text,
                                start=start, end=end, confidence=0.95,
                                rule_id="condition.sequence.en")
        result.append(SemanticCondition(
            condition_id=f"condition-sequence-{start}", predicate="SEQUENCE",
            value="ordered", evidence_span=english_sequence.group(0), evidence=[evidence],
        ))

    # Preserve both branches as semantic data.  The deterministic BT compiler
    # may later choose a runtime fallback, but parsing must never collapse an
    # IF/ELSE command to only its first verb.
    branch_match = re.search(
        r"如果\s*(?P<condition>.+?)\s*(?:就|则)\s*(?P<true>.+?)\s*"
        r"(?:否则|不然)\s*(?P<false>[^，。；,;]+)", text
    )
    if branch_match:
        true_text = branch_match.group("true").strip(" ，,")
        false_text = branch_match.group("false").strip()
        existing = next((item for item in result if item.predicate == "IF"), None)
        if existing is None:
            start, end = branch_match.span()
            evidence = EvidenceSpan(value=branch_match.group(0), source_text=text,
                                    start=start, end=end, confidence=0.98,
                                    rule_id="condition.if_else")
            existing = SemanticCondition(
                condition_id=f"condition-{start}", predicate="IF",
                value=branch_match.group("condition").strip(),
                evidence_span=branch_match.group(0), evidence=[evidence],
            )
            result.append(existing)
        existing.on_true_text = true_text
        existing.on_false_text = false_text
        existing.on_true_action = _action_from_text(true_text)
        existing.on_false_action = _action_from_text(false_text)
    wait_family = re.search(
        r"(?:先别动作|先不要执行动作|先不动作|先暂停|暂缓操作|先观察一会儿|"
        r"后续动作延后到|目标尚未稳住|继续等候|继续等待|保持等待|"
        r"先让运动状态消失)[^。]{0,60}"
        r"(?:停止|停下|静止|稳定|恢复稳定|不再变化|结束|完成|不要开始|再进行)",
        text,
    )
    if wait_family and not any(
            item.predicate == "WAIT_UNTIL" and item.evidence_span == wait_family.group(0)
            for item in result
    ):
        start, end = wait_family.span()
        evidence = EvidenceSpan(value=wait_family.group(0), source_text=text,
                                start=start, end=end, confidence=0.95,
                                rule_id="condition.wait.family")
        result.append(SemanticCondition(
            condition_id=f"condition-wait-family-{start}", predicate="WAIT_UNTIL",
            value=wait_family.group(0), evidence_span=wait_family.group(0), evidence=[evidence],
        ))
    # Overlapping WAIT_UNTIL patterns can describe the same clause (for
    # example “保持等待直到场景稳定”).  Keep one graph atom per span so the
    # downstream WAIT action does not receive duplicate conditions.
    unique: List[SemanticCondition] = []
    seen = set()
    for item in result:
        key = (item.predicate, item.evidence_span or str(item.value))
        if key not in seen:
            unique.append(item)
            seen.add(key)
    return unique


def _action_from_text(text: str) -> str:
    if re.search(r"递给|交给|给我|送给", text):
        return "HANDOVER"
    if re.search(r"放到|放在|放入|放进", text):
        return "PLACE"
    if re.search(r"拿过来|取过来|抓过来", text):
        return "FETCH"
    if re.search(r"抓|拿|取|夹", text):
        return "GRASP"
    if re.search(r"移到|搬运|转移|上料", text):
        return "TRANSFER"
    return "CUSTOM"
