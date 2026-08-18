from copy import deepcopy

from .spatial_context import enrich_spatial_context


STACKING_CUBES = {
    "scene_id": "stacking_cubes",
    "coordinate_frame": "world",
    "objects": [
        {
            "id": "red_cube",
            "category": "红色方块",
            "pose": {"x": 0.25, "y": 0.0, "z": 0.04},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "红色方块", "color": "red"},
            "execution": {
                "movable": True,
                "graspable": True,
                "stackable_destination": True,
                "valid_destination": True,
            },
        },
        {
            "id": "green_cube",
            "category": "绿色方块",
            "pose": {"x": 0.25, "y": 0.0, "z": 0.12},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "绿色方块", "color": "green"},
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "zone_unstack_target",
            "category": "桌子",
            "pose": {"x": 0.4, "y": 0.0, "z": 0.03},
            "dimensions": {"x": 0.50, "y": 0.05, "z": 0.50},
            "attributes": {"purpose": "safe_placement"},
            "execution": {
                "movable": False,
                "graspable": False,
                "valid_destination": True,
            },
        },
    ],
}


SORTING_WORKCELL = {
    "scene_id": "sorting_workcell",
    "coordinate_frame": "world",
    "objects": [
        {
            "id": "red_sort_cube",
            "category": "红色方块",
            "pose": {"x": 0.16, "y": -0.18, "z": 0.04},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {
                "display_name": "红色方块",
                "color": "red",
                "workcell_role": "待分拣物体",
            },
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "green_sort_cube",
            "category": "绿色方块",
            "pose": {"x": 0.16, "y": 0.0, "z": 0.04},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {
                "display_name": "绿色方块",
                "color": "green",
                "workcell_role": "待分拣物体",
            },
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "blue_sort_cube",
            "category": "蓝色方块",
            "pose": {"x": 0.16, "y": 0.18, "z": 0.04},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {
                "display_name": "蓝色方块",
                "color": "blue",
                "workcell_role": "待分拣物体",
            },
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "left_sort_tray",
            "category": "红色托盘",
            "pose": {"x": 0.32, "y": -0.18, "z": 0.03},
            "dimensions": {"x": 0.12, "y": 0.12, "z": 0.03},
            "attributes": {
                "display_name": "红色托盘",
                "color": "red",
                "purpose": "sorting_destination",
                "slot": "left",
            },
            "execution": {
                "movable": False,
                "graspable": False,
                "valid_destination": True,
            },
        },
        {
            "id": "middle_sort_tray",
            "category": "绿色托盘",
            "pose": {"x": 0.32, "y": 0.0, "z": 0.03},
            "dimensions": {"x": 0.12, "y": 0.12, "z": 0.03},
            "attributes": {
                "display_name": "绿色托盘",
                "color": "green",
                "purpose": "sorting_destination",
                "slot": "middle",
            },
            "execution": {
                "movable": False,
                "graspable": False,
                "valid_destination": True,
            },
        },
        {
            "id": "right_sort_tray",
            "category": "蓝色托盘",
            "pose": {"x": 0.32, "y": 0.18, "z": 0.03},
            "dimensions": {"x": 0.12, "y": 0.12, "z": 0.03},
            "attributes": {
                "display_name": "蓝色托盘",
                "color": "blue",
                "purpose": "sorting_destination",
                "slot": "right",
            },
            "execution": {
                "movable": False,
                "graspable": False,
                "valid_destination": True,
            },
        },
    ],
}


def get_mock_scene(scene_id: str) -> dict:
    scenes = {
        "stacking_cubes": STACKING_CUBES,
        "sorting_workcell": SORTING_WORKCELL,
    }
    if scene_id not in scenes:
        raise ValueError(f"unsupported mock scene: {scene_id}")
    scene = deepcopy(scenes[scene_id])
    scene["schema_version"] = "perception.v1"
    scene["execution_context"] = {"backend": "mock", "scene_revision": "1"}
    for item in scene.get("objects", []):
        attributes = item.setdefault("attributes", {})
        attributes.setdefault("display_name", item.get("category", item.get("id", "对象")))
    return enrich_spatial_context(scene)
