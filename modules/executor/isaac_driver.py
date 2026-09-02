"""Isaac Sim 运动驱动抽象层。

``MotionDriver`` 是执行后端与仿真/真机之间的唯一接口。后端只依赖这个 Protocol，
不在模块顶层导入 ``isaacsim`` / ``omni``，因此可以在 `huawei` 环境与 CI 中用
假驱动（FakeDriver）做单元测试。

``OmniDriver`` 是真实 Isaac Sim 6.0 实现，严格对照官方 standalone examples：

- 机器人：``isaacsim.robot.experimental.manipulators.examples.franka.franka.Franka``
  （继承 Articulation，内置差分 IK 与夹爪控制）；
- 笛卡尔运动：差分 IK（``damped-least-squares``），每帧把受速度限制的
  小步目标交给官方 ``set_end_effector_pose`` 再 ``app.update``；
- 夹爪：``set_gripper_position`` / ``open_gripper`` / ``close_gripper``（DOF 7/8）；
- 物理：``SimulationManager.set_physics_sim_device``（默认 CPU）。

Franka 资产路径：``Isaac/Robots/FrankaRobotics/FrankaPanda/franka.usd``。
所有 ``isaacsim.*`` / ``omni.*`` 导入都延迟到方法内部，模块可在无 Isaac 环境 import。
"""

from __future__ import annotations

from typing import Protocol


def _path_text(value) -> str:
    """Return a USD path for the several PhysX/pxr path representations."""
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("pathString", "path_string", "path", "prim_path", "primPath"):
            if key in value:
                path = _path_text(value[key])
                if path:
                    return path
        return ""
    if hasattr(value, "GetPath"):
        try:
            value = value.GetPath()
        except Exception:  # noqa: BLE001
            pass
    for attribute in ("pathString", "path_string"):
        candidate = getattr(value, attribute, None)
        if candidate:
            return str(candidate)
    text = str(value)
    return text if text.startswith("/") else ""


def _decode_physx_path(value) -> str:
    """Decode PhysX's encoded SdfPath variants when direct paths are absent."""
    if value is None:
        return ""
    try:
        from pxr import PhysicsSchemaTools
    except Exception:  # noqa: BLE001 - unavailable outside Isaac Sim
        return ""
    for name in ("decodeSdfPath", "intToSdfPath"):
        decoder = getattr(PhysicsSchemaTools, name, None)
        if not callable(decoder):
            continue
        try:
            decoded = decoder(value)
        except Exception:  # noqa: BLE001 - bindings differ by Isaac version
            continue
        path = _path_text(decoded)
        if path:
            return path
    return ""


def _physx_hit_path(hit) -> str:
    """Extract a collider/body path from Isaac Sim 6 scene-query hit variants."""
    direct_fields = (
        "rigid_body",
        "rigidBody",
        "collision",
        "collider",
        "actor",
        "shape",
        "path",
        "prim_path",
        "primPath",
    )
    encoded_fields = (
        "rigid_body_encoded",
        "rigidBodyEncoded",
        "collision_encoded",
        "collisionEncoded",
    )
    if isinstance(hit, dict):
        for key in direct_fields:
            if key in hit:
                path = _path_text(hit[key])
                if path:
                    return path
        for key in encoded_fields:
            if key in hit:
                path = _decode_physx_path(hit[key])
                if path:
                    return path
        return ""
    for attribute in direct_fields:
        if hasattr(hit, attribute):
            path = _path_text(getattr(hit, attribute))
            if path:
                return path
    for attribute in encoded_fields:
        if hasattr(hit, attribute):
            path = _decode_physx_path(getattr(hit, attribute))
            if path:
                return path
    return ""


def _physx_hit_debug(hit) -> str:
    """Return bounded diagnostics for a hit whose path cannot be decoded."""
    if isinstance(hit, dict):
        keys = sorted(str(key) for key in hit.keys())
        return f"dict_keys={keys[:20]}"
    try:
        names = [
            name for name in dir(hit)
            if not name.startswith("_") and any(token in name.lower() for token in ("path", "body", "collision", "actor", "shape"))
        ]
    except Exception:  # noqa: BLE001
        names = []
    return f"type={type(hit).__name__};attrs={names[:20]}"


def _raycast_overlap_fallback(query, origin, radius, carb_module):
    """Resolve a count-only overlap through short radial raycasts.

    Some GPU PhysX builds return a positive overlap count but do not invoke the
    Python overlap callback.  Raycast-closest still returns the documented
    path-bearing dictionary on those builds, so probe the sphere from its
    center in a bounded 26-direction stencil.  This never reports "free" when
    a hit is seen but cannot be identified.
    """

    diagonal = 0.7071067811865476
    directions = (
        (1.0, 0.0, 0.0), (-1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0), (0.0, -1.0, 0.0),
        (0.0, 0.0, 1.0), (0.0, 0.0, -1.0),
        (diagonal, diagonal, 0.0), (diagonal, -diagonal, 0.0),
        (-diagonal, diagonal, 0.0), (-diagonal, -diagonal, 0.0),
        (diagonal, 0.0, diagonal), (diagonal, 0.0, -diagonal),
        (-diagonal, 0.0, diagonal), (-diagonal, 0.0, -diagonal),
        (0.0, diagonal, diagonal), (0.0, diagonal, -diagonal),
        (0.0, -diagonal, diagonal), (0.0, -diagonal, -diagonal),
        (diagonal, diagonal, diagonal), (diagonal, diagonal, -diagonal),
        (diagonal, -diagonal, diagonal), (diagonal, -diagonal, -diagonal),
        (-diagonal, diagonal, diagonal), (-diagonal, diagonal, -diagonal),
        (-diagonal, -diagonal, diagonal), (-diagonal, -diagonal, -diagonal),
    )
    paths: list[str] = []
    unresolved = False
    for direction in directions:
        try:
            info = query.raycast_closest(
                origin,
                carb_module.Float3(*direction),
                float(radius),
            )
        except Exception:  # noqa: BLE001 - fallback is best effort
            continue
        if not isinstance(info, dict) or not info.get("hit"):
            continue
        path = _physx_hit_path(info)
        if path:
            if path not in paths:
                paths.append(path)
        else:
            unresolved = True
    return paths, unresolved


