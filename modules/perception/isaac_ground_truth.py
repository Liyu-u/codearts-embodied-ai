"""Isaac Sim ground-truth perception provider.

This adapter reads object poses from a live MotionDriver (which in the Isaac
container is backed by USD/PhysX) and combines them with a small semantic scene
manifest. It emits the existing perception.v1 contract so downstream modules
do not need to know whether data came from Mock or simulation truth.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Callable, Iterable

from integration.contract_validation import assert_contract

from .spatial_context import enrich_spatial_context


DEFAULT_STACKING_MANIFEST: tuple[dict[str, Any], ...] = (
    {
        "id": "red_cube",
        "category": "红色方块",
        "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
        "attributes": {"display_name": "红色方块", "color": "red"},
        "execution": {
            "movable": True,
            "graspable": True,
            "stackable_destination": True,
            "valid_destination": True,
        },
    },
    {
        "id": "green_cube",
        "category": "绿色方块",
        "dimensions": {"x": 0.0515, "y": 0.0515, "z": 0.0515},
        "attributes": {"display_name": "绿色方块", "color": "green"},
        "execution": {"movable": True, "graspable": True},
    },
    {
        "id": "zone_unstack_target",
        "category": "放置区域",
        "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02},
        "attributes": {"display_name": "放置区域", "purpose": "safe_placement"},
        "execution": {
            "movable": False,
            "graspable": False,
            "valid_destination": True,
        },
    },
)


def _finite_pose(value: Any, object_id: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"ground-truth pose for {object_id} must be an object")
    result: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        coordinate = value.get(axis)
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise ValueError(f"ground-truth pose {object_id}.{axis} must be numeric")
        if not math.isfinite(float(coordinate)):
            raise ValueError(f"ground-truth pose {object_id}.{axis} must be finite")
        result[axis] = float(coordinate)
    return result


class IsaacGroundTruthProvider:
    """Create a perception.v1 snapshot from a live Isaac-compatible driver."""

    backend = "isaac_ground_truth"

    def __init__(
        self,
        driver: Any = None,
        *,
        scene_id: str = "stacking_cubes",
        coordinate_frame: str = "world",
        scene_revision: str = "isaac-ground-truth-1",
        manifest: Iterable[dict[str, Any]] | None = None,
        read_pose: Callable[[str], dict[str, Any]] | None = None,
        source: str = "isaac_sim.usd_physx",
    ) -> None:
        if driver is None and read_pose is None:
            raise ValueError("IsaacGroundTruthProvider requires a driver or read_pose")
        self.driver = driver
        self.scene_id = scene_id
        self.coordinate_frame = coordinate_frame
        self.scene_revision = scene_revision
        self.source = source
        self._read_pose = read_pose or driver.read_object_pose
        selected = manifest
        if selected is None and scene_id == "stacking_cubes":
            selected = DEFAULT_STACKING_MANIFEST
        if selected is None:
            raise ValueError(f"no ground-truth scene manifest for {scene_id}")
        self.manifest = tuple(deepcopy(list(selected)))
        self._validate_manifest()

    def _validate_manifest(self) -> None:
        seen: set[str] = set()
        for item in self.manifest:
            if not isinstance(item, dict):
                raise ValueError("ground-truth manifest items must be objects")
            object_id = item.get("id")
            if not isinstance(object_id, str) or not object_id:
                raise ValueError("ground-truth manifest object id must be non-empty")
            if object_id in seen:
                raise ValueError(f"duplicate ground-truth object id: {object_id}")
            seen.add(object_id)
            if not isinstance(item.get("category"), str) or not item["category"]:
                raise ValueError(f"ground-truth category missing for {object_id}")

    def observe(self) -> dict[str, Any]:
        objects: list[dict[str, Any]] = []
        for item in self.manifest:
            object_id = item["id"]
            output_item = {
                "id": object_id,
                "category": item["category"],
                "pose": _finite_pose(self._read_pose(object_id), object_id),
                "dimensions": deepcopy(item.get("dimensions", {})),
                "attributes": deepcopy(item.get("attributes", {})),
                "execution": deepcopy(item.get("execution", {})),
            }
            output_item["attributes"].setdefault("display_name", item["category"])
            objects.append(output_item)

        scene = {
            "schema_version": "perception.v1",
            "scene_id": self.scene_id,
            "coordinate_frame": self.coordinate_frame,
            "objects": objects,
            "execution_context": {
                "backend": self.backend,
                "source": self.source,
                "scene_revision": self.scene_revision,
                "pose_source": "live_usd_physx_driver",
                "semantic_source": "scene_manifest",
            },
        }
        scene = enrich_spatial_context(scene)
        assert_contract(scene, "perception.v1")
        return scene

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "backend": self.backend,
            "scene_id": self.scene_id,
            "source": self.source,
            "objects": len(self.manifest),
        }
