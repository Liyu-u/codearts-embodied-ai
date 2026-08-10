"""
Mock 单元测试 — CodeArts 策略生成
同学 B：拿假意图 JSON 测试代码生成逻辑和安全校验器

独立运行，无需等待同学 A/C/D 的模块。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from agent.code_validator import CodeValidator


# ============================================================
# Mock 数据 — 模拟同学 A 输出的规范 JSON
# ============================================================
MOCK_SIMPLE_PICK = {
    "intent_id": "task-001",
    "action": "pick_and_place",
    "target_object": "红色方块",
    "destination": {"x": 0.2000, "y": 0.0000, "z": 0.0300},
}

MOCK_SORT = {
    "intent_id": "task-002",
    "action": "sort_by_color",
    "target_objects": ["红色方块", "蓝色方块", "绿色方块"],
    "sort_criterion": "color",
    "num_piles": 3,
}

MOCK_FILTER = {
    "intent_id": "task-003",
    "action": "filter_by_attribute",
    "target_objects": ["红色方块", "蓝色杯子", "绿色圆柱"],
    "attributes": ["red"],
}


# ============================================================
# Mock 策略代码 (模拟 CodeArts 输出)
# ============================================================
SAFE_POLICY_CODE = '''
def task_pick_and_place():
    """安全的抓取放置策略"""
    objects = get_scene_objects()
    target = next(o for o in objects if "红色方块" in o.name)

    safe_z = max(target.position[2] + 0.10, 0.02)
    assert safe_z >= 0.02

    robot = ExecutionWrapper()
    robot.move_to_pose(target.position[0], target.position[1], safe_z, 0, 0, 0)
    robot.open_gripper(0.08)
    robot.move_to_pose(target.position[0], target.position[1],
                       target.position[2] + 0.005, 0, 0, 0)
    robot.close_gripper(5.0)
    robot.verify_grasp(0.5)
    robot.move_to_pose(0.2000, 0.0000, safe_z, 0, 0, 0)
    robot.move_to_pose(0.2000, 0.0000, 0.0300, 0, 0, 0)
    robot.open_gripper(0.08)
    return {"status": "success", "task_id": "task-001"}
'''

DANGEROUS_CODE = '''
import os
os.system("rm -rf /")
eval("print('pwned')")
'''

CODE_WITH_IMPORT_NUMPY = '''
import numpy as np
def task_with_numpy():
    vec = np.array([0.1, 0.2, 0.15])
    robot.move_to_pose(vec[0], vec[1], vec[2], 0, 0, 0)
    return {"status": "success"}
'''


# ============================================================
# 测试用例
# ============================================================
class TestCodeValidator:
    """测试代码安全校验器"""

    def test_safe_code_passes_syntax(self):
        """安全代码应通过语法检查"""
        ok, msg = CodeValidator.validate_syntax(SAFE_POLICY_CODE)
        assert ok, f"安全代码不应有语法错误: {msg}"

    def test_safe_code_passes_security(self):
        """安全代码应通过安全检查"""
        ok, violations = CodeValidator.validate_security(SAFE_POLICY_CODE)
        assert ok, f"安全代码不应触发安全违规: {violations}"

    def test_dangerous_import_os_blocked(self):
        """应拦截 import os"""
        ok, violations = CodeValidator.validate_security(DANGEROUS_CODE)
        assert not ok, "应检测到 os 导入!"
        assert any("os" in v for v in violations)

    def test_dangerous_eval_blocked(self):
        """应拦截 eval()"""
        ok, violations = CodeValidator.validate_security("eval('1+1')")
        assert not ok, "应检测到 eval()!"

    def test_syntax_error_detected(self):
        """应检测语法错误"""
        ok, msg = CodeValidator.validate_syntax("def broken(:\n    pass")
        assert not ok, "语法错误应被检测到!"

    def test_numpy_import_allowed(self):
        """NumPy 导入应被允许"""
        ok, violations = CodeValidator.validate_security(CODE_WITH_IMPORT_NUMPY)
        # NumPy 不在禁止模块列表中
        forbidden_found = [v for v in violations if "禁止导入模块" in v]
        assert len(forbidden_found) == 0, f"NumPy 不应被拦截: {forbidden_found}"

    def test_z_assertion_check(self):
        """Z 轴安全断言检查"""
        _, warnings = CodeValidator.validate_safety_assertions(SAFE_POLICY_CODE)
        # 安全代码已包含 assert z >= 0.02
        z_warnings = [w for w in warnings if "Z 轴安全高度断言" in w]
        assert len(z_warnings) == 0, f"包含 Z 断言时不应警告: {z_warnings}"

    def test_full_validation_report_structure(self):
        """完整校验报告应有正确的数据结构"""
        result = CodeValidator.full_validation(SAFE_POLICY_CODE)
        assert "passed" in result
        assert "syntax" in result
        assert "security" in result
        assert "safety" in result
        assert result["passed"] is True


class TestIntentJSONStructure:
    """测试意图 JSON 数据结构"""

    def test_simple_pick_structure(self):
        """简单抓取 JSON 结构正确"""
        assert MOCK_SIMPLE_PICK["action"] == "pick_and_place"
        assert "destination" in MOCK_SIMPLE_PICK
        assert all(k in MOCK_SIMPLE_PICK["destination"] for k in ("x", "y", "z"))

    def test_sort_structure(self):
        """排序 JSON 结构正确"""
        assert MOCK_SORT["action"] == "sort_by_color"
        assert len(MOCK_SORT["target_objects"]) == 3

    def test_filter_structure(self):
        """筛选 JSON 结构正确"""
        assert MOCK_FILTER["action"] == "filter_by_attribute"
        assert "red" in MOCK_FILTER["attributes"]
