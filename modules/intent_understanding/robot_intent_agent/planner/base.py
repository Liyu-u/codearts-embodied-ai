"""
Task Planner Interface — 抽象规划器接口

所有 Planner 实现必须遵循此接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.behavior_tree import BehaviorTree


class TaskPlannerInterface(ABC):
    """
    任务规划器抽象接口。

    输入:
        instruction     : 用户自然语言指令
        scene           : 语义场景图
        memory_context  : MemoryRetriever 输出的记忆列表

    输出:
        BehaviorTree    : 可执行行为树 (不含 Python 代码)
    """

    @abstractmethod
    def plan(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> BehaviorTree:
        """
        规划行为树。

        Args:
            instruction:    用户自然语言指令
            scene:          语义场景图 (场景物体 + 空间关系)
            memory_context: 记忆搜索结果列表 (来自 MemoryRetriever.search)

        Returns:
            BehaviorTree (根节点为 Sequence)
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """规划器名称"""
        ...
