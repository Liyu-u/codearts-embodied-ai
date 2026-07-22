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
    near_threshold_m: float = 0.30       # 判定 near 的最大中心距离 (m)
    blocking_angle_deg: float = 30.0     # 判定 blocking 的视线锥角 (deg)
    axis_deadband_m: float = 0.03        # 方向关系轴向死区 (m)
    min_relation_confidence: float = 0.10 # 最低保留置信度
    confidence_full_scale_m: float = 0.30 # 置信度归一化距离 (m)
    stacking_z_overlap_m: float = 0.01   # 判定 supporting 的 Z 重叠裕度 (m)
    bidirectional_relations: bool = True  # 保留逆关系(向后兼容)


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
    "ball": [Affordance.GRASPABLE, Affordance.MOVABLE],
    "needle": [Affordance.GRASPABLE, Affordance.MOVABLE],
    "device": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "rubber": [Affordance.GRASPABLE, Affordance.MOVABLE],
    "metal": [Affordance.GRASPABLE, Affordance.MOVABLE],
    # Chinese keywords
    "药": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "瓶": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.MOVABLE],
    "杯": [Affordance.GRASPABLE, Affordance.CONTAINER, Affordance.FRAGILE],
    "玻璃": [Affordance.GRASPABLE, Affordance.FRAGILE, Affordance.CONTAINER],
    "木": [Affordance.GRASPABLE, Affordance.STACKABLE, Affordance.MOVABLE],
    "块": [Affordance.GRASPABLE, Affordance.STACKABLE, Affordance.MOVABLE],
    "球": [Affordance.GRASPABLE, Affordance.MOVABLE],
    "针": [Affordance.GRASPABLE, Affordance.MOVABLE],
}

