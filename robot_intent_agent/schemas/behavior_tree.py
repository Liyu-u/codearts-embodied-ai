"""
Behavior Tree Schema — Pydantic v2 数据模型

支持的节点类型:
    - Sequence   : 顺序执行所有子节点 (全部成功才成功)
    - Fallback   : 选择执行 (任一成功即成功)
    - Parallel   : 并行执行
    - Action     : 原子技能调用
    - Condition  : 前置条件检查
    - Decorator  : 修饰器 (Retry, Timeout, ForceSuccess...)
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Any, Union
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


# ============================================================
# 节点类型枚举
# ============================================================

class BTNodeType(str, Enum):
    """行为树节点类型"""
    SEQUENCE = "sequence"       # → (顺序)
    FALLBACK = "fallback"       # ? (选择)
    PARALLEL = "parallel"       # ⇉ (并行)
    ACTION = "action"           # □ (原子动作)
    CONDITION = "condition"     # ◇ (条件判断)
    DECORATOR = "decorator"     # ◊ (修饰器)


class DecoratorType(str, Enum):
    """修饰器类型"""
    RETRY = "retry"
    TIMEOUT = "timeout"
    FORCE_SUCCESS = "force_success"
    FORCE_FAILURE = "force_failure"
    INVERT = "invert"
    REPEAT = "repeat"


class BTStatus(str, Enum):
    """节点执行状态"""
    SUCCESS = "success"
    FAILURE = "failure"
    RUNNING = "running"
    IDLE = "idle"


# ============================================================
# 技能动作
# ============================================================

class SkillAction(BaseModel):
    """原子技能 — Action 节点负载"""
    skill_name: str = Field(..., description="技能名称 (Reach, Grasp, MoveTo, Release...)")
    target: Optional[str] = Field(default=None, description="目标物体 ID/名称")
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="技能参数 ({force: 3.0, velocity: 0.1, ...})",
    )
    preconditions: List[str] = Field(default_factory=list, description="技能前置条件")
    success_conditions: List[str] = Field(default_factory=list, description="成功判定条件")
    failure_conditions: List[str] = Field(default_factory=list, description="失败判定条件")
    timeout_s: Optional[float] = Field(default=None, description="超时时间 (s)")
    retry_policy: Dict[str, Any] = Field(default_factory=dict, description="重试策略")
    fallback: Optional[str] = Field(default=None, description="失败后的回退技能或策略")
    runtime_safety_guards: List[str] = Field(default_factory=list, description="运行时安全守卫")
    semantic_role: Optional[str] = Field(default=None, description="主题/来源/目的地/接收者等语义角色")


class ConditionCheck(BaseModel):
    """条件检查 — Condition 节点负载"""
    condition: str = Field(..., description="条件表达式 (is_gripper_empty, object_in_view...)")
    target: Optional[str] = Field(default=None, description="检查目标")
    expected: Any = Field(default=True, description="期望值")


# ============================================================
# 行为树节点 (递归)
# ============================================================

class BTNode(BaseModel):
    """
    行为树节点 — 递归结构。

    Sequence 示例:
        BTNode(
            type=BTNodeType.SEQUENCE,
            name="PickAndPlace",
            children=[
                BTNode(type=BTNodeType.ACTION, name="Reach",
                       skill=SkillAction(skill_name="Reach", target="red_bottle")),
                BTNode(type=BTNodeType.ACTION, name="Grasp",
                       skill=SkillAction(skill_name="GentleGrasp", target="red_bottle",
                                         params={"force": 3.0})),
                BTNode(type=BTNodeType.ACTION, name="MoveTo",
                       skill=SkillAction(skill_name="MoveTo", target="user")),
                BTNode(type=BTNodeType.ACTION, name="Release",
                       skill=SkillAction(skill_name="Release")),
            ]
        )
    """
    type: BTNodeType = Field(..., description="节点类型")
    name: str = Field(..., description="节点名称 (可读描述)")
    children: List[BTNode] = Field(
        default_factory=list,
        description="子节点列表 (Sequence/Fallback/Parallel 使用)",
    )
    skill: Optional[SkillAction] = Field(
        default=None, description="技能动作 (仅 Action 节点)",
    )
    condition: Optional[ConditionCheck] = Field(
        default=None, description="条件检查 (仅 Condition 节点)",
    )
    decorator_type: Optional[DecoratorType] = Field(
        default=None, description="修饰器类型 (仅 Decorator 节点)",
    )
    decorator_params: Dict[str, Any] = Field(
        default_factory=dict, description="修饰器参数 ({max_retries: 3, timeout_s: 5.0})",
    )
    status: BTStatus = Field(default=BTStatus.IDLE, description="执行状态")
    annotation: Optional[str] = Field(
        default=None, description="人工可读注释 (解释为什么这样规划)",
    )

    @model_validator(mode="after")
    def validate_node_consistency(self) -> "BTNode":
        """校验节点类型与负载一致性"""
        if self.type in (BTNodeType.SEQUENCE, BTNodeType.FALLBACK, BTNodeType.PARALLEL):
            if not self.children:
                raise ValueError(
                    f"{self.type.value} 节点 '{self.name}' 必须至少有一个子节点"
                )
        if self.type == BTNodeType.ACTION:
            if self.skill is None:
                raise ValueError(
                    f"Action 节点 '{self.name}' 必须提供 skill 字段"
                )
        if self.type == BTNodeType.CONDITION:
            if self.condition is None:
                raise ValueError(
                    f"Condition 节点 '{self.name}' 必须提供 condition 字段"
                )
        return self

    def is_leaf(self) -> bool:
        """是否为叶子节点"""
        return self.type in (BTNodeType.ACTION, BTNodeType.CONDITION)

    def action_count(self) -> int:
        """递归统计 Action 节点数量"""
        count = 1 if self.type == BTNodeType.ACTION else 0
        for child in self.children:
            count += child.action_count()
        return count

    def flatten_actions(self) -> List[SkillAction]:
        """按顺序展开所有 Action 的技能列表"""
        actions: List[SkillAction] = []
        if self.type == BTNodeType.ACTION and self.skill:
            actions.append(self.skill)
        for child in self.children:
            actions.extend(child.flatten_actions())
        return actions


# ============================================================
# 行为树 (顶层)
# ============================================================

class BehaviorTree(BaseModel):
    """
    完整行为树 — Task Planner 输出。

    包含:
        - 根节点 (BTNode 递归树)
        - 元数据 (任务 ID、规划时间...)
    """
    tree_id: str = Field(default_factory=lambda: f"bt-{uuid4().hex[:8]}")
    task_id: str = Field(..., description="关联的任务 ID")
    description: str = Field(default="", description="任务自然语言描述")
    root: BTNode = Field(..., description="行为树根节点")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="规划元数据")
