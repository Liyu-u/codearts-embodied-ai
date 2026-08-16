"""
Spatial Constraint Factory — 空间约束工厂

约束类型:
    collision_avoid  — 与指定物体保持距离
    trajectory_bound — 轨迹边界限制
    region_constraint — 限定工作区域
    approach_direction — 指定接近方向
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import ConstraintNode, ConstraintCategory, ConstraintPriority


class SpatialConstraint:

    @staticmethod
    def collision_avoid(
        obstacle: str,
        min_distance_m: float = 0.05,
        applies_to_skill: str = "",
        priority: ConstraintPriority = ConstraintPriority.HARD,
    ) -> ConstraintNode:
        """
        碰撞避免约束。

        示例:
            "别碰水杯" → collision_avoid("水杯", 0.05)
        """
        return ConstraintNode(
            category=ConstraintCategory.SPATIAL,
            constraint_type="collision_avoid",
            target=obstacle,
            applies_to_skill=applies_to_skill,
            expression=f"distance({obstacle}) >= {min_distance_m}m",
            params={
                "obstacle": obstacle,
                "min_distance_m": min_distance_m,
                "avoid_strategy": "go_around",
            },
            priority=priority,
            description=f"Maintain >= {min_distance_m}m from {obstacle}",
        )

    @staticmethod
    def trajectory_constraint(
        waypoints: List[Dict[str, float]],
        applies_to_skill: str = "",
        priority: ConstraintPriority = ConstraintPriority.HARD,
    ) -> ConstraintNode:
        """
        轨迹约束 — 强制经过指定路径点。

        示例:
            trajectory_constraint([
                {"x": 0.0, "y": 0.0, "z": 0.15},
                {"x": 0.15, "y": 0.05, "z": 0.15},
            ])
        """
        return ConstraintNode(
            category=ConstraintCategory.SPATIAL,
            constraint_type="trajectory",
            target="",
            applies_to_skill=applies_to_skill,
            expression=f"follow_waypoints({len(waypoints)} points)",
            params={"waypoints": waypoints},
            priority=priority,
            description=f"Trajectory via {len(waypoints)} waypoints",
        )

    @staticmethod
    def region_constraint(
        x_range: tuple,
        y_range: tuple,
        z_range: tuple,
        applies_to_skill: str = "",
    ) -> ConstraintNode:
        """
        工作区域约束。

        示例:
            region_constraint(
                x_range=(-0.3, 0.3), y_range=(-0.3, 0.3), z_range=(0.02, 0.4)
            )
        """
        return ConstraintNode(
            category=ConstraintCategory.SPATIAL,
            constraint_type="region",
            target="",
            applies_to_skill=applies_to_skill,
            expression=(
                f"x in [{x_range[0]}, {x_range[1]}], "
                f"y in [{y_range[0]}, {y_range[1]}], "
                f"z in [{z_range[0]}, {z_range[1]}]"
            ),
            params={
                "x_range": list(x_range),
                "y_range": list(y_range),
                "z_range": list(z_range),
            },
            priority=ConstraintPriority.HARD,
            description="End-effector within workspace bounds",
        )

    @staticmethod
    def approach_direction(
        target: str,
        direction: str = "top_down",
        applies_to_skill: str = "Reach",
    ) -> ConstraintNode:
        """
        指定接近方向。

        示例:
            "从上方接近" → approach_direction("药瓶", "top_down")
            "从侧面接近" → approach_direction("药瓶", "side")
        """
        return ConstraintNode(
            category=ConstraintCategory.SPATIAL,
            constraint_type="approach_direction",
            target=target,
            applies_to_skill=applies_to_skill,
            expression=f"approach({target}) from {direction}",
            params={"target": target, "direction": direction},
            priority=ConstraintPriority.SOFT,
            description=f"Approach {target} from {direction}",
        )