class DriverError(RuntimeError):
    """驱动层不可恢复错误（例如 Isaac 未连接、PhysX 查询失败）。

    后端把该异常一律按 fail-closed 处理：拒绝动作或进入安全停止，
    绝不静默假设“安全”。
    """


def motion_result(
    status: str,
    reason: str = "",
    duration_ms: int = 0,
    *,
    timed_out: bool = False,
    collisions: list | None = None,
    trajectory: list | None = None,
    **extra,
) -> dict:
    """构建统一的驱动动作结果字典。"""
    result = {
        "status": status,
        "reason": reason,
        "duration_ms": int(duration_ms),
        "timed_out": bool(timed_out),
        "collisions": collisions or [],
        "trajectory": trajectory or [],
    }
    result.update(extra)
    return result


def _failed(reason: str, duration_ms: int = 0, **extra) -> dict:
    return motion_result("FAILED", reason, duration_ms, **extra)


def _succeeded(duration_ms: int = 0, **extra) -> dict:
    return motion_result("SUCCESS", "", duration_ms, **extra)


class MotionDriver(Protocol):
    """运动原语接口。所有方法都可能抛出 ``DriverError``。"""

    def connect(self) -> None: ...

    def start(self) -> None: ...

    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict: ...

    def gripper_open(self, width: float, timeout_s: float) -> dict: ...

    def gripper_close(self, force: float, timeout_s: float) -> dict: ...

    def read_object_pose(self, object_id: str) -> dict: ...

    def verify_grasp(
        self,
        object_id: str,
        initial_pose: dict | None = None,
        lift_z: float = 0.20,
    ) -> dict: ...

    def verify_release(
        self,
        object_id: str,
        target_pose: dict,
        tolerance_m: float = 0.075,
    ) -> dict: ...

    def collision_free(
        self,
        pose: dict,
        radius: float,
        excluded_paths: tuple[str, ...] = (),
    ) -> bool: ...

    def e_stop(self) -> None: ...

    def shutdown(self) -> None: ...


