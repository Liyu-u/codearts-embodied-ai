"""
Mock 单元测试 — Isaac Sim 元 API 运动控制
同学 C（吴昌庆）：写死坐标测试机械臂底层运动和安全断言

独立运行，无需等待其他模块。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from isaac.exec_wrapper import ExecutionWrapper


# ============================================================
# 测试夹具
# ============================================================
@pytest.fixture
def robot():
    """每个测试用例独立的 Franka Panda 实例"""
    return ExecutionWrapper(robot_prim_path="/World/TestFranka")


# ============================================================
# 安全断言 — Z 轴防撞（评审判定依据）
# ============================================================
class TestSafetyAssertions:
    """物理安全断言测试"""

    def test_safe_z_passes(self, robot):
        """Z >= 0.02m 应通过"""
        assert robot.move_to_pose(0.1, 0.1, 0.05, 0, 0, 0) is True

    def test_dangerous_z_under_002_raises(self, robot):
        """Z < 0.02m 应触发 AssertionError"""
        with pytest.raises(AssertionError) as exc:
            robot.move_to_pose(0.1, 0.1, 0.01, 0, 0, 0)
        assert "0.01" in str(exc.value) or "安全" in str(exc.value)

    def test_z_at_exact_boundary_passes(self, robot):
        """Z = 0.02m (等于安全线) 应通过"""
        assert robot.move_to_pose(0.1, 0.1, 0.02, 0, 0, 0) is True

    def test_z_negative_raises(self, robot):
        """Z 为负数应触发断言"""
        with pytest.raises(AssertionError):
            robot.move_to_pose(0.1, 0.1, -0.01, 0, 0, 0)


# ============================================================
# 关节运动
# ============================================================
class TestJointMotion:
    """关节空间运动测试"""

    def test_valid_7_joints_passes(self, robot):
        """合法 7 关节角应通过"""
        result = robot.move_joints([0.0, -0.5, 0.0, -1.2, 0.0, 1.0, 0.5])
        assert result is True

    def test_wrong_joint_count_raises(self, robot):
        """非 7 个关节角应报错"""
        with pytest.raises(AssertionError):
            robot.move_joints([0.0, 0.0, 0.0])  # 只有 3 个

    def test_joint_exceeds_limit_raises(self, robot):
        """关节角超出 ±2.9rad 应触发断言"""
        with pytest.raises(AssertionError):
            robot.move_joints([3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])  # 超标

    def test_joint_at_limit_passes(self, robot):
        """关节角 = 2.9rad (极限值) 应通过"""
        result = robot.move_joints([2.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        assert result is True


# ============================================================
# 夹爪控制
# ============================================================
class TestGripperControl:
    """夹爪控制测试"""

    def test_valid_open_width(self, robot):
        """合法开度应通过"""
        assert robot.open_gripper(0.05) is True

    def test_zero_width_passes(self, robot):
        """全闭合应通过"""
        assert robot.open_gripper(0.0) is True

    def test_max_width_passes(self, robot):
        """最大开度应通过"""
        assert robot.open_gripper(0.1) is True

    def test_width_exceeds_range_raises(self, robot):
        """超出范围开度应报错"""
        with pytest.raises(AssertionError):
            robot.open_gripper(0.15)

    def test_valid_force(self, robot):
        """合法夹持力应通过"""
        assert robot.close_gripper(5.0) is True

    def test_max_force_boundary(self, robot):
        """最大力 10N 应通过"""
        assert robot.close_gripper(10.0) is True

    def test_force_exceeds_max_raises(self, robot):
        """超出最大力应报错"""
        with pytest.raises(AssertionError):
            robot.close_gripper(15.0)

    def test_force_zero_raises(self, robot):
        """0 或负力应报错"""
        with pytest.raises(AssertionError):
            robot.close_gripper(0.0)


# ============================================================
# 笛卡尔直线运动
# ============================================================
class TestLinearMotion:
    """直线运动测试"""

    def test_safe_linear_move(self, robot):
        """安全直线运动应通过"""
        assert robot.move_linear(0.1, 0.0, 0.0, speed=0.05) is True

    def test_downward_through_safety_line(self, robot):
        """直线下降穿透安全面应报错"""
        # 当前 Z=0.35 (归位高度), 下降 0.34 = 到 0.01 < 0.02
        with pytest.raises(AssertionError):
            robot.move_linear(0.0, 0.0, -0.34, speed=0.05)

    def test_speed_exceeds_max_raises(self, robot):
        """速度超限应报错"""
        with pytest.raises(AssertionError):
            robot.move_linear(0.1, 0.0, 0.0, speed=0.5)


# ============================================================
# 场景感知
# ============================================================
class TestScenePerception:
    """场景感知测试"""

    def test_get_scene_objects_returns_list(self):
        """应返回物体列表"""
        from isaac.get_scene_json import get_scene_objects
        objects = get_scene_objects()
        assert isinstance(objects, list)
        assert len(objects) >= 3, f"Mock 场景应至少有 3 个物体，实际: {len(objects)}"

    def test_each_object_has_required_fields(self):
        """每个物体应有必要字段"""
        from isaac.get_scene_json import get_scene_objects
        for obj in get_scene_objects():
            assert obj.name, f"物体缺少名称"
            assert len(obj.position) == 3, f"{obj.name} 坐标应为 (x, y, z)"
            assert len(obj.bbox) == 3, f"{obj.name} BBox 应为 (w, h, d)"

    def test_coordinates_are_plausible(self):
        """坐标应在合理范围 (桌面区域约 ±0.5m)"""
        from isaac.get_scene_json import get_scene_objects
        for obj in get_scene_objects():
            x, y, z = obj.position
            assert -0.5 <= x <= 0.5, f"{obj.name}: x={x} 超出桌面范围"
            assert -0.5 <= y <= 0.5, f"{obj.name}: y={y} 超出桌面范围"
            assert z >= 0.0, f"{obj.name}: z={z} 应在桌面上方"
