"""Apply spatial constraints to a candidate set."""

from __future__ import annotations

from typing import Any, Iterable, List

from .spatial_reasoner import SpatialDecision, SpatialReasoner


class RelationalGrounder:
    def __init__(self, spatial_reasoner: SpatialReasoner | None = None):
        self.spatial_reasoner = spatial_reasoner or SpatialReasoner()

    def resolve(self, candidates: Iterable[Any], spatial_constraints: Iterable[Any]) -> List[SpatialDecision]:
        objects = list(candidates)
        return [self.spatial_reasoner.resolve(
            objects, getattr(constraint, "relation", None) or constraint.get("relation", "RELATIVE_TO")
        ) for constraint in spatial_constraints]
