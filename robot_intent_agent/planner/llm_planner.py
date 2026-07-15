"""
LLM Planner — LLM 规划器接口 (预留)

当前实现: Mock (返回空 BehaviorTree)
未来实现: 调用 LLM 将 instruction + scene + memory → BehaviorTree JSON
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNode,
    BTNodeType,
    BTStatus,
    SkillAction,
)

from .base import TaskPlannerInterface


class LLMPlanner(TaskPlannerInterface):
    """
    LLM 规划器 (预留接口)。

    当前: Mock — 返回空 BT，提示"LLM planner not yet implemented"
    未来: 调用 CodeArts / GPT-4o 生成完整 BehaviorTree JSON

    预期 Prompt:
        [System]
        You are a robot task planner.
        Given: user instruction + scene graph + memory context
        Output: BehaviorTree JSON (conforming to behavior_tree.json schema)
        Do NOT generate Python code — generate task logic only.
    """

    def __init__(self, model: str = "gpt-4o"):
        self._model = model

    @property
    def name(self) -> str:
        return f"LLMPlanner({self._model})"

    def plan(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> BehaviorTree:
        """
        [Mock] LLM 规划 — 返回占位行为树。

        未来实现:
            1. 构造 prompt (instruction + scene_graph.json + memory_items)
            2. 调用 LLM API
            3. 解析返回的 BehaviorTree JSON
            4. 验证符合 behavior_tree.json schema
            5. 返回 BehaviorTree 实例
        """
        # TODO: 接入 LLM
        placeholder_action = BTNode(
            type=BTNodeType.ACTION,
            name="LLM_PLACEHOLDER_ACTION",
            skill=SkillAction(
                skill_name="Inspect",
                target="placeholder",
                params={"check_type": "status"},
            ),
            annotation=f"LLM Planner ({self._model}) not yet implemented",
        )
        root = BTNode(
            type=BTNodeType.SEQUENCE,
            name="LLM_PLANNER_PLACEHOLDER",
            children=[placeholder_action],
            annotation=(
                f"LLM Planner ({self._model}) not yet implemented. "
                f"Use RuleBasedPlanner for now. "
                f"Instruction: {instruction[:80]}"
            ),
        )

        return BehaviorTree(
            task_id="task-llm-placeholder",
            description=instruction,
            root=root,
            metadata={
                "planner": self.name,
                "status": "mock",
                "instruction": instruction,
            },
        )
