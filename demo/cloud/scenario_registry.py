from __future__ import annotations

from copy import deepcopy


_CAPABILITIES = ("prepare_and_perceive", "execute_strategy")
_LIVESTREAM_URL = "/live/isaac/index.m3u8"
_SCENE_VERSION = "v1.1-supplement"

_VERIFIED_SCENARIOS: tuple[dict[str, object], ...] = (
    {
        "id": "multi-red-001",
        "case_id": "multi-red-001",
        "name": "红色方块目标绑定",
        "category": "target_binding",
        "instruction": "把红色方块放到桌面区域",
        "backend": "isaac",
        "scene_id": "multi_object_stacking",
        "scene_version": _SCENE_VERSION,
        "object_id": "red_cube",
        "destination_id": "zone_unstack_target",
        "capabilities": _CAPABILITIES,
        "livestream_url": _LIVESTREAM_URL,
    },
    {
        "id": "multi-green-001",
        "case_id": "multi-green-001",
        "name": "绿色方块目标绑定",
        "category": "target_binding",
        "instruction": "把绿色方块放到桌面区域",
        "backend": "isaac",
        "scene_id": "multi_object_stacking",
        "scene_version": _SCENE_VERSION,
        "object_id": "green_cube",
        "destination_id": "zone_unstack_target",
        "capabilities": _CAPABILITIES,
        "livestream_url": _LIVESTREAM_URL,
    },
    {
        "id": "multi-red-003",
        "case_id": "multi-red-003",
        "name": "右侧红色方块目标绑定",
        "category": "target_binding",
        "instruction": "选择靠近右侧的红色方块并放置",
        "backend": "isaac",
        "scene_id": "multi_object_stacking",
        "scene_version": _SCENE_VERSION,
        "object_id": "red_cube_right",
        "destination_id": "zone_unstack_target",
        "initial_scene_poses": {
            "red_cube_left": {"x": 0.46, "y": -0.14, "z": 0.0258},
            "red_cube_right": {"x": 0.60, "y": -0.04, "z": 0.0258},
        },
        "capabilities": _CAPABILITIES,
        "livestream_url": _LIVESTREAM_URL,
    },
)

_BY_ID = {str(scenario["id"]): scenario for scenario in _VERIFIED_SCENARIOS}


def list_verified_scenarios() -> list[dict[str, object]]:
    return deepcopy(list(_VERIFIED_SCENARIOS))


def get_verified_scenario(scene_id: str) -> dict[str, object]:
    try:
        scenario = _BY_ID[scene_id]
    except KeyError as exc:
        raise KeyError(f"scene is not Isaac-verified: {scene_id}") from exc
    return deepcopy(scenario)
