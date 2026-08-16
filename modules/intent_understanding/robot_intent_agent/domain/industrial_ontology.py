"""Industrial event templates.

Templates are semantic evidence/role hints only.  Final role validity always
comes from :mod:`action_schemas`, so a synonym cannot introduce a forbidden
role such as ``recipient`` for a transfer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

from .action_schemas import normalize_action


@dataclass(frozen=True)
class IndustrialEventTemplate:
    name: str
    action: str
    trigger_phrases: Tuple[str, ...]
    role_hints: Dict[str, str]


INDUSTRIAL_EVENT_TEMPLATES = (
    IndustrialEventTemplate("load_to_inspection", "TRANSFER", ("上料到", "送到检测区", "移到检测区"),
                            {"theme": "工件", "destination": "检测区"}),
    IndustrialEventTemplate("station_to_bin", "TRANSFER", ("从工位移到料箱", "工位移到料箱"),
                            {"source": "工位", "destination": "料箱", "theme": "工件"}),
    IndustrialEventTemplate("place_in_bin", "PLACE", ("放入周转箱", "放进料箱", "放到托盘"),
                            {"destination": "周转箱"}),
    IndustrialEventTemplate("handover_to_operator", "HANDOVER", ("递交给操作员", "交给操作员"),
                            {"recipient": "操作员"}),
    IndustrialEventTemplate("wait_for_conveyor", "WAIT", ("等传送带停止", "传送带停止再"),
                            {"condition": "传送带停止"}),
    IndustrialEventTemplate("avoid_fixture", "TRANSFER", ("避开夹具", "绕开夹具"),
                            {"obstacle": "夹具"}),
)


def match_industrial_templates(instruction: str) -> list[IndustrialEventTemplate]:
    text = instruction or ""
    return [template for template in INDUSTRIAL_EVENT_TEMPLATES
            if any(phrase in text for phrase in template.trigger_phrases)]
