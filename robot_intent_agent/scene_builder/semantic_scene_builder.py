"""
Semantic Scene Builder — 语义场景构建器

输入:  原始感知数据 (Mock RGB-D / VLM 结果)
输出:  SemanticSceneGraph (含物体 + 空间关系推断)

架构:
    Raw Perception (dict)
        → SceneObject 实例化
        → SpatialRelation 推理 (规则引擎)
        → SemanticSceneGraph 聚合输出
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from robot_intent_agent.schemas.scene import (
    Position,
    Orientation,
    BoundingBox,
    SceneObject,
    SpatialRelation,
    SpatialPredicate,
    Affordance,
    SemanticSceneGraph,
    RobotState,
    GripperState,
    JointState,
)


# ============================================================
# 空间推理配置
# ============================================================

@dataclass
class SpatialConfig:
    """空间关系推理参数 (可调)"""
    near_threshold_m: float = 0.10       # 判定 near 的最大距离 (m)
    blocking_angle_deg: float = 30.0     # 判定 blocking 的视线锥角 (deg)
    horizontal_threshold: float = 0.02   # 判定水平方向的最小 X 差 (m)
    stacking_z_overlap_m: float = 0.01   # 判定 supporting 的 Z 重叠裕度 (m)


# ============================================================
# 默认物体属性映射 (VLM / 预训练知识)
# ============================================================

_DEFAULT_AFFORDANCES: Dict[str, List[Affordance]] = {
    # English keywords
    "bottle": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "cup": [Affordance.GRASPABLE, Affordance.CONTAINER, Affordance.MOVABLE],
    "glass": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.CONTAINER],
    "block": [Affordance.GRASPABLE, Affordance.STACKABLE, Affordance.MOVABLE],
    "medicine": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "box": [Affordance.GRASPABLE, Affordance.CONTAINER, Affordance.PUSHABLE],
    "table": [Affordance.FIXED],
    "wall": [Affordance.FIXED],
    # Chinese keywords
    "药": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "瓶": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "杯": [Affordance.GRASPABLE, Affordance.CONTAINER, Affordance.FRAGILE],
    "玻璃": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.CONTAINER],
    "木": [Affordance.GRASPABLE, Affordance.STACKABLE, Affordance.MOVABLE],
    "块": [Affordance.GRASPABLE, Affordance.STACKABLE, Affordance.MOVABLE],
}

_DEFAULT_LABELS: Dict[str, str] = {
    # English
    "medicine": "medicine_bottle",
    "bottle": "bottle",
    "cup": "cup",
    "glass": "glass_cup",
    "block": "cube",
    "box": "box",
    # Chinese
    "药": "medicine_bottle",
    "瓶": "bottle",
    "杯": "cup",
    "玻璃": "glass_cup",
    "木": "wooden_block",
    "块": "cube",
}


# ============================================================
# RawPerception — 输入数据模型
# ============================================================

@dataclass
class RawObjectPercept:
    """原始感知物体 (来自 RGB-D / VLM)"""
    name: str
    x: float
    y: float
    z: float
    width: float = 0.05
    height: float = 0.10
    depth: float = 0.05
    color: Optional[str] = None
    material: Optional[str] = None
    extra_attrs: Dict[str, Any] = field(default_factory=dict)

    def to_scene_object(self) -> SceneObject:
        """转换为标准 SceneObject"""
        label = self._infer_label()
        affordances = self._infer_affordances(label)

        return SceneObject(
            name=self.name,
            label=label,
            position=Position(x=self.x, y=self.y, z=self.z),
            orientation=Orientation(),
            bbox=BoundingBox(
                width=self.width, height=self.height, depth=self.depth
            ),
            attributes={
                "color": self.color or "unknown",
                "material": self.material or "unknown",
                **self.extra_attrs,
            },
            affordances=affordances,
        )

    def _infer_label(self) -> Optional[str]:
        """从名称推断语义标签"""
        name_lower = self.name.lower()
        for keyword, label in _DEFAULT_LABELS.items():
            if keyword in name_lower:
                return label
        return None

    def _infer_affordances(self, label: Optional[str]) -> List[Affordance]:
        """从标签推断可供性"""
        if label and label in _DEFAULT_AFFORDANCES:
            return list(_DEFAULT_AFFORDANCES[label])
        # 搜索关键词
        name_lower = self.name.lower()
        for keyword, affs in _DEFAULT_AFFORDANCES.items():
            if keyword in name_lower:
                return list(affs)
        return [Affordance.GRASPABLE, Affordance.MOVABLE]


# ============================================================
# Spatial Reasoner — 空间关系推理引擎 (基于规则)
# ============================================================

class SpatialReasoner:
    """
    基于几何规则的空间关系推理器。

    支持的谓词:
        left_of / right_of   — X 轴比较
        above / below        — Z 轴比较
        in_front_of / behind — Y 轴比较
        near                 — 欧氏距离
        blocking             — A 在 robot↔target 视线路径上
        supporting           — A 在 B 下方且 Z 接近
    """

    def __init__(self, config: Optional[SpatialConfig] = None):
        self.config = config or SpatialConfig()

    def infer_relations(
        self, objects: List[SceneObject], robot_origin: Tuple[float, float, float] = (0, 0, 0.5)
    ) -> List[SpatialRelation]:
        """
        对所有物体两两推断空间关系。

        Args:
            objects:      场景物体列表
            robot_origin: 机械臂基座位置 (用于 blocking 判断)
        """
        relations: List[SpatialRelation] = []

        for i, obj_a in enumerate(objects):
            for j, obj_b in enumerate(objects):
                if i >= j:
                    continue
                relations.extend(
                    self._infer_pair(obj_a, obj_b, robot_origin)
                )

        return relations

    def _infer_pair(
        self,
        a: SceneObject,
        b: SceneObject,
        robot_origin: Tuple[float, float, float],
    ) -> List[SpatialRelation]:
        """推断一对物体间的所有空间关系"""
        results: List[SpatialRelation] = []
        pa = a.position
        pb = b.position

        # --- 水平方向 (X 轴) ---
        dx = pb.x - pa.x
        if abs(dx) > self.config.horizontal_threshold:
            if dx > 0:
                results.append(self._make_rel(a.id, SpatialPredicate.LEFT_OF, b.id, abs(dx)))
                results.append(self._make_rel(b.id, SpatialPredicate.RIGHT_OF, a.id, abs(dx)))
            else:
                results.append(self._make_rel(a.id, SpatialPredicate.RIGHT_OF, b.id, abs(dx)))
                results.append(self._make_rel(b.id, SpatialPredicate.LEFT_OF, a.id, abs(dx)))

        # --- 深度方向 (Y 轴) ---
        dy = pb.y - pa.y
        if abs(dy) > self.config.horizontal_threshold:
            if dy > 0:
                results.append(self._make_rel(b.id, SpatialPredicate.IN_FRONT_OF, a.id, abs(dy)))
            else:
                results.append(self._make_rel(a.id, SpatialPredicate.IN_FRONT_OF, b.id, abs(dy)))

        # --- 垂直方向 (Z 轴) ---
        dz = pb.z - pa.z
        z_mid_a = pa.z + a.bbox.height / 2
        z_mid_b = pb.z + b.bbox.height / 2
        if z_mid_b > z_mid_a + self.config.stacking_z_overlap_m:
            results.append(self._make_rel(a.id, SpatialPredicate.BELOW, b.id, abs(z_mid_b - z_mid_a)))
        elif z_mid_a > z_mid_b + self.config.stacking_z_overlap_m:
            results.append(self._make_rel(a.id, SpatialPredicate.ABOVE, b.id, abs(z_mid_a - z_mid_b)))

        # --- Supporting (A 在 B 下方且 Z 方向接近) ---
        bottom_a = pa.z
        top_a = pa.z + a.bbox.height
        bottom_b = pb.z
        if abs(top_a - bottom_b) < self.config.stacking_z_overlap_m and self._xy_overlap(a, b):
            results.append(self._make_rel(b.id, SpatialPredicate.SUPPORTING, a.id, 0.0))

        # --- Near (欧氏距离) ---
        dist = math.sqrt(dx**2 + dy**2 + dz**2)
        if dist <= self.config.near_threshold_m:
            results.append(self._make_rel(a.id, SpatialPredicate.NEAR, b.id, dist))

        # --- Blocking (A 在 robot→B 路径上, 或 B 在 robot→A 路径上) ---
        if self._is_blocking(a, b, robot_origin):
            results.append(
                SpatialRelation(
                    subject=b.id,
                    predicate=SpatialPredicate.BLOCKING,
                    object=a.id,
                    confidence=0.85,
                    metadata={"description": f"{a.name} blocks path to {b.name}"},
                )
            )
        if self._is_blocking(b, a, robot_origin):
            results.append(
                SpatialRelation(
                    subject=a.id,
                    predicate=SpatialPredicate.BLOCKING,
                    object=b.id,
                    confidence=0.85,
                    metadata={"description": f"{b.name} blocks path to {a.name}"},
                )
            )

        return results

    # ============================================================
    # 辅助判断
    # ============================================================

    def _make_rel(
        self, subject: str, predicate: SpatialPredicate, obj: str, distance: float
    ) -> SpatialRelation:
        return SpatialRelation(
            subject=subject,
            predicate=predicate,
            object=obj,
            confidence=min(1.0, 1.0 - distance * 2),
            metadata={"distance_m": round(distance, 4)},
        )

    def _xy_overlap(self, a: SceneObject, b: SceneObject) -> bool:
        """X-Y 平面投影是否重叠"""
        ax_min, ax_max = a.position.x - a.bbox.width / 2, a.position.x + a.bbox.width / 2
        ay_min, ay_max = a.position.y - a.bbox.depth / 2, a.position.y + a.bbox.depth / 2
        bx_min, bx_max = b.position.x - b.bbox.width / 2, b.position.x + b.bbox.width / 2
        by_min, by_max = b.position.y - b.bbox.depth / 2, b.position.y + b.bbox.depth / 2
        return (ax_min < bx_max and ax_max > bx_min) and (ay_min < by_max and ay_max > by_min)

    def _is_blocking(
        self,
        a: SceneObject,
        target: SceneObject,
        robot_origin: Tuple[float, float, float],
    ) -> bool:
        """
        判断 A 是否阻挡 robot→target 的直线路径。

        简化为: A 在 robot 和 target 之间的 X-Y 连线上,
        且 A 的 XY 投影区域与连线相交。
        """
        rx, ry, _ = robot_origin
        tx, ty = target.position.x, target.position.y
        ax, ay = a.position.x, a.position.y

        # A 是否在 robot 和 target 之间
        dot_product = (ax - rx) * (tx - rx) + (ay - ry) * (ty - ry)
        if dot_product <= 0:
            return False  # A 在 robot 后方

        # A 到连线 robot→target 的距离
        line_len_sq = (tx - rx) ** 2 + (ty - ry) ** 2
        if line_len_sq < 1e-6:
            return False

        t = max(0, min(1, dot_product / line_len_sq))
        closest_x = rx + t * (tx - rx)
        closest_y = ry + t * (ty - ry)
        perpendicular_dist = math.sqrt(
            (ax - closest_x) ** 2 + (ay - closest_y) ** 2
        )

        # 阻挡判定: 垂距 < 物体半径
        obj_radius = max(a.bbox.width, a.bbox.depth) / 2 + 0.02
        return perpendicular_dist < obj_radius


# ============================================================
# SemanticSceneBuilder — 主构建器
# ============================================================

class SemanticSceneBuilder:
    """
    语义场景构建器。

    用法:
        builder = SemanticSceneBuilder()
        raw_percepts = [
            RawObjectPercept(name="红色药瓶", x=0.15, y=0.05, z=0.03, ...),
            RawObjectPercept(name="玻璃水杯", x=-0.05, y=0.02, z=0.06, ...),
        ]
        scene_graph = builder.build(raw_percepts)
    """

    def __init__(self, config: Optional[SpatialConfig] = None):
        self.config = config or SpatialConfig()
        self.reasoner = SpatialReasoner(self.config)

    def build(
        self,
        raw_objects: List[RawObjectPercept],
        robot_state: Optional[RobotState] = None,
        robot_origin: Tuple[float, float, float] = (0, 0, 0.5),
    ) -> SemanticSceneGraph:
        """
        从原始感知数据构建完整语义场景图。

        Args:
            raw_objects:  原始感知物体列表
            robot_state:  机器人当前状态 (None = 默认归位)
            robot_origin: 机械臂基座原点

        Returns:
            SemanticSceneGraph (含物体 + 空间关系)
        """
        # 1. 实例化 SceneObject
        scene_objects = [r.to_scene_object() for r in raw_objects]

        # 2. 推断空间关系
        relations = self.reasoner.infer_relations(scene_objects, robot_origin)

        # 3. 组装场景图
        return SemanticSceneGraph(
            objects=scene_objects,
            relations=relations,
            robot_state=robot_state or self._default_robot_state(),
        )

    def build_from_dict(
        self,
        objects_data: List[Dict[str, Any]],
        robot_origin: Tuple[float, float, float] = (0, 0, 0.5),
    ) -> SemanticSceneGraph:
        """
        从 dict 列表构建 (便捷接口, 用于测试)。

        Args:
            objects_data: [{"name":"药瓶","x":0.15,"y":0.05,"z":0.03,...}, ...]
        """
        raw_objects = [RawObjectPercept(**d) for d in objects_data]
        return self.build(raw_objects, robot_origin=robot_origin)

    @staticmethod
    def _default_robot_state() -> RobotState:
        return RobotState(
            gripper=GripperState(is_open=True, width=0.08, force=0.0, has_object=False),
            joint_state=JointState(angles=[0.0] * 7),
            battery=100.0,
            is_homed=True,
        )