_DEFAULT_LABELS: Dict[str, str] = {
    # English
    "medicine": "medicine_bottle",
    "bottle": "bottle",
    "cup": "cup",
    "glass": "glass_cup",
    "block": "cube",
    "box": "box",
    "tray": "tray",
    "table": "table",
    "ball": "ball",
    "needle": "needle",
    "device": "device",
    "rubber": "rubber",
    "metal": "metal",
    # Chinese
    "药": "medicine_bottle",
    "瓶": "bottle",
    "杯": "cup",
    "玻璃": "glass_cup",
    "木": "wooden_block",
    "块": "cube",
    "托盘": "tray",
    "桌": "table",
    "球": "ball",
    "针": "needle",
    "铁": "metal",
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
    object_id: Optional[str] = None  # original perception object_id from dataset
    has_invalid_data: bool = False    # set when input values failed validation
    extra_attrs: Dict[str, Any] = field(default_factory=dict)

    def to_scene_object(self) -> SceneObject:
        """转换为标准 SceneObject"""
        label = self._infer_label()
        affordances = self._infer_affordances(label)
        specific_class, parent_class, parent_classes = self._infer_class_hierarchy(label)

        attrs = {
            "color": self.color or "unknown",
            "material": self.material or "unknown",
            **self.extra_attrs,
        }
        # Preserve original perception object_id for evaluator mapping
        if self.object_id:
            attrs["_perception_object_id"] = self.object_id
        # Flag invalid input data
        if self.has_invalid_data:
            attrs["_has_invalid_input"] = True

        return SceneObject(
            name=self.name,
            original_mention=self.name,
            label=label,
            specific_class=specific_class,
            parent_class=parent_class,
            parent_classes=parent_classes,
            position=Position(x=self.x, y=self.y, z=self.z),
            orientation=Orientation(),
            bbox=BoundingBox(
                width=self.width, height=self.height, depth=self.depth
            ),
            attributes=attrs,
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

    def _infer_class_hierarchy(self, label: Optional[str]) -> Tuple[Optional[str], Optional[str], List[str]]:
        if not label:
            return None, None, []
        class_map: Dict[str, Tuple[Optional[str], Optional[str], List[str]]] = {
            "cup": ("cup", "container", ["cup", "container"]),
            "glass_cup": ("cup", "container", ["glass_cup", "cup", "container"]),
            "bottle": ("bottle", "container", ["bottle", "container"]),
            "tray": ("tray", "support_surface", ["tray", "support_surface"]),
            "table": ("table", "support_surface", ["table", "support_surface"]),
            "box": ("box", "container", ["box", "container"]),
            "wooden_block": ("block", "object", ["wooden_block", "block", "object"]),
            "cube": ("block", "object", ["cube", "block", "object"]),
            "medicine_bottle": ("medicine_bottle", "container", ["medicine_bottle", "bottle", "container"]),
            "ball": ("ball", "object", ["ball", "object"]),
            "needle": ("needle", "object", ["needle", "object"]),
            "device": ("device", "object", ["device", "object"]),
            "rubber": ("rubber", "material", ["rubber", "material"]),
            "metal": ("metal", "material", ["metal", "material"]),
        }
        return class_map.get(label, (label, None, [label]))


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
        """推断一对物体间的所有空间关系 (v2: 轴向死区 + 置信度过滤 + 规范顺序)"""
        results: List[SpatialRelation] = []
        cfg = self.config
        pa, pb = a.position, b.position
        dx, dy, dz = pb.x - pa.x, pb.y - pa.y, pb.z - pa.z

        def _add_dir(subj, pred, axis_delta):
            if abs(axis_delta) <= cfg.axis_deadband_m:
                return
            conf = self._clamp_confidence((abs(axis_delta) - cfg.axis_deadband_m) / cfg.confidence_full_scale_m)
            if conf < cfg.min_relation_confidence:
                return
            results.append(SpatialRelation(subject=subj, predicate=pred, object=(b.id if subj==a.id else a.id),
                           confidence=round(conf,3),
                           metadata={"axis_delta_m": round(abs(axis_delta),4), "deadband_m": cfg.axis_deadband_m}))

        # X轴: Pb.x > Pa.x → a LEFT_OF b, b RIGHT_OF a (规范: 只存 LEFT_OF)
        if abs(dx) > cfg.axis_deadband_m:
            if dx > 0:
                _add_dir(a.id, SpatialPredicate.LEFT_OF, dx)
                if cfg.bidirectional_relations:
                    _add_dir(b.id, SpatialPredicate.RIGHT_OF, dx)
            else:
                _add_dir(b.id, SpatialPredicate.LEFT_OF, abs(dx))
                if cfg.bidirectional_relations:
                    _add_dir(a.id, SpatialPredicate.RIGHT_OF, abs(dx))

        # Y轴: robot_base: y<0=右侧, y>0=左侧; in_front_of = y值更大
        if abs(dy) > cfg.axis_deadband_m:
            if dy > 0:
                _add_dir(b.id, SpatialPredicate.IN_FRONT_OF, dy)
            else:
                _add_dir(a.id, SpatialPredicate.IN_FRONT_OF, abs(dy))

        # Z轴: above/below (基于中点比较)
        z_mid_a, z_mid_b = pa.z + a.bbox.height/2, pb.z + b.bbox.height/2
        dz_mid = z_mid_b - z_mid_a
        if abs(dz_mid) > cfg.axis_deadband_m:
            if dz_mid > 0:
                _add_dir(a.id, SpatialPredicate.BELOW, dz_mid)
            else:
                _add_dir(a.id, SpatialPredicate.ABOVE, abs(dz_mid))

        # Supporting
        if abs((pa.z + a.bbox.height) - pb.z) < cfg.stacking_z_overlap_m and self._xy_overlap(a, b):
            results.append(SpatialRelation(subject=b.id, predicate=SpatialPredicate.SUPPORTING,
                           object=a.id, confidence=0.85, metadata={"type": "supporting"}))

        # Near
        dist = math.sqrt(dx**2 + dy**2 + dz**2)
        if dist <= cfg.near_threshold_m:
            nc = self._clamp_confidence(1.0 - dist / cfg.near_threshold_m)
            if nc >= cfg.min_relation_confidence:
                results.append(SpatialRelation(subject=a.id, predicate=SpatialPredicate.NEAR, object=b.id,
                               confidence=round(nc,3),
                               metadata={"center_distance_m": round(dist,4), "threshold_m": cfg.near_threshold_m}))

        # Blocking
        if self._is_blocking(a, b, robot_origin):
            results.append(SpatialRelation(subject=b.id, predicate=SpatialPredicate.BLOCKING,
                           object=a.id, confidence=0.85,
                           metadata={"description": f"{a.name} blocks path to {b.name}"}))
        if self._is_blocking(b, a, robot_origin):
            results.append(SpatialRelation(subject=a.id, predicate=SpatialPredicate.BLOCKING,
                           object=b.id, confidence=0.85,
                           metadata={"description": f"{b.name} blocks path to {a.name}"}))

        return results

    # ============================================================
    # 辅助判断
    # ============================================================

    @staticmethod
    def _clamp_confidence(value: float) -> float:
        import math
        if not math.isfinite(value):
            raise ValueError(f"confidence must be finite, got {value!r}")
        return max(0.0, min(1.0, float(value)))

    def _make_rel(
        self, subject: str, predicate: SpatialPredicate, obj: str, distance: float
    ) -> SpatialRelation:
        conf = self._clamp_confidence(1.0 - distance / self.config.near_threshold_m)
        return SpatialRelation(
            subject=subject,
            predicate=predicate,
            object=obj,
            confidence=conf,
            metadata={"center_distance_m": round(distance, 4)},
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
