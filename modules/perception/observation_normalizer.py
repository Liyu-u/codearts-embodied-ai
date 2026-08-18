from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from integration.contract_validation import assert_contract


def _assert_finite_numbers(value: Any, path: str = "$") -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _assert_finite_numbers(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite_numbers(child, f"{path}[{index}]")


def _best_candidate(candidates: list[dict], field: str, required: bool) -> str | None:
    if not candidates:
        if required:
            raise ValueError(f"{field} must contain at least one candidate")
        return None
    return max(candidates, key=lambda candidate: candidate["score"])["name"]


def _normalize_object(raw: dict) -> dict:
    position = raw["pose"]["position"]
    orientation = raw["pose"]["orientation"]
    size = raw["geometry"]["size"]
    appearance = raw["appearance"]

    attributes = {
        "display_name": _best_candidate(
            raw["category_candidates"], "category_candidates", required=True
        ),
        "category_candidates": deepcopy(raw["category_candidates"]),
        "color_candidates": deepcopy(appearance["color_candidates"]),
        "shape_candidates": deepcopy(appearance["shape_candidates"]),
        "texture_candidates": deepcopy(appearance["texture_candidates"]),
        "tracking": deepcopy(raw["tracking"]),
        "geometry_type": raw["geometry"]["type"],
    }
    for name in ("color", "shape", "texture"):
        selected = _best_candidate(
            appearance[f"{name}_candidates"],
            f"appearance.{name}_candidates",
            required=False,
        )
        if selected is not None:
            attributes[name] = selected

    return {
        "id": raw["object_id"],
        "category": attributes["display_name"],
        "pose": {"x": position["x"], "y": position["y"], "z": position["z"]},
        "orientation": deepcopy(orientation),
        "dimensions": {
            "width": size["width"],
            "height": size["height"],
            "depth": size["depth"],
        },
        "attributes": attributes,
    }


def normalize_observation(observation: dict) -> dict:
    """Convert the formal A wire message into the project's internal perception.v1."""
    assert_contract(observation, "perception_observation.1.0.0")
    _assert_finite_numbers(observation)

    seen_ids: set[str] = set()
    objects: list[dict] = []
    for raw in observation["objects"]:
        object_id = raw["object_id"]
        if object_id in seen_ids:
            raise ValueError(f"duplicate object_id: {object_id}")
        seen_ids.add(object_id)
        objects.append(_normalize_object(raw))

    execution_context = {
        "backend": "external_observation",
        "observation_id": observation["observation_id"],
        "timestamp": observation["timestamp"],
        "clock_domain": observation["clock_domain"],
        "source": deepcopy(observation["source"]),
        "orientation_order": "xyzw",
    }
    if "simulation_metadata" in observation:
        execution_context["simulation_metadata"] = deepcopy(
            observation["simulation_metadata"]
        )

    return {
        "schema_version": "perception.v1",
        "scene_id": observation["scene_id"],
        "coordinate_frame": observation["coordinate_system"],
        "objects": objects,
        "execution_context": execution_context,
    }
