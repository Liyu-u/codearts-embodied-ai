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


# ============================================================
# 示例任务（用于自测和演示）
# ============================================================
SAMPLE_TASKS = {
    "pick_and_place": '''
def task_main():
    """简单抓取放置 — 抓红色方块放到(0.2, 0.0, 0.03)"""
    objects = get_scene_objects()
    target = None
    for obj in objects:
        name = obj.name.lower()
        if "red" in name or "cube" in name or "方块" in obj.name:
            target = obj
            break
    if target is None:
        return {"status": "failed", "reason": "未找到目标物体"}

    px, py, pz = target.position
    safe_z = max(pz + 0.15, 0.02)

    move_to_pose(px, py, safe_z, 0, 0, 0)
    open_gripper(0.08)
    move_to_pose(px, py, pz + 0.003, 0, 0, 0)
    close_gripper(5.0)

    if not verify_grasp(0.5):
        open_gripper(0.08)
        return {"status": "failed", "reason": "抓取失败"}

    move_to_pose(px, py, safe_z, 0, 0, 0)
    move_to_pose(0.2, 0.0, safe_z, 0, 0, 0)
    move_to_pose(0.2, 0.0, 0.03, 0, 0, 0)
    open_gripper(0.08)
    return {"status": "success", "action": "pick_and_place"}
''',

    "stack": '''
def task_main():
    """堆叠 — 抓物体A摞到物体B上面"""
    objects = get_scene_objects()
    if len(objects) < 2:
        return {"status": "failed", "reason": "场景中物体不足"}

    obj_a = objects[0]
    obj_b = objects[1]

    safe_z = max(obj_a.position[2] + 0.15, 0.02)
    target_x = obj_b.position[0]
    target_y = obj_b.position[1]
    target_z = obj_b.position[2] + obj_b.bbox[1]  # B顶部

    # 拾取 A
    move_to_pose(obj_a.position[0], obj_a.position[1], safe_z, 0, 0, 0)
    open_gripper(0.08)
    move_to_pose(obj_a.position[0], obj_a.position[1],
                 obj_a.position[2] + 0.003, 0, 0, 0)
    close_gripper(5.0)
    verify_grasp(0.5)
    move_to_pose(obj_a.position[0], obj_a.position[1], safe_z, 0, 0, 0)

    # 放置到 B 上方
    move_to_pose(target_x, target_y, safe_z, 0, 0, 0)
    assert target_z >= 0.02, "目标Z太高或太低!"
    move_to_pose(target_x, target_y, target_z + 0.01, 0, 0, 0)
    open_gripper(0.08)
    return {"status": "success", "action": "stack"}
''',

    "sort_by_color": '''
def task_main():
    """颜色分类 — 红方块放左，蓝方块放中，绿方块放右"""
    objects = get_scene_objects()
    color_targets = {
        "red": (-0.2, 0.0),    # 左边
        "blue": (0.0, 0.0),    # 中间
        "green": (0.2, 0.0),   # 右边
    }

    for obj in objects:
        px, py, pz = obj.position
        safe_z = max(pz + 0.15, 0.02)
        target_xy = None

        if obj.color:
            for cn, txy in color_targets.items():
                if cn in obj.color.lower():
                    target_xy = txy
                    break

        if target_xy is None:
            continue

        # 拾取
        move_to_pose(px, py, safe_z, 0, 0, 0)
        open_gripper(0.08)
        move_to_pose(px, py, pz + 0.003, 0, 0, 0)
        close_gripper(5.0)
        verify_grasp(0.5)
        move_to_pose(px, py, safe_z, 0, 0, 0)

        # 放置到目标位置
        tx, ty = target_xy
        move_to_pose(tx, ty, safe_z, 0, 0, 0)
        move_to_pose(tx, ty, 0.03, 0, 0, 0)
        open_gripper(0.08)

    return {"status": "success", "action": "sort_by_color"}
'''
}


# ============================================================
# 场景搭建函数
# ============================================================
def setup_demo_scene(world):
    """搭建演示场景：桌面 + 几个彩色方块"""
    from isaacsim.core.utils.stage import get_current_stage
    from isaacsim.core.utils.prims import create_prim
    from isaacsim.storage.native import get_assets_root_path

    stage = get_current_stage()
    assets_root = get_assets_root_path()

    # 添加桌面
    table_path = "/World/Table"
    create_prim(
        prim_path=table_path,
        prim_type="Cube",
        position=(0.3, 0.0, 0.0),
        scale=(0.6, 1.0, 0.02),
    )
    print(f"[SCENE] 桌面已添加: {table_path}")

    # 添加彩色方块
    blocks = [
        ("RedCube",    (0.15, 0.05, 0.04),  (1.0, 0.0, 0.0)),  # 红色
        ("BlueCube",   (0.25, -0.08, 0.04), (0.0, 0.0, 1.0)),  # 蓝色
        ("GreenCube",  (0.35, 0.12, 0.04),  (0.0, 1.0, 0.0)),  # 绿色
        ("YellowCube", (0.20, -0.15, 0.04), (1.0, 1.0, 0.0)),  # 黄色
    ]

    for name, pos, color in blocks:
        path = f"/World/{name}"
        create_prim(
            prim_path=path,
            prim_type="Cube",
            position=pos,
            scale=(0.04, 0.04, 0.04),
        )
        # 设置颜色
        prim = stage.GetPrimAtPath(path)
        if prim:
            from pxr import UsdGeom
            geom = UsdGeom.Gprim(prim)
            geom.GetDisplayColorAttr().Set([color])
        print(f"[SCENE] 物体已添加: {name} at {pos}")

    world.step(render=False)


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="Isaac Sim 仿真运行器")
    parser.add_argument(
        "--task",
        type=str,
        default="pick_and_place",
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
    print("\n[2/5] 搭建演示场景...")
    setup_demo_scene(world)

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
