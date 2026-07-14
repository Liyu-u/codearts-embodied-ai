"""
场景感知脚本
同学 C (昌庆)：提取 Isaac Sim 场景中物体的 3D 坐标与 Bounding Box
"""

import json
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, asdict


@dataclass
class BoundingBox:
    """3D 包围盒"""
    width: float
    height: float
    depth: float


@dataclass
class SceneObject:
    """场景物体"""
    name: str
    position: Dict[str, float]  # {x, y, z}
    bbox: Dict[str, float]  # {width, height, depth}
    color: Optional[str] = None
    label: Optional[str] = None


def get_scene_objects() -> List[SceneObject]:
    """
    从 Isaac Sim 场景中提取所有物体的 3D 信息

    Returns:
        List[SceneObject]: 场景中所有可操作物体的列表
    """
    # TODO: 实际调用 Isaac Sim USD API 获取场景图
    # 示例返回
    return [
        SceneObject(
            name="红色方块",
            position={"x": 0.15, "y": 0.05, "z": 0.03},
            bbox={"width": 0.04, "height": 0.04, "depth": 0.04},
            color="#FF0000",
            label="cube",
        ),
        SceneObject(
            name="蓝色杯子",
            position={"x": -0.10, "y": -0.08, "z": 0.06},
            bbox={"width": 0.07, "height": 0.10, "depth": 0.07},
            color="#0000FF",
            label="cup",
        ),
    ]


def export_scene_json(filepath: str = "scene_state.json") -> Dict[str, Any]:
    """将场景状态导出为 JSON 文件"""
    objects = get_scene_objects()
    scene_dict = {
        "scene_id": "scene-001",
        "timestamp": "2026-07-14T00:00:00Z",
        "objects": [asdict(obj) for obj in objects],
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(scene_dict, f, indent=2, ensure_ascii=False)
    return scene_dict
