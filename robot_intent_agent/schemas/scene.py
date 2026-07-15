"""
Semantic Scene Schema — Pydantic v2 数据模型

定义:
    - SceneObject   : 场景物体 (id, position, orientation, attributes, affordances)
    - SpatialRelation: 空间关系 (subject, predicate, object)
    - RobotState    : 机器人状态 (gripper, joint_state, battery)
    - SemanticSceneGraph: 完整场景图
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class SpatialPredicate(str, Enum):
    """空间关系谓词"""
    LEFT_OF = "left_of"
    RIGHT_OF = "right_of"
    ABOVE = "above"
    BELOW = "below"
    IN_FRONT_OF = "in_front_of"
    BEHIND = "behind"
    INSIDE = "inside"
    NEXT_TO = "next_to"
    NEAR = "near"
    BLOCKING = "blocking"
    SUPPORTING = "supporting"


class Affordance(str, Enum):
    """物体可供性 (Gibsonian affordance)"""
    GRASPABLE = "graspable"
    PUSHABLE = "pushable"
    POURABLE = "pourable"
    STACKABLE = "stackable"
    OPENABLE = "openable"
    CONTAINER = "container"
    FRAGILE = "fragile"
    MOVABLE = "movable"
    FIXED = "fixed"


# ============================================================
# 场景物体
# ============================================================

class Position(BaseModel):
    """3D 位置 (世界坐标系, 单位: m)"""
    x: float = Field(..., description="X 坐标 (m)")
    y: float = Field(..., description="Y 坐标 (m)")
    z: float = Field(..., description="Z 坐标 (m)")


class Orientation(BaseModel):
    """3D 朝向 (Euler angles, 单位: rad)"""
    roll: float = Field(default=0.0, description="Roll (rad)")
    pitch: float = Field(default=0.0, description="Pitch (rad)")
    yaw: float = Field(default=0.0, description="Yaw (rad)")


class BoundingBox(BaseModel):
    """3D 包围盒 (单位: m)"""
    width: float = Field(..., gt=0, description="宽度 (m)")
    height: float = Field(..., gt=0, description="高度 (m)")
    depth: float = Field(..., gt=0, description="深度 (m)")


class SceneObject(BaseModel):
    """
    场景中的单个物体。

    示例:
        SceneObject(
            id="obj-001",
            name="红色药瓶",
            position=Position(x=0.15, y=0.05, z=0.03),
            bbox=BoundingBox(width=0.03, height=0.08, depth=0.03),
            attributes={"color": "red", "material": "plastic"},
            affordances=[Affordance.GRASPABLE, Affordance.FRAGILE],
        )
    """
    id: str = Field(default_factory=lambda: f"obj-{uuid4().hex[:6]}")
    name: str = Field(..., description="物体名称")
    label: Optional[str] = Field(default=None, description="语义标签 (bottle, cup, cube...)")
    position: Position = Field(..., description="3D 世界坐标")
    orientation: Orientation = Field(default_factory=Orientation)
    bbox: BoundingBox = Field(..., description="包围盒尺寸")
    attributes: Dict[str, Any] = Field(
        default_factory=dict,
        description="自由属性 (color, material, weight...)",
    )
    affordances: List[Affordance] = Field(
        default_factory=list,
        description="可供性列表",
    )


# ============================================================
# 空间关系
# ============================================================

class SpatialRelation(BaseModel):
    """两个物体之间的空间关系"""
    subject: str = Field(..., description="主体物体 ID")
    predicate: SpatialPredicate = Field(..., description="空间谓词")
    object: str = Field(..., description="客体物体 ID")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="关系置信度",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="额外信息 (距离、遮挡程度...)",
    )


# ============================================================
# 机器人状态
# ============================================================

class GripperState(BaseModel):
    """夹爪状态"""
    is_open: bool = Field(default=True, description="是否张开")
    width: float = Field(default=0.08, ge=0.0, le=0.1, description="夹爪开度 (m)")
    force: float = Field(default=0.0, ge=0.0, le=10.0, description="当前夹持力 (N)")
    has_object: bool = Field(default=False, description="是否持有物体")


class JointState(BaseModel):
    """关节状态"""
    angles: List[float] = Field(..., description="7 自由度关节角 (rad)")
    velocities: List[float] = Field(default_factory=list, description="关节速度 (rad/s)")


class RobotState(BaseModel):
    """机器人完整状态快照"""
    gripper: GripperState = Field(default_factory=GripperState)
    joint_state: JointState = Field(
        default_factory=lambda: JointState(angles=[0.0]*7)
    )
    battery: float = Field(default=100.0, ge=0.0, le=100.0, description="电量 (%)")
    is_homed: bool = Field(default=True, description="是否已归位")


# ============================================================
# 场景图 (顶层聚合)
# ============================================================

class SemanticSceneGraph(BaseModel):
    """
    完整语义场景图。

    包含:
        - 所有场景物体 (含属性、可供性)
        - 物体间空间关系
        - 机器人当前状态
    """
    scene_id: str = Field(default_factory=lambda: f"scene-{uuid4().hex[:8]}")
    timestamp: Optional[str] = Field(default=None, description="ISO 时间戳")
    objects: List[SceneObject] = Field(default_factory=list)
    relations: List[SpatialRelation] = Field(default_factory=list)
    robot_state: RobotState = Field(default_factory=RobotState)

    def find_object(self, name: str) -> Optional[SceneObject]:
        """按名称查找物体"""
        for obj in self.objects:
            if obj.name == name or obj.id == name:
                return obj
        return None

    def relations_of(self, object_id: str) -> List[SpatialRelation]:
        """返回涉及指定物体的所有空间关系"""
        return [
            r for r in self.relations
            if r.subject == object_id or r.object == object_id
        ]

    def blocking_objects(self, target_id: str) -> List[str]:
        """找出阻挡指定物体的所有物体 ID"""
        return [
            r.object for r in self.relations
            if r.subject == target_id and r.predicate == SpatialPredicate.BLOCKING
        ]
