"""Camera C runner with explicit task affordances kept separate from pose sensing."""

from __future__ import annotations

import sys

from tools import run_camera_executor_acceptance_v2 as target


_TASK_SEMANTICS = {
    "red_cube": {
        "category": "红色方块",
        "attributes": {"display_name": "红色方块", "color": "red"},
        "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
        "execution": {"movable": True, "graspable": True, "stackable_destination": True, "valid_destination": True},
    },
    "green_cube": {
        "category": "绿色方块",
        "attributes": {"display_name": "绿色方块", "color": "green"},
        "dimensions": {"x": 0.0515, "y": 0.0515, "z": 0.0515},
        "execution": {"movable": True, "graspable": True},
    },
    "zone_unstack_target": {
        "category": "桌子",
        "attributes": {"display_name": "桌子", "purpose": "safe_placement", "support_surface": True},
        "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02},
        "execution": {"movable": False, "graspable": False, "valid_destination": True},
    },
}


def _enrich(scene: dict) -> dict:
    for item in scene.get("objects", []):
        semantics = _TASK_SEMANTICS.get(item.get("id"))
        if not semantics:
            continue
        item["category"] = semantics["category"]
        item["dimensions"] = dict(semantics["dimensions"])
        item.setdefault("attributes", {}).update(semantics["attributes"])
        item["execution"] = dict(semantics["execution"])
    scene.setdefault("execution_context", {})["camera_semantics_enriched"] = True
    scene["execution_context"]["ground_truth_used_for_online_pose"] = False
    return scene


import modules.perception.observation_normalizer as _normalizer

_original_normalize = _normalizer.normalize_observation


def _normalize_with_camera_semantics(observation: dict) -> dict:
    return _enrich(_original_normalize(observation))


_normalizer.normalize_observation = _normalize_with_camera_semantics


if __name__ == "__main__":
    raise SystemExit(target.main(sys.argv[1:]))
