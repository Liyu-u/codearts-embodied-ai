"""Isaac Sim 运动驱动抽象层。

``MotionDriver`` 是执行后端与仿真/真机之间的唯一接口。后端只依赖这个 Protocol，
不在模块顶层导入 ``isaacsim`` / ``omni``，因此可以在 `huawei` 环境与 CI 中用
假驱动（FakeDriver）做单元测试。

``OmniDriver`` 是真实 Isaac Sim 6.0 实现，严格对照官方 standalone examples：

- 机器人：``isaacsim.robot.experimental.manipulators.examples.franka.franka.Franka``
  （继承 Articulation，内置差分 IK 与夹爪控制）；
- 笛卡尔运动：差分 IK（``damped-least-squares``），每帧调用
  ``set_end_effector_pose`` 再 ``app.update``；
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

    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict: ...

    def gripper_open(self, width: float, timeout_s: float) -> dict: ...

    def gripper_close(self, force: float, timeout_s: float) -> dict: ...

    def read_object_pose(self, object_id: str) -> dict: ...

    def collision_free(self, pose: dict, radius: float) -> bool: ...

    def e_stop(self) -> None: ...

    def shutdown(self) -> None: ...


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
    STEP_LIMIT_M = 0.02          # 每帧最多朝目标移动 2cm，避免差分 IK 冲过头
    COLLISION_RADIUS_M = 0.05
    IK_METHOD = "damped-least-squares"

    def __init__(self, app, device: str = "cpu",
                 robot_path: str = ROBOT_PRIM_PATH) -> None:
        self._app = app
        self._device = device
        self._robot_path = robot_path
        self._franka = None
        self._connected = False
        self._stopped = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self._connected:
            return
        import omni.kit.app

        # 官方例子要求先启用 manipulators examples 扩展，再导入 Franka 类。
        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "isaacsim.robot.experimental.manipulators.examples", True
        )
        import omni.timeline
        import isaacsim.core.experimental.utils.stage as stage_utils
        from isaacsim.core.experimental.objects import DomeLight, GroundPlane
        from isaacsim.core.simulation_manager import SimulationManager
        from isaacsim.robot.experimental.manipulators.examples.franka.franka import Franka

        SimulationManager.set_physics_sim_device(self._device)
        self._app.update()

        stage_utils.create_new_stage()
        stage = stage_utils.get_current_stage()
        if not stage.GetPrimAtPath("/World/ground_plane"):
            GroundPlane("/World/ground_plane")
            DomeLight("/World/DomeLight").set_intensities(1000)

        self._franka = Franka(robot_path=self._robot_path, create_robot=True)
        self._connected = True

        omni.timeline.get_timeline_interface().play()
        self._app.update()

        # 复位到已知 home 位姿（夹爪张开）。必须在 play() 之后，否则物理 tensor 未初始化。
        self._franka.reset_to_default_pose()
        for _ in range(10):
            self._app.update()

    def shutdown(self) -> None:
        try:
            self.e_stop()
        finally:
            self._connected = False
            self._franka = None

    # ------------------------------------------------------------------
    # 运动原语
    # ------------------------------------------------------------------
    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict:
        """差分 IK 移动末端到目标位姿，直到收敛或超时。

        每帧调用 ``set_end_effector_pose``（差分 IK 解算关节增量）再 ``app.update``，
        然后回读末端位置判断是否到达。``linear_speed`` 仅作后端限速语义参考。
        """
        self._ensure_connected()
        import time

        import numpy as np

        target = np.array([float(pose["x"]), float(pose["y"]), float(pose["z"])])
        orientation = np.array([self._franka.get_downward_orientation()])

        deadline = time.monotonic() + float(timeout_s)
        start_wall = time.monotonic()
        frames = 0
        trajectory: list[dict] = []
        best_distance = float("inf")
        stall_frames = 0

        while True:
            _, ee_pos, _ = self._franka.get_current_state()
            current = np.asarray(ee_pos[0], dtype=float)
            direction = target - current
            distance = float(np.linalg.norm(direction))
            best_distance = min(best_distance, distance)
            trajectory.append(
                {
                    "timestamp_ms": int((time.monotonic() - start_wall) * 1000),
                    "coordinate_frame": "world",
                    "position": {"x": current[0], "y": current[1], "z": current[2]},
                    "distance_m": distance,
                    "joint_positions": self._joint_positions_np().tolist(),
                }
            )

            if distance <= self.POSITION_TOLERANCE_M:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return _succeeded(
                    wall_ms, pose={"x": current[0], "y": current[1], "z": current[2]},
                    trajectory=trajectory,
                    wall_ms=wall_ms,
                    wall_ms_per_frame=(wall_ms / frames) if frames else 0.0,
                )

            # 每帧最多朝目标移动 STEP_LIMIT_M，避免全步长 IK 冲过头。
            step = min(distance, self.STEP_LIMIT_M)
            step_target = current + (direction / max(distance, 1e-9)) * step
            self._franka.set_end_effector_pose(
                position=step_target.reshape(1, -1),
                orientation=orientation,
                ik_method=self.IK_METHOD,
            )
            self._app.update()
            frames += 1

            # 卡死检测：连续 60 帧距离未改善（>2mm）。
            if distance < best_distance - 0.002:
                stall_frames = 0
            else:
                stall_frames += 1
            if stall_frames >= 60:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return motion_result(
                    "FAILED", "IK_STALLED", wall_ms,
                    trajectory=trajectory,
                    joint_positions=self._joint_positions_np().tolist(),
                    best_distance_m=best_distance,
                    wall_ms=wall_ms,
                )

            if time.monotonic() >= deadline:
                wall_ms = int((time.monotonic() - start_wall) * 1000)
                return motion_result(
                    "FAILED", "ACTION_TIMEOUT", wall_ms,
                    timed_out=True, trajectory=trajectory,
                    joint_positions=self._joint_positions_np().tolist(),
                    best_distance_m=best_distance,
                    wall_ms=wall_ms,
                    wall_ms_per_frame=(wall_ms / frames),
                )

    def gripper_open(self, width: float, timeout_s: float) -> dict:
        self._ensure_connected()
        half = float(width) / 2.0
        return self._set_gripper([half, half], timeout_s)

    def gripper_close(self, force: float, timeout_s: float) -> dict:
        self._ensure_connected()
        result = self._set_gripper([0.0, 0.0], timeout_s)
        # 夹持力尚未接入真实力传感器，暂以“是否完全闭合”作为抓取成功代理。
        result["grasp_force_n"] = float(force) if result["status"] == "SUCCESS" else 0.0
        return result

    def read_object_pose(self, object_id: str) -> dict:
        self._ensure_connected()
        from isaacsim.core.experimental.prims import GeomPrim

        geom = GeomPrim(self._prim_path_for(object_id))
        positions, _ = geom.get_world_poses()
        pos = positions[0]
        return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}

    def collision_free(self, pose: dict, radius: float) -> bool:
        """查询目标位姿附近是否无碰撞。查询本身失败时抛 ``DriverError``（fail-closed）。"""
        self._ensure_connected()
        from omni.physx import get_physx_interface

        try:
            physx = get_physx_interface()
            overlapping = physx.overlap_sphere(
                float(radius),
                (float(pose["x"]), float(pose["y"]), float(pose["z"])),
            )
        except Exception as exc:
            raise DriverError(f"PhysX overlap query failed: {exc}") from exc

        for prim_path in overlapping:
            sp = str(prim_path)
            if self._robot_path in sp:
                continue
            if "GroundPlane" in sp or "ground_plane" in sp:
                continue
            return False
        return True

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
