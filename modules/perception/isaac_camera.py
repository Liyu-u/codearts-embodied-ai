"""Isaac Sim RGB-D camera perception provider.

The provider deliberately has no Isaac Sim import at module import time.  It
consumes a small ``CameraSensor``-like interface (``get_data(name)``), which
keeps the contract and geometry logic unit-testable on the host machine while
the real sensor is created inside the Isaac Sim container.

The online result is an ``perception_observation 1.0.0`` message.  The
existing observation normalizer remains the only boundary that converts this
camera observation into the internal ``perception.v1`` scene.
"""

from __future__ import annotations

import math
import time
from copy import deepcopy
from typing import Any, Iterable

from integration.contract_validation import assert_contract


DEFAULT_CAMERA_MANIFEST: tuple[dict[str, Any], ...] = (
    {
        "object_id": "red_cube",
        "category": "红色方块",
        "color": "red",
        "shape": "cube",
        "geometry_prior": {"width": 0.04, "height": 0.04, "depth": 0.04},
        "segmentation_labels": ("red_cube",),
    },
    {
        "object_id": "green_cube",
        "category": "绿色方块",
        "color": "green",
        "shape": "cube",
        "geometry_prior": {"width": 0.0515, "height": 0.0515, "depth": 0.0515},
        "segmentation_labels": ("green_cube",),
    },
    {
        "object_id": "zone_unstack_target",
        "category": "放置区域",
        "color": "gray",
        "shape": "box",
        "geometry_prior": {"width": 0.10, "height": 0.02, "depth": 0.10},
        "segmentation_labels": ("zone_unstack_target", "table", "placement_zone"),
    },
)


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - only hit in a broken runtime
        raise RuntimeError("Isaac camera perception requires numpy") from exc
    return np


def _array(value: Any):
    np = _numpy()
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("data", "values", "buffer"):
            if key in value:
                return _array(value[key])
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def _read_sensor(sensor: Any, name: str) -> tuple[Any, dict[str, Any]]:
    result = sensor.get_data(name)
    if isinstance(result, tuple) and len(result) == 2:
        data, info = result
        return data, info if isinstance(info, dict) else {}
    if isinstance(result, dict):
        return result.get("data", result), result.get("info", {}) or {}
    return result, {}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _as_float(value: Any, fallback: float = 0.0) -> float:
    return float(value) if _finite(value) else fallback


def _extract_label_text(value: Any) -> set[str]:
    labels: set[str] = set()
    if isinstance(value, str):
        labels.add(value)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            labels.update(_extract_label_text(item))
    elif isinstance(value, dict):
        for key in ("class", "label", "semantic", "name", "primPath", "prim_path"):
            if key in value:
                labels.update(_extract_label_text(value[key]))
    return labels


def _segmentation_label_map(info: dict[str, Any]) -> dict[str, set[int]]:
    """Normalize CameraSensor/Replicator id-to-label variants."""

    result: dict[str, set[int]] = {}
    candidates = (
        info.get("idToLabels"),
        info.get("id_to_labels"),
        info.get("idToLabel"),
        info.get("labels"),
    )
    mapping = next((item for item in candidates if isinstance(item, dict)), None)
    if mapping is None:
        return result
    for raw_id, raw_label in mapping.items():
        try:
            numeric_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        for label in _extract_label_text(raw_label):
            result.setdefault(label, set()).add(numeric_id)
    return result


def _normalize_segmentation(segmentation: Any):
    np = _numpy()
    array = _array(segmentation)
    if array is None:
        return None
    if array.dtype.fields:
        for name in ("data", "id", "semanticId", "instanceId"):
            if name in array.dtype.fields:
                return np.asarray(array[name])
    if array.ndim == 3:
        return np.asarray(array[..., 0])
    return array


def _world_points(mask: Any, depth: Any, camera_model: dict[str, Any]):
    np = _numpy()
    depth_array = _array(depth)
    if depth_array is None:
        return np.empty((0, 3), dtype=float), np.empty((0, 2), dtype=int)
    if depth_array.ndim == 3:
        depth_array = depth_array[..., 0]
    if mask.shape != depth_array.shape:
        raise ValueError(
            f"camera segmentation/depth shape mismatch: {mask.shape} vs {depth_array.shape}"
        )
    fx = float(camera_model["fx"])
    fy = float(camera_model["fy"])
    cx = float(camera_model["cx"])
    cy = float(camera_model["cy"])
    if min(fx, fy) <= 0:
        raise ValueError("camera intrinsics fx/fy must be positive")
    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return np.empty((0, 3), dtype=float), np.empty((0, 2), dtype=int)
    values = depth_array[rows, cols].astype(float)
    valid = np.isfinite(values) & (values > 0.0)
    rows, cols, values = rows[valid], cols[valid], values[valid]
    if len(rows) == 0:
        return np.empty((0, 3), dtype=float), np.empty((0, 2), dtype=int)

    # Isaac Sim camera optical axis is -Z.  distance_to_image_plane is the
    # positive distance along that axis, so local camera coordinates are
    # (x, y, -depth), with image y pointing down.
    x = (cols.astype(float) - cx) * values / fx
    y = (cy - rows.astype(float)) * values / fy
    z = -values
    points = np.column_stack((x, y, z))
    transform = np.asarray(camera_model.get("world_from_camera", np.eye(4)), dtype=float)
    if transform.shape != (4, 4):
        raise ValueError("camera world_from_camera must be a 4x4 matrix")
    world = points @ transform[:3, :3].T + transform[:3, 3]
    return world, np.column_stack((rows, cols))


