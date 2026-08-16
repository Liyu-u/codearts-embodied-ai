"""
Constraint Schema — Pydantic v2 数据模型

支持的约束类型:
    - ForceConstraint     : 力约束 (夹持力、接触力)
    - VelocityConstraint  : 速度约束 (末端速度、关节速度)
    - CollisionConstraint : 碰撞约束 (与特定物体保持距离)
    - HeightConstraint    : 高度约束 (Z 轴安全)
    - TemporalConstraint  : 时序约束
    - PreferenceConstraint: 用户偏好约束 (soft)
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Any, Union
from uuid import uuid4

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class ConstraintPriority(str, Enum):
    """约束优先级"""
    HARD = "hard"  # 必须满足 (安全相关, 不可违反)
    SOFT = "soft"  # 尽量满足 (用户偏好, 可降级)


class ConstraintType(str, Enum):
    """约束类型"""
    FORCE = "force"
    VELOCITY = "velocity"
    COLLISION = "collision"
    HEIGHT = "height"
    TEMPORAL = "temporal"
    PREFERENCE = "preference"


# ============================================================
# 具体约束类型
# ============================================================

class ForceConstraint(BaseModel):
    """
    力约束。

    示例:
        "轻一点" → ForceConstraint(max_force_n=3.0, axis="z")
        "用力抓住" → ForceConstraint(min_force_n=5.0, max_force_n=8.0)
    """
    constraint_type: ConstraintType = Field(default=ConstraintType.FORCE, frozen=True)
    priority: ConstraintPriority = Field(default=ConstraintPriority.HARD)
    min_force_n: Optional[float] = Field(default=None, ge=0.0, description="最小力 (N)")
    max_force_n: float = Field(default=10.0, gt=0.0, le=10.0, description="最大力 (N)")
    axis: Optional[str] = Field(default=None, description="约束轴向 (x|y|z|grip)")
    description: str = Field(default="", description="人类可读约束说明")


class VelocityConstraint(BaseModel):
    """
    速度约束。

    示例:
        "慢一点" → VelocityConstraint(max_linear_ms=0.15)
        "动作快些" → VelocityConstraint(max_linear_ms=0.5)
    """
    constraint_type: ConstraintType = Field(default=ConstraintType.VELOCITY, frozen=True)
    priority: ConstraintPriority = Field(default=ConstraintPriority.HARD)
    max_linear_ms: float = Field(default=0.3, gt=0.0, le=1.0, description="最大线速度 (m/s)")
    max_angular_rads: Optional[float] = Field(default=None, gt=0.0, description="最大角速度 (rad/s)")
    description: str = Field(default="", description="人类可读约束说明")


class CollisionConstraint(BaseModel):
    """
    碰撞约束 — 与指定物体保持安全距离。

    示例:
        "别碰玻璃杯" → CollisionConstraint(
            avoid_object="glass_cup",
            min_distance_m=0.05,
        )
    """
    constraint_type: ConstraintType = Field(default=ConstraintType.COLLISION, frozen=True)
    priority: ConstraintPriority = Field(default=ConstraintPriority.HARD)
    avoid_object: str = Field(..., description="避免碰撞的物体 ID/名称")
    min_distance_m: float = Field(default=0.05, gt=0.0, description="最小安全距离 (m)")
    description: str = Field(default="", description="人类可读约束说明")


class HeightConstraint(BaseModel):
    """
    高度约束 — Z 轴安全。

    示例:
        assert z >= 0.02  → HeightConstraint(min_z_m=0.02)
    """
    constraint_type: ConstraintType = Field(default=ConstraintType.HEIGHT, frozen=True)
    priority: ConstraintPriority = Field(default=ConstraintPriority.HARD)
    min_z_m: float = Field(default=0.02, gt=0.0, description="Z 轴最低安全高度 (m)")
    description: str = Field(default="", description="人类可读约束说明")


class TemporalConstraint(BaseModel):
    """
    时序约束。

    示例:
        "先打开夹爪再抓取" → TemporalConstraint(before="open_gripper", after="close_gripper")
    """
    constraint_type: ConstraintType = Field(default=ConstraintType.TEMPORAL, frozen=True)
    priority: ConstraintPriority = Field(default=ConstraintPriority.HARD)
    before: Optional[str] = Field(default=None, description="在此技能之前执行")
    after: Optional[str] = Field(default=None, description="在此技能之后执行")
    description: str = Field(default="", description="人类可读约束说明")


class PreferenceConstraint(BaseModel):
    """
    用户偏好约束 (SOFT)。

    示例:
        "用左手递给我" → PreferenceConstraint(
            key="hand", value="left",
        )
    """
    constraint_type: ConstraintType = Field(default=ConstraintType.PREFERENCE, frozen=True)
    priority: ConstraintPriority = Field(default=ConstraintPriority.SOFT)
    key: str = Field(..., description="偏好键 (hand, speed_label, grip_style...)")
    value: Any = Field(..., description="偏好值")
    description: str = Field(default="", description="人类可读约束说明")


# ============================================================
# 联合类型
# ============================================================

AnyConstraint = Union[
    ForceConstraint,
    VelocityConstraint,
    CollisionConstraint,
    HeightConstraint,
    TemporalConstraint,
    PreferenceConstraint,
]


# ============================================================
# 约束集
# ============================================================

class ConstraintSet(BaseModel):
    """
    约束集合 — Hybrid Constraint Compiler 输出。

    将 Hard 和 Soft 约束分组管理。
    """
    set_id: str = Field(default_factory=lambda: f"cs-{uuid4().hex[:8]}")
    task_id: str = Field(..., description="关联的任务 ID")
    hard_constraints: List[AnyConstraint] = Field(
        default_factory=list, description="硬约束 (必须满足)",
    )
    soft_constraints: List[AnyConstraint] = Field(
        default_factory=list, description="软约束 (尽量满足)",
    )

    @property
    def all_constraints(self) -> List[AnyConstraint]:
        """全部约束"""
        return self.hard_constraints + self.soft_constraints

    def add_hard(self, constraint: AnyConstraint) -> None:
        """添加硬约束"""
        constraint.priority = ConstraintPriority.HARD
        self.hard_constraints.append(constraint)

    def add_soft(self, constraint: AnyConstraint) -> None:
        """添加软约束"""
        constraint.priority = ConstraintPriority.SOFT
        self.soft_constraints.append(constraint)

    def get_by_type(self, constraint_type: ConstraintType) -> List[AnyConstraint]:
        """按类型筛选约束"""
        return [
            c for c in self.all_constraints
            if c.constraint_type == constraint_type
        ]
