"""领域受限语义编译器的能力定义。

领域模块只定义“允许表达什么、动作需要什么”，不负责从自然语言猜测
实体 ID 或执行状态。这样所有上游候选都可以经过同一个确定性契约检查。
"""

from .action_schemas import ACTION_SCHEMAS, ActionSchema, get_action_schema
from .role_ontology import ROLE_ONTOLOGY, RoleDefinition
from .industrial_ontology import INDUSTRIAL_EVENT_TEMPLATES, IndustrialEventTemplate
from .relation_ontology import SUPPORTED_RELATIONS

__all__ = [
    "ACTION_SCHEMAS", "ActionSchema", "get_action_schema",
    "ROLE_ONTOLOGY", "RoleDefinition",
    "INDUSTRIAL_EVENT_TEMPLATES", "IndustrialEventTemplate",
    "SUPPORTED_RELATIONS",
]
