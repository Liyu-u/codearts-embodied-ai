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
    print("  返回 perception_observation v1.0.0 格式")
    print("=" * 60)
    objects = get_scene_objects()
    print(f"\n  发现 {len(objects)} 个物体:\n")
    for i, obj in enumerate(objects, 1):
        top_cat = obj.category_candidates[0]
        top_color = obj.appearance.color_candidates[0] if obj.appearance.color_candidates else None
        print(f"  [{i}] {obj.name} (id={obj.object_id})")
        print(f"      类别: {top_cat.name} (置信度 {top_cat.score:.0%})")
        print(f"      位置: x={obj.pose.position.x:.4f}m  y={obj.pose.position.y:.4f}m  z={obj.pose.position.z:.4f}m")
        print(f"      朝向: qx={obj.pose.orientation.x:.2f} qy={obj.pose.orientation.y:.2f} qz={obj.pose.orientation.z:.2f} qw={obj.pose.orientation.w:.2f}")
        g = obj.geometry
        s = g.size_3d
        print(f"      尺寸: 宽{s[0]:.4f}m x 高{s[1]:.4f}m x 深{s[2]:.4f}m")
        if top_color:
            print(f"      颜色: {top_color.name} (置信度 {top_color.score:.0%})")
        print(f"      追踪: {obj.tracking.track_age_frames}帧, 速度置信度 {obj.tracking.velocity_confidence:.0%}")
        print()
    return objects


def demo_robot_motion(robot):
    """演示2: 机械臂运动控制 — 用元API操作Franka Panda"""
    print("=" * 60)
    print("【演示2】机械臂运动控制 — 6个核心元API")
    print("=" * 60)

    print("\n[1] move_to_pose(0.15, 0.05, 0.20)")
    print("    机械臂末端飞到坐标(0.15, 0.05, 0.20)")
    robot.move_to_pose(0.15, 0.05, 0.20)

    print("\n[2] open_gripper(0.08)")
    print("    张开夹爪到8cm宽度")
    robot.open_gripper(0.08)

    print("\n[3] move_to_pose(0.15, 0.05, 0.03)")
    print("    末端下降到物体表面(0.03m)")
    robot.move_to_pose(0.15, 0.05, 0.03)

    print("\n[4] close_gripper(5.0)")
    print("    用5N力闭合夹爪抓取物体")
    robot.close_gripper(5.0)

    print("\n[5] verify_grasp(0.5)")
    print("    检查夹爪力是否>=0.5N，确认抓稳了")
    ok = robot.verify_grasp(0.5)
    print(f"    抓取结果: {'成功' if ok else '失败'}")

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

    # 队友B的策略代码 — 使用向后兼容属性 (obj.position, obj.name, obj.color)
    strategy_code = '''
def task_main():
    """队友B生成的抓取策略"""
    objects = get_scene_objects()
    target = None
    for obj in objects:
        # 用向后兼容的 .color 属性匹配
        if obj.color and "red" in obj.color:
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
    print("\n  队友B传入的策略代码(前5行):")
    for line in strategy_code.strip().split('\n')[:6]:
        print(f"    {line}")
    print("    ...")

    from isaac.code_loader import execute_strategy_code
    robot = ExecutionWrapper()
    result = execute_strategy_code(strategy_code, robot, get_scene_objects)
    print(f"\n  执行结果: {result['message']}")
    if result['result']:
        print(f"  返回值: {result['result']}")
    print(f"  是否成功: {result['success']}")


def demo_json_export():
    """演示5: JSON 导出 — 感知结果导出为 perception_observation 格式"""
    print("\n" + "=" * 60)
    print("【演示5】场景状态 JSON 导出")
    print("  perception_observation v1.0.0 格式")
    print("=" * 60)
    from isaac.get_scene_json import export_scene_state
    scene = export_scene_state(log_dir="logs")
    print(f"\n  格式版本: {scene['schema_version']}")
    print(f"  消息类型: {scene['message_type']}")
    print(f"  观测ID:   {scene['observation_id']}")
    print(f"  场景ID:   {scene['scene_id']}")
    print(f"  物体数:   {len(scene['objects'])}")
    print(f"  坐标系:   {scene['coordinate_system']}")
    print(f"  真值物体数: {len(scene['simulation_metadata']['ground_truth_objects'])}")
    print(f"\n  完整 JSON 已导出至: logs/scene_state.json")


if __name__ == "__main__":
    # 演示1: 看场景（新格式）
    objects = demo_scene_perception()

    # 演示2: 操作机械臂
    robot = ExecutionWrapper()
    demo_robot_motion(robot)

    # 演示3: 安全机制
    demo_safety()

    # 演示4: 代码执行流程
    demo_code_loader_flow()

    # 演示5: JSON 导出
    demo_json_export()

    print("\n" + "=" * 60)
    print("  全部5项演示完成!")
    print("  如果你想在Isaac Sim 3D里看到真实效果:")
    print("    set OMNI_KIT_ACCEPT_EULA=YES")
    print("    isaacsim.exe --exec src/isaac/run_simulation.py")
    print("=" * 60)
