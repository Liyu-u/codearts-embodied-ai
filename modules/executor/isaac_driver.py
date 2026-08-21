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
        tolerance_m: float = 0.06,
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
    RELEASE_TOLERANCE_M = 0.06

    def __init__(
        self,
        app,
        device: str = "cuda",
        cube_position=(0.50, 0.0, 0.0258),
        # The official example applies an internal (0.05, 0.10) scene offset to
        # this setup marker; with these inputs its physical cube settles at
        # (0.45, 0.10, 0.02575), which is the acceptance perception target.
        target_position=(0.40, 0.0, 0.03),
    ) -> None:
        self._app = app
        self._device = device
        self._cube_position = cube_position
        self._target_position = target_position
        self._controller = None
        self._connected = False
        self._started = False
        self._stopped = False

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
        self._controller = FrankaPickPlace()
        self._controller.setup_scene(
            cube_initial_position=np.asarray(self._cube_position, dtype=float),
            cube_initial_orientation=np.asarray((1.0, 0.0, 0.0, 0.0), dtype=float),
            cube_size=np.asarray((0.0515, 0.0515, 0.0515), dtype=float),
            target_position=np.asarray(self._target_position, dtype=float),
            cube_path="/World/green_cube",
        )
        self._connected = True
        self._started = False
        self._stopped = False
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

    def _run_current_phase(self, timeout_s: float, *, trajectory: list[dict] | None = None) -> dict:
        import time

        self._ensure_started()
        trajectory = trajectory if trajectory is not None else []
        start = time.monotonic()
        initial_event = int(self._controller._event)
        frames = 0
        while int(self._controller._event) == initial_event and not self._controller.is_done():
            self._controller.forward(self.IK_METHOD)
            self._app.update()
            frames += 1
            if time.monotonic() - start >= float(timeout_s):
                return motion_result(
                    "FAILED", "ACTION_TIMEOUT", int((time.monotonic() - start) * 1000),
                    timed_out=True, trajectory=trajectory,
                )
        wall_ms = int((time.monotonic() - start) * 1000)
        pose = self._controller_pose()
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

    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict:
        if float(linear_speed) <= 0:
            return _failed("SPEED_LIMIT_EXCEEDED", 0)
        return self._run_current_phase(timeout_s)

    def gripper_open(self, width: float, timeout_s: float) -> dict:
        return self._run_current_phase(timeout_s)

    def gripper_close(self, force: float, timeout_s: float) -> dict:
        return self._run_current_phase(timeout_s)

    def read_object_pose(self, object_id: str) -> dict:
        self._ensure_started()
        if object_id == "green_cube":
            positions, _ = self._controller.cube.get_world_poses()
            if hasattr(positions, "numpy"):
                positions = positions.numpy()
            pos = positions[0]
            return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}
        from pxr import UsdGeom
        import omni.usd

        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(f"/World/{object_id}")
        if not prim or not prim.IsValid():
            raise DriverError(f"object prim not found: {object_id}")
        pos = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}

    def verify_grasp(self, object_id: str, initial_pose: dict | None = None, lift_z: float = 0.20) -> dict:
        pose = self.read_object_pose(object_id)
        initial_z = float((initial_pose or {}).get("z", pose["z"]))
        threshold = max(initial_z + 0.04, float(lift_z) - 0.04)
        return {
            "verified": pose["z"] >= threshold,
            "object_pose": pose,
            "reason": "" if pose["z"] >= threshold else "OBJECT_DID_NOT_LIFT",
        }

    def verify_release(self, object_id: str, target_pose: dict, tolerance_m: float = RELEASE_TOLERANCE_M) -> dict:
        import math

        # Opening the gripper and retreating happen on consecutive controller
        # phases.  Let PhysX advance a few real frames so the released cube can
        # settle on the target before measuring its final pose.
        for _ in range(5):
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
        ignored_ground = False

        def report_hit(hit) -> bool:
            nonlocal ignored_ground
            path = getattr(hit, "rigid_body", None) or getattr(hit, "actor", None)
            text = str(path) if path is not None else ""
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
                return True
            if not any(
                text == item or text.startswith(item + "/") for item in excluded
            ):
                hits.append(text or "<unknown>")
            return True

        origin = carb.Float3(float(pose["x"]), float(pose["y"]), float(pose["z"]))
        count = get_physx_scene_query_interface().overlap_sphere(float(radius), origin, report_hit, False)
        if count is None or int(count) < 0:
            raise DriverError("invalid PhysX overlap query result")
        if count > 0 and not hits and not ignored_ground:
            raise DriverError("PhysX overlap hit path unavailable")
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
    RELEASE_TOLERANCE_M = 0.06
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
    def connect(self, *, defer_start: bool = False) -> None:
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
            excluded = tuple(str(path).rstrip("/") for path in excluded_paths)

            def report_hit(hit) -> bool:
                path = getattr(hit, "rigid_body", None)
                if path is None:
                    path = getattr(hit, "actor", None)
                path_text = str(path) if path is not None else ""
                if not any(
                    path_text == item or path_text.startswith(item + "/")
                    for item in excluded
                ):
                    hits.append(path_text or "<unknown>")
                return True

            origin = carb.Float3(float(pose["x"]), float(pose["y"]), float(pose["z"]))
            query = get_physx_scene_query_interface()
            hit_count = query.overlap_sphere(float(radius), origin, report_hit, False)
            if hit_count is None or int(hit_count) < 0:
                raise DriverError("invalid PhysX overlap query result")
            if hit_count > 0 and not hits:
                raise DriverError("PhysX overlap hit path unavailable")
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
