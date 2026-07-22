"""
Observation Schema -- formal perception input protocol.

Perception provides ONLY directly observable facts.
It MUST NOT output: fragile, graspable, movable, material, max_force.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Forbidden fields that perception must NEVER provide
FORBIDDEN_FIELDS = {
    "fragile", "fragility", "fragility_level",
    "graspable", "movable", "material",
    "max_force", "max_force_N", "force_limit",
    "safe_to_move",
}


@dataclass
class CategoryCandidate:
    """A single category hypothesis from perception."""
    name: str
    score: float  # 0.0 - 1.0

    def __post_init__(self):
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"Category score must be in [0,1], got {self.score}")


@dataclass
class Geometry:
    """Object geometry in meters. All dimensions must be > 0."""
    width: float
    height: float
    depth: float

    def __post_init__(self):
        for dim_name, dim_val in [("width", self.width), ("height", self.height), ("depth", self.depth)]:
            if dim_val <= 0:
                raise ValueError(f"Geometry.{dim_name} must be > 0, got {dim_val}")

    def grasp_dimension(self) -> float:
        """Smallest dimension suitable for gripper assessment."""
        return min(self.width, self.depth)


@dataclass
class Position:
    x: float; y: float; z: float


@dataclass
class Quaternion:
    x: float = 0.0; y: float = 0.0; z: float = 0.0; w: float = 1.0


@dataclass
class Appearance:
    color: str = "unknown"
    shape: str = "unknown"
    texture: str = "unknown"


@dataclass
class ObjectObservation:
    """
    A single object observed by perception.

    MUST NOT contain: fragile, graspable, movable, material, max_force.
    """
    object_id: str
    category_candidates: List[CategoryCandidate] = field(default_factory=list)
    pose: Position = field(default_factory=lambda: Position(0, 0, 0))
    orientation: Quaternion = field(default_factory=Quaternion)
    geometry: Geometry = field(default_factory=lambda: Geometry(0.05, 0.05, 0.05))
    appearance: Appearance = field(default_factory=Appearance)

    # Optional: only used in evaluation/test mode
    simulation_metadata: Optional[Dict[str, Any]] = field(default=None, repr=False)

    def __post_init__(self):
        if not self.object_id:
            raise ValueError("object_id must be non-empty")
        if not self.category_candidates:
            raise ValueError("At least one category_candidate is required")

    def top_category(self) -> CategoryCandidate:
        """Return highest-scoring category candidate."""
        return max(self.category_candidates, key=lambda c: c.score)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ObjectObservation":
        """Construct from dict, rejecting forbidden fields."""
        for forbidden in FORBIDDEN_FIELDS:
            if forbidden in data:
                raise ValueError(
                    f"Perception Observation MUST NOT contain '{forbidden}'. "
                    f"This is an inferred property, not an observation."
                )

        candidates = [
            CategoryCandidate(name=c["name"], score=c["score"])
            for c in data.get("category_candidates", [])
        ]

        geo_raw = data.get("geometry", {})
        geometry = Geometry(
            width=geo_raw.get("width", 0.05),
            height=geo_raw.get("height", 0.08),
            depth=geo_raw.get("depth", 0.05),
        )

        pose_raw = data.get("pose", {})
        pose = Position(
            x=pose_raw.get("x", 0), y=pose_raw.get("y", 0), z=pose_raw.get("z", 0.03)
        )

        ori_raw = data.get("orientation", {})
        orientation = Quaternion(
            x=ori_raw.get("x", 0), y=ori_raw.get("y", 0),
            z=ori_raw.get("z", 0), w=ori_raw.get("w", 1.0),
        )

        app_raw = data.get("appearance", {})
        appearance = Appearance(
            color=app_raw.get("color", "unknown"),
            shape=app_raw.get("shape", "unknown"),
            texture=app_raw.get("texture", "unknown"),
        )

        sim_meta = data.get("simulation_metadata")

        return cls(
            object_id=data["object_id"],
            category_candidates=candidates,
            pose=pose, orientation=orientation,
            geometry=geometry, appearance=appearance,
            simulation_metadata=sim_meta,
        )


@dataclass
class PerceptionObservation:
    """Top-level perception frame."""
    schema_version: str = "1.0"
    scene_id: str = "scene_001"
    timestamp: int = 0
    coordinate_system: str = "robot_base"
    objects: List[ObjectObservation] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PerceptionObservation":
        return cls(
            schema_version=data.get("schema_version", "1.0"),
            scene_id=data.get("scene_id", "scene_001"),
            timestamp=data.get("timestamp", 0),
            coordinate_system=data.get("coordinate_system", "robot_base"),
            objects=[ObjectObservation.from_dict(o) for o in data.get("objects", [])],
        )
