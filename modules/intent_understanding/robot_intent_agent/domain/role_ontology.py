"""Role ontology used by action schemas and joint grounding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    description: str
    expected_capabilities: Tuple[str, ...] = ()
    can_share_entity_with: Tuple[str, ...] = ()


ROLE_ONTOLOGY: Dict[str, RoleDefinition] = {
    "theme": RoleDefinition("theme", "被操作的实体", ("graspable", "movable")),
    "source": RoleDefinition("source", "操作的来源位置或容器", ("locatable",), ("destination",)),
    "destination": RoleDefinition("destination", "操作的目标位置或支撑面", ("reachable", "support_surface"), ("source",)),
    "recipient": RoleDefinition("recipient", "接收实体或用户", ("receivable",)),
    "obstacle": RoleDefinition("obstacle", "必须避开的实体", ("locatable",)),
    "support_surface": RoleDefinition("support_surface", "放置时提供支撑的实体", ("support_surface",)),
    "condition": RoleDefinition("condition", "等待或分支使用的状态谓词", ("observable",)),
}
