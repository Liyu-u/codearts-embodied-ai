"""Isaac Sim 运动驱动抽象层。

``MotionDriver`` 是执行后端与仿真/真机之间的唯一接口。后端只依赖这个 Protocol，
不在模块顶层导入 ``isaacsim`` / ``omni``，因此可以在 `huawei` 环境与 CI 中用
假驱动（FakeDriver）做单元测试。

``OmniDriver`` 是真实 Isaac Sim 6.0 实现。所有 ``isaacsim.*`` / ``omni.*`` 导入都
延迟到方法内部执行，保证本模块在未安装 Isaac Sim 的机器上仍可被正常 import。

注意：``OmniDriver`` 中涉及具体控制器/IK/PhysX 的调用点，必须在 `isaacsim` 环境
中做一次最小实机冒烟验证（见 modules/executor/README.md 的“实机验证清单”）。
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
    """真实 Isaac Sim 6.0 运动驱动（待实机验证）。

    在 Kit 运行时内构造，通常由离线批处理入口脚本先创建 ``World`` 再传入：

        world = World(sim_context=..., physics_dt=1.0 / 60.0, rendering_dt=1.0 / 60.0)
        driver = OmniDriver(world=world, robot_prim_path="/World/Franka")
    """

    FRANKA_USD_PATH = "Isaac/Robots/Franka/franka.usd"
    POSITION_TOLERANCE_M = 0.005
    GRIPPER_TOLERANCE_M = 0.005
    COLLISION_RADIUS_M = 0.05
    HOME_JOINTS = [0.0, -0.5, 0.0, -1.2, 0.0, 1.0, 0.5]

    def __init__(
        self,
        world=None,
        robot_prim_path: str = "/World/Franka",
        gripper_prim_path: str = "/World/Franka/panda_hand",
    ) -> None:
        self._world = world
        self._robot_prim_path = robot_prim_path
        self._gripper_prim_path = gripper_prim_path
        self._robot = None
        self._gripper = None
        self._connected = False
        self._stopped = False
        self._elapsed_ms = 0

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self._connected:
            return
        if self._world is None:
            raise DriverError(
                "OmniDriver requires a World instance; construct it in the Kit "
                "entry script and pass it in."
            )
        # 延迟导入：只有真正进入 Kit 运行时才会触碰 isaacsim。
        try:
            from isaacsim.core.utils.stage import get_current_stage
            from isaacsim.core.experimental.prims import Articulation
            from isaacsim.storage.native import get_assets_root_path
        except ImportError as exc:  # pragma: no cover - 依赖真实环境
            raise DriverError(f"isaacsim runtime unavailable: {exc}") from exc

        stage = get_current_stage()
        assets_root = get_assets_root_path()
        franka_usd = f"{assets_root}/{self.FRANKA_USD_PATH}"
        prim = stage.DefinePrim(self._robot_prim_path, "Xform")
        prim.GetReferences().AddReference(franka_usd)
        if self._world is not None:
            self._world.step(render=False)

        self._robot = Articulation(prim_path=self._robot_prim_path)
        self._robot.initialize()
        self._gripper = Articulation(prim_path=self._gripper_prim_path)
        self._gripper.initialize()
        self._connected = True

    def shutdown(self) -> None:
        try:
            self.e_stop()
        finally:
            self._connected = False
            self._robot = None
            self._gripper = None

    # ------------------------------------------------------------------
    # 运动原语
    # ------------------------------------------------------------------
    def move_to(self, pose: dict, linear_speed: float, timeout_s: float) -> dict:
        self._ensure_connected()
        from time import monotonic

        from isaacsim.core.utils.types import ArticulationAction

        position = (float(pose["x"]), float(pose["y"]), float(pose["z"]))
        orientation = pose.get("orientation") or (0.0, 0.0, 0.0)
        if isinstance(orientation, dict):
            orientation = (
                float(orientation.get("x", 0.0)),
                float(orientation.get("y", 0.0)),
                float(orientation.get("z", 0.0)),
            )
        else:
            orientation = tuple(float(v) for v in orientation[:3])

        # IK 求解：end_effector.set_world_pose 返回 (target_joints, ik_success)。
        target_joints, ik_success = self._robot.end_effector.set_world_pose(
            position=position,
            orientation=orientation,
        )
        if not ik_success or target_joints is None or len(target_joints) == 0:
            return _failed("IK_UNREACHABLE")

        deadline = monotonic() + float(timeout_s)
        start_ms = self._elapsed_ms
        trajectory = [self._current_eef_point()]
        # 收敛循环：持续下发关节目标并回读，直到收敛或超时。
        while True:
            self._robot.apply_action(
                ArticulationAction(
                    joint_positions=target_joints,
                    joint_velocities=None,
                )
            )
            if self._world is not None:
                self._world.step(render=False)
            current = self._read_eef_position()
            trajectory.append(
                {"timestamp_ms": self._elapsed_ms, "position": current}
            )
            if self._distance(current, position) <= self.POSITION_TOLERANCE_M:
                duration_ms = self._elapsed_ms - start_ms
                return _succeeded(
                    duration_ms,
                    pose={"x": current[0], "y": current[1], "z": current[2]},
                    trajectory=trajectory,
                )
            if monotonic() >= deadline:
                duration_ms = self._elapsed_ms - start_ms
                return motion_result(
                    "FAILED",
                    "ACTION_TIMEOUT",
                    duration_ms,
                    timed_out=True,
                    trajectory=trajectory,
                )

    def gripper_open(self, width: float, timeout_s: float) -> dict:
        self._ensure_connected()
        from time import monotonic

        from isaacsim.core.utils.types import ArticulationAction

        deadline = monotonic() + float(timeout_s)
        start_ms = self._elapsed_ms
        while True:
            self._gripper.apply_action(
                ArticulationAction(joint_positions=[width / 2, width / 2])
            )
            if self._world is not None:
                self._world.step(render=False)
            actual = self._read_gripper_width()
            if abs(actual - width) <= self.GRIPPER_TOLERANCE_M:
                return _succeeded(
                    self._elapsed_ms - start_ms,
                    width=actual,
                )
            if monotonic() >= deadline:
                return motion_result(
                    "FAILED",
                    "ACTION_TIMEOUT",
                    self._elapsed_ms - start_ms,
                    timed_out=True,
                )

    def gripper_close(self, force: float, timeout_s: float) -> dict:
        self._ensure_connected()
        from time import monotonic

        from isaacsim.core.utils.types import ArticulationAction

        deadline = monotonic() + float(timeout_s)
        start_ms = self._elapsed_ms
        while True:
            self._gripper.apply_action(
                ArticulationAction(
                    joint_positions=[0.0, 0.0],
                    joint_efforts=[float(force), float(force)],
                )
            )
            if self._world is not None:
                self._world.step(render=False)
            width = self._read_gripper_width()
            effort = self._read_gripper_force()
            if width < self.GRIPPER_TOLERANCE_M and effort > 0.0:
                return _succeeded(
                    self._elapsed_ms - start_ms,
                    width=width,
                    grasp_force_n=effort,
                )
            if monotonic() >= deadline:
                return motion_result(
                    "FAILED",
                    "ACTION_TIMEOUT",
                    self._elapsed_ms - start_ms,
                    timed_out=True,
                    width=width,
                    grasp_force_n=effort,
                )

    def read_object_pose(self, object_id: str) -> dict:
        self._ensure_connected()
        try:
            from isaacsim.core.experimental.prims import XFormPrim
        except ImportError as exc:  # pragma: no cover
            raise DriverError(f"isaacsim runtime unavailable: {exc}") from exc
        prim_path = self._prim_path_for(object_id)
        xform = XFormPrim(prim_path=str(prim_path))
        world_pos, _ = xform.get_world_pose()
        return {
            "x": float(world_pos[0]),
            "y": float(world_pos[1]),
            "z": float(world_pos[2]),
        }

    def collision_free(self, pose: dict, radius: float) -> bool:
        """查询目标位姿附近是否无碰撞。查询本身失败时抛 ``DriverError``（fail-closed）。"""
        self._ensure_connected()
        try:
            from omni.physx import get_physx_interface
        except ImportError as exc:  # pragma: no cover
            raise DriverError(f"omni.physx unavailable: {exc}") from exc
        try:
            physx = get_physx_interface()
            overlapping = physx.overlap_sphere(
                float(radius),
                (float(pose["x"]), float(pose["y"]), float(pose["z"])),
            )
        except Exception as exc:
            # 关键安全修复：查询异常不再“假设安全”，而是向上抛出 fail-closed。
            raise DriverError(f"PhysX overlap query failed: {exc}") from exc

        robot_prefix = self._robot_prim_path
        for prim_path in overlapping:
            sp = str(prim_path)
            if robot_prefix in sp:
                continue
            if "GroundPlane" in sp or "defaultGroundPlane" in sp:
                continue
            return False  # 发现与场景物体的碰撞风险
        return True

    def e_stop(self) -> None:
        """急停：立即停止电机。"""
        self._stopped = True
        if self._robot is None:
            return
        try:
            from isaacsim.core.utils.types import ArticulationAction
        except ImportError:  # pragma: no cover
            return
        # 零速度指令，停止运动；关节位置保持当前值。
        self._robot.apply_action(
            ArticulationAction(
                joint_positions=self._robot.get_joint_positions(),
                joint_velocities=[0.0] * 7,
            )
        )

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
        # 统一仓库约定：稳定 object_id 同时用作场景 prim 名称。
        return f"/World/{object_id}"

    def _read_eef_position(self):
        pos, _ = self._robot.end_effector.get_world_pose()
        return (float(pos[0]), float(pos[1]), float(pos[2]))

    def _read_gripper_width(self) -> float:
        joints = self._gripper.get_joint_positions()
        if len(joints) >= 2:
            return float(joints[0]) + float(joints[1])
        return float(joints[0]) * 2 if len(joints) == 1 else 0.0

    def _read_gripper_force(self) -> float:
        efforts = self._gripper.get_applied_joint_efforts()
        return float(max(efforts)) if efforts else 0.0

    def _current_eef_point(self) -> dict:
        pos = self._read_eef_position()
        return {
            "timestamp_ms": self._elapsed_ms,
            "coordinate_frame": "world",
            "position": {"x": pos[0], "y": pos[1], "z": pos[2]},
        }

    @staticmethod
    def _distance(a, b) -> float:
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