class FrankaPickPlaceDriver:
    """Adapter around NVIDIA's official ``FrankaPickPlace`` controller.

    The controller is intentionally kept as the authoritative SIM execution
    loop.  It owns the articulation, dynamic cube and phase timing, while this
    adapter exposes the same primitive driver contract consumed by
    ``BaseRobotBackend``.  This avoids re-implementing a second IK/physics loop
    in the integration harness.
    """

    IK_METHOD = "damped-least-squares"
    # Keep the official phase sequence, but shorten the transport phase.  The
    # right-side dynamic-object cases showed that the last carry ticks can
    # lose contact before the release phase begins; fewer transport ticks
    # reduce that exposure without changing the grasp/release ordering.
    CONTROLLER_EVENTS_DT = (60, 40, 20, 40, 60, 20, 20)
    # The placement zone is 0.10 m wide and the dynamic cube is 0.0515 m
    # wide.  A center error up to half-zone + half-cube (0.07575 m) still
    # leaves the physical footprint inside the zone; keep a rounded 0.075 m
    # bound instead of rejecting a valid edge placement by center distance.
    RELEASE_TOLERANCE_M = 0.075
    # FrankaPickPlace.target_position is the end-effector command, not the
    # physical cube center.  Start from the measured tool offset and close
    # the remaining XY error during transport with a bounded PhysX feedback
    # correction; a fixed offset alone is not reliable across IK states.
    CONTROLLER_TARGET_XY_OFFSET_M = (0.05, 0.05)
    CONTROLLER_TARGET_Z_M = 0.03
    # The first fixed-scene calibration was intentionally conservative.  The
    # supplement includes targets approached from both sides of the table;
    # retain a bounded correction but leave enough authority for the measured
    # XY error to converge before release.
    TRANSPORT_FEEDBACK_GAIN = 0.75
    TRANSPORT_FEEDBACK_MAX_CORRECTION_M = (0.06, 0.08)

    @classmethod
    def bounded_transport_correction(cls, error_xy) -> tuple[float, float]:
        """Return a bounded XY correction for a measured object-target error."""
        return tuple(
            max(-float(limit), min(float(limit), float(cls.TRANSPORT_FEEDBACK_GAIN) * float(error)))
            for error, limit in zip(error_xy, cls.TRANSPORT_FEEDBACK_MAX_CORRECTION_M)
        )

    def __init__(
        self,
        app,
        device: str = "cuda",
        cube_position=(0.50, 0.0, 0.0258),
        # This default is retained for the fixed acceptance scene.  The
        # backend calls set_target_pose() for camera-derived destinations.
        target_position=(0.40, 0.05, 0.03),
        dynamic_object_id: str = "green_cube",
    ) -> None:
        if not isinstance(dynamic_object_id, str) or not dynamic_object_id.strip():
            raise ValueError("dynamic_object_id must be a non-empty string")
        self._app = app
        self._device = device
        self._cube_position = cube_position
        self._target_position = target_position
        self._dynamic_object_id = dynamic_object_id.strip()
        self._controller = None
        self._connected = False
        self._started = False
        self._stopped = False
        self._phase_history: list[dict] = []
        self._release_settle_history: list[dict] = []
        self._physical_target_pose: dict | None = None
        self._controller_target_nominal = None
        self._last_transport_feedback: dict | None = None

    @property
    def dynamic_object_id(self) -> str:
        """The one PhysX body owned by the official pick/place controller."""
        return self._dynamic_object_id

    def connect(self, *, defer_start: bool = False) -> None:
        if self._connected:
            if not defer_start:
                self.start()
            return
        import numpy as np
        import omni.kit.app

        # The FrankaPickPlace class lives in an optional extension.  Isaac Sim
        # must load that extension (and process one app tick) before importing
        # the Python package; importing first raises ModuleNotFoundError.
        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "isaacsim.robot.experimental.manipulators.examples", True
        )
        self._app.update()
        from isaacsim.core.simulation_manager import SimulationManager
        from isaacsim.robot.experimental.manipulators.examples.franka import FrankaPickPlace

        SimulationManager.set_physics_sim_device(self._device)
        self._app.update()
        # Keep NVIDIA's published phase timing.  The controller's close and
        # lift phases are coupled to its internal gripper trajectory; adding
        # extra frames here can move the fingers through the contact window
        # and make a real grasp less reliable, even though the end-effector
        # phase history still looks successful.
        self._controller = FrankaPickPlace(
            events_dt=list(self.CONTROLLER_EVENTS_DT)
        )
        self._controller.setup_scene(
            cube_initial_position=np.asarray(self._cube_position, dtype=float),
            cube_initial_orientation=np.asarray((1.0, 0.0, 0.0, 0.0), dtype=float),
            cube_size=np.asarray((0.0515, 0.0515, 0.0515), dtype=float),
            target_position=np.asarray(self._target_position, dtype=float),
            cube_path=f"/World/{self._dynamic_object_id}",
        )
        self._connected = True
        self._started = False
        self._stopped = False
        self._phase_history = []
        self._last_transport_feedback = None
        if not defer_start:
            self.start()

    def start(self) -> None:
        if not self._connected or self._controller is None:
            raise DriverError("FrankaPickPlaceDriver not connected")
        if self._started:
            return
        import omni.timeline

        omni.timeline.get_timeline_interface().play()
        self._app.update()
        self._controller.reset()
        self._started = True

    def shutdown(self) -> None:
        self.e_stop()
        self._connected = False
        self._started = False
        self._controller = None

    def reset_for_control(self) -> None:
        """Reset the official controller immediately before C execution.

        The live RGB-D capture intentionally advances a few simulation ticks
        after the timeline starts.  Resetting once more after that capture
        restores the same reset-to-first-forward boundary as NVIDIA's sample
        and prevents an idle physics tick from changing the contact setup.
        """
        self._ensure_started()
        self._controller.reset()
        self._phase_history = []
        self._release_settle_history = []

    def warmup_for_control(self, *, forward_steps: int = 1) -> dict:
        """Compile the first controller path, then restore the task boundary.

        Isaac Sim 6 may compile CUDA/IK kernels during the first controller
        ``forward`` call.  If that cost is charged to ``move_to_object``, a
        valid task can be incorrectly classified as an action timeout.  The
        warm-up is deliberately bounded to one forward step and is followed
        by a controller reset, so every variant starts the measured task from
        the same initial state.
        """
        import time

        self._ensure_started()
        steps = max(1, int(forward_steps))
        started = time.monotonic()
        self._controller.reset()
        event_before = int(self._controller._event)
        for _ in range(steps):
            self._controller.forward(self.IK_METHOD)
            self._app.update()
        event_after = int(self._controller._event)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._controller.reset()
        self._phase_history = []
        return {
            "forward_steps": steps,
            "event_before": event_before,
            "event_after": event_after,
            "elapsed_ms": elapsed_ms,
            "reset_after_warmup": True,
        }

    def _ensure_started(self) -> None:
        if not self._connected or self._controller is None:
            raise DriverError("FrankaPickPlaceDriver not connected")
        if not self._started:
            raise DriverError("FrankaPickPlaceDriver simulation not started")
        if self._stopped:
            raise DriverError("FrankaPickPlaceDriver is in emergency stop state")

    def _controller_pose(self) -> dict:
        _, positions, _ = self._controller.robot.get_current_state()
        pos = positions[0]
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}

    def _controller_cube_pose(self) -> dict | None:
        """Read the official dynamic-body tensor for diagnostic comparison."""
        try:
            positions = self._controller.cube.get_world_poses()[0]
            if hasattr(positions, "numpy"):
                positions = positions.numpy()
            pos = positions[0]
            return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
        except Exception:  # noqa: BLE001
            return None

    def _run_current_phase(self, timeout_s: float, *, trajectory: list[dict] | None = None) -> dict:
        import time

        self._ensure_started()
        trajectory = trajectory if trajectory is not None else []
        start = time.monotonic()
        initial_event = int(self._controller._event)
        frames = 0
        while int(self._controller._event) == initial_event and not self._controller.is_done():
            # Apply the bounded live-pose correction during transport only.
            # The controller's short descent phase has its own contact
            # trajectory; changing its target mid-descent introduces more
            # lateral impulse than it removes for the right-side cube.
            if initial_event == 4:
                self._apply_transport_feedback()
            self._controller.forward(self.IK_METHOD)
            self._app.update()
            frames += 1
            if time.monotonic() - start >= float(timeout_s):
                wall_ms = int((time.monotonic() - start) * 1000)
                self._phase_history.append({
                    "event_before": initial_event,
                    "event_after": int(self._controller._event),
                    "frames": frames,
                    "wall_ms": wall_ms,
                    "timed_out": True,
                    "timeout_s": float(timeout_s),
                })
                return motion_result(
                    "FAILED", "ACTION_TIMEOUT", wall_ms,
                    timed_out=True,
                    trajectory=trajectory,
                    frames=frames,
                    event_before=initial_event,
                    event_after=int(self._controller._event),
                )
        wall_ms = int((time.monotonic() - start) * 1000)
        pose = self._controller_pose()
        self._phase_history.append({
            "event_before": initial_event,
            "event_after": int(self._controller._event),
            "frames": frames,
            "pose": dict(pose),
            "controller_cube_pose": self._controller_cube_pose(),
        })
        trajectory.append({
            "timestamp_ms": wall_ms,
            "coordinate_frame": "world",
            "position": pose,
            "distance_m": 0.0,
            "velocity_m_s": 0.20,
            "joint_positions": [],
        })
        return motion_result(
            "SUCCESS", "", wall_ms,
            pose=pose,
            trajectory=trajectory,
            frames=frames,
            velocity_m_s=0.20,
        )

    def diagnostics(self) -> dict:
        """Return bounded controller state for real-run audit evidence."""
        controller = self._controller
        if controller is None:
            return {"connected": False, "phase_history": list(self._phase_history)}
        values = {
            "connected": bool(self._connected),
            "started": bool(self._started),
            "dynamic_object_id": self._dynamic_object_id,
            "event": int(getattr(controller, "_event", -1)),
            "step": int(getattr(controller, "_step", -1)),
            "phase_history": list(self._phase_history),
            "physical_target_pose": self._physical_target_pose,
            "controller_target_nominal": (
                self._controller_target_nominal.tolist()
                if self._controller_target_nominal is not None
                else None
            ),
            "last_transport_feedback": self._last_transport_feedback,
            "release_settle_history": list(self._release_settle_history),
        }
        for name in ("events_dt", "cube_position", "target_position"):
            value = getattr(controller, name, None)
            if value is None:
                value = getattr(self, f"_{name}", None)
            if value is not None:
                try:
                    values[name] = value.tolist()
                except AttributeError:
                    values[name] = list(value) if isinstance(value, tuple) else value
        return values

    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict:
        if float(linear_speed) <= 0:
            return _failed("SPEED_LIMIT_EXCEEDED", 0)
        return self._run_current_phase(timeout_s)

    def set_target_pose(self, target_pose: dict) -> None:
        """Map a physical camera target to the official controller target.

        The experimental NVIDIA example accepts an end-effector target even
        though its public argument is named ``target_position``.  Passing the
        camera's object-center pose directly therefore leaves a systematic
        XY placement error.  Apply the measured tool offset before phase 4;
        changing it after transport starts would make the sequence ambiguous.
        """
        if not self._connected or self._controller is None:
            raise DriverError("FrankaPickPlaceDriver not connected")
        if self._started and int(getattr(self._controller, "_event", 0)) > 4:
            raise DriverError("target calibration is too late; controller is already releasing")
        import numpy as np

        offset_x, offset_y = self.CONTROLLER_TARGET_XY_OFFSET_M
        target = np.asarray(
            (
                float(target_pose["x"]) - float(offset_x),
                float(target_pose["y"]) - float(offset_y),
                float(self.CONTROLLER_TARGET_Z_M),
            ),
            dtype=float,
        )
        # Record the physical target even when the official controller already
        # has the same numerical target.  This keeps the transport feedback
        # and audit evidence correct on repeated setter calls.
        self._target_position = tuple(float(value) for value in target)
        self._controller_target_nominal = target.copy()
        self._physical_target_pose = {
            "x": float(target_pose["x"]),
            "y": float(target_pose["y"]),
            "z": float(target_pose.get("z", 0.0)),
        }
        if np.allclose(
            np.asarray(getattr(self._controller, "target_position", target), dtype=float),
            target,
        ):
            return
        self._controller.target_position = target

    def _apply_transport_feedback(self) -> None:
        """Bounded XY correction using the live PhysX cube pose.

        The NVIDIA controller remains responsible for all motion and gripper
        commands.  This only nudges its phase-4 end-effector target from the
        measured cube error, preventing the fixed tool offset from turning
        into a systematic placement miss.
        """
        if self._physical_target_pose is None or self._controller_target_nominal is None:
            return
        cube = self._controller_cube_pose()
        if cube is None:
            return
        import numpy as np

        error = np.asarray(
            [
                self._physical_target_pose["x"] - cube["x"],
                self._physical_target_pose["y"] - cube["y"],
            ],
            dtype=float,
        )
        correction = np.asarray(self.bounded_transport_correction(error), dtype=float)
        target = np.asarray(self._controller_target_nominal, dtype=float).copy()
        target[:2] += correction
        self._controller.target_position = target
        self._last_transport_feedback = {
            "cube_pose": dict(cube),
            "target_pose": dict(self._physical_target_pose),
            "error_xy_m": [float(value) for value in error],
            "correction_xy_m": [float(value) for value in correction],
            "controller_target": [float(value) for value in target],
        }

    def gripper_open(self, width: float, timeout_s: float) -> dict:
        return self._run_current_phase(timeout_s)

    def settle_before_release(self, *, steps: int = 5) -> dict:
        """Advance PhysX while the closed gripper and payload are stationary."""
        import time

        self._ensure_started()
        count = max(1, min(int(steps), 30))
        started = time.monotonic()
        event_before = int(getattr(self._controller, "_event", -1))
        for _ in range(count):
            self._app.update()
        self._release_settle_history.append({
            "hook": "settle_before_release",
            "event": event_before,
            "steps": count,
            "controller_cube_pose": self._controller_cube_pose(),
        })
        return motion_result(
            "SUCCESS", "", int((time.monotonic() - started) * 1000),
            settle_steps=count,
            controller_cube_pose=self._controller_cube_pose(),
        )

    # Compatibility alias for callers written against the first release
    # stabilization hook. New release flows call the pre-open hook so the
    # payload is settled before the fingers are opened.
    def settle_after_release(self, *, steps: int = 5) -> dict:
        return self.settle_before_release(steps=steps)

    def gripper_close(self, force: float, timeout_s: float) -> dict:
        return self._run_current_phase(timeout_s)

    def read_object_pose(self, object_id: str) -> dict:
        self._ensure_started()
        # The official Franka controller owns green_cube as a PhysX dynamic
        # body.  On Isaac Sim 6 GPU physics, its USD root transform can lag
        # the live rigid-body tensor for several frames (or remain at the
        # spawn pose after a timeline stop).  Use the controller's dynamic
        # tensor for the moving object while the controller is active; keep
        # USD as the source for static scene objects.
        if object_id == self._dynamic_object_id:
            controller_pose = self._controller_cube_pose()
            if controller_pose is not None:
                return controller_pose
        from pxr import UsdGeom
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(f"/World/{object_id}")
        if not prim or not prim.IsValid():
            raise DriverError(f"object prim not found: {object_id}")
        pos = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}

    def verify_grasp(self, object_id: str, initial_pose: dict | None = None, lift_z: float = 0.20) -> dict:
        # The controller phase boundary is observed immediately after a
        # physics tick, while the USD stage bridge can publish the dynamic
        # body's transform a few app ticks later.  Sample after a short
        # settling window so a valid physical lift is not rejected merely
        # because USD has not caught up with PhysX yet.
        for _ in range(5):
            self._app.update()
        pose = self.read_object_pose(object_id)
        initial_z = float((initial_pose or {}).get("z", pose["z"]))
        threshold = max(initial_z + 0.04, float(lift_z) - 0.04)
        return {
            "verified": pose["z"] >= threshold,
            "object_pose": pose,
            "controller_cube_pose": self._controller_cube_pose(),
            "reason": "" if pose["z"] >= threshold else "OBJECT_DID_NOT_LIFT",
        }

    def verify_release(
        self,
        object_id: str,
        target_pose: dict,
        tolerance_m: float = RELEASE_TOLERANCE_M,
        *,
        settle_steps: int = 5,
    ) -> dict:
        import math

        # The pre-retreat check passes settle_steps=0 and therefore reads the
        # body immediately after opening while the TCP is still stationary.
        # The final post-retreat check keeps a short settling window so the
        # reported pose represents the physically settled cube.
        for _ in range(max(0, min(int(settle_steps), 30))):
            self._app.update()
        pose = self.read_object_pose(object_id)
        distance = math.sqrt(sum((pose[a] - float(target_pose[a])) ** 2 for a in ("x", "y", "z")))
        return {
            "verified": distance <= float(tolerance_m),
            "object_pose": pose,
            "distance_m": distance,
            "reason": "" if distance <= float(tolerance_m) else "OBJECT_NOT_AT_TARGET",
        }

    def collision_free(self, pose: dict, radius: float, excluded_paths: tuple[str, ...] = ()) -> bool:
        self._ensure_started()
        import carb
        from omni.physx import get_physx_scene_query_interface

        excluded = tuple(str(path).rstrip("/") for path in excluded_paths)
        hits: list[str] = []
        unknown_hits: list[str] = []
        ignored_ground = False
        resolved_overlap = False

        def report_hit(hit) -> bool:
            nonlocal ignored_ground, resolved_overlap
            text = _physx_hit_path(hit)
            # The broad-phase safety sphere is intentionally conservative.  A
            # grasp pose puts the sphere's lower edge slightly below z=0 even
            # though the gripper itself is above the cube.  Treat the ground
            # plane as a supporting surface when the commanded TCP is already
            # above the minimum clearance; positions below that threshold still
            # fail closed on a ground hit.
            is_ground = text == "/World/ground_plane" or text.startswith("/World/ground_plane/")
            ground_clearance = float(pose.get("z", 0.0)) >= max(0.04, float(radius) * 0.8)
            if is_ground and ground_clearance:
                ignored_ground = True
                resolved_overlap = True
                return True
            if any(text == item or text.startswith(item + "/") for item in excluded):
                resolved_overlap = True
                return True
            if not any(
                text == item or text.startswith(item + "/") for item in excluded
            ):
                if text:
                    hits.append(text)
                else:
                    unknown_hits.append(_physx_hit_debug(hit))
                    hits.append("<unknown>")
            return True

        origin = carb.Float3(float(pose["x"]), float(pose["y"]), float(pose["z"]))
        count = get_physx_scene_query_interface().overlap_sphere(float(radius), origin, report_hit, False)
        if count is None or int(count) < 0:
            raise DriverError("invalid PhysX overlap query result")
        if count > 0 and not resolved_overlap and (not hits or all(item == "<unknown>" for item in hits)):
            if hits and all(item == "<unknown>" for item in hits):
                hits.clear()
            fallback_paths, fallback_unresolved = _raycast_overlap_fallback(
                get_physx_scene_query_interface(), origin, radius, carb
            )
            for text in fallback_paths:
                is_ground = text == "/World/ground_plane" or text.startswith("/World/ground_plane/")
                ground_clearance = float(pose.get("z", 0.0)) >= max(0.04, float(radius) * 0.8)
                if is_ground and ground_clearance:
                    ignored_ground = True
                    resolved_overlap = True
                elif not any(text == item or text.startswith(item + "/") for item in excluded):
                    hits.append(text)
                    resolved_overlap = True
                else:
                    resolved_overlap = True
            if fallback_unresolved or not fallback_paths:
                detail = ";".join(unknown_hits[:2]) or "raycast fallback found no path"
                raise DriverError(f"PhysX overlap hit path unavailable ({detail})")
        if hits:
            print(
                "[collision-query] "
                f"pose={pose!r} "
                f"radius={float(radius)!r} "
                f"excluded={excluded!r} "
                f"hits={hits!r} "
                f"count={count!r} "
                f"ignored_ground={ignored_ground!r}",
                flush=True,
            )
        return not hits

    def e_stop(self) -> None:
        self._stopped = True
        try:
            import omni.timeline
            omni.timeline.get_timeline_interface().stop()
        except Exception:  # noqa: BLE001
            pass


