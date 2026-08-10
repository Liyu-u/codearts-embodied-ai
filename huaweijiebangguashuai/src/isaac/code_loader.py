"""
动态代码加载器 — B-C 联调桥梁
同学 C（吴昌庆）上传

负责：
1. 接收同学 B（CodeArts）生成的 Python 策略代码字符串
2. 在安全沙箱中 exec() 执行
3. 将 ExecutionWrapper 元 API + 场景感知函数注入到执行命名空间
4. 执行前后的安全校验（通过 code_validator）
"""

import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

# 将 agent 模块加入 path（引用 validator）
sys.path.insert(0, str(Path(__file__).parent.parent))
from agent.code_validator import CodeValidator


# ============================================================
# 策略执行器
# ============================================================
class StrategyExecutor:
    """
    安全策略执行器。

    执行流程:
        1. 语法校验 (AST)
        2. 安全检查 (黑/白名单)
        3. 注入 API 命名空间
        4. exec() 执行代码
        5. 调用入口函数 task_main()
    """

    # 暴露给策略代码的 API 命名空间
    API_NAMESPACE_KEYS = {
        # 运动控制
        "move_to_pose",
        "move_joints",
        "move_linear",
        "open_gripper",
        "close_gripper",
        # 状态查询
        "get_robot_state",
        "get_gripper_state",
        # 场景感知
        "get_scene_objects",
        # 逻辑判断
        "check_collision",
        "verify_grasp",
        # Python 内置
        "print", "len", "range", "enumerate", "zip", "sorted",
        "sum", "min", "max", "abs", "round",
        "int", "float", "str", "list", "dict", "tuple", "set", "bool",
        "True", "False", "None",
    }

    def __init__(self, robot, scene_provider: Optional[Callable] = None):
        """
        Args:
            robot: ExecutionWrapper 实例
            scene_provider: 获取场景物体列表的函数
        """
        self.robot = robot
        self.scene_provider = scene_provider

    def build_namespace(self) -> Dict[str, Any]:
        """构建注入策略代码的命名空间（包含元 API + 动作库）"""
        from isaac.get_scene_json import get_scene_objects
        from isaac.action_library import ACTION_LIBRARY, ACTION_CONSTANTS

        ns = {
            # 元 API — 运动控制
            "move_to_pose": self.robot.move_to_pose,
            "move_joints": self.robot.move_joints,
            "move_linear": self.robot.move_linear,
            "open_gripper": self.robot.open_gripper,
            "close_gripper": self.robot.close_gripper,
            # 元 API — 状态查询
            "get_robot_state": self.robot.get_robot_state,
            "get_gripper_state": self.robot.get_gripper_state,
            # 元 API — 场景感知
            "get_scene_objects": self.scene_provider or get_scene_objects,
            # 元 API — 逻辑判断
            "check_collision": self.robot.check_collision,
            "verify_grasp": self.robot.verify_grasp,
            # 动作库 — 队友 B 可直接调用的高层动作
            "pick_up": ACTION_LIBRARY["pick_up"],
            "place_at": ACTION_LIBRARY["place_at"],
            "pick_and_place": ACTION_LIBRARY["pick_and_place"],
            "move_home": ACTION_LIBRARY["move_home"],
            "approach_safely": ACTION_LIBRARY["approach_safely"],
            "retreat_safely": ACTION_LIBRARY["retreat_safely"],
            "push": ACTION_LIBRARY["push"],
            "stack": ACTION_LIBRARY["stack"],
            "find_object": ACTION_LIBRARY["find_object"],
            "scan_table": ACTION_LIBRARY["scan_table"],
            "sort_by_color": ACTION_LIBRARY["sort_by_color"],
            # 安全常量
            "SAFE_Z": ACTION_CONSTANTS["SAFE_Z"],
            "PLACE_Z": ACTION_CONSTANTS["PLACE_Z"],
            "DEFAULT_FORCE": ACTION_CONSTANTS["DEFAULT_FORCE"],
            "DEFAULT_WIDTH": ACTION_CONSTANTS["DEFAULT_WIDTH"],
            # Python 内置
            "print": print,
            "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "sorted": sorted,
            "sum": sum, "min": min, "max": max, "abs": abs, "round": round,
            "int": int, "float": float, "str": str,
            "list": list, "dict": dict, "tuple": tuple, "set": set, "bool": bool,
            "True": True, "False": False, "None": None,
            "__builtins__": __builtins__,
        }

        # 尝试导入 NumPy
        try:
            import numpy as np
            ns["np"] = np
        except ImportError:
            pass

        return ns

    def execute(self, code: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        安全执行策略代码。

        Args:
            code: CodeArts 生成的 Python 策略代码

        Returns:
            (success, message, result_dict)
        """
        # === 阶段 1: 安全校验 ===
        validation = CodeValidator.full_validation(code)
        if not validation["passed"]:
            return False, f"安全校验失败: {validation['summary']}", validation

        # === 阶段 2: 构建命名空间并执行 ===
        namespace = self.build_namespace()

        try:
            exec(code, namespace)
        except Exception as e:
            tb = traceback.format_exc()
            return False, f"代码执行异常: {e}\n{tb}", None

        # === 阶段 3: 调用入口函数 task_main() ===
        if "task_main" not in namespace:
            return False, "代码中未定义 task_main() 入口函数", None

        try:
            result = namespace["task_main"]()
            return True, "策略执行成功", result
        except Exception as e:
            tb = traceback.format_exc()
            return False, f"task_main() 执行失败: {e}\n{tb}", None


# ============================================================
# 快捷执行函数（供 server.py 调用）
# ============================================================
def execute_strategy_code(
    code: str,
    robot,
    scene_provider: Optional[Callable] = None,
) -> dict:
    """
    供后端 server.py 调用的入口函数。

    Args:
        code: 策略代码字符串
        robot: ExecutionWrapper 实例
        scene_provider: 场景感知函数

    Returns:
        {
            "success": bool,
            "message": str,
            "result": dict | None,
            "validation": dict,
        }
    """
    executor = StrategyExecutor(robot, scene_provider)
    success, message, result = executor.execute(code)
    return {
        "success": success,
        "message": message,
        "result": result,
    }


# ============================================================
# 自检（独立运行 — 需要 Mock robot）
# ============================================================
if __name__ == "__main__":
    # Mock robot for self-test
    class _MockRobot:
        def move_to_pose(self, x, y, z, r=0, p=0, w=0):
            assert z >= 0.02
            print(f"  [MOCK] move_to_pose({x:.3f}, {y:.3f}, {z:.3f})")
            return True
        def move_joints(self, angles):
            return True
        def move_linear(self, dx, dy, dz, speed=0.05):
            return True
        def open_gripper(self, width=0.08):
            print(f"  [MOCK] open_gripper({width:.3f})")
            return True
        def close_gripper(self, force=5.0):
            print(f"  [MOCK] close_gripper({force:.1f})")
            return True
        def get_robot_state(self):
            return object()
        def get_gripper_state(self):
            return object()
        def check_collision(self, x, y, z):
            return True
        def verify_grasp(self, threshold=0.5):
            return True

    # 测试安全代码
    safe_code = '''
def task_main():
    """简单抓取放置任务"""
    objects = get_scene_objects()
    target = None
    for obj in objects:
        if "red" in obj.name.lower() or "红" in obj.name:
            target = obj
            break
    if target is None:
        return {"status": "failed", "reason": "no target"}

    px, py, pz = target.position
    safe_z = max(pz + 0.15, 0.02)
    assert safe_z >= 0.02

    move_to_pose(px, py, safe_z, 0, 0, 0)
    open_gripper(0.08)
    move_to_pose(px, py, pz + 0.005, 0, 0, 0)
    close_gripper(5.0)
    assert verify_grasp(0.5), "Grasp verification failed!"

    move_to_pose(px, py, safe_z, 0, 0, 0)
    move_to_pose(0.2, 0.0, safe_z, 0, 0, 0)
    move_to_pose(0.2, 0.0, 0.03, 0, 0, 0)
    open_gripper(0.08)
    return {"status": "success"}
'''

    print("=" * 60)
    print("策略代码加载器自检")
    print("=" * 60)

    from isaac.get_scene_json import get_scene_objects
    mock_robot = _MockRobot()
    executor = StrategyExecutor(mock_robot, get_scene_objects)
    success, msg, result = executor.execute(safe_code)

    print(f"[Result] {'SUCCESS' if success else 'FAILED'}")
    print(f"[Message] {msg}")
    if result:
        print(f"[Return] {result}")
