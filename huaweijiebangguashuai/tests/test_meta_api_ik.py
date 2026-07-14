"""
测试底层逆运动学求解是否正常
同学 C (昌庆)：验证 ExecWrapper 的 IK 求解和物理安全断言
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from isaac.exec_wrapper import ExecutionWrapper


class TestExecutionWrapper:
    """底层执行包装器单元测试"""

    @pytest.fixture
    def executor(self):
        return ExecutionWrapper(robot_prim_path="/World/test_robot")

    def test_safe_z_pass(self, executor):
        """安全高度以上的位姿应能通过"""
        result = executor.move_to_pose(0.1, 0.1, 0.05, 0, 0, 0)
        assert result is True

    def test_dangerous_z_assertion(self, executor):
        """低于安全高度的位姿应触发断言"""
        with pytest.raises(AssertionError) as exc_info:
            executor.move_to_pose(0.1, 0.1, 0.01, 0, 0, 0)
        assert "安全断言失败" in str(exc_info.value)

    def test_z_at_boundary(self, executor):
        """安全高度边界值应通过"""
        result = executor.move_to_pose(0.1, 0.1, executor.MIN_Z_HEIGHT, 0, 0, 0)
        assert result is True

    def test_gripper_open_range(self, executor):
        """夹爪开度应在有效范围内"""
        assert executor.open_gripper(0.08) is True

    def test_gripper_open_out_of_range(self, executor):
        """超出范围的夹爪开度应触发断言"""
        with pytest.raises(AssertionError):
            executor.open_gripper(0.2)

    def test_gripper_force_limit(self, executor):
        """超出最大力应触发断言"""
        with pytest.raises(AssertionError):
            executor.close_gripper(force=15.0)

    def test_gripper_force_valid(self, executor):
        """有效力范围内应通过"""
        result = executor.close_gripper(force=5.0)
        assert result is True

    def test_get_robot_state(self, executor):
        """应能获取模拟的机械臂状态"""
        state = executor.get_robot_state()
        assert len(state.joint_angles) == 6
        assert len(state.end_effector_pose) == 6

    def test_get_gripper_state(self, executor):
        """应能获取模拟的夹爪状态"""
        state = executor.get_gripper_state()
        assert state.width == 0.08
        assert state.force == 0.0
        assert state.is_closed is False
