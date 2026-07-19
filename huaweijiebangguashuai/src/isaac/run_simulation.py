"""
Isaac Sim 仿真入口脚本 — 通过 isaacsim.exe --exec 运行
同学 C（吴昌庆）

用法:
    isaacsim.exe --exec run_simulation.py -- --task sample_pick_and_place

    或通过后端 server.py 动态调用 execute_strategy_code()
"""

import sys
import os
import json
import argparse
from pathlib import Path

# 确保 src 目录在 path 中
SRC_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SRC_DIR))

from isaac.exec_wrapper import (
    ExecutionWrapper,
    create_isaac_environment,
    print_safety_manifest,
)
from isaac.get_scene_json import export_scene_state, get_scene_objects
from isaac.code_loader import execute_strategy_code
from isaac.scene_builder import ALL_SCENES, get_scene_def, _build_scene_in_kit, list_scenes


# ============================================================
# 示例任务（对应 3 个标准测试场景）
# ============================================================
SAMPLE_TASKS = {
    # 场景1: 方块堆叠 → 把绿色方块从顶上拿下来
    "stacking_unstack": '''
def task_main():
    """方块堆叠场景: 从堆叠塔顶取出绿色方块, 放到旁边"""
    target = find_object(color="green", name_contains="Top")
    if not target:
        return {"status": "failed", "reason": "未找到绿色方块"}
    return pick_and_place(robot, target, 0.4, 0.0)
''',

    # 场景1: 方块堆叠 → 堆叠 (红色放蓝色上)
    "stacking_restack": '''
def task_main():
    """方块堆叠场景: 把红色方块堆到蓝色方块上面"""
    red = find_object(color="red")
    blue = find_object(color="blue")
    if not red or not blue:
        return {"status": "failed", "reason": "未找到目标物体"}
    return stack(robot, red, blue)
''',

    # 场景2: 杯子排列 → 把杯子排成一行
    "cup_lineup": '''
def task_main():
    """杯子排列场景: 5个杯子从左到右依次排成一行"""
    objects = get_scene_objects()
    cups = [o for o in objects if o.label and "cup" in o.label]
    if len(cups) < 5:
        return {"status": "failed", "reason": f"只有{len(cups)}个杯子"}

    start_x = 0.10
    spacing = 0.12
    for i, cup in enumerate(cups):
        tx = start_x + spacing * i
        result = pick_and_place(robot, cup, tx, 0.15)
        if result["status"] != "success":
            return result
    return {"status": "success", "cups_arranged": len(cups)}
''',

    # 场景3: 颜色分类
    "color_sorting": '''
def task_main():
    """颜色分类场景: 红蓝绿方块分别放到对应分类区"""
    result = sort_by_color(robot, "red", (0.10, -0.20, 0.03))
    if result["status"] != "success":
        return result
    result = sort_by_color(robot, "blue", (0.30, -0.20, 0.03))
    if result["status"] != "success":
        return result
    result = sort_by_color(robot, "green", (0.50, -0.20, 0.03))
    if result["status"] != "success":
        return result
    move_home(robot)
    return {"status": "success", "action": "color_sorting"}
''',
}


# ============================================================
# 场景搭建函数（委托给 scene_builder）
# ============================================================
def setup_demo_scene(world, scene_name: str = "stacking_cubes"):
    """使用 scene_builder 搭建标准测试场景"""
    scene_def = get_scene_def(scene_name)
    if scene_def is None:
        print(f"[WARN] 未知场景 '{scene_name}', 使用 stacking_cubes 默认值")
        scene_def = get_scene_def("stacking_cubes")
    _build_scene_in_kit(scene_def)
    world.step(render=False)
    print(f"[SCENE] 场景 '{scene_def.name}' 搭建完成: {scene_def.description}")


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Isaac Sim 仿真运行器")
    parser.add_argument(
        "--scene",
        type=str,
        default="stacking_cubes",
        choices=list_scenes(),
        help="测试场景",
    )
    parser.add_argument(
        "--task",
        type=str,
        default="stacking_unstack",
        choices=list(SAMPLE_TASKS.keys()) + ["custom"],
        help="要执行的任务类型",
    )
    parser.add_argument(
        "--code",
        type=str,
        default=None,
        help="自定义策略代码（当 --task=custom 时使用）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="启用无头模式",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("  Isaac Sim 具身智能仿真运行器")
    print("  华为揭榜挂帅 · 同学 C（吴昌庆）")
    print("=" * 60)

    print_safety_manifest()

    # 1. 创建仿真环境
    print("\n[1/5] 初始化仿真环境...")
    world, robot = create_isaac_environment(headless=args.headless)

    # 2. 搭建场景
    print(f"\n[2/5] 搭建标准测试场景: {args.scene}...")
    setup_demo_scene(world, scene_name=args.scene)

    # 3. 初始化机械臂
    print("\n[3/5] 初始化 Franka Panda 机械臂...")
    robot._ensure_initialized()

    # 4. 感知场景
    print("\n[4/5] 场景感知...")
    scene_state = export_scene_state(robot=robot)
    print(f"  检测到 {len(scene_state['objects'])} 个物体")

    # 5. 执行任务
    print("\n[5/5] 执行策略...")
    if args.task == "custom" and args.code:
        code = args.code
    else:
        code = SAMPLE_TASKS.get(args.task, SAMPLE_TASKS["pick_and_place"])

    result = execute_strategy_code(code, robot)
    print(f"\n执行结果: {json.dumps(result, indent=2, ensure_ascii=False)}")

    # 仿真循环（展示结果）
    if not args.headless:
        print("\n按 Ctrl+C 退出...")
        try:
            while True:
                world.step(render=True)
        except KeyboardInterrupt:
            pass

    # 清理
    print("\n仿真结束。")
    import omni.kit.app
    omni.kit.app.get_app().post_quit()


if __name__ == "__main__":
    main()
