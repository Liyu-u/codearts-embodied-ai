# -*- coding: utf-8 -*-
"""
元 API 使用演示 — 给队友看效果的脚本
直接运行: python demo_api_usage.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from isaac.exec_wrapper import ExecutionWrapper
from isaac.get_scene_json import get_scene_objects

def demo_scene_perception():
    """演示1: 场景感知 — 看仿真战场里有什么物体"""
    print("=" * 60)
    print("【演示1】场景感知 get_scene_objects()")
    print("  调用后返回当前场景中所有可抓取物体的列表")
    print("=" * 60)
    objects = get_scene_objects()
    print(f"\n  发现 {len(objects)} 个物体:\n")
    for i, obj in enumerate(objects, 1):
        print(f"  [{i}] {obj.name}")
        print(f"      位置: x={obj.position[0]:.4f}m  y={obj.position[1]:.4f}m  z={obj.position[2]:.4f}m")
        print(f"      尺寸: 宽{obj.bbox[0]:.4f}m x 高{obj.bbox[1]:.4f}m x 深{obj.bbox[2]:.4f}m")
        print(f"      颜色: {obj.color}  标签: {obj.label}")
        print()
    return objects


def demo_robot_motion(robot):
    """演示2: 机械臂运动控制 — 用元API操作Franka Panda"""
    print("=" * 60)
    print("【演示2】机械臂运动控制 — 6个核心元API")
    print("=" * 60)

    # API 1: 飞到目标位姿
    print("\n[1] move_to_pose(0.15, 0.05, 0.20)")
    print("    机械臂末端飞到坐标(0.15, 0.05, 0.20)")
    robot.move_to_pose(0.15, 0.05, 0.20)

    # API 2: 张开夹爪
    print("\n[2] open_gripper(0.08)")
    print("    张开夹爪到8cm宽度")
    robot.open_gripper(0.08)

    # API 3: 下降到物体位置
    print("\n[3] move_to_pose(0.15, 0.05, 0.03)")
    print("    末端下降到物体表面(0.03m)")
    robot.move_to_pose(0.15, 0.05, 0.03)

    # API 4: 闭合夹爪抓取
    print("\n[4] close_gripper(5.0)")
    print("    用5N力闭合夹爪抓取物体")
    robot.close_gripper(5.0)

    # API 5: 验证抓取成功
    print("\n[5] verify_grasp(0.5)")
    print("    检查夹爪力是否>=0.5N，确认抓稳了")
    ok = robot.verify_grasp(0.5)
    print(f"    抓取结果: {'成功' if ok else '失败'}")

    # API 6: 抬升
    print("\n[6] move_to_pose(0.15, 0.05, 0.20)")
    print("    抬升回安全高度")
    robot.move_to_pose(0.15, 0.05, 0.20)

    print("\n全部6个API调用完成!")


def demo_safety():
    """演示3: 安全断言 — 越界操作会被自动拦截"""
    print("\n" + "=" * 60)
    print("【演示3】安全断言 — 危险操作自动拦截")
    print("=" * 60)
    robot = ExecutionWrapper()
    print("\n  尝试让机械臂撞桌面: move_to_pose(0, 0, 0.005)")
    print("  (Z=0.005m 低于安全高度 0.02m)")
    try:
        robot.move_to_pose(0, 0, 0.005)
        print("  [FAIL] 不应该到这里 — 安全断言失效了!")
    except AssertionError as e:
        print("  [OK] 安全断言拦截成功! 机械臂没有运动.")
        print(f"  拦截原因: Z轴高度 0.005m < 安全线 0.02m")


def demo_code_loader_flow():
    """演示4: 策略代码执行流程 — 队友B给的代码怎么跑"""
    print("\n" + "=" * 60)
    print("【演示4】策略代码执行流程 (code_loader)")
    print("  展示队友B生成的策略代码如何被执行")
    print("=" * 60)

    # 同学B生成的策略代码（字符串）
    strategy_code = '''
def task_main():
    """队友B生成的抓取策略"""
    objects = get_scene_objects()
    # 找红色物体
    target = None
    for obj in objects:
        if obj.color and "FF0000" in obj.color:
            target = obj
            break
    if not target:
        return {"status": "failed", "reason": "no red object"}

    px, py, pz = target.position
    safe_z = max(pz + 0.15, 0.02)

    move_to_pose(px, py, safe_z)
    open_gripper(0.08)
    move_to_pose(px, py, pz + 0.003)
    close_gripper(5.0)

    if verify_grasp(0.5):
        move_to_pose(px, py, safe_z)
        move_to_pose(0.2, 0.0, safe_z)
        move_to_pose(0.2, 0.0, 0.03)
        open_gripper(0.08)
        return {"status": "success"}
    return {"status": "failed", "reason": "grasp failed"}
'''
    print("\n  队友B传入的代码(前5行):")
    for line in strategy_code.strip().split('\n')[:6]:
        print(f"    {line}")
    print("    ...")

    # 执行
    from isaac.code_loader import execute_strategy_code
    robot = ExecutionWrapper()
    result = execute_strategy_code(strategy_code, robot, get_scene_objects)
    print(f"\n  执行结果: {result['message']}")
    if result['result']:
        print(f"  返回值: {result['result']}")
    print(f"  是否成功: {result['success']}")


if __name__ == "__main__":
    # 演示1: 看场景
    objects = demo_scene_perception()

    # 演示2: 操作机械臂
    robot = ExecutionWrapper()
    demo_robot_motion(robot)

    # 演示3: 安全机制
    demo_safety()

    # 演示4: 代码执行流程
    demo_code_loader_flow()

    print("\n" + "=" * 60)
    print("  全部4项演示完成!")
    print("  如果你想在Isaac Sim 3D里看到真实效果:")
    print("    set OMNI_KIT_ACCEPT_EULA=YES")
    print("    isaacsim.exe --exec src/isaac/run_simulation.py")
    print("=" * 60)
