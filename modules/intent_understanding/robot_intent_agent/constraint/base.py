"""
Constraint Graph Foundation — 约束图基础设施

Constraint Graph 是 PDDL / SayCan 风格的约束声明表示。

与 BehaviorTree 的关系:
    BehaviorTree  = "机器人应该做什么"  (HOW)
    ConstraintGraph = "机器人必须满足什么条件才能做" (CONSTRAINTS)

每条约束绑定到 BehaviorTree 的特定节点:
    BTNode: "Grasp(药瓶)"
        ├── PhysicalConstraint: force ∈ (0, 3.0] N
        ├── CollisionConstraint: distance(cup) >= 0.05 m
        └── SafetyConstraint:   z >= 0.02 m
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


# ============================================================
# Enums
# ============================================================

class ConstraintCategory(str, Enum):
    """约束类别"""
    SPATIAL = "spatial"        # 空间约束 (距离、区域、轨迹)
    PHYSICAL = "physical"      # 物理约束 (力、速度、高度)
    SAFETY = "safety"          # 安全约束 (硬红线)
    TEMPORAL = "temporal"      # 时序约束 (先后顺序)
    INTERACTION = "interaction" # 人机交互约束


class ConstraintPriority(str, Enum):
    """约束优先级"""
    HARD = "hard"    # 不可违反 (安全)
    SOFT = "soft"    # 尽量满足 (偏好)


class ConstraintStatus(str, Enum):
    """约束满足状态"""
    PENDING = "pending"
    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNKNOWN = "unknown"


# ============================================================
# Constraint Node (single constraint)
# ============================================================

@dataclass
class ConstraintNode:
    """
    单条可执行约束。

    示例:
        ConstraintNode(
            id="c-001",
            category=ConstraintCategory.PHYSICAL,
            constraint_type="force_limit",
            target="红色药瓶",
            applies_to_skill="Grasp",
            expression="force_n <= 3.0",
            params={"force_n": {"max": 3.0}},
            priority=ConstraintPriority.HARD,
            description="Grasp force must not exceed 3.0N",
        )
    """
    id: str = field(default_factory=lambda: f"c-{uuid4().hex[:8]}")
    category: ConstraintCategory = ConstraintCategory.SAFETY
    constraint_type: str = ""                  # force_limit, velocity_limit, collision_avoid...
    target: str = ""                            # 目标物体/位置
    applies_to_skill: str = ""                  # 绑定的 BT 技能名
    expression: str = ""                        # 人类可读: "force_n <= 3.0"
    params: Dict[str, Any] = field(default_factory=dict)
    priority: ConstraintPriority = ConstraintPriority.HARD
    description: str = ""
    status: ConstraintStatus = ConstraintStatus.PENDING

    # 验证函数 (可注入)
    check_fn: Optional[callable] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "constraint_type": self.constraint_type,
            "target": self.target,
            "applies_to_skill": self.applies_to_skill,
            "expression": self.expression,
            "params": self.params,
            "priority": self.priority.value,
            "description": self.description,
            "status": self.status.value,
        }

    def evaluate(self, state: Dict[str, Any]) -> ConstraintStatus:
        """评估约束在当前状态下是否满足"""
        if self.check_fn:
            try:
                ok = self.check_fn(state)
                self.status = ConstraintStatus.SATISFIED if ok else ConstraintStatus.VIOLATED
                return self.status
            except Exception:
                self.status = ConstraintStatus.UNKNOWN
                return self.status
        self.status = ConstraintStatus.UNKNOWN
        return self.status


# ============================================================
# Constraint Graph (DAG of constraints)
# ============================================================

@dataclass
class ConstraintGraph:
    """
    可执行约束图 — 约束编译器的最终输出。

    结构:
        ConstraintGraph
            ├── nodes: List[ConstraintNode]     (所有约束)
            ├── edges: 依赖关系 (future)
            └── bindings: skill_name → List[ConstraintNode]
    """
    graph_id: str = field(default_factory=lambda: f"cg-{uuid4().hex[:8]}")
    task_id: str = ""
    nodes: List[ConstraintNode] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ============================================================
    # 查询
    # ============================================================

    def by_category(self, category: ConstraintCategory) -> List[ConstraintNode]:
        """按类别筛选"""
        return [n for n in self.nodes if n.category == category]

    def by_skill(self, skill_name: str) -> List[ConstraintNode]:
        """按绑定的技能筛选"""
        return [n for n in self.nodes if n.applies_to_skill == skill_name]

    def by_priority(self, priority: ConstraintPriority) -> List[ConstraintNode]:
        """按优先级筛选"""
        return [n for n in self.nodes if n.priority == priority]

    def hard_constraints(self) -> List[ConstraintNode]:
        """所有硬约束"""
        return self.by_priority(ConstraintPriority.HARD)

    def soft_constraints(self) -> List[ConstraintNode]:
        """所有软约束"""
        return self.by_priority(ConstraintPriority.SOFT)

    def violated(self) -> List[ConstraintNode]:
        """所有被违反的约束"""
        return [n for n in self.nodes if n.status == ConstraintStatus.VIOLATED]

    # ============================================================
    # 构建
    # ============================================================

    def add(self, node: ConstraintNode) -> None:
        """添加约束节点"""
        self.nodes.append(node)

    def add_all(self, nodes: List[ConstraintNode]) -> None:
        """批量添加"""
        self.nodes.extend(nodes)

    def bind_to_skills(self) -> Dict[str, List[ConstraintNode]]:
        """
        将约束按技能分组。

        Returns:
            {"Grasp": [force_limit, ...], "MoveTo": [velocity_limit, avoid_cup, ...]}
        """
        bindings: Dict[str, List[ConstraintNode]] = {}
        for node in self.nodes:
            skill = node.applies_to_skill or "_global"
            if skill not in bindings:
                bindings[skill] = []
            bindings[skill].append(node)
        return bindings

    # ============================================================
    # 摘要
    # ============================================================

    def summary(self) -> str:
        """人类可读摘要"""
        hard = len(self.hard_constraints())
        soft = len(self.soft_constraints())
        by_cat = {}
        for n in self.nodes:
            cat = n.category.value
            by_cat[cat] = by_cat.get(cat, 0) + 1
        cats = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))

        lines = [
            f"ConstraintGraph [{self.graph_id}]",
            f"  Hard: {hard}, Soft: {soft}",
            f"  Categories: {cats}",
        ]
        for n in self.nodes:
            lines.append(f"  [{n.priority.value.upper()}] {n.expression}")
        return "\n".join(lines)
