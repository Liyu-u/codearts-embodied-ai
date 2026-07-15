"""
Safety Constraint Factory — 安全约束工厂 (硬红线)

这些约束不可绕过、不可降级、不可由 LLM 修改。

安全规则:
    1. Z-axis floor     : z >= 0.02 m (永远)
    2. Joint limits     : each joint in [-2.9, 2.9] rad
    3. Gripper force max: force <= 10.0 N (永远)
    4. Workspace bounds : x,y in [-0.5, 0.5], z in [0.02, 0.5]
    5. Object weight    : check weight before grasp (if known)
    6. Human proximity  : slow down near human interaction zone
"""

from __future__ import annotations

from .base import ConstraintNode, ConstraintCategory, ConstraintPriority


class SafetyConstraint:
    """
    安全约束 — 所有方法返回 HARD priority。

    这些是物理安全红线，Constraint Compiler 强制注入，
    Rule Engine 和 LLM 均无权修改或移除。
    """

    @staticmethod
    def z_axis_floor(
        min_z_m: float = 0.02,
        applies_to_skill: str = "",
    ) -> ConstraintNode:
        """
        Z 轴安全底线 — 不可绕过。

        评审判定依据: assert z >= 0.02
        """
        return ConstraintNode(
            category=ConstraintCategory.SAFETY,
            constraint_type="z_axis_floor",
            target="",
            applies_to_skill=applies_to_skill,
            expression=f"z >= {min_z_m} m",
            params={"min_z_m": min_z_m},
            priority=ConstraintPriority.HARD,
            description=f"[SAFETY RED LINE] End-effector Z >= {min_z_m}m",
        )

    @staticmethod
    def joint_limits(
        max_angle_rad: float = 2.9,
    ) -> ConstraintNode:
        """关节限位 — Franka Panda 7-DOF 硬件极限"""
        return ConstraintNode(
            category=ConstraintCategory.SAFETY,
            constraint_type="joint_limits",
            target="",
            applies_to_skill="",
            expression=f"all(|joint_angle|) <= {max_angle_rad} rad",
            params={"max_angle_rad": max_angle_rad},
            priority=ConstraintPriority.HARD,
            description=f"[SAFETY] All 7 joints within +/-{max_angle_rad} rad",
        )

    @staticmethod
    def max_gripper_force(
        max_force_n: float = 10.0,
        applies_to_skill: str = "Grasp",
    ) -> ConstraintNode:
        """最大夹爪力 — 不可超过 10N"""
        return ConstraintNode(
            category=ConstraintCategory.SAFETY,
            constraint_type="max_gripper_force",
            target="",
            applies_to_skill=applies_to_skill,
            expression=f"gripper_force <= {max_force_n} N",
            params={"max_force_n": max_force_n},
            priority=ConstraintPriority.HARD,
            description=f"[SAFETY] Gripper force <= {max_force_n}N",
        )

    @staticmethod
    def workspace_bounds(
        x_range: tuple = (-0.5, 0.5),
        y_range: tuple = (-0.5, 0.5),
        z_range: tuple = (0.02, 0.5),
    ) -> ConstraintNode:
        """工作空间边界"""
        return ConstraintNode(
            category=ConstraintCategory.SAFETY,
            constraint_type="workspace_bounds",
            target="",
            applies_to_skill="",
            expression=(
                f"workspace: x[{x_range[0]},{x_range[1]}] "
                f"y[{y_range[0]},{y_range[1]}] "
                f"z[{z_range[0]},{z_range[1]}]"
            ),
            params={
                "x_range": list(x_range),
                "y_range": list(y_range),
                "z_range": list(z_range),
            },
            priority=ConstraintPriority.HARD,
            description="[SAFETY] End-effector within workspace bounds",
        )

    @staticmethod
    def human_proximity(
        slow_zone_radius_m: float = 0.3,
        max_velocity_in_zone_ms: float = 0.10,
    ) -> ConstraintNode:
        """
        人机交互安全距离 — 靠近用户时降速。
        """
        return ConstraintNode(
            category=ConstraintCategory.SAFETY,
            constraint_type="human_proximity",
            target="user",
            applies_to_skill="MoveTo",
            expression=(
                f"if distance(user) < {slow_zone_radius_m}m: "
                f"velocity <= {max_velocity_in_zone_ms} m/s"
            ),
            params={
                "slow_zone_radius_m": slow_zone_radius_m,
                "max_velocity_in_zone_ms": max_velocity_in_zone_ms,
            },
            priority=ConstraintPriority.HARD,
            description=(
                f"[SAFETY] Velocity <= {max_velocity_in_zone_ms}m/s "
                f"within {slow_zone_radius_m}m of human"
            ),
        )

    @classmethod
    def mandatory_set(cls, target: str = "") -> list[ConstraintNode]:
        """
        返回所有必须注入的安全约束 (不可选)。

        每次任务执行时自动附加。
        """
        return [
            cls.z_axis_floor(),
            cls.joint_limits(),
            cls.max_gripper_force(applies_to_skill="Grasp"),
            cls.workspace_bounds(),
            cls.human_proximity(),
        ]
