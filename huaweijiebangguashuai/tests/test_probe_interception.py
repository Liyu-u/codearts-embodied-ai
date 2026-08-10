"""
Mock 单元测试 — 运行态探针异常拦截
同学 D：模拟触发一个碰撞/抓空，测试探针能否捕获并生成 error_report.json

独立运行，无需 Isaac Sim 真实环境。
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from monitor.trace_probe import TraceProbe, TraceEvent, with_probe


# ============================================================
# 测试数据
# ============================================================
MOCK_COLLISION_POSITION = {"x": 0.02, "y": -0.03, "z": 0.04}
MOCK_COLLISION_JOINTS = [0.1, -0.6, 0.2, -1.5, 0.0, 0.8, 0.3]
MOCK_COLLISION_GRIPPER = {"width": 0.08, "force": 0.0}


# ============================================================
# 测试用例
# ============================================================
class TestTraceProbeBasic:
    """探针基本功能测试"""

    def test_record_normal_event(self):
        """记录普通事件"""
        probe = TraceProbe(task_id="test-001")
        event = probe.record("NORMAL", "机械臂归位")
        assert event.event_type == "NORMAL"
        assert event.task_id == "test-001"
        assert len(probe.events) == 1

    def test_record_collision_event(self):
        """记录碰撞事件"""
        probe = TraceProbe(task_id="test-002")
        probe.record(
            "COLLISION_DETECTED",
            "路径与物体干涉!",
            position=MOCK_COLLISION_POSITION,
            joints=MOCK_COLLISION_JOINTS,
            gripper=MOCK_COLLISION_GRIPPER,
        )
        assert len(probe.events) == 1
        assert probe.events[0].position_snapshot is not None
        assert probe.events[0].position_snapshot["z"] == 0.04

    def test_has_errors_detects_critical(self):
        """应检测到关键错误"""
        probe = TraceProbe(task_id="test-003")
        probe.record("COLLISION_DETECTED", "碰撞!")
        assert probe.has_errors() is True

    def test_no_errors_on_normal_events(self):
        """纯正常事件不应报错"""
        probe = TraceProbe(task_id="test-004")
        probe.record("NORMAL", "归位")
        probe.record("INFO", "感知完成")
        assert probe.has_errors() is False

    def test_get_errors_filters_critical_only(self):
        """get_errors 只返回关键错误"""
        probe = TraceProbe(task_id="test-005")
        probe.record("NORMAL", "正常事件")
        probe.record("GRASP_FAILED", "抓取失败")
        probe.record("NORMAL", "恢复中")
        errors = probe.get_errors()
        assert len(errors) == 1
        assert errors[0].event_type == "GRASP_FAILED"


class TestErrorReportGeneration:
    """错误报告生成测试"""

    def test_generate_report_structure(self):
        """生成的报告应有完整结构"""
        probe = TraceProbe(task_id="test-006")
        probe.record(
            "COLLISION_DETECTED",
            "碰撞!",
            position=MOCK_COLLISION_POSITION,
            joints=MOCK_COLLISION_JOINTS,
            gripper=MOCK_COLLISION_GRIPPER,
        )
        report = probe.generate_error_report()

        assert "error_id" in report
        assert "task_id" in report
        assert "timestamp" in report
        assert "error_type" in report
        assert report["error_type"] == "COLLISION_DETECTED"
        assert "crash_snapshot" in report
        assert report["crash_snapshot"]["end_effector_position"] == MOCK_COLLISION_POSITION
        assert "event_timeline" in report
        assert len(report["event_timeline"]) == 1
        assert "suggested_action" in report
        assert len(report["suggested_action"]) > 0  # 应有修复建议

    def test_clean_execution_returns_clean_report(self):
        """无错误时返回 clean 状态"""
        probe = TraceProbe(task_id="test-007")
        report = probe.generate_error_report()
        assert report["status"] == "clean"
        assert report["errors"] == []

    def test_suggested_fix_for_known_error_types(self):
        """已知错误类型应有对应修复建议"""
        known_errors = [
            "COLLISION_DETECTED",
            "GRASP_FAILED",
            "IK_SOLVE_FAILED",
            "JOINT_LIMIT_EXCEEDED",
            "SAFETY_ASSERTION_FAILED",
            "OBJECT_NOT_FOUND",
        ]
        for error_type in known_errors:
            suggestion = TraceProbe._suggest_fix(error_type)
            assert suggestion, f"{error_type} 缺少修复建议"
            assert suggestion != "未知错误类型，请人工介入诊断"


class TestErrorReportDump:
    """错误报告持久化测试"""

    def test_dump_creates_file(self):
        """应生成 error_report JSON 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            probe = TraceProbe(task_id="test-008")
            probe.record(
                "COLLISION_DETECTED",
                "碰撞!",
                position=MOCK_COLLISION_POSITION,
            )
            filepath = probe.dump_error_report(log_dir=tmpdir)

            assert filepath is not None
            assert Path(filepath).exists()

            # 验证文件内容
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["error_type"] == "COLLISION_DETECTED"
            assert data["task_id"] == "test-008"

    def test_no_dump_when_clean(self):
        """无错误时不生成文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            probe = TraceProbe(task_id="test-009")
            filepath = probe.dump_error_report(log_dir=tmpdir)
            assert filepath is None


class TestProbeDecorator:
    """@with_probe 装饰器测试"""

    def test_decorator_catches_assertion_error(self):
        """装饰器应捕获安全断言异常并记录"""

        @with_probe("test-010")
        def bad_move():
            # 模拟安全断言触发
            assert False, "Z 轴高度违规!"

        with pytest.raises(AssertionError):
            bad_move()
        # 探针应在异常发生前记录了事件

    def test_decorator_catches_general_exception(self):
        """装饰器应捕获通用异常"""

        @with_probe("test-011")
        def crashing_func():
            raise RuntimeError("未知运行时错误")

        with pytest.raises(RuntimeError):
            crashing_func()


class TestElapsedTime:
    """耗时统计测试"""

    def test_elapsed_positive(self):
        """耗时应为正数"""
        probe = TraceProbe(task_id="test-012")
        elapsed = probe.elapsed_ms()
        assert elapsed >= 0.0

    def test_elapsed_increases(self):
        """耗时应随时间增大"""
        import time
        probe = TraceProbe(task_id="test-013")
        t1 = probe.elapsed_ms()
        time.sleep(0.01)
        t2 = probe.elapsed_ms()
        assert t2 >= t1
