"""
环境感知脚本 — Isaac Sim 6.0.1 真实 USD Stage 版
同学 C（吴昌庆）上传：实时从仿真战场抓取所有物体信息

输出格式: perception_observation (v1.0.0)
- 供队友 A（意图解析）读取场景
- 供队友 B（策略生成）获取目标物体
- 供队友 D（监控探针）对比感知 vs 真值
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# Isaac Sim 6.0.1 API 导入
# ============================================================
_KIT_MODE = False
try:
    from isaacsim.core.utils.stage import get_current_stage
    from isaacsim.core.utils.prims import get_prim_at_path, get_all_matching_child_prims
    from isaacsim.core.experimental.prims import XFormPrim
    _KIT_MODE = True
except ImportError:
    pass


# ============================================================
# 数据结构 — perception_observation v1.0.0
# ============================================================
@dataclass
class Candidate:
    """感知候选结果（带置信度）"""
    name: str
    score: float


@dataclass
class Position3D:
    x: float
    y: float
    z: float


@dataclass
class Orientation:
    """四元数朝向"""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


@dataclass
class Pose:
    """6D 位姿（位置 + 四元数朝向）"""
    position: Position3D
    orientation: Orientation = field(default_factory=Orientation)


@dataclass
class Geometry:
    """包围盒几何信息"""
    type: str = "oriented_bbox_3d"
    size_3d: Optional[Any] = None  # 将在 __post_init__ 中转换

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "size": {
                "width": self.size_3d[0] if self.size_3d else 0.04,
                "height": self.size_3d[1] if self.size_3d else 0.04,
                "depth": self.size_3d[2] if self.size_3d else 0.04,
            },
        }


@dataclass
class Appearance:
    """外观感知结果（颜色/形状/纹理候选列表）"""
    color_candidates: List[Candidate] = field(default_factory=list)
    shape_candidates: List[Candidate] = field(default_factory=list)
    texture_candidates: List[Candidate] = field(default_factory=list)


@dataclass
class Tracking:
    """物体追踪信息"""
    track_age_frames: int = 0
    velocity: Position3D = field(default_factory=Position3D)
    velocity_confidence: float = 0.0


@dataclass
class GroundTruthObject:
    """仿真真值（仅 evaluation_only 模式）"""
    object_id: str
    prim_path: str = ""
    mass_kg: float = 0.0
    material: str = ""
    friction: float = 0.0
    rigid_body: bool = True
    collision_enabled: bool = True


@dataclass
class SceneObject:
    """单个可操作物体的完整感知信息"""
    object_id: str
    name: str  # 显示名称，用于策略代码中的人类可读匹配
    category_candidates: List[Candidate] = field(default_factory=list)
    pose: Pose = field(default_factory=lambda: Pose(position=Position3D(0, 0, 0)))
    geometry: Geometry = field(default_factory=Geometry)
    appearance: Appearance = field(default_factory=Appearance)
    tracking: Tracking = field(default_factory=Tracking)

    # ==== 向后兼容属性（策略代码可继续用 obj.position / obj.bbox / obj.color / obj.label）====

    @property
    def position(self) -> Tuple[float, float, float]:
        """向后兼容：返回 (x, y, z) 元组"""
        p = self.pose.position
        return (p.x, p.y, p.z)

    @property
    def bbox(self) -> Tuple[float, float, float]:
        """向后兼容：返回 (width, height, depth) 元组"""
        s = self.geometry.size_3d
        if s:
            return (s[0], s[1], s[2]) if len(s) >= 3 else (s[0], s[1], s[0])
        return (0.04, 0.04, 0.04)

    @property
    def color(self) -> Optional[str]:
        """向后兼容：返回最高置信度颜色名"""
        if self.appearance.color_candidates:
            return self.appearance.color_candidates[0].name
        return None

    @property
    def label(self) -> Optional[str]:
        """向后兼容：返回最高置信度类别名"""
        if self.category_candidates:
            return self.category_candidates[0].name
        return None

    def to_dict(self) -> Dict[str, Any]:
        """转为 perception_observation.objects[] 格式"""
        return {
            "object_id": self.object_id,
            "category_candidates": [
                {"name": c.name, "score": c.score}
                for c in self.category_candidates
            ],
            "pose": {
                "position": {
                    "x": self.pose.position.x,
                    "y": self.pose.position.y,
                    "z": self.pose.position.z,
                },
                "orientation": {
                    "x": self.pose.orientation.x,
                    "y": self.pose.orientation.y,
                    "z": self.pose.orientation.z,
                    "w": self.pose.orientation.w,
                },
            },
            "geometry": self.geometry.to_dict(),
            "appearance": {
                "color_candidates": [
                    {"name": c.name, "score": c.score}
                    for c in self.appearance.color_candidates
                ],
                "shape_candidates": [
                    {"name": c.name, "score": c.score}
                    for c in self.appearance.shape_candidates
                ],
                "texture_candidates": [
                    {"name": c.name, "score": c.score}
                    for c in self.appearance.texture_candidates
                ],
            },
            "tracking": {
                "track_age_frames": self.tracking.track_age_frames,
                "velocity": {
                    "x": self.tracking.velocity.x,
                    "y": self.tracking.velocity.y,
                    "z": self.tracking.velocity.z,
                },
                "velocity_confidence": self.tracking.velocity_confidence,
            },
        }


# ============================================================
# 场景感知 — 真实 USD 遍历
# ============================================================
IGNORED_PRIMS = {
    "defaultGroundPlane", "default_ground_plane",
    "World", "GroundPlane", "defaultLight",
    "camera", "Camera", "Light", "light",
    "Franka", "panda_link", "panda_hand",
    "PhysicsScene", "environment",
}


def _is_manipulable(prim_path: str, prim_name: str) -> bool:
    if prim_name in IGNORED_PRIMS:
        return False
    if any(ignored in prim_name for ignored in IGNORED_PRIMS):
        return False
    return True


def _extract_color_from_prim(prim) -> Optional[str]:
    """从 Prim 材质中提取颜色（返回 hex 字符串或颜色名）"""
    try:
        if prim.HasAttribute("displayColor"):
            color_vals = prim.GetAttribute("displayColor").Get()
            if color_vals:
                r, g, b = color_vals[0], color_vals[1], color_vals[2]
                return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
    except Exception:
        pass
    return None


def _extract_label_from_prim(prim) -> Optional[str]:
    """从语义标签提取物体类别"""
    try:
        from omni.usd.schema.semantics import SemanticsAPI
        semantics = SemanticsAPI(prim)
        if semantics:
            return semantics.GetLabel()
    except Exception:
        pass
    return None


def _compute_bbox(prim) -> Tuple[float, float, float]:
    """通过 USD Boundable / Extent 属性计算 Bounding Box"""
    try:
        if prim.HasAttribute("extent"):
            extent = prim.GetAttribute("extent").Get()
            if extent and len(extent) == 2:
                bbox_min, bbox_max = extent
                return (
                    float(bbox_max[0] - bbox_min[0]),
                    float(bbox_max[1] - bbox_min[1]),
                    float(bbox_max[2] - bbox_min[2]),
                )
    except Exception:
        pass
    return (0.04, 0.04, 0.04)


def _color_hex_to_name(hex_color: Optional[str]) -> str:
    """将 hex 颜色映射为人类可读颜色名"""
    if not hex_color:
        return "unknown"
    mapping = {
        "#FF0000": "red", "#FF0500": "red",
        "#0000FF": "blue", "#0005FF": "blue",
        "#00FF00": "green", "#00FF05": "green",
        "#FFFF00": "yellow", "#FF00FF": "purple",
    }
    return mapping.get(hex_color.upper(), hex_color)


def get_scene_objects() -> List[SceneObject]:
    """
    从 Isaac Sim USD Stage 中提取所有可操作物体信息。

    Kit 模式: 遍历真实 USD Stage
    Mock 模式: 返回硬编码测试数据

    Returns:
        List[SceneObject]: 场景中所有物体的感知列表
    """
    if not _KIT_MODE:
        return _get_mock_scene_objects()

    stage = get_current_stage()
    objects = []
    obj_counter = 0

    for prim in stage.TraverseAll():
        prim_path = str(prim.GetPath())
        prim_name = prim.GetName()

        if not _is_manipulable(prim_path, prim_name):
            continue

        try:
            obj_counter += 1
            xform = XFormPrim(prim_path=str(prim_path))
            world_pos, world_rot = xform.get_world_pose()

            bbox = _compute_bbox(prim)
            hex_color = _extract_color_from_prim(prim)
            color_name = _color_hex_to_name(hex_color)
            label = _extract_label_from_prim(prim) or "unknown"

            obj = SceneObject(
                object_id=f"obj_{obj_counter:03d}",
                name=prim_name,
                category_candidates=[
                    Candidate(name=label, score=0.90),
                    Candidate(name="unknown", score=0.10),
                ],
                pose=Pose(
                    position=Position3D(
                        x=float(world_pos[0]),
                        y=float(world_pos[1]),
                        z=float(world_pos[2]),
                    ),
                    orientation=Orientation(
                        x=float(world_rot[0]) if len(world_rot) > 0 else 0.0,
                        y=float(world_rot[1]) if len(world_rot) > 1 else 0.0,
                        z=float(world_rot[2]) if len(world_rot) > 2 else 0.0,
                        w=float(world_rot[3]) if len(world_rot) > 3 else 1.0,
                    ),
                ),
                geometry=Geometry(type="oriented_bbox_3d", size_3d=bbox),
                appearance=Appearance(
                    color_candidates=[
                        Candidate(name=color_name, score=0.90),
                    ],
                    shape_candidates=[
                        Candidate(name=label, score=0.90),
                    ],
                    texture_candidates=[
                        Candidate(name="smooth", score=0.70),
                    ],
                ),
                tracking=Tracking(
                    track_age_frames=1,
                    velocity=Position3D(0, 0, 0),
                    velocity_confidence=0.95,
                ),
            )
            objects.append(obj)
        except Exception:
            continue

    return objects


# ============================================================
# Mock 模式数据
# ============================================================
def _get_mock_scene_objects() -> List[SceneObject]:
    """Mock 模式 — 返回硬编码测试数据（5 个物体）"""
    return [
        SceneObject(
            object_id="obj_001",
            name="红色方块",
            category_candidates=[
                Candidate(name="cube", score=0.95),
                Candidate(name="box", score=0.03),
                Candidate(name="block", score=0.02),
            ],
            pose=Pose(
                position=Position3D(x=0.15, y=0.05, z=0.03),
                orientation=Orientation(),
            ),
            geometry=Geometry(type="oriented_bbox_3d", size_3d=(0.04, 0.04, 0.04)),
            appearance=Appearance(
                color_candidates=[
                    Candidate(name="red", score=0.95),
                    Candidate(name="orange", score=0.03),
                ],
                shape_candidates=[
                    Candidate(name="cubic", score=0.92),
                ],
                texture_candidates=[
                    Candidate(name="matte", score=0.80),
                ],
            ),
            tracking=Tracking(
                track_age_frames=30,
                velocity=Position3D(0, 0, 0),
                velocity_confidence=0.98,
            ),
        ),
        SceneObject(
            object_id="obj_002",
            name="蓝色杯子",
            category_candidates=[
                Candidate(name="cup", score=0.93),
                Candidate(name="bottle", score=0.04),
                Candidate(name="container", score=0.03),
            ],
            pose=Pose(
                position=Position3D(x=-0.12, y=0.08, z=0.06),
                orientation=Orientation(),
            ),
            geometry=Geometry(type="oriented_bbox_3d", size_3d=(0.05, 0.10, 0.05)),
            appearance=Appearance(
                color_candidates=[
                    Candidate(name="blue", score=0.91),
                    Candidate(name="cyan", score=0.06),
                ],
                shape_candidates=[
                    Candidate(name="cylindrical", score=0.91),
                ],
                texture_candidates=[
                    Candidate(name="smooth", score=0.76),
                ],
            ),
            tracking=Tracking(
                track_age_frames=25,
                velocity=Position3D(0, 0, 0),
                velocity_confidence=0.96,
            ),
        ),
        SceneObject(
            object_id="obj_003",
            name="绿色圆柱",
            category_candidates=[
                Candidate(name="cylinder", score=0.96),
                Candidate(name="can", score=0.03),
                Candidate(name="tube", score=0.01),
            ],
            pose=Pose(
                position=Position3D(x=0.20, y=-0.12, z=0.04),
                orientation=Orientation(),
            ),
            geometry=Geometry(type="oriented_bbox_3d", size_3d=(0.04, 0.08, 0.04)),
            appearance=Appearance(
                color_candidates=[
                    Candidate(name="green", score=0.94),
                    Candidate(name="teal", score=0.04),
                ],
                shape_candidates=[
                    Candidate(name="cylindrical", score=0.94),
                ],
                texture_candidates=[
                    Candidate(name="smooth", score=0.82),
                ],
            ),
            tracking=Tracking(
                track_age_frames=28,
                velocity=Position3D(0, 0, 0),
                velocity_confidence=0.97,
            ),
        ),
        SceneObject(
            object_id="obj_004",
            name="黄色三角",
            category_candidates=[
                Candidate(name="triangle", score=0.88),
                Candidate(name="wedge", score=0.07),
                Candidate(name="prism", score=0.05),
            ],
            pose=Pose(
                position=Position3D(x=-0.15, y=-0.10, z=0.02),
                orientation=Orientation(),
            ),
            geometry=Geometry(type="oriented_bbox_3d", size_3d=(0.06, 0.03, 0.06)),
            appearance=Appearance(
                color_candidates=[
                    Candidate(name="yellow", score=0.92),
                    Candidate(name="gold", score=0.05),
                ],
                shape_candidates=[
                    Candidate(name="triangular", score=0.88),
                ],
                texture_candidates=[
                    Candidate(name="matte", score=0.78),
                ],
            ),
            tracking=Tracking(
                track_age_frames=20,
                velocity=Position3D(0, 0, 0),
                velocity_confidence=0.94,
            ),
        ),
        SceneObject(
            object_id="obj_005",
            name="蓝色方块",
            category_candidates=[
                Candidate(name="cube", score=0.94),
                Candidate(name="box", score=0.04),
                Candidate(name="block", score=0.02),
            ],
            pose=Pose(
                position=Position3D(x=0.05, y=-0.15, z=0.03),
                orientation=Orientation(),
            ),
            geometry=Geometry(type="oriented_bbox_3d", size_3d=(0.04, 0.04, 0.04)),
            appearance=Appearance(
                color_candidates=[
                    Candidate(name="blue", score=0.93),
                    Candidate(name="cyan", score=0.05),
                ],
                shape_candidates=[
                    Candidate(name="cubic", score=0.93),
                ],
                texture_candidates=[
                    Candidate(name="matte", score=0.81),
                ],
            ),
            tracking=Tracking(
                track_age_frames=32,
                velocity=Position3D(0, 0, 0),
                velocity_confidence=0.98,
            ),
        ),
    ]


# ============================================================
# 机械臂状态查询
# ============================================================
def get_robot_state(robot: Any = None) -> Dict[str, Any]:
    """获取 Franka Panda 当前状态"""
    if robot is not None:
        state = robot.get_robot_state()
        return {
            "joint_angles": state.joint_angles,
            "end_effector_pose": {
                "x": state.end_effector_pose[0],
                "y": state.end_effector_pose[1],
                "z": state.end_effector_pose[2],
                "roll": state.end_effector_pose[3],
                "pitch": state.end_effector_pose[4],
                "yaw": state.end_effector_pose[5],
            },
        }
    return {
        "joint_angles": [0.0, -0.5, 0.0, -1.2, 0.0, 1.0, 0.5],
        "end_effector_pose": {
            "x": 0.0, "y": 0.0, "z": 0.35,
            "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
        },
    }


def get_gripper_state(robot: Any = None) -> Dict[str, Any]:
    """获取夹爪当前状态"""
    if robot is not None:
        state = robot.get_gripper_state()
        return {
            "width": state.width,
            "force": state.force,
            "is_closed": state.is_closed,
        }
    return {"width": 0.08, "force": 0.0, "is_closed": False}


# ============================================================
# 场景状态导出 — perception_observation v1.0.0
# ============================================================
def export_scene_state(
    robot: Any = None,
    log_dir: str = "logs",
) -> Dict[str, Any]:
    """
    导出 perception_observation v1.0.0 格式的完整场景状态。

    每次场景变化时自动调用，生成 logs/scene_state.json。

    Returns:
        dict: perception_observation 格式的完整场景信息
    """
    scene_objects = get_scene_objects()
    ts_ms = int(time.time() * 1000)
    obs_id = f"obs_{ts_ms}_{uuid.uuid4().hex[:4]}"

    # 构建仿真元数据（真值）
    ground_truth = []
    for obj in scene_objects:
        gt = GroundTruthObject(
            object_id=obj.object_id,
            prim_path=f"/World/Table/{obj.name.replace(' ', '_')}",
            mass_kg=0.15 if "方块" in obj.name else 0.2,
            material="plastic" if "方块" in obj.name else "glass",
            friction=0.4,
            rigid_body=True,
            collision_enabled=True,
        )
        ground_truth.append(gt)

    scene_dict = {
        "schema_version": "1.0.0",
        "message_type": "perception_observation",
        "observation_id": obs_id,
        "scene_id": "table_scene_001",
        "timestamp": ts_ms,
        "clock_domain": "unix_utc",
        "coordinate_system": "robot_base",
        "source": {
            "module": "perception_pipeline",
            "pipeline_version": "1.0.0",
            "sensor_ids": ["camera_front", "depth_front"],
        },
        "objects": [obj.to_dict() for obj in scene_objects],
        "simulation_metadata": {
            "evaluation_only": True,
            "ground_truth_objects": [
                {
                    "object_id": gt.object_id,
                    "prim_path": gt.prim_path,
                    "mass_kg": gt.mass_kg,
                    "material": gt.material,
                    "friction": gt.friction,
                    "rigid_body": gt.rigid_body,
                    "collision_enabled": gt.collision_enabled,
                }
                for gt in ground_truth
            ],
        },
    }

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    output_file = log_path / "scene_state.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(scene_dict, f, indent=2, ensure_ascii=False)

    print(f"[PERCEPTION] 场景状态已导出 -> {output_file} "
          f"({len(scene_objects)} 个物体)")
    return scene_dict


# ============================================================
# 自检
# ============================================================
if __name__ == "__main__":
    result = export_scene_state()
    print(json.dumps(result, indent=2, ensure_ascii=False))
