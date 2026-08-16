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

        # 2. 规避关键词 → 空间约束 (实体接地)
        constraints.extend(self._extract_avoid_constraints(instruction, scene=scene, target=target))

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
                    priority=ConstraintPriority.SOFT,
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

    def _extract_avoid_constraints(
        self, text: str, scene: Optional[SemanticSceneGraph] = None,
        target: str = ""
    ) -> List[ConstraintNode]:
        """
        从自然语言指令 + 场景图中提取碰撞避免约束。

        策略 (Entity Grounding):
            1. 遍历场景中所有物体，若其 name/label/specific_class 在指令中出现
               或通过跨语言别名匹配，且不是当前抓取目标 → 加入规避列表
            2. 若用户使用泛指 ("别碰周围/前边的东西")，遍历 scene.relations，
               找出与目标有 blocking/near 关系的物体
            3. 废弃原有的正则盲切 (避免 "到前边正" 等碎片)
        """
        from robot_intent_agent.task_semantics import _CN_CATEGORY_ALIASES as CAT_ALIASES
        constraints: List[ConstraintNode] = []
        seen_obstacles: set = set()

        # --- 策略 1: 场景物体名 + 跨语言别名匹配 ---
        if scene:
            target_obj = scene.find_object(target) if target else None
            target_id = target_obj.id if target_obj else None

            for obj in scene.objects:
                # Skip target by ID (not name — two objects can share a name)
                if target_id is not None and getattr(obj, "id", None) == target_id:
                    continue
                if obj.name in seen_obstacles:
                    continue
                if len(obj.name) < 1:
                    continue

                # 构建匹配名列表: name + label + specific_class
                names = [
                    getattr(obj, "name", ""),
                    getattr(obj, "label", "") or "",
                    getattr(obj, "specific_class", "") or "",
                ]
                # 跨语言别名
                specific_class = getattr(obj, "specific_class", "") or ""
                aliases = CAT_ALIASES.get(specific_class, [])
                all_names = [n for n in names if n] + aliases

                if any(n and n in text for n in all_names):
                    obstacle_name = obj.name
                    seen_obstacles.add(obstacle_name)
                    constraints.append(
                        SpatialConstraint.collision_avoid(
                            obstacle=obstacle_name,
                            min_distance_m=0.05,
                            applies_to_skill="",
                        )
                    )

        # --- 策略 2: 几何关系兜底 ---
        # 如果用户说了"别碰"/"避开"等关键词，自动将 blocking 和 near 关系的物体加入规避列表
        if AVOID_KEYWORDS.search(text) and scene and target:
            target_obj = scene.find_object(target)
            if target_obj:
                blocking_ids = scene.blocking_objects(target_obj.id)
                for bid in blocking_ids:
                    blocker = scene.find_object(bid)
                    if blocker and blocker.name != target and blocker.name not in seen_obstacles:
                        seen_obstacles.add(blocker.name)
                        constraints.append(
                            SpatialConstraint.collision_avoid(
                                obstacle=blocker.name,
                                min_distance_m=0.05,
                                applies_to_skill="",
                            )
                        )

                # near 关系也加入
                for rel in scene.relations_of(target_obj.id):
                    if rel.predicate.value == "near":
                        near_obj = scene.find_object(rel.object) or scene.find_object(rel.subject)
                        if near_obj and near_obj.name != target and near_obj.name not in seen_obstacles:
                            seen_obstacles.add(near_obj.name)
                            constraints.append(
                                SpatialConstraint.collision_avoid(
                                    obstacle=near_obj.name,
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
        """从物体语义属性 (SemanticObject) 提取约束 (v3.0 fragility_level-based)"""
        constraints: List[ConstraintNode] = []

        target_obj = scene.find_object(target)
        if not target_obj:
            return constraints

        # v3.0: 使用 SemanticObject 的 fragility_level 推导约束
        from robot_intent_agent.semantic_reasoner.property_fusion import PropertyFusion
        sem_obj = PropertyFusion.from_scene_object(target_obj)

        if sem_obj.fragility_level >= 1:  # SENSITIVE or above
            max_f = sem_obj.max_grasp_force_n
            max_v = sem_obj.max_velocity_ms
            constraints.append(
                PhysicalConstraint.force_limit(
                    target=target,
                    max_force_n=max_f,
                    min_force_n=0.1,
                    applies_to_skill="Grasp",
                    priority=ConstraintPriority.HARD,
                )
            )
            constraints.append(
                PhysicalConstraint.velocity_limit(
                    max_linear_ms=max_v,
                    applies_to_skill="",
                    priority=ConstraintPriority.SOFT,
                )
            )

        # container → pour constraints (reserved)
        if sem_obj.container:
            pass

        # fixed obstacle → collision risk tag
        if sem_obj.mobility_type.value == "fixed" if hasattr(sem_obj.mobility_type, 'value') else str(sem_obj.mobility_type) == "fixed":
            pass  # Marked as blocking by SpatialReasoner

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
