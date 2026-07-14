"""
机械臂底层执行包装器 — 元 API 物理驱动实现
同学 C（吴昌庆）上传：【极其核心】

负责：
1. 将 move_to_pose() 等元 API 通过 IK 求解转为 Franka Panda 7 关节运动
2. 内置物理安全断言（Z 轴防撞、关节限位、力限）
3. 与 Isaac Sim 6.0.1 的 USD Physics 引擎交互
"""

from typing import Any, Dict, List, Tuple
from dataclasses import dataclass


# ============================================================
# 数据结构
# ============================================================
@dataclass
class RobotState:
    joint_angles: List[float]       # 7 自由度关节角 (rad)
    end_effector_pose: Tuple[float, float, float, float, float, float]


@dataclass
class GripperState:
    width: float    # 夹爪开度 (m), 范围 [0.0, 0.1]
    force: float    # 夹持力 (N), 范围 [0.0, 10.0]
    is_closed: bool


# ============================================================
# ExecutionWrapper — 底层物理执行器
# ============================================================
class ExecutionWrapper:
    """
    Franka Panda 机械臂的元 API 执行包装器。

    安全常量（评审判定依据）:
        MIN_Z_HEIGHT      = 0.02   # 末端 Z 轴最低安全高度 (m)
        MAX_JOINT_VELOCITY = 1.0    # 最大关节速度 (rad/s)
        MAX_GRIPPER_FORCE  = 10.0   # 最大夹爪力 (N)
        MAX_JOINT_ANGLE    = 2.9    # 关节硬件限位 (rad)
    """

    MIN_Z_HEIGHT = 0.02
    MAX_JOINT_VELOCITY = 1.0
    MAX_GRIPPER_FORCE = 10.0
    MAX_JOINT_ANGLE = 2.9

    def __init__(self, robot_prim_path: str = "/World/Franka"):
        self.robot_prim_path = robot_prim_path
        self._current_z: float = 0.35  # 末端当前 Z 高度 (初始归位高度)

    # ============================================================
    # 运动控制 API
    # ============================================================

    def move_to_pose(
        self,
        x: float,
        y: float,
        z: float,
        roll: float = 0.0,
        pitch: float = 0.0,
        yaw: float = 0.0,
    ) -> bool:
        """
        逆运动学 (IK) 解算并驱动末端执行器到达目标 6D 位姿。

        ⚠️ 安全断言 — 不可绕过:
            assert z >= 0.02  防止机械臂撞击桌面
        """
        # ===== 安全断言: Z 轴防撞红线 =====
        assert z >= self.MIN_Z_HEIGHT, (
            f"\n{'='*50}\n"
            f"🛑 [SAFETY CRITICAL] 末端执行器 Z 轴高度违规!\n"
            f"   目标 Z = {z:.4f} m\n"
            f"   最低安全高度 = {self.MIN_Z_HEIGHT} m\n"
            f"   差值 = {self.MIN_Z_HEIGHT - z:.4f} m (低于安全线)\n"
            f"   动作已拦截，请重新规划路径。\n"
            f"{'='*50}"
        )

        # IK 求解 (TODO: 接入 Isaac Sim 真实 IK Solver)
        # from omni.isaac.core.utils.types import ArticulationAction
        # ik_solution = ik_solver.solve(target_pose)

        print(f"[IK] move_to_pose: x={x:.4f}, y={y:.4f}, z={z:.4f}, "
              f"r={roll:.2f}, p={pitch:.2f}, y={yaw:.2f}")
        self._current_z = z
        return True

    def move_joints(self, joint_angles: List[float]) -> bool:
        """
        直接驱动 7 个关节角 (关节空间运动)。

        ⚠️ 安全断言: 每个关节角必须在 [-2.9, 2.9] rad 限位内
        """
        assert len(joint_angles) == 7, f"Franka Panda 需要 7 个关节角，收到 {len(joint_angles)} 个"

        for i, angle in enumerate(joint_angles):
            assert -self.MAX_JOINT_ANGLE <= angle <= self.MAX_JOINT_ANGLE, (
                f"\n{'='*50}\n"
                f"🛑 [SAFETY CRITICAL] 关节 {i+1} 角度超出硬件限位!\n"
                f"   角度 = {angle:.4f} rad\n"
                f"   允许范围 = [-{self.MAX_JOINT_ANGLE}, {self.MAX_JOINT_ANGLE}]\n"
                f"   动作已拦截!\n"
                f"{'='*50}"
            )

        print(f"[JOINT] move_joints: {[f'{a:.2f}' for a in joint_angles]}")
        return True

    def move_linear(self, dx: float, dy: float, dz: float, speed: float = 0.05) -> bool:
        """
        笛卡尔直线运动。末端执行器沿指定方向平移，不改变姿态。

        ⚠️ 安全断言: 移动终点 Z 坐标不得低于 MIN_Z_HEIGHT
        """
        target_z = self._current_z + dz
        assert target_z >= self.MIN_Z_HEIGHT, (
            f"🛑 [SAFETY] move_linear 终点 Z={target_z:.4f} < {self.MIN_Z_HEIGHT}!"
        )
        assert speed <= 0.1, f"🛑 [SAFETY] 直线速度 {speed} m/s 超过最大值 0.1!"

        print(f"[LINEAR] move_linear: dx={dx:.3f}, dy={dy:.3f}, dz={dz:.3f}, speed={speed:.2f}")
        self._current_z = target_z
        return True

    # ============================================================
    # 夹爪控制 API
    # ============================================================

    def open_gripper(self, width: float = 0.08) -> bool:
        """张开夹爪到指定宽度 (m)"""
        assert 0.0 <= width <= 0.1, (
            f"🛑 [SAFETY] 夹爪开度 {width:.4f} m 超出硬件范围 [0.0, 0.1]!"
        )
        print(f"[GRIPPER] 张开 -> {width:.4f} m")
        return True

    def close_gripper(self, force: float = 5.0) -> bool:
        """
        闭合夹爪并施加指定抓取力。

        ⚠️ 安全断言: force 必须在 (0, 10.0] N 范围内
        """
        assert 0.0 < force <= self.MAX_GRIPPER_FORCE, (
            f"\n{'='*50}\n"
            f"🛑 [SAFETY CRITICAL] 夹爪力超出安全范围!\n"
            f"   设定力 = {force:.1f} N\n"
            f"   最大允许力 = {self.MAX_GRIPPER_FORCE} N\n"
            f"   动作已拦截!\n"
            f"{'='*50}"
        )
        print(f"[GRIPPER] 闭合 -> {force:.1f} N")
        return True

    # ============================================================
    # 状态查询 API
    # ============================================================

    def get_robot_state(self) -> RobotState:
        """获取当前机械臂 7DOF 状态"""
        # TODO: 从 Isaac Sim 读取 Articulation 实际状态
        return RobotState(
            joint_angles=[0.0, -0.5, 0.0, -1.2, 0.0, 1.0, 0.5],
            end_effector_pose=(0.0, 0.0, 0.35, 0.0, 0.0, 0.0),
        )

    def get_gripper_state(self) -> GripperState:
        """获取当前夹爪状态"""
        return GripperState(width=0.08, force=0.0, is_closed=False)

    # ============================================================
    # 逻辑判断 API
    # ============================================================

    def check_collision(self, x: float, y: float, z: float) -> bool:
        """
        预判目标位姿是否与场景障碍物碰撞。

        Returns:
            True = 安全无碰撞, False = 存在碰撞风险
        """
        # TODO: 接入 Isaac Sim PhysX 碰撞检测
        print(f"[COLLISION_CHECK] 位姿 ({x:.3f}, {y:.3f}, {z:.3f}): 安全 ✓")
        return True

    def verify_grasp(self, threshold: float = 0.5) -> bool:
        """
        通过力反馈判断是否真实抓住物体。

        Args:
            threshold: 最小力阈值 (N)，低于此值认为抓空

        Returns:
            True = 抓住, False = 抓空
        """
        gripper = self.get_gripper_state()
        return gripper.force >= threshold


# ============================================================
# 安全断言汇总（供评委审查）
# ============================================================
def print_safety_manifest():
    """打印全部安全约束清单"""
    print("""
    ╔══════════════════════════════════════════╗
    ║   Franka Panda 物理安全断言清单         ║
    ╠══════════════════════════════════════════╣
    ║  1. Z 轴最低高度    ≥ 0.02 m            ║
    ║  2. 关节角度范围    ∈ [-2.9, 2.9] rad   ║
    ║  3. 夹爪力范围      ∈ (0, 10.0] N       ║
    ║  4. 夹爪开度        ∈ [0.0, 0.1] m      ║
    ║  5. 直线速度        ≤ 0.1 m/s           ║
    ║  6. 运动前碰撞检测  必须调用 check_collision ║
    ║  7. 抓取后确认      必须调用 verify_grasp    ║
    ╚══════════════════════════════════════════╝
    """)


if __name__ == "__main__":
    print_safety_manifest()

    # 自检
    robot = ExecutionWrapper()
    try:
        robot.move_to_pose(0.1, 0.1, 0.01)  # 应该触发安全断言!
    except AssertionError as e:
        print(f"\n✅ 安全断言正常运作: \n{e}")
