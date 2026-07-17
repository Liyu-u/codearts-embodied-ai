"""
环境感知脚本 — Isaac Sim 6.0.1 真实 USD Stage 版
同学 C（吴昌庆）上传：实时从仿真战场抓取所有物体信息

功能：
1. 遍历 USD Stage 中所有 Xform Prim
2. 提取物体的名称、世界坐标、Bounding Box 尺寸
3. 通过语义标签匹配物体属性（颜色、类别）
4. 导出 scene_state.json 供 A（意图解析）和 B（策略生成）读取
"""

import json
import time
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
# 数据结构
# ============================================================
@dataclass
class SceneObject:
    """场景中的单个可操作物体"""
    name: str
    position: Tuple[float, float, float]
    bbox: Tuple[float, float, float]
    color: Optional[str] = None
    label: Optional[str] = None


@dataclass
class SceneState:
    """完整场景状态快照"""
    scene_id: str
    timestamp: str
    objects: List[Dict[str, Any]] = field(default_factory=list)
    robot_joint_angles: List[float] = field(default_factory=list)
    gripper_width: float = 0.08
    gripper_force: float = 0.0


# ============================================================
# 场景感知 — 真实 USD 遍历
# ============================================================
# 需要忽略的 Prim 名称（地面、光源、相机等非交互物体）
IGNORED_PRIMS = {
    "defaultGroundPlane", "default_ground_plane",
    "World", "GroundPlane", "defaultLight",
    "camera", "Camera", "Light", "light",
    "Franka", "panda_link", "panda_hand",
    "PhysicsScene", "environment",
}


def _is_manipulable(prim_path: str, prim_name: str) -> bool:
    """判断 Prim 是否为可操作物体"""
    if prim_name in IGNORED_PRIMS:
        return False
    if any(ignored in prim_name for ignored in IGNORED_PRIMS):
        return False
    # 只处理 Xform 类型的 Prim
    return True


def _extract_color(prim) -> Optional[str]:
    """从 Prim 材质/MDL 中提取颜色信息"""
    try:
        # 尝试从 displayColor 属性读取
        if prim.HasAttribute("displayColor"):
            color_attr = prim.GetAttribute("displayColor")
            color_vals = color_attr.Get()
            if color_vals:
                r, g, b = color_vals[0], color_vals[1], color_vals[2]
                return f"#{int(r*255):02X}{int(g*255):02X}{int(b*255):02X}"
    except Exception:
        pass

    # 从语义标签推断
    try:
        from omni.usd.schema.semantics import SemanticsAPI
        semantics = SemanticsAPI(prim)
        if semantics:
            label = semantics.GetLabel()
            if label:
                # 从语义标签提取颜色
                color_map = {
                    "red": "#FF0000", "blue": "#0000FF",
                    "green": "#00FF00", "yellow": "#FFFF00",
                }
                for cn, cv in color_map.items():
                    if cn in label.lower():
                        return cv
    except Exception:
        pass

    return None


def _extract_label(prim) -> Optional[str]:
    """从 Prim 语义标签中提取物体类别"""
    try:
        from omni.usd.schema.semantics import SemanticsAPI
        semantics = SemanticsAPI(prim)
        if semantics:
            return semantics.GetLabel()
    except Exception:
        pass
    return None


def get_scene_objects() -> List[SceneObject]:
    """
    从 Isaac Sim USD Stage 中提取所有可操作物体信息。

    在 Kit 模式下调用真实的 Isaac Sim API 遍历 Stage；
    在 Mock 模式下返回硬编码的测试数据。

    Returns:
        List[SceneObject]: 场景中所有物体的感知列表
    """
    if not _KIT_MODE:
        return _get_mock_scene_objects()

    stage = get_current_stage()
    objects = []

    for prim in stage.TraverseAll():
        prim_path = str(prim.GetPath())
        prim_name = prim.GetName()

        if not _is_manipulable(prim_path, prim_name):
            continue

        try:
            xform = XFormPrim(prim_path=str(prim_path))
            position = xform.get_world_pose()[0]

            # 计算 Bounding Box
            bbox = _compute_bbox(prim)

            color = _extract_color(prim)
            label = _extract_label(prim)

            objects.append(SceneObject(
                name=prim_name,
                position=(float(position[0]), float(position[1]), float(position[2])),
                bbox=bbox,
                color=color,
                label=label,
            ))
        except Exception as e:
            # 跳过无法处理的 Prim（如材质、Shader 等）
            continue

    return objects


def _compute_bbox(prim) -> Tuple[float, float, float]:
    """通过 USD Boundable 或 Extent 属性计算 Bounding Box"""
    try:
        # 尝试从 extent 属性读取
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

    # 默认返回 4cm 立方体
    return (0.04, 0.04, 0.04)


# ============================================================
# 机械臂状态查询
# ============================================================
def _get_mock_scene_objects() -> List[SceneObject]:
    """Mock 模式 — 返回硬编码测试数据"""
    return [
        SceneObject(
            name="红色方块",
            position=(0.1500, 0.0500, 0.0300),
            bbox=(0.0400, 0.0400, 0.0400),
            color="#FF0000",
            label="cube",
        ),
        SceneObject(
            name="蓝色杯子",
            position=(-0.1000, -0.0800, 0.0600),
            bbox=(0.0700, 0.1000, 0.0700),
            color="#0000FF",
            label="cup",
        ),
        SceneObject(
            name="绿色圆柱",
            position=(0.2000, -0.1200, 0.0400),
            bbox=(0.0500, 0.0800, 0.0500),
            color="#00FF00",
            label="cylinder",
        ),
        SceneObject(
            name="黄色三角",
            position=(-0.1500, 0.1000, 0.0200),
            bbox=(0.0600, 0.0300, 0.0600),
            color="#FFFF00",
            label="triangle",
        ),
        SceneObject(
            name="蓝色方块",
            position=(0.0500, -0.1500, 0.0300),
            bbox=(0.0400, 0.0400, 0.0400),
            color="#0000FF",
            label="cube",
        ),
    ]


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
# 场景状态导出（供 server.py 调用）
# ============================================================
def export_scene_state(
    robot: Any = None,
    log_dir: str = "logs",
) -> Dict[str, Any]:
    """
    将当前场景状态导出为 JSON 文件。
    每次场景变化时自动调用，生成 logs/scene_state.json。

    Args:
        robot: ExecutionWrapper 实例（可选）
        log_dir: 日志输出目录

    Returns:
        dict: 完整场景信息
    """
    scene_objects = get_scene_objects()
    robot_state = get_robot_state(robot)
    gripper_state = get_gripper_state(robot)

    scene_dict = {
        "scene_id": f"scene-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "objects": [
            {
                "name": obj.name,
                "position": {
                    "x": obj.position[0],
                    "y": obj.position[1],
                    "z": obj.position[2],
                },
                "bbox": {
                    "width": obj.bbox[0],
                    "height": obj.bbox[1],
                    "depth": obj.bbox[2],
                },
                "color": obj.color,
                "label": obj.label,
            }
            for obj in scene_objects
        ],
        "robot_state": robot_state,
        "gripper_state": gripper_state,
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
# 自检（独立运行）
# ============================================================
if __name__ == "__main__":
    result = export_scene_state()
    print(json.dumps(result, indent=2, ensure_ascii=False))
