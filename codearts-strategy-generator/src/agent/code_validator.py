"""
代码安全校验器 — 同学 B (冯海) 负责
通过 AST + 正则表达式检查 CodeArts 生成代码的语法错误，
并拦截对未知函数/危险模块的调用。

三层防护:
  1. 语法检查 (AST 解析)
  2. 安全检查 (黑名单模块/函数 + 白名单 API)
  3. 物理安全断言检查 (Z轴高度、夹爪力、入口函数)
"""

import ast
import re
from typing import Dict, List, Tuple


class CodeValidator:
    """代码沙盒校验器 — 三层防护"""

    # === 第一层：黑名单 ===
    FORBIDDEN_MODULES = {
        "os", "subprocess", "shutil", "sys", "socket", "requests",
        "pickle", "marshal", "ctypes", "signal", "atexit",
        "http", "urllib", "ftplib", "smtplib", "telnetlib",
    }
    FORBIDDEN_FUNCTIONS = {
        "eval", "exec", "compile", "__import__", "open",
        "getattr", "setattr", "delattr", "globals", "locals",
        "input", "breakpoint", "exit", "quit",
    }

    # === 第二层：白名单 (元 API + 动作库 + 安全常量) ===
    ALLOWED_API_CALLS = {
        # 元 API — 运动控制
        "move_to_pose", "move_joints", "move_linear",
        "open_gripper", "close_gripper", "verify_grasp",
        # 元 API — 感知
        "get_scene_objects", "get_robot_state", "get_gripper_state",
        # 元 API — 逻辑判断
        "check_collision",
        # 动作库 — 高层封装 (action_library.py)
        "pick_up", "place_at", "pick_and_place",
        "move_home", "approach_safely", "retreat_safely",
        "push", "stack", "find_object", "scan_table",
        "sort_by_color",
        # 安全常量
        "SAFE_Z", "PLACE_Z", "DEFAULT_FORCE", "DEFAULT_WIDTH",
        # 策略代码常用
        "next", "ExecutionWrapper", "robot",
        "print", "len", "range", "enumerate", "zip", "sorted",
        "sum", "min", "max", "abs", "round", "int", "float", "str",
        "list", "dict", "tuple", "set", "bool",
        "isinstance", "type", "hasattr", "any", "all",
        # NumPy 允许
        "np.array", "np.dot", "np.linalg", "np.sin", "np.cos",
        "np.sqrt", "np.pi", "np.arctan2", "np.norm",
    }

    @classmethod
    def validate_syntax(cls, code: str) -> Tuple[bool, str]:
        """语法检查 — 使用 AST 解析"""
        try:
            ast.parse(code)
            return True, "[OK] Syntax check passed"
        except SyntaxError as e:
            return False, f"[FAIL] 语法错误 [行 {e.lineno}]：{e.msg}"

    @classmethod
    def validate_security(cls, code: str) -> Tuple[bool, List[str]]:
        """安全检查 — 正则扫描黑名单"""
        violations = []

        # 检查导入
        for mod in cls.FORBIDDEN_MODULES:
            if re.search(rf"\bimport\s+{mod}\b", code) or \
               re.search(rf"\bfrom\s+{mod}\b", code):
                violations.append(f"[BLOCK] 禁止导入模块: `{mod}`")

        # 检查函数调用
        for func in cls.FORBIDDEN_FUNCTIONS:
            if re.search(rf"\b{func}\s*\(", code):
                violations.append(f"[BLOCK] 禁止调用函数: `{func}()`")

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
            # 允许对象方法调用: obj.method(), name.lower(), target.position
            is_method = "." in call and call.split(".")[0][0].islower()
            if not is_allowed and not is_local and not is_method and call not in cls.FORBIDDEN_FUNCTIONS:
                # 只报告明显的调用
                if not call.startswith("_"):
                    violations.append(f"[WARN] 未知函数调用: `{call}()` — 请确认在元 API 范围内")

        return len(violations) == 0, violations

    @classmethod
    def validate_entry_function(cls, code: str) -> Tuple[bool, str]:
        """检查是否定义了 task_main() 入口函数"""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "task_main":
                    return True, "[OK] task_main() 入口函数已定义"
            return False, "[WARN] 缺少 task_main() 入口函数 — code_loader 需要此函数作为执行入口"
        except SyntaxError:
            return False, "[FAIL] 代码有语法错误，无法检查入口函数"

    @classmethod
    def validate_gripper_force(cls, code: str) -> Tuple[bool, List[str]]:
        """检查 close_gripper 的力参数是否在安全范围内"""
        warnings = []
        for match in re.finditer(r'close_gripper\s*\(\s*([^)]+)\)', code):
            arg = match.group(1).strip()
            try:
                force_val = float(arg)
                if force_val <= 0 or force_val > 10.0:
                    warnings.append(
                        f"[WARN] close_gripper({force_val}) 力参数超出安全范围 (0, 10.0]N"
                    )
            except ValueError:
                pass
        return len(warnings) == 0, warnings

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
                warnings.append(f"[WARN] {message}")

        return len(warnings) == 0, warnings

    @classmethod
    def full_validation(cls, code: str) -> dict:
        """执行全部校验，返回详细报告"""
        syntax_ok, syntax_msg = cls.validate_syntax(code)

        if not syntax_ok:
            return {
                "passed": False,
                "stage": "SYNTAX",
                "syntax": (syntax_ok, syntax_msg),
                "security": (True, []),
                "safety": (True, []),
                "entry": (True, ""),
                "gripper_force": (True, []),
                "summary": syntax_msg,
            }

        security_ok, security_violations = cls.validate_security(code)
        safety_ok, safety_warnings = cls.validate_safety_assertions(code)
        entry_ok, entry_msg = cls.validate_entry_function(code)
        force_ok, force_warnings = cls.validate_gripper_force(code)

        passed = syntax_ok and security_ok

        all_warnings = safety_warnings + force_warnings
        if not entry_ok:
            all_warnings.append(entry_msg)

        return {
            "passed": passed,
            "stage": "SECURITY" if not security_ok else "DONE",
            "syntax": (syntax_ok, syntax_msg),
            "security": (security_ok, security_violations),
            "safety": (safety_ok, safety_warnings),
            "entry": (entry_ok, entry_msg),
            "gripper_force": (force_ok, force_warnings),
            "summary": (
                "[OK] 全部校验通过"
                if passed
                else f"[FAIL] 校验失败 — {len(security_violations)} 个安全违规"
            ),
        }


