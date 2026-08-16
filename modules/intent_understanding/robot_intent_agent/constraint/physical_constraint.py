"""
Physical Constraint Factory — 物理约束工厂

约束类型:
    force_limit      — 力上限
    velocity_limit   — 速度上限
    height_limit     — Z 轴高度限制
    gripper_width    — 夹爪开度限制
"""

from __future__ import annotations

from .base import ConstraintNode, ConstraintCategory, ConstraintPriority


class PhysicalConstraint:

    @staticmethod
    def force_limit(
        target: str,
        max_force_n: float = 10.0,
        min_force_n: float = 0.1,
        applies_to_skill: str = "Grasp",
        priority: ConstraintPriority = ConstraintPriority.HARD,
    ) -> ConstraintNode:
        """
        力限制约束。

        示例:
            "轻一点"  → force_limit("药瓶", max_force_n=3.0)
            "用力抓"  → force_limit("方块", min_force_n=5.0, max_force_n=8.0)
        """
        return ConstraintNode(
            category=ConstraintCategory.PHYSICAL,
            constraint_type="force_limit",
            target=target,
            applies_to_skill=applies_to_skill,
            expression=f"force({target}) in ({min_force_n}, {max_force_n}] N",
            params={
                "target": target,
                "max_force_n": max_force_n,
                "min_force_n": min_force_n,
            },
            priority=priority,
            description=f"Grasp force for {target}: ({min_force_n}, {max_force_n}] N",
        )

    @staticmethod
    def velocity_limit(
        max_linear_ms: float = 0.3,
        applies_to_skill: str = "",
        priority: ConstraintPriority = ConstraintPriority.SOFT,
    ) -> ConstraintNode:
        """
        速度限制约束。

        示例:
            "慢一点" → velocity_limit(0.10)
        """
        return ConstraintNode(
            category=ConstraintCategory.PHYSICAL,
            constraint_type="velocity_limit",
            target="",
            applies_to_skill=applies_to_skill,
            expression=f"velocity <= {max_linear_ms} m/s",
            params={"max_linear_ms": max_linear_ms},
            priority=priority,
            description=f"End-effector velocity <= {max_linear_ms} m/s",
        )

    @staticmethod
    def height_limit(
        min_z_m: float = 0.02,
        max_z_m: float = 0.5,
        applies_to_skill: str = "",
        priority: ConstraintPriority = ConstraintPriority.HARD,
    ) -> ConstraintNode:
        """
        Z 轴高度限制。

        安全红线: z >= 0.02m (不可绕过)
        """
        return ConstraintNode(
            category=ConstraintCategory.PHYSICAL,
            constraint_type="height_limit",
            target="",
            applies_to_skill=applies_to_skill,
            expression=f"z in [{min_z_m}, {max_z_m}] m",
            params={"min_z_m": min_z_m, "max_z_m": max_z_m},
            priority=priority,
            description=f"End-effector Z height in [{min_z_m}, {max_z_m}] m",
        )

    @staticmethod
    def gripper_width(
        width_m: float = 0.08,
        applies_to_skill: str = "Grasp",
    ) -> ConstraintNode:
        """夹爪开度约束"""
        return ConstraintNode(
            category=ConstraintCategory.PHYSICAL,
            constraint_type="gripper_width",
            target="",
            applies_to_skill=applies_to_skill,
            expression=f"gripper_width = {width_m} m",
            params={"width_m": width_m},
            priority=ConstraintPriority.SOFT,
            description=f"Gripper opening width = {width_m} m",
        )

    @staticmethod
    def release_height(
        target: str,
        max_height_m: float = 0.10,
        applies_to_skill: str = "Release",
    ) -> ConstraintNode:
        """
        释放高度约束 (人机交互)。

        示例:
            release_height("药瓶", 0.10)  →  release_height <= 10cm
        """
        return ConstraintNode(
            category=ConstraintCategory.INTERACTION,
            constraint_type="release_height",
            target=target,
            applies_to_skill=applies_to_skill,
            expression=f"release_height({target}) <= {max_height_m} m",
            params={"target": target, "max_height_m": max_height_m},
            priority=ConstraintPriority.HARD,
            description=f"Release {target} at height <= {max_height_m}m",
        )
