"""
代码安全校验与沙盒语法拦截器
同学 B：对 LLM 生成的策略代码进行安全审查
"""

import ast
import re
from typing import List, Tuple


class CodeValidator:
    """代码安全校验器 — 拦截危险操作"""

    # 黑名单函数/模块
    FORBIDDEN_IMPORTS = {"os", "subprocess", "shutil", "sys", "socket", "requests"}
    FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open"}

    # 物理安全边界
    MIN_Z_HEIGHT = 0.02  # 末端执行器最低 Z 高度 (m)
    MAX_GRIPPER_FORCE = 10.0  # 夹爪最大力 (N)

    @classmethod
    def validate_syntax(cls, code: str) -> Tuple[bool, str]:
        """语法检查"""
        try:
            ast.parse(code)
            return True, "语法检查通过"
        except SyntaxError as e:
            return False, f"语法错误: {e}"

    @classmethod
    def validate_security(cls, code: str) -> Tuple[bool, List[str]]:
        """安全检查 — 检测黑名单调用"""
        violations = []

        # 检查导入
        for mod in cls.FORBIDDEN_IMPORTS:
            if re.search(rf"\bimport\s+{mod}\b", code) or re.search(
                rf"\bfrom\s+{mod}\b", code
            ):
                violations.append(f"禁止导入模块: {mod}")

        # 检查函数调用
        for func in cls.FORBIDDEN_CALLS:
            if re.search(rf"\b{func}\s*\(", code):
                violations.append(f"禁止调用函数: {func}()")

        return len(violations) == 0, violations

    @classmethod
    def validate_safety_assertions(cls, code: str) -> Tuple[bool, List[str]]:
        """验证是否包含必要的物理安全断言"""
        warnings = []

        if 'assert z >= 0.02' not in code and 'MIN_Z_HEIGHT' not in code:
            warnings.append("缺少 Z 轴安全高度断言 (assert z >= 0.02)")

        return len(warnings) == 0, warnings

    @classmethod
    def full_validation(cls, code: str) -> dict:
        """执行全部校验流程"""
        results = {
            "syntax": cls.validate_syntax(code),
            "security": cls.validate_security(code),
            "safety": cls.validate_safety_assertions(code),
        }

        results["passed"] = all(
            [
                results["syntax"][0],
                results["security"][0],
            ]
        )

        return results
