from __future__ import annotations

from copy import deepcopy


DURATIONS_MS = {
    "detect_object": 10,
    "move_to_object": 100,
    "grasp": 120,
    "move_to_target": 150,
    "release": 80,
}


class MockBackend:
    mode = "mock"

    def __init__(self, objects: dict[str, dict], failures: dict[str, int] | None = None):
        self._objects = deepcopy(objects)
        self._failures = dict(failures or {})
        self._approached_id: str | None = None
        self._held_id: str | None = None
        self._target_id: str | None = None
        self._placement_pose: dict | None = None
        self._eef_pose = {"x": 0.0, "y": 0.0, "z": 0.35}
        self._trajectory: list[dict] = []
        self._elapsed_ms = 0
        self._safe_stopped = False
        self._safe_stop_reason: str | None = None

    @classmethod
    def from_perception(
        cls,
        perception: dict,
        failures: dict[str, int] | None = None,
    ) -> "MockBackend":
        objects: dict[str, dict] = {}
        for item in perception.get("objects", []):
            object_id = item.get("id")
            if object_id in objects:
                raise ValueError(f"duplicate object id: {object_id}")
            objects[object_id] = deepcopy(item)
        return cls(objects, failures)

    def execute(self, action: str, arguments: dict) -> dict:
        if self._safe_stopped:
            return self._failed("BACKEND_SAFE_STOPPED", 0)
        if action not in DURATIONS_MS:
            return self._failed(f"UNKNOWN_ACTION:{action}", 0)

        duration_ms = DURATIONS_MS[action]
        self._elapsed_ms += duration_ms
        remaining_failures = self._failures.get(action, 0)
        if remaining_failures > 0:
            self._failures[action] = remaining_failures - 1
            return self._failed(f"INJECTED_FAILURE:{action}", duration_ms)

        handlers = {
            "detect_object": self._detect_object,
            "move_to_object": self._move_to_object,
            "grasp": self._grasp,
            "move_to_target": self._move_to_target,
            "release": self._release,
        }
        return handlers[action](arguments, duration_ms)

    def _detect_object(self, arguments: dict, duration_ms: int) -> dict:
        object_id = arguments.get("object_id")
        # Compatibility is limited to direct MockBackend callers. Formal
        # strategy.v1 is validated by action_catalog and only accepts object_id.
        if object_id is None:
            object_name = arguments.get("object_name")
            matches = [
                item_id
                for item_id, item in self._objects.items()
                if item_id == object_name
                or item.get("attributes", {}).get("display_name") == object_name
            ]
            if len(matches) > 1:
                return self._failed(f"AMBIGUOUS_OBJECT_NAME:{object_name}", duration_ms)
            object_id = matches[0] if matches else object_name
        item = self._objects.get(object_id)
        if item is None:
            return self._failed(f"OBJECT_NOT_FOUND:{object_id}", duration_ms)
        return self._succeeded(
            duration_ms,
            object_id=object_id,
            pose=deepcopy(item["pose"]),
        )

    def _move_to_object(self, arguments: dict, duration_ms: int) -> dict:
        object_id = arguments.get("object_id")
        item = self._objects.get(object_id)
        if item is None:
            return self._failed(f"OBJECT_NOT_FOUND:{object_id}", duration_ms)
        self._eef_pose = deepcopy(item["pose"])
        self._approached_id = object_id
        self._append_trajectory("move_to_object")
        return self._succeeded(duration_ms, object_id=object_id)

    def _grasp(self, arguments: dict, duration_ms: int) -> dict:
        object_id = arguments.get("object_id")
        item = self._objects.get(object_id)
        if item is None:
            return self._failed(f"OBJECT_NOT_FOUND:{object_id}", duration_ms)
        if self._approached_id != object_id:
            return self._failed("OBJECT_NOT_APPROACHED", duration_ms)
        if not item.get("execution", {}).get("graspable", False):
            return self._failed(f"OBJECT_NOT_GRASPABLE:{object_id}", duration_ms)
        if self._held_id is not None:
            return self._failed(f"GRIPPER_ALREADY_HOLDING:{self._held_id}", duration_ms)
        self._held_id = object_id
        self._append_trajectory("grasp")
        return self._succeeded(duration_ms, object_id=object_id)

    def _move_to_target(self, arguments: dict, duration_ms: int) -> dict:
        # Legacy target is accepted only for direct backend calls; the formal
        # strategy contract requires destination_id.
        destination_id = arguments.get("destination_id", arguments.get("target"))
        item = self._objects.get(destination_id)
        placement_mode = arguments.get("placement_mode", "direct")
        if item is None:
            return self._failed(
                f"INVALID_DESTINATION:{destination_id}",
                duration_ms,
            )
        if placement_mode == "stack_on":
            if not item.get("execution", {}).get("stackable_destination", False):
                return self._failed(
                    f"INVALID_STACK_DESTINATION:{destination_id}",
                    duration_ms,
                )
            if destination_id == self._held_id:
                return self._failed("STACK_TARGET_IS_HELD_OBJECT", duration_ms)
            if self._held_id is None:
                return self._failed("NOT_HOLDING_OBJECT", duration_ms)
            placement_pose = self._stack_pose(item, self._objects[self._held_id])
        else:
            if not item.get("execution", {}).get("valid_destination", False):
                return self._failed(
                    f"INVALID_DESTINATION:{destination_id}",
                    duration_ms,
                )
            if self._held_id is None:
                return self._failed("NOT_HOLDING_OBJECT", duration_ms)
            placement_pose = deepcopy(item["pose"])
        self._target_id = destination_id
        self._placement_pose = placement_pose
        self._eef_pose = deepcopy(placement_pose)
        self._append_trajectory("move_to_target")
        return self._succeeded(duration_ms, destination_id=destination_id)

    @staticmethod
    def _stack_pose(destination: dict, held: dict) -> dict:
        """Return the held object's center pose immediately above a base."""
        destination_pose = destination.get("pose") or {}
        held_pose = held.get("pose") or {}
        destination_dimensions = destination.get("dimensions") or {}
        held_dimensions = held.get("dimensions") or {}
        destination_height = float(destination_dimensions.get("z", 0.0) or 0.0)
        held_height = float(held_dimensions.get("z", 0.0) or 0.0)
        return {
            "x": float(destination_pose.get("x", held_pose.get("x", 0.0))),
            "y": float(destination_pose.get("y", held_pose.get("y", 0.0))),
            "z": float(destination_pose.get("z", 0.0))
            + destination_height / 2.0
            + held_height / 2.0,
        }

    def _release(self, arguments: dict, duration_ms: int) -> dict:
        if self._held_id is None:
            return self._failed("NOT_HOLDING_OBJECT", duration_ms)
        if self._target_id is None:
            return self._failed("TARGET_NOT_REACHED", duration_ms)
        released_id = self._held_id
        self._objects[released_id]["pose"] = deepcopy(
            self._placement_pose or self._objects[self._target_id]["pose"]
        )
        self._held_id = None
        self._approached_id = None
        self._placement_pose = None
        self._append_trajectory("release")
        return self._succeeded(duration_ms, object_id=released_id)

    def _append_trajectory(self, action: str) -> None:
        self._trajectory.append(
            {
                "timestamp_ms": self._elapsed_ms,
                "action": action,
                "pose": deepcopy(self._eef_pose),
            }
        )

    @staticmethod
    def _succeeded(duration_ms: int, **fields: object) -> dict:
        return {
            "status": "SUCCESS",
            "reason": "",
            "duration_ms": duration_ms,
            **fields,
        }

    @staticmethod
    def _failed(reason: str, duration_ms: int) -> dict:
        return {
            "status": "FAILED",
            "reason": reason,
            "duration_ms": duration_ms,
        }

    def safe_stop(self, reason: str) -> dict:
        self._safe_stopped = True
        self._safe_stop_reason = reason
        return {"status": "SAFE_STOP", "reason": reason, "duration_ms": 0}

    def trajectory_points(self) -> list[dict]:
        return deepcopy(self._trajectory)

    def snapshot(self) -> dict:
        return {
            "objects": deepcopy(self._objects),
            "approached_id": self._approached_id,
            "held_id": self._held_id,
            "target_id": self._target_id,
            "placement_pose": deepcopy(self._placement_pose),
            "eef_pose": deepcopy(self._eef_pose),
            "safe_stopped": self._safe_stopped,
            "safe_stop_reason": self._safe_stop_reason,
        }
