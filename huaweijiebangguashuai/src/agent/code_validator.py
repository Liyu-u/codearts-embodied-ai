"""
代码安全校验器
同学 B 上传：通过正则表达式检查 CodeArts 生成代码的语法错误，
并拦截对未知函数/危险模块的调用。
"""

import ast
import re
from typing import List, Tuple


class CodeValidator:
    """代码沙盒校验器 — 三层防护"""

    # === 第一层：黑名单 ===
    FORBIDDEN_MODULES = {
        "os", "subprocess", "shutil", "sys", "socket", "requests",
        "pickle", "marshal", "ctypes", "signal", "atexit",
    }
    FORBIDDEN_FUNCTIONS = {
        "eval", "exec", "compile", "__import__", "open",
        "getattr", "setattr", "delattr", "globals", "locals",
    }

    # === 第二层：白名单 (元 API 合法函数) ===
    ALLOWED_API_CALLS = {
        "move_to_pose", "move_joints", "move_linear",
        "open_gripper", "close_gripper", "verify_grasp",
        "get_scene_objects", "get_robot_state", "get_gripper_state",
        "check_collision",
        "print", "len", "range", "enumerate", "zip", "sorted",
        "sum", "min", "max", "abs", "round", "int", "float", "str",
        "list", "dict", "tuple", "set", "bool",
        # NumPy 允许
        "np.array", "np.dot", "np.linalg", "np.sin", "np.cos",
    }

    @classmethod
    def validate_syntax(cls, code: str) -> Tuple[bool, str]:
        """语法检查 — 使用 AST 解析"""
        try:
            ast.parse(code)
            return True, "✅ 语法检查通过"
        except SyntaxError as e:
            return False, f"❌ 语法错误 [行 {e.lineno}]：{e.msg}"

    @classmethod
    def validate_security(cls, code: str) -> Tuple[bool, List[str]]:
        """安全检查 — 正则扫描黑名单"""
        violations = []

        # 检查导入
        for mod in cls.FORBIDDEN_MODULES:
            if re.search(rf"\bimport\s+{mod}\b", code) or \
               re.search(rf"\bfrom\s+{mod}\b", code):
                violations.append(f"🚫 禁止导入模块: `{mod}`")

        # 检查函数调用
        for func in cls.FORBIDDEN_FUNCTIONS:
            if re.search(rf"\b{func}\s*\(", code):
                violations.append(f"🚫 禁止调用函数: `{func}()`")

        # 检查未知函数调用 (不在白名单内的函数)
        all_calls = re.findall(r'\b(\w+(?:\.\w+)?)\s*\(', code)
        for call in all_calls:
            # 跳过 Python 关键字和字符串
            if call in ("def", "class", "if", "for", "while", "return"):
                continue
            # 检查是否在白名单中（完全匹配或前缀匹配）
            is_allowed = any(
                call == allowed or call.startswith(allowed + ".")
                for allowed in cls.ALLOWED_API_CALLS
            )
            # 也允许自定义函数（def task_xx 等）和局部变量
            is_local = call.startswith("task_") or call.startswith("robot.")
            if not is_allowed and not is_local and call not in cls.FORBIDDEN_FUNCTIONS:
                # 只报告明显的调用
                if not call.startswith("_"):
                    violations.append(f"⚠️ 未知函数调用: `{call}()` — 请确认在元 API 范围内")

        return len(violations) == 0, violations

    @classmethod
    def validate_safety_assertions(cls, code: str) -> Tuple[bool, List[str]]:
        """验证是否包含必要的物理安全断言"""
        warnings = []

        checks = {
            r'assert\s+.*z\s*>=\s*0\.02': "缺少 Z 轴安全高度断言 (assert z >= 0.02)",
            r'close_gripper\s*\(': "close_gripper() 调用后应有 verify_grasp() 检查",
        }

        for pattern, message in checks.items():
            if not re.search(pattern, code):
                warnings.append(f"⚠️ {message}")

        return len(warnings) == 0, warnings

    @classmethod
    def full_validation(cls, code: str) -> dict:
        """执行全部三层校验，返回详细报告"""
        syntax_ok, syntax_msg = cls.validate_syntax(code)

        if not syntax_ok:
            return {
                "passed": False,
                "stage": "SYNTAX",
                "syntax": (syntax_ok, syntax_msg),
                "security": (True, []),
                "safety": (True, []),
                "summary": syntax_msg,
            }

        security_ok, security_violations = cls.validate_security(code)
        safety_ok, safety_warnings = cls.validate_safety_assertions(code)

        passed = syntax_ok and security_ok

        return {
            "passed": passed,
            "stage": "SECURITY" if not security_ok else "DONE",
            "syntax": (syntax_ok, syntax_msg),
            "security": (security_ok, security_violations),
            "safety": (safety_ok, safety_warnings),
            "summary": (
                "✅ 全部校验通过"
                if passed
                else f"❌ 校验失败 — {len(security_violations)} 个安全违规"
            ),
        }


# ============================================================
# 快捷测试 (独立运行)
# ============================================================
if __name__ == "__main__":
    # 测试安全代码
    safe = '''
def task_test():
    safe_z = max(0.15, 0.02)
    robot.move_to_pose(0.1, 0.2, safe_z, 0, 0, 0)
    robot.close_gripper(5.0)
'''
    print("--- 安全代码校验 ---")
    print(CodeValidator.full_validation(safe))

    # 测试危险代码
    dangerous = '''
import os
os.system("rm -rf /")
eval("print('hacked')")
'''
    print("\n--- 危险代码校验 ---")
    print(CodeValidator.full_validation(dangerous))
