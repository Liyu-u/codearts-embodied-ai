"""Geometry-backed spatial relations and human-readable perception messages."""

from __future__ import annotations

from copy import deepcopy
from math import sqrt


# World-frame convention shared by perception, intent grounding, and the UI.
# x is front/back depth, y is left/right lateral position, z is height.
AXES = {"left_right": "y", "front_back": "x", "vertical": "z"}
_DEADBAND_M = 0.01
_NEAR_THRESHOLD_M = 0.30


def _pose(item: dict) -> dict:
    value = item.get("pose") if isinstance(item, dict) else {}
    return value if isinstance(value, dict) else {}


def _label(item: dict) -> str:
    attributes = item.get("attributes") if isinstance(item, dict) else {}
    attributes = attributes if isinstance(attributes, dict) else {}
    return str(attributes.get("display_name") or item.get("category") or item.get("id") or "对象")


def _add_relation(
    relations: list[dict],
    messages: list[dict],
    subject: dict,
    predicate: str,
    target: dict,
    confidence: float = 1.0,
) -> None:
    relation = {
        "subject": subject["id"],
        "predicate": predicate,
        "object": target["id"],
        "confidence": round(float(confidence), 3),
    }
    key = (relation["subject"], relation["predicate"], relation["object"])
    if any((item.get("subject"), item.get("predicate"), item.get("object")) == key for item in relations):
        return
    relations.append(relation)
    labels = {
        "left_of": "左侧",
        "right_of": "右侧",
        "in_front_of": "前方",
        "behind": "后方",
        "above": "上方",
        "below": "下方",
        "near": "附近",
    }
    messages.append({
        "subject_id": subject["id"],
        "predicate": predicate,
        "object_id": target["id"],
        "confidence": relation["confidence"],
        "message": (
            f"{_label(subject)}（{subject['id']}）位于"
            f"{_label(target)}（{target['id']}）{labels.get(predicate, predicate)}"
        ),
    })


def enrich_spatial_context(scene: dict) -> dict:
    """Add structured spatial relations and explainable spatial messages."""

    output = deepcopy(scene)
    objects = [item for item in output.get("objects", []) if isinstance(item, dict) and item.get("id")]
    relations = [item for item in output.get("relations", []) if isinstance(item, dict)]
    messages: list[dict] = []

    for index, first in enumerate(objects):
        first_pose = _pose(first)
        for second in objects[index + 1:]:
            second_pose = _pose(second)
            first_x, second_x = float(first_pose.get("x", 0.0)), float(second_pose.get("x", 0.0))
            first_y, second_y = float(first_pose.get("y", 0.0)), float(second_pose.get("y", 0.0))
            first_z, second_z = float(first_pose.get("z", 0.0)), float(second_pose.get("z", 0.0))

            lateral_delta = second_y - first_y
            if abs(lateral_delta) > _DEADBAND_M:
                if lateral_delta > 0:
                    _add_relation(relations, messages, first, "left_of", second)
                    _add_relation(relations, messages, second, "right_of", first)
                else:
                    _add_relation(relations, messages, second, "left_of", first)
                    _add_relation(relations, messages, first, "right_of", second)

            depth_delta = second_x - first_x
            if abs(depth_delta) > _DEADBAND_M:
                if depth_delta > 0:
                    _add_relation(relations, messages, second, "in_front_of", first)
                    _add_relation(relations, messages, first, "behind", second)
                else:
                    _add_relation(relations, messages, first, "in_front_of", second)
                    _add_relation(relations, messages, second, "behind", first)

            if abs(first_z - second_z) > _DEADBAND_M:
                if first_z > second_z:
                    _add_relation(relations, messages, first, "above", second)
                    _add_relation(relations, messages, second, "below", first)
                else:
                    _add_relation(relations, messages, second, "above", first)
                    _add_relation(relations, messages, first, "below", second)

            distance = sqrt(
                (first_x - second_x) ** 2
                + (first_y - second_y) ** 2
                + (first_z - second_z) ** 2
            )
            if distance <= _NEAR_THRESHOLD_M:
                _add_relation(
                    relations,
                    messages,
                    first,
                    "near",
                    second,
                    max(0.1, 1.0 - distance / _NEAR_THRESHOLD_M),
                )

    output["relations"] = relations
    output["spatial_axes"] = dict(AXES)
    output["spatial_messages"] = messages
    return output