class OmniDriver:
    """真实 Isaac Sim 6.0 运动驱动（官方 Franka + 差分 IK）。

    在 Kit 运行时内构造，由服务器入口脚本先创建 ``SimulationApp`` 再传入：

        app = SimulationApp({"headless": True})
        driver = OmniDriver(app, device="cpu")
        driver.connect()
    """

    ROBOT_PRIM_PATH = "/World/robot"
    GRIPPER_DOF_INDICES = [7, 8]
    GRIPPER_TOLERANCE_M = 0.005
    POSITION_TOLERANCE_M = 0.01
    STEP_LIMIT_M = 0.01          # 防止实验版 IK 一帧跨越目标或穿透场景
    COLLISION_RADIUS_M = 0.05
    RELEASE_TOLERANCE_M = 0.075
    IK_METHOD = "damped-least-squares"
    DEFAULT_PHYSICS_DT_S = 1.0 / 60.0

    def __init__(self, app, device: str = "cpu",
                 robot_path: str = ROBOT_PRIM_PATH) -> None:
        self._app = app
        self._device = device
        self._robot_path = robot_path
        self._franka = None
        self._connected = False
        self._started = False
        self._stopped = False
        self._physics_dt_s = self.DEFAULT_PHYSICS_DT_S
        self._last_ee_pose = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def connect(self, *, defer_start: bool = False, create_stage: bool = True) -> None:
        if self._connected:
            if not defer_start:
                self.start()
            return
        import omni.kit.app

        # 官方例子要求先启用 manipulators examples 扩展，再导入 Franka 类。
        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "isaacsim.robot.experimental.manipulators.examples", True
        )
        import omni.timeline
        import isaacsim.core.experimental.utils.stage as stage_utils
        from isaacsim.core.experimental.objects import DomeLight, GroundPlane
        from isaacsim.core.simulation_manager import PhysicsScene, SimulationManager
        from isaacsim.robot.experimental.manipulators.examples.franka.franka import Franka

        SimulationManager.set_physics_sim_device(self._device)
        self._app.update()

        # 直播 Worker 复用 streaming 应用的默认 stage（保留默认相机/渲染目标，
        # 否则 WebRTC 无帧可推、Client 黑屏）；批处理模式仍重建独立 stage。
        if create_stage:
            stage_utils.create_new_stage()
        stage = stage_utils.get_current_stage()
        # Keep the same creation order as NVIDIA's FrankaPickPlace example:
        # articulation first, then ground/light, then dynamic objects.
        self._franka = Franka(robot_path=self._robot_path, create_robot=True)
        # Match the official FrankaPickPlace scene setup: explicitly place the
        # robot base before any dynamic rigid body is created.  Leaving the
        # imported USD transform implicit can make PhysX rebuild the articulation
        # when the first dynamic object is registered, which is extremely slow on
        # the server's CPU physics path.
        self._franka.set_world_poses(
            positions=(0.0, 0.0, 0.0),
            orientations=(1.0, 0.0, 0.0, 0.0),
        )
        if not stage.GetPrimAtPath("/World/ground_plane"):
            GroundPlane("/World/ground_plane")
            DomeLight("/World/DomeLight").set_intensities(1000)
        self._connected = True

        # Use the active physics scene timestep for the Cartesian speed limit.
        try:
            scenes = PhysicsScene.get_physics_scenes()
            if scenes:
                dt = float(scenes[0].get_dt())
                if dt > 0:
                    self._physics_dt_s = dt
        except Exception:  # noqa: BLE001
            self._physics_dt_s = self.DEFAULT_PHYSICS_DT_S

        # 场景中的动态刚体必须在时间轴启动前全部创建。调用方可以用
        # ``defer_start=True`` 先补充物体，再显式调用 ``start``；这与官方
        # FrankaPickPlace.setup_scene() -> play() -> reset() 的顺序一致。
        self._started = False
        if not defer_start:
            self.start()

    def start(self) -> None:
        """启动时间轴并在完整场景创建后复位机器人。"""
        if not self._connected or self._franka is None:
            raise DriverError("OmniDriver not connected; call connect() first")
        if self._started:
            return
        import omni.timeline

        omni.timeline.get_timeline_interface().play()
        self._app.update()
        # 复位必须在 play() 之后，否则物理 tensor 未初始化。
        self._franka.reset_to_default_pose()
        for _ in range(10):
            self._app.update()
        self._started = True

    def shutdown(self) -> None:
        try:
            self.e_stop()
        finally:
            self._connected = False
            self._started = False
            self._franka = None

    def reset_for_task(self) -> None:
        """Reset the existing articulation without rebuilding Kit or Stage."""
        if not self._connected or self._franka is None:
            raise DriverError("OmniDriver not connected; call connect() first")
        import omni.timeline

        self._stopped = False
        omni.timeline.get_timeline_interface().play()
        self._app.update()
        self._franka.reset_to_default_pose()
        self._last_ee_pose = None
        for _ in range(5):
            self._app.update()
        self._started = True

    # ------------------------------------------------------------------
    # 运动原语
    # ------------------------------------------------------------------
    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict:
        """差分 IK 移动末端到目标位姿，直到收敛或超时。

        Isaac Sim 6 的实验版 Franka 控制器对远距离的完整目标有时会让
        物理步进长时间阻塞。这里沿用官方 ``set_end_effector_pose``，但把
        每帧目标限制在 ``linear_speed * physics_dt`` 以内，减少 IK 跳变并
        把执行器的速度上限真正传递给仿真。
        """
        self._ensure_connected()
        import time

        import numpy as np

        target = np.array([float(pose["x"]), float(pose["y"]), float(pose["z"])])
        # 官方示例传入一维 [x, y, z] / [w, x, y, z]；Franka 内部再统一
        # reshape 成 batch。保持原始形状可避免实验版 IK 的异常广播路径。
        orientation = np.asarray(self._franka.get_downward_orientation(), dtype=float)

        deadline = time.monotonic() + float(timeout_s)
        start_wall = time.monotonic()
        frames = 0
        trajectory: list[dict] = []
        best_distance = float("inf")
        stall_reference_distance = float("inf")
        stall_samples = 0
        last_joint_positions: list[float] = []

        # Do not read the PhysX articulation tensor before every command.  The
        # official controller issues a target, advances the app, and samples
        # state only periodically.  Reading ``get_current_state`` on every tick
        # invalidates the dynamic-body tensor cache on this server and makes the
        # next physics update block for tens of seconds.
        current = getattr(self, "_last_ee_pose", None)
        if current is None:
            current = np.asarray([0.3893041, 0.0046846, 0.4562795], dtype=float)
        else:
            current = np.asarray(current, dtype=float).copy()

        while True:
            distance = float(np.linalg.norm(target - current))
            if distance < best_distance:
                best_distance = distance
            if frames > 0 and frames % 10 == 0:
                try:
                    _, ee_pos, _ = self._franka.get_current_state()
                    current = np.asarray(ee_pos[0], dtype=float)
                    self._last_ee_pose = current.copy()
                    distance = float(np.linalg.norm(target - current))
                    if distance < best_distance:
                        best_distance = distance
                    if (
                        stall_reference_distance == float("inf")
                        or distance < stall_reference_distance - 0.002
                    ):
                        stall_reference_distance = distance
                        stall_samples = 0
                    else:
                        stall_samples += 1
                    last_joint_positions = self._joint_positions_np().tolist()
                except Exception:
                    # Keep commanding the target; the final state read below is
                    # still fail-closed if the simulator cannot provide pose.
                    pass
                trajectory.append(
                    {
                        "timestamp_ms": int((time.monotonic() - start_wall) * 1000),
                        "coordinate_frame": "world",
                        "position": {"x": current[0], "y": current[1], "z": current[2]},
                        "distance_m": distance,
                        "velocity_m_s": float(linear_speed),
                        "joint_positions": list(last_joint_positions),
                    }
                )

            if distance <= self.POSITION_TOLERANCE_M:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return _succeeded(
                    wall_ms, pose={"x": current[0], "y": current[1], "z": current[2]},
                    trajectory=trajectory,
                    wall_ms=wall_ms,
                    wall_ms_per_frame=(wall_ms / frames) if frames else 0.0,
                    velocity_m_s=float(linear_speed),
                )

            if float(linear_speed) <= 0:
                return _failed("SPEED_LIMIT_EXCEEDED", 0)
            self._franka.set_end_effector_pose(
                position=target,
                orientation=orientation,
                ik_method=self.IK_METHOD,
            )
            self._app.update()
            frames += 1

            if stall_samples >= 6:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return motion_result(
                    "FAILED", "IK_STALLED", wall_ms,
                    trajectory=trajectory,
                    joint_positions=list(last_joint_positions),
                    best_distance_m=best_distance,
                    wall_ms=wall_ms,
                    velocity_m_s=float(linear_speed),
                )

            if time.monotonic() >= deadline:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return motion_result(
                    "FAILED", "ACTION_TIMEOUT", wall_ms,
                    timed_out=True, trajectory=trajectory,
                    joint_positions=list(last_joint_positions),
                    best_distance_m=best_distance,
                    wall_ms=wall_ms,
                    wall_ms_per_frame=(wall_ms / frames) if frames else 0.0,
                    velocity_m_s=float(linear_speed),
                )

    def gripper_open(self, width: float, timeout_s: float) -> dict:
        self._ensure_connected()
        half = float(width) / 2.0
        return self._set_gripper([half, half], timeout_s)

    def gripper_close(self, force: float, timeout_s: float) -> dict:
        self._ensure_connected()
        # 关节闭合本身不能证明物体已被抓住；实际抓取由
        # ``verify_grasp`` 在抬升后根据物体真实位姿确认。
        return self._set_gripper([0.0, 0.0], timeout_s)

    def read_object_pose(self, object_id: str) -> dict:
        self._ensure_connected()
        # Read the USD world transform directly instead of constructing an
        # experimental GeomPrim tensor view.  The latter can synchronize the
        # PhysX tensor before the first control tick (and stalls this server's
        # first update for more than a minute), while USD still contains the
        # authoritative pose written by PhysX.
        from pxr import UsdGeom
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self._prim_path_for(object_id))
        if not prim or not prim.IsValid():
            raise DriverError(f"object prim not found: {object_id}")
        matrix = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        pos = matrix.ExtractTranslation()
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}

    def verify_grasp(
        self,
        object_id: str,
        initial_pose: dict | None = None,
        lift_z: float = 0.20,
    ) -> dict:
        """确认物体是否随末端抬升，避免用夹爪指令值冒充抓取力。"""
        for _ in range(5):
            self._app.update()
        pose = self.read_object_pose(object_id)
        initial_z = float((initial_pose or {}).get("z", pose["z"]))
        threshold = max(initial_z + 0.04, float(lift_z) - 0.04)
        verified = pose["z"] >= threshold
        return {
            "verified": verified,
            "object_pose": pose,
            "reason": "" if verified else "OBJECT_DID_NOT_LIFT",
        }

    def verify_release(
        self,
        object_id: str,
        target_pose: dict,
        tolerance_m: float = RELEASE_TOLERANCE_M,
    ) -> dict:
        """Confirm that the released object actually rests near the target pose.

        This is intentionally a post-release observation, not a copy of the
        planned pose.  A real Isaac run must therefore fail closed when the
        object was not transported or the pose cannot be read.
        """
        import math

        pose = self.read_object_pose(object_id)
        distance = math.sqrt(sum(
            (float(pose[axis]) - float(target_pose[axis])) ** 2
            for axis in ("x", "y", "z")
        ))
        verified = distance <= float(tolerance_m)
        return {
            "verified": verified,
            "object_pose": pose,
            "distance_m": distance,
            "reason": "" if verified else "OBJECT_NOT_AT_TARGET",
        }

    def collision_free(
        self,
        pose: dict,
        radius: float,
        excluded_paths: tuple[str, ...] = (),
    ) -> bool:
        """使用 Isaac Sim 官方 PhysX 球体重叠查询检查目标区域。

        查询异常、命中路径无法识别或场景查询 API 不可用时一律抛出
        ``DriverError``，由执行器安全门 fail-closed；不会再以 ``True`` 放行。
        """
        self._ensure_connected()
        try:
            import carb
            from omni.physx import get_physx_scene_query_interface

            hits: list[str] = []
            unknown_hits: list[str] = []
            resolved_overlap = False
            excluded = tuple(str(path).rstrip("/") for path in excluded_paths)

            def report_hit(hit) -> bool:
                nonlocal resolved_overlap
                path_text = _physx_hit_path(hit)
                if any(
                    path_text == item or path_text.startswith(item + "/")
                    for item in excluded
                ):
                    resolved_overlap = True
                    return True
                if not any(
                    path_text == item or path_text.startswith(item + "/")
                    for item in excluded
                ):
                    if path_text:
                        hits.append(path_text)
                    else:
                        unknown_hits.append(_physx_hit_debug(hit))
                        hits.append("<unknown>")
                return True

            origin = carb.Float3(float(pose["x"]), float(pose["y"]), float(pose["z"]))
            query = get_physx_scene_query_interface()
            hit_count = query.overlap_sphere(float(radius), origin, report_hit, False)
            if hit_count is None or int(hit_count) < 0:
                raise DriverError("invalid PhysX overlap query result")
            if hit_count > 0 and not resolved_overlap and (not hits or all(item == "<unknown>" for item in hits)):
                if hits and all(item == "<unknown>" for item in hits):
                    hits.clear()
                fallback_paths, fallback_unresolved = _raycast_overlap_fallback(
                    query, origin, radius, carb
                )
                for path_text in fallback_paths:
                    if not any(
                        path_text == item or path_text.startswith(item + "/")
                        for item in excluded
                    ):
                        hits.append(path_text)
                    resolved_overlap = True
                else:
                    resolved_overlap = True
                if fallback_unresolved or not fallback_paths:
                    detail = ";".join(unknown_hits[:2]) or "raycast fallback found no path"
                    raise DriverError(f"PhysX overlap hit path unavailable ({detail})")
            # The grasp TCP intentionally operates close to the support
            # surface. With the configured 5 cm safety sphere, a valid grasp
            # pose around z=4.85 cm geometrically overlaps the ground plane by
            # roughly 1.5 mm. Mirror the existing FrankaPickPlaceDriver
            # semantics: ground is ignored only when TCP clearance is still
            # above the bounded safe threshold. All other colliders remain
            # fail-closed.
            ground_clearance = (
                float(pose.get("z", 0.0))
                >= max(0.04, float(radius) * 0.8)
            )

            if ground_clearance:
                hits = [
                    item
                    for item in hits
                    if not (
                        item == "/World/ground_plane"
                        or item.startswith("/World/ground_plane/")
                    )
                ]

            if int(hit_count) > 0:
                print(
                    "[omni-collision-query] "
                    f"pose={pose!r} "
                    f"radius={float(radius)!r} "
                    f"excluded_paths={excluded_paths!r} "
                    f"hit_count={int(hit_count)!r} "
                    f"ground_clearance={ground_clearance!r} "
                    f"remaining_hits={hits!r}",
                    flush=True,
                )

            return not hits
        except DriverError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise DriverError(f"collision query failed: {exc}") from exc

    def e_stop(self) -> None:
        """急停：停止时间轴（冻结物理）并置位停止标志。"""
        self._stopped = True
        try:
            import omni.timeline

            omni.timeline.get_timeline_interface().stop()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _ensure_connected(self) -> None:
        if not self._connected:
            raise DriverError("OmniDriver not connected; call connect() first")
        if not self._started:
            raise DriverError("OmniDriver simulation not started; call start() after scene setup")
        if self._stopped:
            raise DriverError("OmniDriver is in emergency stop state")

    @staticmethod
    def _prim_path_for(object_id: str) -> str:
        return f"/World/{object_id}"

    def _joint_positions_np(self):
        import numpy as np

        joints = self._franka.get_dof_positions()
        arr = joints.numpy() if hasattr(joints, "numpy") else np.asarray(joints)
        return np.asarray(arr).reshape(-1)

    def _set_gripper(self, targets: list[float], timeout_s: float) -> dict:
        import time

        import numpy as np

        deadline = time.monotonic() + float(timeout_s)
        start_wall = time.monotonic()
        frames = 0
        while True:
            self._franka.set_gripper_position(np.asarray(targets))
            self._app.update()
            frames += 1
            fingers = self._joint_positions_np()[self.GRIPPER_DOF_INDICES]
            error = float(np.max(np.abs(fingers - np.asarray(targets))))
            if error <= self.GRIPPER_TOLERANCE_M:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return _succeeded(wall_ms, width=float(fingers.sum()))
            if time.monotonic() >= deadline:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return motion_result(
                    "FAILED", "ACTION_TIMEOUT", wall_ms,
                    timed_out=True, width=float(fingers.sum()),
                )
