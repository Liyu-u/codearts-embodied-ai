"""Small data helpers shared by the policy-repair modules."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def clone(value: Any) -> Any:
    return deepcopy(value)


def normalize_strategy(strategy: dict) -> dict:
    """Return a copy with stable ids and action-node defaults."""
    normalized = clone(strategy)
    normalized.setdefault("strategy_id", "generated_strategy")
    steps = normalized.setdefault("steps", [])
    for index, step in enumerate(steps, start=1):
        step.setdefault("id", f"step_{index}")
        step.setdefault("type", "action")
        if step.get("type") == "action":
            step.setdefault("arguments", {})
    return normalized


def get_objects(state: dict) -> list[dict]:
    objects = state.get("objects", [])
    if isinstance(objects, dict):
        return [{"id": key, **value} for key, value in objects.items()]
    return objects


def find_object(state: dict, object_id_or_name: str) -> dict | None:
    for item in get_objects(state):
        if item.get("id") == object_id_or_name or item.get("name") == object_id_or_name:
            return item
    return None


def update_object(state: dict, object_id: str, **changes: Any) -> None:
    objects = state.setdefault("objects", [])
    if isinstance(objects, dict):
        objects.setdefault(object_id, {}).update(changes)
        return
    for item in objects:
        if item.get("id") == object_id:
            item.update(changes)
            return


def distance(first: list[float] | None, second: list[float] | None) -> float:
    if not first or not second or len(first) != len(second):
        return float("inf")
    return sum((float(a) - float(b)) ** 2 for a, b in zip(first, second)) ** 0.5