def _color_name(rgb: Any, mask: Any, fallback: str, channel_order: str = "rgb") -> tuple[str, float]:
    np = _numpy()
    image = _array(rgb)
    if image is None or image.ndim < 3 or image.shape[:2] != mask.shape:
        return fallback, 0.5
    pixels = image[..., :3][mask]
    if len(pixels) == 0:
        return fallback, 0.5
    mean = np.nanmean(pixels.astype(float), axis=0)
    if not np.isfinite(mean).all():
        return fallback, 0.5
    if str(channel_order).lower() == "bgr":
        mean = mean[[2, 1, 0]]
    r, g, b = mean
    total = max(float(r + g + b), 1.0)
    if g > r * 1.15 and g > b * 1.10:
        return "green", min(0.99, 0.55 + float((g - max(r, b)) / total))
    if r > g * 1.15 and r > b * 1.10:
        return "red", min(0.99, 0.55 + float((r - max(g, b)) / total))
    if b > r * 1.15 and b > g * 1.10:
        return "blue", min(0.99, 0.55 + float((b - max(r, g)) / total))
    return fallback, 0.65


class IsaacCameraObservationProvider:
    """Turn a real Isaac Sim CameraSensor frame into a formal observation."""

    backend = "isaac_camera"

    def __init__(
        self,
        sensor: Any,
        camera_model: dict[str, Any],
        *,
        scene_id: str = "stacking_cubes",
        sensor_id: str = "overhead_rgbd",
        manifest: Iterable[dict[str, Any]] | None = None,
        segmentation_annotator: str = "instance_id_segmentation",
        depth_annotator: str = "distance_to_image_plane",
        clock: Any = time.time_ns,
    ) -> None:
        self.sensor = sensor
        self.camera_model = deepcopy(camera_model)
        self.scene_id = scene_id
        self.sensor_id = sensor_id
        self.segmentation_annotator = segmentation_annotator
        self.depth_annotator = depth_annotator
        self.clock = clock
        self.manifest = tuple(deepcopy(manifest or DEFAULT_CAMERA_MANIFEST))
        self._tracks: dict[str, tuple[dict[str, float], int, int]] = {}
        self.last_metrics: dict[str, Any] = {}
        self.last_observation: dict[str, Any] | None = None
        self._validate_manifest()

    def _validate_manifest(self) -> None:
        seen: set[str] = set()
        for item in self.manifest:
            object_id = item.get("object_id")
            if not isinstance(object_id, str) or not object_id or object_id in seen:
                raise ValueError(f"invalid or duplicate camera object_id: {object_id}")
            seen.add(object_id)
            prior = item.get("geometry_prior") or {}
            if any(not _finite(prior.get(axis)) or float(prior[axis]) <= 0 for axis in ("width", "height", "depth")):
                raise ValueError(f"invalid geometry_prior for {object_id}")

    def _candidate_ids(self, item: dict[str, Any], label_map: dict[str, set[int]]) -> set[int]:
        ids: set[int] = set()
        for label in item.get("segmentation_labels", ()):
            ids.update(label_map.get(str(label), set()))
        for value in item.get("segmentation_ids", ()):
            try:
                ids.add(int(value))
            except (TypeError, ValueError):
                continue
        return ids

    def _object(self, item: dict[str, Any], mask: Any, rgb: Any, depth: Any, timestamp: int, label_map: dict[str, set[int]]) -> dict[str, Any] | None:
        np = _numpy()
        points, pixels = _world_points(mask, depth, self.camera_model)
        if len(points) < 3:
            return None
        center = np.median(points, axis=0)
        pose = {"x": float(center[0]), "y": float(center[1]), "z": float(center[2])}
        previous = self._tracks.get(item["object_id"])
        if previous is None:
            age = 1
            velocity = {"x": 0.0, "y": 0.0, "z": 0.0}
        else:
            previous_pose, previous_timestamp, previous_age = previous
            dt = max((timestamp - previous_timestamp) / 1e9, 1e-6)
            velocity = {axis: (pose[axis] - previous_pose[axis]) / dt for axis in ("x", "y", "z")}
            age = previous_age + 1
        self._tracks[item["object_id"]] = (pose, timestamp, age)

        prior = deepcopy(item["geometry_prior"])
        observed_spans: dict[str, float] = {}
        rejected_spans: dict[str, float] = {}
        if len(points) >= 4:
            span = np.max(points, axis=0) - np.min(points, axis=0)
            # An overhead camera cannot observe the full vertical extent of a
            # flat top surface; retain the calibrated class prior for that
            # axis while using depth-derived horizontal spans only when they
            # are physically plausible.  A wrong camera intrinsic matrix can
            # otherwise turn a 5 cm cube into a 20 cm object in the contract.
            bounds = self.camera_model.get("geometry_span_ratio_bounds", (0.5, 2.0))
            try:
                min_ratio, max_ratio = float(bounds[0]), float(bounds[1])
            except (TypeError, ValueError, IndexError):
                min_ratio, max_ratio = 0.5, 2.0
            if not (0.0 < min_ratio <= max_ratio):
                min_ratio, max_ratio = 0.5, 2.0
            for axis, index in (("width", 0), ("depth", 1)):
                if span[index] <= 1e-4:
                    continue
                observed = float(span[index])
                observed_spans[axis] = observed
                ratio = observed / float(prior[axis])
                if min_ratio <= ratio <= max_ratio:
                    prior[axis] = observed
                else:
                    rejected_spans[axis] = observed

        color, color_score = _color_name(
            rgb,
            mask,
            str(item.get("color", "unknown")),
            str(self.camera_model.get("rgb_channel_order", "rgb")),
        )
        label_ids = self._candidate_ids(item, label_map)
        mask_area = int(np.count_nonzero(mask))
        return {
            "object_id": item["object_id"],
            "category_candidates": [{"name": item["category"], "score": 0.99}],
            "pose": {
                "position": pose,
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            },
            "geometry": {"type": "box", "size": prior},
            "appearance": {
                "color_candidates": [{"name": color, "score": round(color_score, 4)}],
                "shape_candidates": [{"name": str(item.get("shape", "object")), "score": 0.90}],
                "texture_candidates": [{"name": "smooth", "score": 0.70}],
            },
            "tracking": {
                "track_age_frames": age,
                "velocity": velocity,
                "velocity_confidence": 0.90 if previous else 0.50,
            },
            # Kept outside the formal schema only in the local metrics; do not
            # attach it to the observation object sent to the normalizer.
            "_camera_debug": {
                "mask_area": mask_area,
                "segmentation_ids": sorted(label_ids),
                "observed_spans": observed_spans,
                "rejected_spans": rejected_spans,
                "geometry_source": "rgbd_span" if not rejected_spans and observed_spans else "geometry_prior",
            },
        }

    def observe(self) -> dict[str, Any]:
        np = _numpy()
        rgb_raw, _ = _read_sensor(self.sensor, "rgb")
        depth_raw, _ = _read_sensor(self.sensor, self.depth_annotator)
        segmentation_raw, segmentation_info = _read_sensor(self.sensor, self.segmentation_annotator)
        rgb = _array(rgb_raw)
        depth = _array(depth_raw)
        segmentation = _normalize_segmentation(segmentation_raw)
        if rgb is None or depth is None or segmentation is None:
            raise RuntimeError("camera frame is incomplete: rgb, depth, and segmentation are required")
        if segmentation.ndim != 2:
            raise ValueError(f"camera segmentation must be 2D, got shape {segmentation.shape}")
        label_map = _segmentation_label_map(segmentation_info)
        timestamp = int(self.clock())
        objects: list[dict[str, Any]] = []
        debug: dict[str, Any] = {}
        for item in self.manifest:
            ids = self._candidate_ids(item, label_map)
            if not ids:
                continue
            mask = np.isin(segmentation, list(ids))
            output = self._object(item, mask, rgb, depth, timestamp, label_map)
            if output is None:
                continue
            debug[item["object_id"]] = output.pop("_camera_debug")
            objects.append(output)

        depth_values = depth[..., 0] if depth.ndim == 3 else depth
        valid_depth = np.isfinite(depth_values) & (depth_values > 0.0)
        observation_id = f"{self.sensor_id}-{timestamp}"
        observation = {
            "schema_version": "1.0.0",
            "message_type": "perception_observation",
            "observation_id": observation_id,
            "scene_id": self.scene_id,
            "timestamp": timestamp,
            "clock_domain": "isaac_sim",
            "coordinate_system": "world",
            "source": {
                "module": "isaac_sim_camera",
                "pipeline_version": "camera-perception.v1",
                "sensor_ids": [self.sensor_id],
            },
            "objects": objects,
            "simulation_metadata": {"evaluation_only": True, "ground_truth_objects": []},
        }
        assert_contract(observation, "perception_observation.1.0.0")
        self.last_observation = deepcopy(observation)
        self.last_metrics = {
            "observation_id": observation_id,
            "rgb_shape": list(rgb.shape),
            "depth_shape": list(depth_values.shape),
            "depth_valid_ratio": float(np.mean(valid_depth)),
            "segmentation_unique_values": int(len(np.unique(segmentation))),
            "visible_objects": len(objects),
            "objects": debug,
        }
        return observation

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok" if self.last_metrics else "not_ready",
            "backend": self.backend,
            "scene_id": self.scene_id,
            "sensor_id": self.sensor_id,
            "last_metrics": deepcopy(self.last_metrics),
        }
