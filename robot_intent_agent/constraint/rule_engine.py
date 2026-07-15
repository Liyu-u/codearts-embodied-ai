"""
Rule Engine — 基于规则的约束提取引擎

从 NL 指令 + 场景 + Memory 中提取约束，
输出 ConstraintNode 列表。

规则来源:
    1. NL 修饰语映射     (轻→force, 慢→velocity, 别碰→avoid)
    2. 场景空间关系       (blocking → collision_avoid)
    3. Memory 偏好        (grip_style, hand_preference → constraint params)
    4. 物体属性           (fragile → force_limit, container → pour constraints)
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .base import ConstraintNode, ConstraintCategory, ConstraintPriority
from .spatial_constraint import SpatialConstraint
from .physical_constraint import PhysicalConstraint
from .safety_constraint import SafetyConstraint
from robot_intent_agent.schemas.scene import SemanticSceneGraph, Affordance


# ============================================================
# 修饰语 → 约束映射
# ============================================================

MODIFIER_TO_CONSTRAINT: Dict[str, Dict[str, Any]] = {
    # "轻一点" / "轻" → 下调力上限
    "轻一点": {"force_n_max": 3.0},
    "轻":     {"force_n_max": 3.0},
    # "慢一点" / "慢" → 下调速度上限
    "慢一点": {"velocity_ms": 0.10},
    "慢":     {"velocity_ms": 0.10},
    # "快一点" → 上调速度 (但不超硬件限制)
    "快一点": {"velocity_ms": 0.25},
    # "小心" → 力+速度双降
    "小心":   {"force_n_max": 3.0, "velocity_ms": 0.10},
    # "用力" → 上调力下限
    "用力":   {"force_n_min": 5.0, "force_n_max": 8.0},
}

# 规避关键词 (复用 Step 5 逻辑)
AVOID_KEYWORDS = re.compile(r"别碰|不要碰|千万别碰|避开|绕过|躲开")


class ConstraintRuleEngine:
    """
    约束规则引擎。

    三步提取:
        1. NL 修饰语 → 物理约束参数
        2. 场景分析 → 空间约束 (collision_avoid)
        3. 物体属性 → 上下文约束 (fragile→force)
    """

    def __init__(self):
        pass

    # ============================================================
    # 主入口
    # ============================================================

    def extract(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        target: str = "",
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> List[ConstraintNode]:
        """
        从所有来源提取约束。

        Args:
            instruction:    用户自然语言指令
            scene:          语义场景图
            target:         主目标物体名
            memory_context: Memory 检索结果
        """
        constraints: List[ConstraintNode] = []

        # 1. NL 修饰语 → 物理约束
        constraints.extend(self._extract_from_modifiers(instruction, target))

        # 2. 规避关键词 → 空间约束
        constraints.extend(self._extract_avoid_constraints(instruction))

        # 3. 场景分析 → 空间约束
        if scene and target:
            constraints.extend(self._extract_scene_constraints(scene, target))

        # 4. 物体属性 → 物理约束
        if scene and target:
            constraints.extend(self._extract_object_constraints(scene, target))

        # 5. Memory → 偏好约束
        if memory_context:
            constraints.extend(self._extract_memory_constraints(memory_context, target))

        return constraints

    # ============================================================
    # 提取器
    # ============================================================

    def _extract_from_modifiers(
        self, text: str, target: str
    ) -> List[ConstraintNode]:
        """从修饰语提取约束"""
        constraints: List[ConstraintNode] = []

        # 按长关键词优先匹配
        sorted_modifiers = sorted(
            MODIFIER_TO_CONSTRAINT.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        matched_params: Dict[str, Any] = {}
        for keyword, params in sorted_modifiers:
            if keyword in text:
                matched_params.update(params)

        # 生成约束节点
        if "force_n_max" in matched_params or "force_n_min" in matched_params:
            constraints.append(
                PhysicalConstraint.force_limit(
                    target=target,
                    max_force_n=matched_params.get("force_n_max", 10.0),
                    min_force_n=matched_params.get("force_n_min", 0.1),
                    applies_to_skill="Grasp",
                    priority=ConstraintPriority.HARD,
                )
            )

        if "velocity_ms" in matched_params:
            constraints.append(
                PhysicalConstraint.velocity_limit(
                    max_linear_ms=matched_params["velocity_ms"],
                    applies_to_skill="",
                    priority=ConstraintPriority.SOFT,
                )
            )

        return constraints

    def _extract_avoid_constraints(self, text: str) -> List[ConstraintNode]:
        """从规避关键词提取碰撞避免约束"""
        constraints: List[ConstraintNode] = []

        for match in AVOID_KEYWORDS.finditer(text):
            start = match.end()
            obj_match = re.match(r"([一-鿿\w]{1,4})", text[start:])
            if obj_match:
                obstacle = obj_match.group(1).strip()
                obstacle = re.sub(r"[的了呢吗啊]$", "", obstacle)
                if obstacle:
                    constraints.append(
                        SpatialConstraint.collision_avoid(
                            obstacle=obstacle,
                            min_distance_m=0.05,
                            applies_to_skill="",
                        )
                    )

        return constraints

    def _extract_scene_constraints(
        self, scene: SemanticSceneGraph, target: str
    ) -> List[ConstraintNode]:
        """从场景图提取空间约束"""
        constraints: List[ConstraintNode] = []

        target_obj = scene.find_object(target)
        if not target_obj:
            return constraints

        # blocking → collision_avoid
        blockers = scene.blocking_objects(target_obj.id)
        for blocker_id in blockers:
            blocker_obj = scene.find_object(blocker_id)
            if blocker_obj:
                constraints.append(
                    SpatialConstraint.collision_avoid(
                        obstacle=blocker_obj.name,
                        min_distance_m=0.05,
                        applies_to_skill="",
                    )
                )

        return constraints

    def _extract_object_constraints(
        self, scene: SemanticSceneGraph, target: str
    ) -> List[ConstraintNode]:
        """从物体属性 (affordance) 提取约束"""
        constraints: List[ConstraintNode] = []

        target_obj = scene.find_object(target)
        if not target_obj:
            return constraints

        # fragile → 降低抓取力
        if Affordance.FRAGILE in target_obj.affordances:
            constraints.append(
                PhysicalConstraint.force_limit(
                    target=target,
                    max_force_n=3.0,
                    applies_to_skill="Grasp",
                    priority=ConstraintPriority.HARD,
                )
            )

        # container → 需要 pour 约束
        if Affordance.CONTAINER in target_obj.affordances:
            pass  # Future: add pour tilt constraints

        return constraints

    def _extract_memory_constraints(
        self, memory_items: List[Dict[str, Any]], target: str
    ) -> List[ConstraintNode]:
        """从 Memory 检索结果提取约束"""
        constraints: List[ConstraintNode] = []

        for item in memory_items:
            mtype = item.get("memory_type", "")

            if mtype == "skill_experience":
                value = item.get("value", {})
                if isinstance(value, dict):
                    if "force_n" in value:
                        constraints.append(
                            PhysicalConstraint.force_limit(
                                target=target,
                                max_force_n=float(value["force_n"]),
                                applies_to_skill="Grasp",
                                priority=ConstraintPriority.SOFT,
                            )
                        )
                    if "velocity_ms" in value:
                        constraints.append(
                            PhysicalConstraint.velocity_limit(
                                max_linear_ms=float(value["velocity_ms"]),
                                applies_to_skill="",
                                priority=ConstraintPriority.SOFT,
                            )
                        )

        return constraints
