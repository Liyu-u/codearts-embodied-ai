"""
Mock 单元测试 — CodeArts 策略生成 + 安全校验
同学 B (冯海)：拿假意图 JSON 测试代码生成逻辑和安全校验器

独立运行，无需等待同学 A/C/D 的模块。
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from agent.code_validator import CodeValidator
from agent.strategy_generator import StrategyGenerator, generate_strategy


# ============================================================
# Mock 数据 — 模拟同学 A 输出的规范 JSON
# ============================================================
MOCK_SIMPLE_PICK = {
    "intent_id": "task-001",
    "action": "pick_and_place",
    "target_object": "红色方块",
    "destination": {"x": 0.2000, "y": 0.0000, "z": 0.0300},
}

MOCK_PUSH = {
    "intent_id": "task-002",
    "action": "push",
    "target_object": "绿色圆柱",
    "destination": {"x": 0.4000, "y": -0.2000, "z": 0.0400},
}

MOCK_STACK = {
    "intent_id": "task-003",
    "action": "stack",
    "target_object": "红色方块",
    "reference_object": "蓝色方块",
    "spatial_relation": "on_top",
}

MOCK_SORT = {
    "intent_id": "task-004",
    "action": "sort_by_color",
    "target_objects": ["红色方块", "蓝色方块", "绿色方块"],
    "sort_criterion": "color",
    "num_piles": 3,
}

MOCK_FILTER = {
    "intent_id": "task-005",
    "action": "filter_by_attribute",
    "target_objects": ["红色方块", "蓝色杯子", "绿色圆柱"],
    "attributes": ["red"],
}

MOCK_SORT_SIZE = {
    "intent_id": "task-006",
    "action": "sort_by_size",
    "target_objects": ["红色方块", "蓝色方块", "绿色方块"],
    "sort_criterion": "size",
}


# ============================================================
# Mock 策略代码 (模拟 CodeArts 输出)
# ============================================================
SAFE_POLICY_CODE = '''
def task_main():
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

SAFE_ACTION_LIB_CODE = '''
def task_main():
    """使用动作库的安全策略"""
    target = find_object(color="red")
    if not target:
        return {"status": "failed", "reason": "no red object"}
    result = pick_and_place(robot, target, 0.2, 0.0)
    return result
'''

DANGEROUS_CODE = '''
import os
os.system("rm -rf /")
eval("print('pwned')")
'''

CODE_WITH_IMPORT_NUMPY = '''
import numpy as np
def task_main():
    vec = np.array([0.1, 0.2, 0.15])
    robot.move_to_pose(vec[0], vec[1], vec[2], 0, 0, 0)
    return {"status": "success"}
'''

CODE_NO_ENTRY_FUNCTION = '''
def some_other_func():
    move_to_pose(0.1, 0.2, 0.05)
    return {"status": "success"}
'''

CODE_FORCE_EXCEED = '''
def task_main():
    close_gripper(15.0)
    return {"status": "success"}
'''

CODE_WITH_SORT_BY_COLOR = '''
def task_main():
    result = sort_by_color(robot, "red", (0.2, -0.1, 0.03))
    if result["status"] != "success":
        return result
    move_home(robot)
    return {"status": "success"}
'''


# ============================================================
# 测试用例 — CodeValidator
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
        forbidden_found = [v for v in violations if "禁止导入模块" in v]
        assert len(forbidden_found) == 0, f"NumPy 不应被拦截: {forbidden_found}"

    def test_z_assertion_check(self):
        """Z 轴安全断言检查"""
        _, warnings = CodeValidator.validate_safety_assertions(SAFE_POLICY_CODE)
        z_warnings = [w for w in warnings if "Z 轴安全高度断言" in w]
        assert len(z_warnings) == 0, f"包含 Z 断言时不应警告: {z_warnings}"

    def test_full_validation_report_structure(self):
        """完整校验报告应有正确的数据结构"""
        result = CodeValidator.full_validation(SAFE_POLICY_CODE)
        assert "passed" in result
        assert "syntax" in result
        assert "security" in result
        assert "safety" in result
        assert "entry" in result
        assert "gripper_force" in result
        assert result["passed"] is True

    def test_entry_function_check_passes(self):
        """包含 task_main 的代码应通过入口函数检查"""
        ok, msg = CodeValidator.validate_entry_function(SAFE_POLICY_CODE)
        assert ok, f"包含 task_main 的代码应通过: {msg}"

    def test_entry_function_check_fails(self):
        """缺少 task_main 的代码应未通过入口函数检查"""
        ok, msg = CodeValidator.validate_entry_function(CODE_NO_ENTRY_FUNCTION)
        assert not ok, "缺少 task_main 应未通过检查"

    def test_gripper_force_valid(self):
        """合法夹爪力应通过检查"""
        ok, warnings = CodeValidator.validate_gripper_force("close_gripper(5.0)")
        assert ok, f"合法力不应触发警告: {warnings}"

    def test_gripper_force_exceed(self):
        """超限夹爪力应被检测"""
        ok, warnings = CodeValidator.validate_gripper_force(CODE_FORCE_EXCEED)
        assert not ok, "超限力应被检测"
        assert any("15.0" in w for w in warnings)

    def test_action_library_calls_allowed(self):
        """动作库函数调用应被允许"""
        ok, violations = CodeValidator.validate_security(SAFE_ACTION_LIB_CODE)
        blocked = [v for v in violations if "禁止" in v]
        assert len(blocked) == 0, f"动作库函数不应被拦截: {blocked}"

    def test_sort_by_color_allowed(self):
        """sort_by_color 函数应被允许"""
        ok, violations = CodeValidator.validate_security(CODE_WITH_SORT_BY_COLOR)
        blocked = [v for v in violations if "禁止" in v]
        assert len(blocked) == 0, f"sort_by_color 不应被拦截: {blocked}"


# ============================================================
# 测试用例 — StrategyGenerator
# ============================================================
class TestStrategyGenerator:
    """测试策略代码生成器"""

    @pytest.fixture
    def generator(self):
        return StrategyGenerator()

    def test_pick_and_place_generation(self, generator):
        """pick_and_place 意图应生成有效策略代码"""
        result = generator.generate(MOCK_SIMPLE_PICK)
        assert result["success"], f"生成失败: {result['message']}"
        assert result["code"] is not None
        assert "task_main" in result["code"]
        assert "pick_and_place" in result["code"] or "move_to_pose" in result["code"]

    def test_push_generation(self, generator):
        """push 意图应生成有效策略代码"""
        result = generator.generate(MOCK_PUSH)
        assert result["success"], f"生成失败: {result['message']}"
        assert "task_main" in result["code"]

    def test_stack_generation(self, generator):
        """stack 意图应生成有效策略代码"""
        result = generator.generate(MOCK_STACK)
        assert result["success"], f"生成失败: {result['message']}"
        assert "task_main" in result["code"]
        assert "stack" in result["code"]

    def test_sort_by_color_generation(self, generator):
        """sort_by_color 意图应生成有效策略代码"""
        result = generator.generate(MOCK_SORT)
        assert result["success"], f"生成失败: {result['message']}"
        assert "task_main" in result["code"]
        assert "sort_by_color" in result["code"]

    def test_filter_by_attribute_generation(self, generator):
        """filter_by_attribute 意图应生成有效策略代码"""
        result = generator.generate(MOCK_FILTER)
        assert result["success"], f"生成失败: {result['message']}"
        assert "task_main" in result["code"]

    def test_sort_by_size_generation(self, generator):
        """sort_by_size 意图应生成有效策略代码"""
        result = generator.generate(MOCK_SORT_SIZE)
        assert result["success"], f"生成失败: {result['message']}"
        assert "task_main" in result["code"]

    def test_unknown_action_falls_back(self, generator):
        """未知 action 应返回失败"""
        result = generator.generate({"action": "fly_to_moon", "target_object": "moon"})
        assert result["success"] is False

    def test_generated_code_passes_validation(self, generator):
        """生成的代码应通过安全校验"""
        for intent in [MOCK_SIMPLE_PICK, MOCK_PUSH, MOCK_STACK, MOCK_SORT, MOCK_FILTER]:
            result = generator.generate(intent)
            if result["success"] and result["code"]:
                validation = CodeValidator.full_validation(result["code"])
                assert validation["passed"], (
                    f"intent={intent['intent_id']} 生成的代码未通过校验: "
                    f"{validation['summary']}"
                )

    def test_generate_strategy_shortcut(self):
        """快捷函数 generate_strategy 应正常工作"""
        result = generate_strategy(MOCK_SIMPLE_PICK)
        assert "success" in result
        assert "code" in result
        assert "mode" in result


# ============================================================
# 测试用例 — 意图 JSON 数据结构
# ============================================================
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

    def test_stack_structure(self):
        """堆叠 JSON 结构正确"""
        assert MOCK_STACK["action"] == "stack"
        assert "reference_object" in MOCK_STACK
