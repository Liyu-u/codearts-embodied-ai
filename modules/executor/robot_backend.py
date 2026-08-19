"""受安全守卫的机器人执行后端基类。

``BaseRobotBackend`` 实现与 ``MockBackend`` 相同的公开接口
（``execute`` / ``safe_stop`` / ``trajectory_points`` / ``snapshot`` + ``mode``），
但把五个原子动作映射到真实运动原语，并在每次运动前强制执行安全守卫：

- 工作空间限制（越界 fail-closed）；
- 线速度上限（限速，超限即拒绝）；
- 动作墙钟超时（超时 fail-closed）；
- 碰撞检查（查询异常时 fail-closed，绝不假设安全）；
- 人工确认（真机模式强制）；
- 急停与失败安全停止。

后端只依赖 ``MotionDriver`` Protocol，不在模块顶层导入 Isaac Sim，
因此可在 `huawei` 环境与 CI 中用假驱动（FakeDriver）完整测试。
"""

from __future__ import annotations

from copy import deepcopy

from modules.executor.isaac_driver import DriverError
from modules.executor.safety import SafetyPolicy, workspace_violations

COLLISION_RADIUS_M = 0.05
SAFE_LIFT_Z_M = 0.20          # 抓取后抬升 / 放置前接近的安全高度
GRASP_Z_OFFSET_M = 0.003      # 抓取时末端略低于物体顶部的补偿
PLACE_Z_MIN_M = 0.02          # 放置高度下限


def _failed(reason: str, duration_ms: int = 0, **extra) -> dict:
    result = {"status": "FAILED", "reason": reason, "duration_ms": duration_ms}
    result.update(extra)
    return result


def _succeeded(duration_ms: int = 0, **extra) -> dict:
    result = {"status": "SUCCESS", "reason": "", "duration_ms": duration_ms}
    result.update(extra)
    return result


