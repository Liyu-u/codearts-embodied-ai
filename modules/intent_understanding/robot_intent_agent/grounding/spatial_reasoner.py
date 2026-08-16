"""Geometry-backed spatial relation reasoning."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot
from typing import Any, Iterable, List, Optional, Sequence


@dataclass
class SpatialDecision:
    relation: str
    candidate_ids: List[str] = field(default_factory=list)
    selected_id: Optional[str] = None
    margin: float = 0.0
    status: str = "UNRESOLVED"
    evidence: List[str] = field(default_factory=list)
    reference_frame: str = "robot_base"


def _position(obj: Any) -> tuple[float, float, float]:
    position = getattr(obj, "position", None)
    if position is None and isinstance(obj, dict):
        position = obj.get("position", {})
    if isinstance(position, dict):
        return (float(position.get("x", 0.0)), float(position.get("y", 0.0)), float(position.get("z", 0.0)))
    return (float(getattr(position, "x", 0.0)), float(getattr(position, "y", 0.0)), float(getattr(position, "z", 0.0)))


class SpatialReasoner:
    def __init__(self, tolerance: float = 0.01, reference_frame: str = "robot_base"):
        self.tolerance = tolerance
        self.reference_frame = reference_frame

    def rank(self, objects: Sequence[Any], relation: str, reference: Any = None) -> SpatialDecision:
        items = list(objects)
        ids = [str(getattr(item, "id", item.get("id", "")) if isinstance(item, dict) else getattr(item, "id", "")) for item in items]
        if not items:
            return SpatialDecision(relation=relation, candidate_ids=[], reference_frame=self.reference_frame)
        normalized = relation.upper()
        axis = "y" if normalized in {"LEFT", "RIGHT", "LEFTMOST", "RIGHTMOST"} else "x"
        coords = [(_position(item)[1] if axis == "y" else _position(item)[0]) for item in items]
        if normalized in {"LEFT", "LEFTMOST", "FRONT"}:
            order = sorted(range(len(items)), key=lambda idx: coords[idx])
        elif normalized in {"RIGHT", "RIGHTMOST", "BEHIND"}:
            order = sorted(range(len(items)), key=lambda idx: coords[idx], reverse=True)
        elif normalized in {"NEAR", "FAR"} and reference is not None:
            ref = _position(reference)
            distances = [hypot(_position(item)[0]-ref[0], _position(item)[1]-ref[1]) for item in items]
            order = sorted(range(len(items)), key=lambda idx: distances[idx], reverse=normalized == "FAR")
            coords = distances
        elif normalized in {"ABOVE", "BELOW"}:
            coords = [_position(item)[2] for item in items]
            order = sorted(range(len(items)), key=lambda idx: coords[idx], reverse=normalized == "ABOVE")
        else:
            order = list(range(len(items)))
        best = order[0]
        margin = abs(coords[order[1]] - coords[best]) if len(order) > 1 else float("inf")
        status = "RESOLVED"
        selected = ids[best]
        if len(order) > 1 and margin < self.tolerance:
            status = "NEEDS_CLARIFICATION"
            selected = None
        return SpatialDecision(relation=normalized, candidate_ids=ids, selected_id=selected,
                               margin=0.0 if margin == float("inf") else margin, status=status,
                               evidence=[f"reference_frame={self.reference_frame}", f"axis={axis}",
                                         f"relation={normalized}"])

    def resolve(self, objects: Sequence[Any], relation: str, reference: Any = None) -> SpatialDecision:
        return self.rank(objects, relation, reference=reference)

    def compare_same_category(self, objects: Iterable[Any], relation: str, category: Optional[str] = None) -> SpatialDecision:
        items = list(objects)
        if category:
            items = [item for item in items if (getattr(item, "specific_class", None) or getattr(item, "label", None) or "") == category]
        return self.rank(items, relation)
