"""Camera-aware A/B/D batch wrapper with preserved base loader."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_real_acceptance_batch as base

_ORIGINAL_LOAD_PERCEPTION = base._load_perception
_TASK_SEMANTICS = {
    "red_cube": {"category": "红色方块", "attributes": {"display_name": "红色方块", "color": "red"}, "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04}, "execution": {"movable": True, "graspable": True, "stackable_destination": True, "valid_destination": True}},
    "green_cube": {"category": "绿色方块", "attributes": {"display_name": "绿色方块", "color": "green"}, "dimensions": {"x": 0.0515, "y": 0.0515, "z": 0.0515}, "execution": {"movable": True, "graspable": True}},
    "zone_unstack_target": {"category": "桌子", "attributes": {"display_name": "桌子", "purpose": "safe_placement", "support_surface": True}, "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02}, "execution": {"movable": False, "graspable": False, "valid_destination": True}},
}


def _load_perception(document: dict, case: dict) -> dict:
    scene = _ORIGINAL_LOAD_PERCEPTION(document, case)
    for item in scene.get("objects", []):
        semantics = _TASK_SEMANTICS.get(item.get("id"))
        if semantics:
            item["category"] = semantics["category"]
            item["dimensions"] = dict(semantics["dimensions"])
            item.setdefault("attributes", {}).update(semantics["attributes"])
            item["execution"] = dict(semantics["execution"])
    scene.setdefault("execution_context", {})["camera_semantics_enriched"] = True
    scene["execution_context"]["ground_truth_used_for_online_pose"] = False
    return scene


base._load_perception = _load_perception


if __name__ == "__main__":
    raise SystemExit(base.main())
