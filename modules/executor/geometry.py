"""Small geometry helpers shared by mock and real execution backends."""

from __future__ import annotations

from typing import Any


_AXIS_ALIASES = {
    # perception.v1 uses width/height/depth; legacy scenes use x/y/z.
    "x": ("x", "width"),
    "y": ("y", "depth"),
    "z": ("z", "height"),
}


def dimension_axis(dimensions: Any, axis: str, default: float = 0.0) -> float:
    """Read one physical axis from either supported dimension convention."""
    if not isinstance(dimensions, dict):
        return float(default)
    for key in _AXIS_ALIASES.get(axis, (axis,)):
        value = dimensions.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return float(default)


__all__ = ["dimension_axis"]
