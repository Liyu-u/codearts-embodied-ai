"""
环境感知脚本
同学 C（吴昌庆）上传：实时从 Isaac Sim 6.0.1 仿真战场中
抓取所有物体的名字、3D 世界坐标和 Bounding Box 尺寸，
并导出为 scene_state.json 供其他模块读取。
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path


# ============================================================
# 数据结构定义
# ============================================================
@dataclass
class SceneObject:
    """场景中的单个可操作物体"""
    name: str
    position: Tuple[float, float, float]  # (x, y, z) 世界坐标 (m)
    bbox: Tuple[float, float, float]      # (width, height, depth) Bounding Box (m)
    color: Optional[str] = None            # RGBA 颜色值
    label: Optional[str] = None            # 语义标签


@dataclass
class SceneState:
    """完整场景状态快照"""
    scene_id: str
    timestamp: str
    objects: List[SceneObject]
    robot_joint_angles: List[float]
    gripper_width: float
    gripper_force: float


# ============================================================
# 场景感知主函数
# ============================================================
def get_scene_objects() -> List[SceneObject]:
    """
    从 Isaac Sim USD Stage 中提取所有物体信息。

    实际部署时将调用 Isaac Sim Python API:
        from omni.isaac.core.utils.stage import get_current_stage
        stage = get_current_stage()
        for prim in stage.TraverseAll(): ...

    Returns:
        List[SceneObject]: 场景中所有物体的感知列表
    """
    # TODO: 替换为真实 Isaac Sim API 调用
    # 当前返回 Mock 数据供独立调试
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


def get_robot_state() -> Dict[str, Any]:
    """获取 Franka Panda 当前状态"""
    # TODO: 从 Isaac Sim 读取真实关节角度
    return {
        "joint_angles": [0.0, -0.5, 0.0, -1.2, 0.0, 1.0, 0.5],
        "end_effector_pose": {
            "x": 0.0, "y": 0.0, "z": 0.35,
            "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
        },
    }


def get_gripper_state() -> Dict[str, Any]:
    """获取夹爪当前状态"""
    return {"width": 0.08, "force": 0.0, "is_closed": False}


# ============================================================
# 场景状态持久化 (自动生成 scene_state.json)
# ============================================================
def export_scene_state(log_dir: str = "logs") -> Dict[str, Any]:
    """
    将当前场景状态导出为 JSON 文件。
    此函数在每次场景变化时自动调用，生成 logs/scene_state.json。

    Args:
        log_dir: 日志输出目录 (默认 logs/)

    Returns:
        dict: 包含完整场景信息的字典
    """
    objects = get_scene_objects()
    robot = get_robot_state()
    gripper = get_gripper_state()

    scene_dict = {
        "scene_id": "scene-001",
        "timestamp": "2026-07-14T10:00:00Z",
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
            for obj in objects
        ],
        "robot_state": robot,
        "gripper_state": gripper,
    }

    # 确保 logs 目录存在
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    output_file = log_path / "scene_state.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(scene_dict, f, indent=2, ensure_ascii=False)

    print(f"[PERCEPTION] 场景状态已导出 -> {output_file}")
    return scene_dict


if __name__ == "__main__":
    result = export_scene_state()
    print(json.dumps(result, indent=2, ensure_ascii=False))
