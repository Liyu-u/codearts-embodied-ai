"""A deterministic, lightweight robot meta-API simulator."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from .defaults import DEFAULT_API_CATALOG, MOVE_SPEED
from .models import distance, find_object, get_objects, update_object


class RobotSimulator:
    """Execute a small common subset of robot actions without real hardware."""

    def __init__(self, initial_state: dict, scenario: dict | None = None):
        self.state = deepcopy(initial_state)
        self.state.setdefault("robot", {})
        self.state["robot"].setdefault("position", [0.0, 0.0, 0.0])
        self.state["robot"].setdefault("gripper_empty", True)
        self.state["robot"].setdefault("speed", 0.0)
        self.state.setdefault("elapsed_time", 0.0)
        self.state.setdefault("safety", {})
        self.state["safety"].setdefault("collision_count", 0)
        self.state["safety"].setdefault("entered_human_zone", False)
        for item in get_objects(self.state):
            item.setdefault("orientation", 0.0)
        self.scenario = deepcopy(scenario or {})
        self.failure_counts: dict[str, int] = {}
        self.trajectory_points: list[list[float]] = []
        self._last_duration: float = 0.0
        self.handlers: dict[str, Callable[[dict], dict]] = {
            "detect_object": self._detect_object,
            "move_to_object": self._move_to_object,
            "move_to_target": self._move_to_target,
            "grasp": self._grasp,
            "release": self._release,
            "rotate": self._rotate,
            "sweep": self._sweep,
            "stop": self._stop,
        }
        self._apply_scenario_changes()

    def register_action(self, name: str, handler: Callable[[dict], dict]) -> None:
        self.handlers[name] = handler

    def _default_duration(self, action: str) -> float:
        return float(
            self.scenario.get("api_catalog", DEFAULT_API_CATALOG)
            .get(action, {})
            .get("duration_ms", 0)
            or DEFAULT_API_CATALOG.get(action, {}).get("duration_ms", 0) or 0
        ) / 1000.0

    def execute(self, action: str, arguments: dict) -> dict:
        if self._should_fail(action):
            self._last_duration = self._default_duration(action) or 0.1
            return {
                "status": "FAILED",
                "reason": self.scenario.get("failure_reasons", {}).get(
                    action, f"INJECTED_{action.upper()}_FAILURE"
                ),
            }
        handler = self.handlers.get(action)
        if handler is None:
            self._last_duration = 0.1
            return {"status": "FAILED", "reason": "UNKNOWN_ACTION"}
        result = handler(arguments)
        result.setdefault("duration_ms", int(self._last_duration * 1000))
        self.state["elapsed_time"] += self._last_duration
        return result

    def _apply_scenario_changes(self) -> None:
        for object_id, changes in self.scenario.get("object_changes", {}).items():
            update_object(self.state, object_id, **changes)
        if self.scenario.get("collision"):
            self.state["safety"]["collision_count"] += 1
        if self.scenario.get("entered_human_zone"):
            self.state["safety"]["entered_human_zone"] = True

    def _should_fail(self, action: str) -> bool:
        configured = self.scenario.get("failures", {}).get(action, 0)
        current = self.failure_counts.get(action, 0)
        self.failure_counts[action] = current + 1
        return current < configured

    def _detect_object(self, arguments: dict) -> dict:
        attribute = arguments.get("attribute")
        if isinstance(attribute, dict):
            # 按物体属性（如 color/shape/texture）筛选，返回第一个匹配项。
            item = self._find_by_attribute(attribute.get("name"), attribute.get("value"))
        else:
            item = find_object(self.state, arguments.get("object_name"))
        self._last_duration = self._default_duration("detect_object") or 0.4
        if not item or not item.get("visible", True):
            return {"status": "FAILED", "reason": "OBJECT_NOT_FOUND"}
        return {
            "status": "SUCCESS",
            "object_id": item["id"],
            "position": deepcopy(item.get("position")),
        }

    def _find_by_attribute(self, name: str | None, value) -> dict | None:
        for item in get_objects(self.state):
            if name and item.get(name) == value:
                return item
        return None

    def _move_to_object(self, arguments: dict) -> dict:
        item = find_object(self.state, arguments.get("object_id"))
        if not item:
            self._last_duration = 0.1
            return {"status": "FAILED", "reason": "OBJECT_NOT_FOUND"}
        if not item.get("reachable", True):
            self._last_duration = 0.1
            return {"status": "FAILED", "reason": "OBJECT_UNREACHABLE"}
        self._traverse_to(item.get("position"))
        return {"status": "SUCCESS", "position": deepcopy(item.get("position"))}

    def _move_to_target(self, arguments: dict) -> dict:
        target = find_object(self.state, arguments.get("target"))
        if not target:
            self._last_duration = 0.1
            return {"status": "FAILED", "reason": "TARGET_NOT_FOUND"}
        self._traverse_to(target.get("position"))
        return {"status": "SUCCESS", "position": deepcopy(target.get("position"))}

    def _traverse_to(self, target: list[float]) -> None:
        """移动机器人：按距离/速度计时，并记录中间轨迹点。"""
        robot = self.state["robot"]
        start = deepcopy(robot.get("position"))
        path_length = distance(start, target)
        self._last_duration = (
            path_length / MOVE_SPEED if path_length != float("inf") else 0.3
        )
        robot["speed"] = MOVE_SPEED if path_length else 0.0
        robot["position"] = deepcopy(target)
        if path_length != float("inf") and path_length > 1e-6:
            self._emit_trajectory(start, target)

    def _emit_trajectory(self, start: list[float], target: list[float]) -> None:
        """在线段上均匀采样几个中间点，供平滑度评分使用。"""
        segments = 4
        for index in range(1, segments + 1):
            ratio = index / segments
            point = [
                start[axis] + (target[axis] - start[axis]) * ratio
                for axis in range(min(len(start), len(target)))
            ]
            self.trajectory_points.append(point)

    def _grasp(self, arguments: dict) -> dict:
        robot = self.state["robot"]
        item = find_object(self.state, arguments.get("object_id"))
        self._last_duration = self._default_duration("grasp") or 0.8
        if not item:
            return {"status": "FAILED", "reason": "OBJECT_NOT_FOUND"}
        if not robot.get("gripper_empty", True):
            return {"status": "FAILED", "reason": "GRIPPER_NOT_EMPTY"}
        if distance(robot.get("position"), item.get("position")) > 0.15:
            return {"status": "FAILED", "reason": "OBJECT_NOT_REACHABLE_FROM_CURRENT_POSE"}
        robot["gripper_empty"] = False
        robot["gripper_object"] = item["id"]
        update_object(self.state, item["id"], location="gripper", container=None)
        return {"status": "SUCCESS", "object_id": item["id"]}

    def _release(self, arguments: dict) -> dict:
        del arguments
        robot = self.state["robot"]
        object_id = robot.get("gripper_object")
        self._last_duration = self._default_duration("release") or 0.4
        if not object_id:
            return {"status": "FAILED", "reason": "GRIPPER_EMPTY"}

        nearest = None
        nearest_distance = float("inf")
        for item in get_objects(self.state):
            if item.get("id") == object_id:
                continue
            # 已经放在某个容器内的物体不是容器候选（否则同一容器放多个物体时，
            # 后放的物体会把先放的物体当成"容器"，container 指向错误对象）。
            if item.get("container") is not None:
                continue
            current_distance = distance(robot.get("position"), item.get("position"))
            if current_distance < nearest_distance:
                nearest, nearest_distance = item, current_distance

        changes = {
            "position": deepcopy(robot.get("position")),
            "location": "world",
            "container": nearest.get("id") if nearest and nearest_distance <= 0.2 else None,
        }
        update_object(self.state, object_id, **changes)
        robot["gripper_empty"] = True
        robot.pop("gripper_object", None)
        return {"status": "SUCCESS", "object_id": object_id}

    def _rotate(self, arguments: dict) -> dict:
        robot = self.state["robot"]
        object_id = arguments.get("object_id")
        angle = float(arguments.get("angle", 0.0))
        self._last_duration = self._default_duration("rotate") or 0.6
        if not object_id:
            return {"status": "FAILED", "reason": "OBJECT_NOT_FOUND"}
        if robot.get("gripper_object") != object_id:
            return {"status": "FAILED", "reason": "OBJECT_NOT_IN_GRIPPER"}
        item = find_object(self.state, object_id)
        if not item:
            return {"status": "FAILED", "reason": "OBJECT_NOT_FOUND"}
        item["orientation"] = (float(item.get("orientation", 0.0)) + angle) % 360.0
        return {"status": "SUCCESS", "object_id": object_id, "orientation": item["orientation"]}

    def _sweep(self, arguments: dict) -> dict:
        target = find_object(self.state, arguments.get("target_area"))
        item = find_object(self.state, arguments.get("object_id"))
        self._last_duration = self._default_duration("sweep") or 0.9
        if not item:
            return {"status": "FAILED", "reason": "OBJECT_NOT_FOUND"}
        if not target:
            return {"status": "FAILED", "reason": "TARGET_NOT_FOUND"}
        if not item.get("reachable", True):
            return {"status": "FAILED", "reason": "OBJECT_UNREACHABLE"}
        update_object(
            self.state, item["id"],
            position=deepcopy(target.get("position")),
            location="world",
            container=None,
        )
        # 若物体正在夹爪中被扫出，夹爪随之释放。
        robot = self.state["robot"]
        if robot.get("gripper_object") == item["id"]:
            robot["gripper_empty"] = True
            robot.pop("gripper_object", None)
        return {"status": "SUCCESS", "object_id": item["id"]}

    def _stop(self, arguments: dict) -> dict:
        del arguments
        self._last_duration = self._default_duration("stop") or 0.05
        self.state["robot"]["stopped"] = True
        return {"status": "SUCCESS", "stopped": True}
