"""
机械臂底层执行包装器
同学 C (昌庆)：封装 Isaac Sim 机械臂控制指令，含安全物理断言
"""

from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass


@dataclass
class RobotState:
    """机械臂状态"""
    joint_angles: List[float]
    end_effector_pose: Tuple[float, float, float, float, float, float]


@dataclass
class GripperState:
    """夹爪状态"""
    width: float
    force: float
    is_closed: bool


class ExecutionWrapper:
    """机械臂底层执行包装器"""

    MIN_Z_HEIGHT = 0.02  # 安全高度阈值 (m)
    MAX_JOINT_VELOCITY = 1.0  # 最大关节速度 (rad/s)
    MAX_GRIPPER_FORCE = 10.0  # 最大夹爪力 (N)

    def __init__(self, robot_prim_path: str = "/World/robot"):
        self.robot_prim_path = robot_prim_path
        self._last_z: float = 0.0

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
        逆运动学解算并将末端执行器移动到目标位姿
        安全约束：z >= MIN_Z_HEIGHT 必须先满足
        """
        assert z >= self.MIN_Z_HEIGHT, (
            f"安全断言失败: 目标 Z={z:.4f} < 最低安全高度 {self.MIN_Z_HEIGHT}"
        )

        print(f"[IK] 移动到: x={x:.4f} y={y:.4f} z={z:.4f}")
        # TODO: 调用 Isaac Sim IK 求解器
        self._last_z = z
        return True

    def move_joints(self, joint_angles: List[float]) -> bool:
        """直接关节空间运动"""
        print(f"[JOINT] 目标关节角度: {joint_angles}")
        # TODO: 调用 Isaac Sim 关节控制器
        return True

    def open_gripper(self, width: float = 0.08) -> bool:
        """张开夹爪到指定宽度"""
        assert 0.0 <= width <= 0.1, "夹爪开度范围 0.0 ~ 0.1 m"
        print(f"[GRIPPER] 张开到 {width:.4f} m")
        return True

    def close_gripper(self, force: float = 5.0) -> bool:
        """闭合夹爪，施加指定力"""
        assert 0.0 < force <= self.MAX_GRIPPER_FORCE, (
            f"夹爪力 {force}N 超出最大值 {self.MAX_GRIPPER_FORCE}N"
        )
        print(f"[GRIPPER] 闭合，力={force:.1f} N")
        return True

    def get_robot_state(self) -> RobotState:
        """获取当前机械臂状态"""
        # TODO: 从 Isaac Sim 读取实际状态
        return RobotState(
            joint_angles=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            end_effector_pose=(0.0, 0.0, 0.3, 0.0, 0.0, 0.0),
        )

    def get_gripper_state(self) -> GripperState:
        """获取当前夹爪状态"""
        return GripperState(width=0.08, force=0.0, is_closed=False)
