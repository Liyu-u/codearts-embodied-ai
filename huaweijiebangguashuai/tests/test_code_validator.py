"""
测试沙盒是否能成功拦截危险代码
同学 B：验证 CodeValidator 安全拦截能力
"""

import pytest
import sys
from pathlib import Path

# 临时添加 src 到 path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.code_validator import CodeValidator


SAFE_CODE = '''
def task_pick_block():
    """安全的任务代码"""
    safe_z = 0.15
    move_to_pose(0.1, 0.2, safe_z, 0, 0, 0)
    close_gripper(5.0)
    return {"status": "success"}
'''


class TestCodeValidator:
    """代码校验器单元测试"""

    def test_syntax_valid(self):
        """语法检查应通过合法代码"""
        ok, msg = CodeValidator.validate_syntax(SAFE_CODE)
        assert ok, f"合法代码应通过语法检查: {msg}"

    def test_syntax_invalid(self):
        """语法检查应拒绝非法代码"""
        ok, msg = CodeValidator.validate_syntax("def broken(:\n    pass")
        assert not ok, "非法语法应被拒绝"

    def test_security_forbidden_import_os(self):
        """应禁止 import os"""
        code = "import os\nos.system('ls')"
        ok, violations = CodeValidator.validate_security(code)
        assert not ok, f"应检测到禁止导入: {violations}"
        assert any("os" in v for v in violations)

    def test_security_forbidden_eval(self):
        """应禁止 eval()"""
        code = "eval('1+1')"
        ok, violations = CodeValidator.validate_security(code)
        assert not ok, f"应检测到 eval(): {violations}"

    def test_security_forbidden_exec(self):
        """应禁止 exec()"""
        code = "exec('print(1)')"
        ok, violations = CodeValidator.validate_security(code)
        assert not ok, f"应检测到 exec(): {violations}"

    def test_security_safe_code(self):
        """安全代码应通过安全检查"""
        ok, violations = CodeValidator.validate_security(SAFE_CODE)
        assert ok, f"安全代码不应有违规: {violations}"

    def test_safety_z_assertion_missing(self):
        """缺失 Z 轴安全断言应有警告"""
        ok, warnings = CodeValidator.validate_safety_assertions("move_to_pose(0.1, 0.2, 0.01, 0, 0, 0)")
        # 缺少断言会产生警告但不会拒绝（因为可能是上层包装了）
        assert len(warnings) > 0

    def test_full_validation_safe_code(self):
        """安全代码应通过完整校验"""
        result = CodeValidator.full_validation(SAFE_CODE)
        assert result["passed"], f"安全代码应通过全量校验: {result}"