class BaseRobotBackend:
    mode = "robot"

    def __init__(
        self,
        objects: dict,
        safety: SafetyPolicy | None = None,
        driver=None,
    ) -> None:
        self.safety = safety or SafetyPolicy()
        self._driver = driver
        self._objects = deepcopy(objects)
        self._approached_id: str | None = None
        self._held_id: str | None = None
        self._target_id: str | None = None
        self._placement_pose: dict | None = None
        self._eef_pose = {"x": 0.0, "y": 0.0, "z": SAFE_LIFT_Z_M}
        self._trajectory: list[dict] = []
        self._elapsed_ms = 0
        self._safe_stopped = False
        self._safe_stop_reason: str | None = None
        self._safety_events: list[dict] = []
        self._confirmed = not self.safety.require_human_confirmation
        self._confirmed_by: str | None = None

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def from_perception(
        cls,
        perception: dict,
        safety: SafetyPolicy | None = None,
        driver=None,
    ) -> "BaseRobotBackend":
        objects: dict[str, dict] = {}
        for item in perception.get("objects", []):
            object_id = item.get("id")
            if not object_id:
                raise ValueError("perception object is missing id")
            if object_id in objects:
                raise ValueError(f"duplicate object id: {object_id}")
            objects[object_id] = deepcopy(item)
        return cls(objects, safety=safety, driver=driver)

    # ------------------------------------------------------------------
    # 生命周期与安全门
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self._driver is None:
            raise DriverError("no driver bound to backend")
        self._driver.connect()

    def confirm(self, operator_id: str) -> None:
        """真机模式的人工确认。确认前任何运动动作都会被拒绝。"""
        self._confirmed = True
        self._confirmed_by = operator_id

    def require_confirmation(self) -> bool:
        return self.safety.require_human_confirmation

    def is_confirmed(self) -> bool:
        return self._confirmed

    def emergency_stop(self) -> dict:
        """急停：立即停止驱动并进入安全停止状态。"""
        if self._driver is not None:
            try:
                self._driver.e_stop()
            except DriverError:
                pass
        self._safe_stopped = True
        self._safe_stop_reason = "E_STOP_TRIGGERED"
        self._record_safety_event("E_STOP_TRIGGERED", "error", None,
                                  "emergency stop engaged")
        return {"status": "SAFE_STOP", "reason": "E_STOP_TRIGGERED", "duration_ms": 0}

    def safe_stop(self, reason: str) -> dict:
        self._safe_stopped = True
        self._safe_stop_reason = reason
        if self._driver is not None:
            try:
                self._driver.e_stop()
            except DriverError:
                pass
        return {"status": "SAFE_STOP", "reason": reason, "duration_ms": 0}

    def _safety_failure(
        self,
        action: str,
        reason: str,
        duration_ms: int,
        message: str,
    ) -> dict:
        event = self._record_safety_event(reason, "error", None, message)
        self._enter_fail_closed(action, [event])
        return _failed(reason, duration_ms, safety_events=[event])

    def health(self) -> dict:
        driver = self._driver
        connected = bool(
            driver is not None
            and getattr(driver, "connected", getattr(driver, "_connected", True))
        )
        if self._safe_stopped:
            status = "blocked"
        elif not connected:
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "backend": self.mode,
            "driver_bound": driver is not None,
            "driver_connected": connected,
            "safe_stopped": self._safe_stopped,
            "safe_stop_reason": self._safe_stop_reason,
        }

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
            "confirmed": self._confirmed,
            "confirmed_by": self._confirmed_by,
            "safety_events": deepcopy(self._safety_events),
        }

    # ------------------------------------------------------------------
    # 动作分发
    # ------------------------------------------------------------------
    def execute(self, action: str, arguments: dict) -> dict:
        if self._safe_stopped:
            return _failed("BACKEND_SAFE_STOPPED", 0)
        if action not in self._handlers():
            return _failed(f"UNKNOWN_ACTION:{action}", 0)

        # 人工确认：除只读 detect 外，任何可能动作都必须在真机模式确认后执行。
        if action != "detect_object" and self._require_confirmation_unmet():
            self._record_safety_event(
                "HUMAN_CONFIRMATION_REQUIRED", "error", None,
                f"{action} blocked until operator confirmation",
            )
            return _failed(
                "HUMAN_CONFIRMATION_REQUIRED", 0,
                safety_events=[self._safety_events[-1]],
            )

        try:
            return self._handlers()[action](arguments)
        except DriverError as exc:
            return self._fail_closed_driver_error(action, exc)

    def _handlers(self) -> dict:
        return {
            "detect_object": self._detect_object,
            "move_to_object": self._move_to_object,
            "grasp": self._grasp,
            "move_to_target": self._move_to_target,
            "release": self._release,
        }

    # ------------------------------------------------------------------
    # 五个原子动作
    # ------------------------------------------------------------------
    def _detect_object(self, arguments: dict) -> dict:
        object_id = arguments.get("object_id")
        if object_id is None:
            object_name = arguments.get("object_name")
            matches = [
                item_id
                for item_id, item in self._objects.items()
                if item_id == object_name
                or item.get("attributes", {}).get("display_name") == object_name
            ]
            if len(matches) > 1:
                return _failed(f"AMBIGUOUS_OBJECT_NAME:{object_name}", 0)
            object_id = matches[0] if matches else object_name
        item = self._objects.get(object_id)
        if item is None:
            return _failed(f"OBJECT_NOT_FOUND:{object_id}", 0)

        pose = deepcopy(item["pose"])
        # 真实后端：若驱动已连接，用仿真中的真实位姿刷新（ground-truth 感知）。
        if self._driver is not None:
            try:
                pose = self._driver.read_object_pose(object_id)
            except Exception as exc:  # noqa: BLE001
                event = self._record_safety_event(
                    "OBJECT_POSE_UNAVAILABLE", "error", None, str(exc)
                )
                self._enter_fail_closed("detect_object", [event])
                return _failed(
                    "OBJECT_POSE_UNAVAILABLE", 0, safety_events=[event]
                )
            item["pose"] = deepcopy(pose)
        return _succeeded(0, object_id=object_id, pose=pose)

    def _move_to_object(self, arguments: dict) -> dict:
        object_id = arguments.get("object_id")
        item = self._objects.get(object_id)
        if item is None:
            return _failed(f"OBJECT_NOT_FOUND:{object_id}", 0)

        approach = self._approach_pose(item)
        result = self._guarded_move_to(
            approach, "move_to_object", ignore_object_ids=(object_id,)
        )
        if result["status"] != "SUCCESS":
            return result
        self._approached_id = object_id
        self._eef_pose = deepcopy(result["pose"])
        return _succeeded(
            result["duration_ms"],
            object_id=object_id,
            pose=deepcopy(result["pose"]),
            safety_events=result.get("safety_events", []),
        )

    def _grasp(self, arguments: dict) -> dict:
        object_id = arguments.get("object_id")
        item = self._objects.get(object_id)
        if item is None:
            return _failed(f"OBJECT_NOT_FOUND:{object_id}", 0)
        if self._approached_id != object_id:
            return _failed("OBJECT_NOT_APPROACHED", 0)
        if not item.get("execution", {}).get("graspable", False):
            return _failed(f"OBJECT_NOT_GRASPABLE:{object_id}", 0)
        if self._held_id is not None:
            return _failed(f"GRIPPER_ALREADY_HOLDING:{self._held_id}", 0)

        if self._driver is None:
            return _failed("DRIVER_NOT_CONNECTED", 0)

        # 下降到抓取高度（略低于物体顶部）。
        grasp_pose = self._grasp_pose(item)
        descend = self._guarded_move_to(
            grasp_pose, "grasp", ignore_object_ids=(object_id,)
        )
        if descend["status"] != "SUCCESS":
            return descend

        # 闭合夹爪并验证夹持力。
        timeout = self.safety.motion.action_timeout_s
        close = self._driver.gripper_close(self.safety.motion.max_force_n, timeout)
        self._elapsed_ms += close.get("duration_ms", 0)
        self._extend_trajectory(close.get("trajectory", []))
        if close.get("timed_out"):
            return self._safety_failure(
                "grasp", "ACTION_TIMEOUT", close.get("duration_ms", 0),
                "gripper close exceeded the configured action timeout",
            )
        if close["status"] != "SUCCESS":
            return _failed(close.get("reason") or "GRASP_FAILED",
                           close.get("duration_ms", 0))
        force = close.get("grasp_force_n")
        if force is not None and force < self.safety.motion.grasp_verify_force_n:
            return _failed("GRASP_WEAK", close.get("duration_ms", 0),
                           grasp_force_n=force)

        # 抬升到安全高度。
        lift = self._guarded_move_to(
            {"x": grasp_pose["x"], "y": grasp_pose["y"], "z": SAFE_LIFT_Z_M},
            "grasp",
            ignore_object_ids=(object_id,),
        )
        if lift["status"] != "SUCCESS":
            return lift

        verifier = getattr(self._driver, "verify_grasp", None)
        if not callable(verifier):
            return self._safety_failure(
                "grasp", "GRASP_VERIFICATION_UNAVAILABLE", 0,
                "driver does not provide post-lift grasp verification",
            )
        try:
            verification = verifier(
                object_id, initial_pose=item.get("pose"), lift_z=SAFE_LIFT_Z_M
            )
        except Exception as exc:  # noqa: BLE001
            event = self._record_safety_event(
                "GRASP_VERIFICATION_ERROR", "error", None, str(exc)
            )
            self._enter_fail_closed("grasp", [event])
            return _failed("GRASP_VERIFICATION_ERROR", 0,
                           safety_events=[event])
        if not isinstance(verification, dict):
            return self._safety_failure(
                "grasp", "GRASP_VERIFICATION_ERROR", 0,
                "driver returned an invalid grasp verification result",
            )
        if not verification.get("verified", False):
            event = self._record_safety_event(
                "GRASP_UNVERIFIED", "error", None,
                verification.get("reason") or "object did not lift",
            )
            self._enter_fail_closed("grasp", [event])
            return _failed("GRASP_UNVERIFIED", 0,
                           safety_events=[event])

        verified_pose = verification.get("object_pose")
        if isinstance(verified_pose, dict):
            self._objects[object_id]["pose"] = deepcopy(verified_pose)
        if verification.get("grasp_force_n") is not None:
            force = verification["grasp_force_n"]

        self._held_id = object_id
        return _succeeded(
            descend["duration_ms"] + close.get("duration_ms", 0)
            + lift["duration_ms"],
            object_id=object_id,
            **({"grasp_force_n": force} if force is not None else {}),
            safety_events=descend.get("safety_events", [])
            + close.get("safety_events", [])
            + lift.get("safety_events", []),
        )

    def _move_to_target(self, arguments: dict) -> dict:
        destination_id = arguments.get("destination_id", arguments.get("target"))
        placement_mode = arguments.get("placement_mode", "direct")
        item = self._objects.get(destination_id)
        if item is None:
            return _failed(f"INVALID_DESTINATION:{destination_id}", 0)
        execution = item.get("execution", {})

        if placement_mode == "stack_on":
            if not execution.get("stackable_destination", False):
                return _failed(f"INVALID_STACK_DESTINATION:{destination_id}", 0)
            if destination_id == self._held_id:
                return _failed("STACK_TARGET_IS_HELD_OBJECT", 0)
            if self._held_id is None:
                return _failed("NOT_HOLDING_OBJECT", 0)
            placement_pose = self._stack_pose(item, self._objects[self._held_id])
        elif placement_mode == "direct":
            if (
                not execution.get("valid_destination", False)
                or execution.get("stackable_destination", False)
            ):
                return _failed(f"INVALID_DESTINATION:{destination_id}", 0)
            if self._held_id is None:
                return _failed("NOT_HOLDING_OBJECT", 0)
            placement_pose = deepcopy(item["pose"])
        else:
            return _failed(f"PLACEMENT_MODE_UNSUPPORTED:{placement_mode}", 0)

        approach = self._approach_above(placement_pose)
        result = self._guarded_move_to(
            approach, "move_to_target", ignore_object_ids=(destination_id,)
        )
        if result["status"] != "SUCCESS":
            return result
        self._target_id = destination_id
        self._placement_pose = placement_pose
        self._eef_pose = deepcopy(result["pose"])
        return _succeeded(
            result["duration_ms"],
            destination_id=destination_id,
            placement_mode=placement_mode,
            pose=deepcopy(result["pose"]),
            safety_events=result.get("safety_events", []),
        )

    def _release(self, arguments: dict) -> dict:
        if self._held_id is None:
            return _failed("NOT_HOLDING_OBJECT", 0)
        if self._target_id is None:
            return _failed("TARGET_NOT_REACHED", 0)
        if self._driver is None:
            return _failed("DRIVER_NOT_CONNECTED", 0)

        released_id = self._held_id
        timeout = self.safety.motion.action_timeout_s
        opened = self._driver.gripper_open(0.08, timeout)
        self._elapsed_ms += opened.get("duration_ms", 0)
        self._extend_trajectory(opened.get("trajectory", []))
        if opened.get("timed_out"):
            return self._safety_failure(
                "release", "ACTION_TIMEOUT", opened.get("duration_ms", 0),
                "gripper open exceeded the configured action timeout",
            )
        if opened["status"] != "SUCCESS":
            return _failed(opened.get("reason") or "RELEASE_FAILED",
                           opened.get("duration_ms", 0))

        # 撤离到安全高度。
        retreat = self._guarded_move_to(
            {"x": self._eef_pose["x"], "y": self._eef_pose["y"], "z": SAFE_LIFT_Z_M},
            "release",
            ignore_object_ids=(self._target_id,),
        )
        if retreat["status"] != "SUCCESS":
            return retreat

        # 把物体真实位姿更新到放置点（direct=目标中心，stack_on=堆叠位置）。
        self._objects[released_id]["pose"] = deepcopy(
            self._placement_pose or self._objects[self._target_id]["pose"]
        )
        self._held_id = None
        self._approached_id = None
        self._target_id = None
        self._placement_pose = None
        return _succeeded(
            opened.get("duration_ms", 0) + retreat["duration_ms"],
            object_id=released_id,
            safety_events=opened.get("safety_events", [])
            + retreat.get("safety_events", []),
        )

    # ------------------------------------------------------------------
    # 安全守卫与运动封装
    # ------------------------------------------------------------------
    def _guarded_move_to(
        self,
        pose: dict,
        action: str,
        ignore_object_ids: tuple[str | None, ...] = (),
    ) -> dict:
        """执行一次带完整安全守卫的笛卡尔运动。"""
        events: list[dict] = []

        # 1) 工作空间检查（fail-closed）。
        violations = workspace_violations(pose, self.safety.workspace)
        if violations:
            for reason in violations:
                events.append(self._record_safety_event(
                    "WORKSPACE_VIOLATION", "error", None, reason))
            self._enter_fail_closed(action, events)
            return _failed("WORKSPACE_VIOLATION", 0, safety_events=events)

        # 2) 碰撞检查（查询异常一律 fail-closed）。
        if self.safety.collision_check:
            if self._driver is None:
                events.append(self._record_safety_event(
                    "COLLISION_CHECK_UNAVAILABLE", "error", None,
                    "no driver bound, cannot verify collision-free motion"))
                self._enter_fail_closed(action, events)
                return _failed("COLLISION_CHECK_UNAVAILABLE", 0, safety_events=events)
            try:
                excluded_paths = tuple(
                    ["/World/robot"]
                    + [
                        f"/World/{object_id}"
                        for object_id in ignore_object_ids
                        if object_id
                    ]
                )
                if not self._driver.collision_free(
                    pose, COLLISION_RADIUS_M, excluded_paths=excluded_paths
                ):
                    events.append(self._record_safety_event(
                        "COLLISION_DETECTED", "error", None,
                        f"collision risk at {pose!r}"))
                    self._enter_fail_closed(action, events)
                    return _failed("COLLISION_DETECTED", 0, safety_events=events)
            except Exception as exc:  # noqa: BLE001
                if self.safety.fail_closed_on_error:
                    events.append(self._record_safety_event(
                        "COLLISION_CHECK_ERROR", "error", None, str(exc)))
                    self._enter_fail_closed(action, events)
                    return _failed(f"COLLISION_CHECK_ERROR:{exc}", 0,
                                   safety_events=events)

        # 3) 限速（超限即拒绝；正常按上限截断）。
        speed = self._effective_speed()
        if speed is None:
            events.append(self._record_safety_event(
                "SPEED_LIMIT_EXCEEDED", "error", None,
                "configured max_linear_velocity_m_s is not positive"))
            self._enter_fail_closed(action, events)
            return _failed("SPEED_LIMIT_EXCEEDED", 0, safety_events=events)

        # 4) 运动 + 超时判断。
        move = self._driver.move_to(pose, speed, self.safety.motion.action_timeout_s)
        self._elapsed_ms += move.get("duration_ms", 0)
        self._extend_trajectory(move.get("trajectory", []))
        if move.get("timed_out"):
            events.append(self._record_safety_event(
                "ACTION_TIMEOUT", "error", None,
                f"{action} exceeded {self.safety.motion.action_timeout_s}s"))
            self._enter_fail_closed(action, events)
            return _failed("ACTION_TIMEOUT", move.get("duration_ms", 0),
                           safety_events=events)
        if move["status"] != "SUCCESS":
            return _failed(move.get("reason") or "MOTION_FAILED",
                           move.get("duration_ms", 0),
                           safety_events=events)
        for collision in move.get("collisions", []):
            events.append(self._record_safety_event(
                "COLLISION_DETECTED", "error", None, str(collision)))
        if events:
            self._enter_fail_closed(action, events)
            return _failed(
                "COLLISION_DETECTED", move.get("duration_ms", 0),
                safety_events=events,
            )
        return _succeeded(move.get("duration_ms", 0),
                          pose=deepcopy(move.get("pose", pose)),
                          safety_events=events)

    def _effective_speed(self) -> float | None:
        ceiling = self.safety.motion.max_linear_velocity_m_s
        if ceiling <= 0:
            return None
        return min(self.safety.motion.default_linear_speed_m_s, ceiling)

    def _require_confirmation_unmet(self) -> bool:
        return self.safety.require_human_confirmation and not self._confirmed

    def _fail_closed_driver_error(self, action: str, exc: DriverError) -> dict:
        event = self._record_safety_event(
            "BACKEND_ERROR", "error", None, f"{action}: {exc}")
        self._enter_fail_closed(action, [event])
        return _failed(f"BACKEND_ERROR:{exc}", 0, safety_events=[event])

    def _enter_fail_closed(self, action: str, events: list[dict]) -> None:
        """安全门触发后进入安全停止（失败安全停止）。"""
        self._safe_stopped = True
        self._safe_stop_reason = events[-1]["type"] if events else "SAFETY_GATE"
        if self._driver is not None:
            try:
                self._driver.e_stop()
            except DriverError:
                pass

    def _record_safety_event(self, event_type, severity, step_id, message) -> dict:
        event = {
            "type": event_type,
            "severity": severity,
            "step_id": step_id,
            "message": message,
        }
        self._safety_events.append(event)
        return event

    def _extend_trajectory(self, points: list[dict]) -> None:
        for point in points:
            self._trajectory.append(deepcopy(point))

    # ------------------------------------------------------------------
    # 几何工具
    # ------------------------------------------------------------------
    @staticmethod
    def _object_center(item: dict) -> dict:
        pose = item.get("pose", {})
        return {"x": pose.get("x", 0.0), "y": pose.get("y", 0.0),
                "z": pose.get("z", 0.0)}

    @staticmethod
    def _object_top_z(item: dict) -> float:
        pose = item.get("pose", {})
        dimensions = item.get("dimensions", {})
        half_height = float(dimensions.get("z", 0.04)) / 2.0
        return float(pose.get("z", 0.0)) + half_height

    def _approach_pose(self, item: dict) -> dict:
        center = self._object_center(item)
        return {"x": center["x"], "y": center["y"],
                "z": max(self._object_top_z(item) + 0.10, SAFE_LIFT_Z_M)}

    def _grasp_pose(self, item: dict) -> dict:
        center = self._object_center(item)
        return {"x": center["x"], "y": center["y"],
                "z": max(self._object_top_z(item) - GRASP_Z_OFFSET_M, PLACE_Z_MIN_M)}

    def _approach_above(self, pose: dict) -> dict:
        """返回放置点上方 0.10m（且不低于安全抬升高度）的接近位姿。"""
        return {"x": pose.get("x", 0.0), "y": pose.get("y", 0.0),
                "z": max(float(pose.get("z", 0.0)) + 0.10, SAFE_LIFT_Z_M)}

    @staticmethod
    def _stack_pose(destination: dict, held: dict) -> dict:
        """返回被夹持物体堆叠到底座上方时的中心位姿（对齐 MockBackend._stack_pose）。"""
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
