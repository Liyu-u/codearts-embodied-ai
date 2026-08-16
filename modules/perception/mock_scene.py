from copy import deepcopy


STACKING_CUBES = {
    "scene_id": "stacking_cubes",
    "coordinate_frame": "world",
    "objects": [
        {
            "id": "red_cube",
            "category": "cube",
            "pose": {"x": 0.25, "y": 0.0, "z": 0.04},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "红色方块", "color": "red"},
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "green_cube",
            "category": "cube",
            "pose": {"x": 0.25, "y": 0.0, "z": 0.12},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "绿色方块", "color": "green"},
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "zone_unstack_target",
            "category": "target_zone",
            "pose": {"x": 0.4, "y": 0.0, "z": 0.03},
            "attributes": {"purpose": "safe_placement"},
            "execution": {
                "movable": False,
                "graspable": False,
                "valid_destination": True,
            },
        },
    ],
}


def get_mock_scene(scene_id: str) -> dict:
    if scene_id != "stacking_cubes":
        raise ValueError(f"unsupported mock scene: {scene_id}")
    return deepcopy(STACKING_CUBES)
