"""
Robot Task IR Schema — Pydantic v2 数据模型

这是整个意图理解管道的最终输出:
    NL + Scene + Memory → Robot Task IR

CodeArts 未来将直接解析此 JSON 生成 Python 控制代码。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .scene import SemanticSceneGraph
from .behavior_tree import BehaviorTree
from .constraint import ConstraintSet


# ============================================================
# 任务元数据
# ============================================================

class TaskMetadata(BaseModel):
    """任务元数据"""
    task_id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:8]}")
    raw_instruction: str = Field(..., description="用户原始自然语言指令")
    language: str = Field(default="zh", description="语言 (zh|en)")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="创建时间 (ISO 8601)",
    )
    user_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="用户上下文 (偏好、历史...)",
    )


# ============================================================
# 前置条件断言
# ============================================================

class PreconditionAssertion(BaseModel):
    """单个前置条件"""
    assertion: str = Field(..., description="断言表达式")
    description: str = Field(default="", description="人类可读说明")


class PreconditionSet(BaseModel):
    """
    前置条件集合。

    在执行行为树之前必须全部满足。
    """
    assertions: List[PreconditionAssertion] = Field(default_factory=list)

    def add(self, assertion: str, description: str = "") -> None:
        self.assertions.append(
            PreconditionAssertion(assertion=assertion, description=description)
        )


# ============================================================
# 优化空间
# ============================================================

class OptimizationTarget(str):
    """优化目标枚举"""
    MIN_TIME = "min_time"
    MIN_ENERGY = "min_energy"
    MAX_SAFETY = "max_safety"
    MAX_SMOOTHNESS = "max_smoothness"


class OptimizationSpace(BaseModel):
    """
    下游优化器可调节的参数空间。

    CodeArts / TraceCoder 可以在这些边界内微调。
    """
    force_range_n: tuple = Field(default=(1.0, 10.0), description="力范围 (min, max) N")
    velocity_range_ms: tuple = Field(default=(0.05, 0.3), description="速度范围 (min, max) m/s")
    z_safe_margin_m: tuple = Field(default=(0.02, 0.10), description="Z 安全边距范围 (m)")
    collision_margin_m: tuple = Field(default=(0.03, 0.15), description="碰撞边距范围 (m)")
    targets: List[str] = Field(default_factory=list, description="优化目标优先级")
    free_params: Dict[str, Any] = Field(
        default_factory=dict,
        description="扩展可调参数",
    )


# ============================================================
# Robot Task IR (顶层)
# ============================================================

class RobotTaskIR(BaseModel):
    """
    机器人任务中间表示 (Intermediate Representation)。

    这是整个意图理解管道的最终输出 Schema。

    架构:
        ir_version              → 版本号 (CodeArts 兼容性)
        task_metadata           → 任务元数据
        precondition_assertions → 前置条件 (执行前必须全部满足)
        scene                   → 语义场景图
        behavior_tree           → 可执行行为树
        skills                  → 技能映射表
        compiled_constraints    → 编译后的约束集
        optimization_space      → 下游优化边界
    """
    ir_version: str = Field(
        default="1.0.0",
        description="IR 版本号 (兼容性标识)",
    )
    task_metadata: TaskMetadata = Field(..., description="任务元数据")
    precondition_assertions: PreconditionSet = Field(
        default_factory=PreconditionSet,
        description="前置条件断言",
    )
    scene: Optional[SemanticSceneGraph] = Field(
        default=None, description="语义场景图",
    )
    behavior_tree: BehaviorTree = Field(..., description="行为树")
    skills: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="技能映射表 (skill_name → {params, constraints, ...})",
    )
    compiled_constraints: ConstraintSet = Field(
        default_factory=lambda: ConstraintSet(task_id="pending"),
        description="编译后约束集",
    )
    optimization_space: OptimizationSpace = Field(
        default_factory=OptimizationSpace,
        description="可调优参数空间",
    )
    memory_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Memory 模块注入的上下文",
    )

    def model_post_init(self, __context: Any) -> None:
        """初始化后自动同步 task_id"""
        if self.compiled_constraints.task_id == "pending":
            self.compiled_constraints.task_id = self.task_metadata.task_id
        if self.behavior_tree.task_id == "":
            self.behavior_tree.task_id = self.task_metadata.task_id

    def summary(self) -> str:
        """生成人类可读摘要"""
        actions = self.behavior_tree.root.flatten_actions()
        hard_count = len(self.compiled_constraints.hard_constraints)
        soft_count = len(self.compiled_constraints.soft_constraints)

        lines = [
            f"Task IR: {self.task_metadata.task_id}",
            f"  Instruction: {self.task_metadata.raw_instruction}",
            f"  Actions: {' → '.join(a.skill_name for a in actions)}",
            f"  Hard Constraints: {hard_count}",
            f"  Soft Constraints: {soft_count}",
            f"  Objects: {len(self.scene.objects) if self.scene else 0}",
        ]
        return "\n".join(lines)