# ============================================================
# 快捷测试 (独立运行)
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  代码安全校验器自检 — 同学 B (冯海)")
    print("=" * 60)

    # 测试安全代码
    safe = '''
def task_main():
    """安全的抓取放置策略"""
    objects = get_scene_objects()
    target = find_object(color="red")
    if not target:
        return {"status": "failed", "reason": "no target"}
    safe_z = max(target.position[2] + 0.10, 0.02)
    assert safe_z >= 0.02, "[SAFETY] 安全高度不足!"
    pick_and_place(robot, target, 0.2, 0.0)
    return {"status": "success", "task_id": "task-001"}
'''
    print("\n--- 安全代码校验 ---")
    result = CodeValidator.full_validation(safe)
    for key, val in result.items():
        print(f"  {key}: {val}")

    # 测试危险代码
    dangerous = '''
import os
os.system("rm -rf /")
eval("print('hacked')")
'''
    print("\n--- 危险代码校验 ---")
    result = CodeValidator.full_validation(dangerous)
    for key, val in result.items():
        print(f"  {key}: {val}")

    # 测试缺少入口函数
    no_entry = '''
def some_other_func():
    move_to_pose(0.1, 0.2, 0.05)
'''
    print("\n--- 缺少入口函数校验 ---")
    result = CodeValidator.full_validation(no_entry)
    print(f"  passed: {result['passed']}")
    print(f"  entry: {result['entry']}")

    # 测试夹爪力超限
    force_exceed = '''
def task_main():
    close_gripper(15.0)
    return {"status": "success"}
'''
    print("\n--- 夹爪力超限校验 ---")
    result = CodeValidator.full_validation(force_exceed)
    print(f"  gripper_force: {result['gripper_force']}")
