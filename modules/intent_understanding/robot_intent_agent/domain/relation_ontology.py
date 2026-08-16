"""Closed relation vocabulary for semantic graph validation."""

from __future__ import annotations

from typing import Dict, Tuple


SUPPORTED_RELATIONS = {
    "BEFORE", "AFTER", "DURING", "IF_TRUE", "IF_FALSE",
    "LEFT", "RIGHT", "LEFTMOST", "RIGHTMOST", "FRONT", "BEHIND",
    "ABOVE", "BELOW", "NEAR", "FAR", "ADJACENT", "MIDDLE",
    "FIRST", "SECOND", "RELATIVE_TO", "CONTAINS", "ON", "IN",
    "AVOID", "NO_CONTACT",
}

RELATION_ALIASES: Dict[str, str] = {
    "左": "LEFT", "左边": "LEFT", "左侧": "LEFT", "最左": "LEFTMOST",
    "右": "RIGHT", "右边": "RIGHT", "右侧": "RIGHT", "最右": "RIGHTMOST",
    "前": "FRONT", "前面": "FRONT", "后": "BEHIND", "后面": "BEHIND",
    "上": "ABOVE", "上面": "ABOVE", "下": "BELOW", "下面": "BELOW",
    "附近": "NEAR", "靠近": "NEAR", "旁边": "ADJACENT", "中间": "MIDDLE",
    "第一个": "FIRST", "第二个": "SECOND", "避开": "AVOID", "不要碰": "NO_CONTACT",
}


def normalize_relation(value: str) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    if upper in SUPPORTED_RELATIONS:
        return upper
    return RELATION_ALIASES.get(text, upper if upper in SUPPORTED_RELATIONS else "RELATIVE_TO")
