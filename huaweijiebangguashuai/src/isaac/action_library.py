"""
常见动作封装库 — 队友 B (CodeArts 策略生成) 可直接调用
同学 C（吴昌庆）封装

所有高层动作基于 9 个元 API 组合而成，内含完整安全断言。
队友 B 生成的策略代码可以直接调用这些函数，无需手写抓取流程。
"""

from typing import Any, Dict, List, Optional, Tuple

from isaac.get_scene_json import SceneObject, get_scene_objects

# ============================================================
# 安全常量
# ============================================================
SAFE_Z = 0.20          # 默认安全抬升高度 (m)
PLACE_Z = 0.03         # 放置高度 (m)
APPROACH_OFFSET = 0.15 # 物体上方预接近偏移 (m)
GRASP_OFFSET = 0.003   # 抓取时末端低于物体顶部的补偿 (m)
DEFAULT_FORCE = 5.0    # 默认抓取力 (N)
DEFAULT_WIDTH = 0.08   # 默认夹爪开度 (m)
DEFAULT_SPEED = 0.05   # 默认直线速度 (m/s)


# ============================================================
# 单步动作
# ============================================================
def pick_up(
    robot,
    target: SceneObject,
    safe_z: float = SAFE_Z,
    grasp_force: float = DEFAULT_FORCE,
) -> Dict[str, Any]:
    """
    抓取单个物体（完整流程：接近 → 张开 → 下降 → 抓取 → 验证 → 抬升）

    Args:
        robot: ExecutionWrapper 实例
        target: 要抓取的 SceneObject
        safe_z: 安全抬升高度
        grasp_force: 夹爪力 (N)

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    px, py, pz = target.pose.position.x, target.pose.position.y, target.pose.position.z
    approach_z = max(pz + APPROACH_OFFSET, 0.02)

    # 1. 飞到物体上方
    if not robot.move_to_pose(px, py, approach_z):
        return {"status": "failed", "reason": "move_to_pose approach failed"}

    # 2. 张开夹爪
    if not robot.open_gripper(DEFAULT_WIDTH):
        return {"status": "failed", "reason": "open_gripper failed"}

    # 3. 下降到抓取高度
    grasp_z = max(pz + GRASP_OFFSET, 0.02)
    if not robot.move_to_pose(px, py, grasp_z):
        return {"status": "failed", "reason": "move_to_pose descend failed"}

    # 4. 闭合夹爪
    if not robot.close_gripper(grasp_force):
        return {"status": "failed", "reason": "close_gripper failed"}

    # 5. 验证抓稳
    if not robot.verify_grasp(0.5):
        return {"status": "failed", "reason": "grasp verification failed — object slipped"}

    # 6. 抬升回安全高度
    if not robot.move_to_pose(px, py, safe_z):
        return {"status": "failed", "reason": "move_to_pose retreat failed"}

    return {"status": "success"}


def place_at(
    robot,
    x: float,
    y: float,
    z: float = PLACE_Z,
    safe_z: float = SAFE_Z,
) -> Dict[str, Any]:
    """
    放置物体到指定坐标。

    Args:
        robot: ExecutionWrapper 实例
        x, y, z: 放置目标坐标
        safe_z: 接近时的安全高度

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    place_z = max(z, 0.02)

    # 1. 飞到目标上方
    if not robot.move_to_pose(x, y, safe_z):
        return {"status": "failed", "reason": "move_to_pose approach failed"}

    # 2. 下降到放置高度
    if not robot.move_to_pose(x, y, place_z):
        return {"status": "failed", "reason": "move_to_pose descend failed"}

    # 3. 张开夹爪释放
    if not robot.open_gripper(DEFAULT_WIDTH):
        return {"status": "failed", "reason": "open_gripper failed"}

    # 4. 抬升离开
    if not robot.move_to_pose(x, y, safe_z):
        return {"status": "failed", "reason": "move_to_pose retreat failed"}

    return {"status": "success"}


def pick_and_place(
    robot,
    target: SceneObject,
    dest_x: float,
    dest_y: float,
    dest_z: float = PLACE_Z,
) -> Dict[str, Any]:
    """
    抓取物体并放置到指定位置（最常用的复合动作）。

    Args:
        robot: ExecutionWrapper 实例
        target: 要抓取的目标物体
        dest_x, dest_y, dest_z: 放置坐标

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    # 抓取
    result = pick_up(robot, target)
    if result["status"] != "success":
        result["reason"] = f"Pick failed: {result['reason']}"
        return result

    # 放置
    result = place_at(robot, dest_x, dest_y, dest_z)
    if result["status"] != "success":
        result["reason"] = f"Place failed: {result['reason']}"
        return result

    return {"status": "success"}


def move_home(robot) -> Dict[str, Any]:
    """
    机械臂回到安全初始位姿（关节空间运动）。

    Args:
        robot: ExecutionWrapper 实例

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    home_joints = [0.0, -0.5, 0.0, -1.2, 0.0, 1.0, 0.5]
    if not robot.move_joints(home_joints):
        return {"status": "failed", "reason": "move_joints home failed"}
    return {"status": "success"}


def push(
    robot,
    target: SceneObject,
    dx: float,
    dy: float,
    push_z: float = 0.025,
    speed: float = DEFAULT_SPEED,
) -> Dict[str, Any]:
    """
    沿水平方向推动物体。

    Args:
        robot: ExecutionWrapper 实例
        target: 要推动的物体
        dx, dy: 推动方向（桌面坐标系）
        push_z: 推压高度（应低于物体质心）
        speed: 移动速度

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    px, py, _ = target.pose.position.x, target.pose.position.y, target.pose.position.z
    approach_z = max(push_z + 0.10, SAFE_Z)
    push_z = max(push_z, 0.02)

    # 1. 飞到推动侧上方
    if not robot.move_to_pose(px, py, approach_z):
        return {"status": "failed", "reason": "approach failed"}

    # 2. 下降到推动高度
    if not robot.move_to_pose(px, py, push_z):
        return {"status": "failed", "reason": "descend failed"}

    # 3. 直线推动
    if not robot.move_linear(dx, dy, 0.0, speed):
        return {"status": "failed", "reason": "move_linear push failed"}

    # 4. 抬升
    if not robot.move_to_pose(px + dx, py + dy, approach_z):
        return {"status": "failed", "reason": "retreat failed"}

    return {"status": "success"}


def stack(
    robot,
    top_obj: SceneObject,
    bottom_obj: SceneObject,
) -> Dict[str, Any]:
    """
    将 top_obj 堆叠到 bottom_obj 上面。

    Args:
        robot: ExecutionWrapper 实例
        top_obj: 要堆放到上方的物体
        bottom_obj: 底层物体

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    bx, by, bz = bottom_obj.pose.position.x, bottom_obj.pose.position.y, bottom_obj.pose.position.z
    bot_h = bottom_obj.geometry.size_3d[1] if bottom_obj.geometry.size_3d else 0.04
    top_h = top_obj.geometry.size_3d[1] if top_obj.geometry.size_3d else 0.04
    stack_z = max(bz + bot_h / 2 + top_h / 2 + 0.002, 0.02)

    return pick_and_place(robot, top_obj, bx, by, stack_z)


def sort_by_color(
    robot,
    target_color: str,
    drop_zone: Tuple[float, float, float],
) -> Dict[str, Any]:
    """
    按颜色分类：找到所有指定颜色的物体，依次搬运到 drop_zone。

    Args:
        robot: ExecutionWrapper 实例
        target_color: 目标颜色名 (如 "red", "blue", "green")
        drop_zone: 放置区域坐标 (x, y, z)

    Returns:
        {"status": "success", "moved_count": N} 或 {"status": "failed", ...}
    """
    objects = get_scene_objects()
    matching = []
    for obj in objects:
        if obj.color and target_color in obj.color.lower():
            matching.append(obj)

    if not matching:
        return {"status": "failed", "reason": f"no {target_color} objects found"}

    dx, dy = 0.03, 0.0
    for i, obj in enumerate(matching):
        dest_x = drop_zone[0] + dx * i
        dest_y = drop_zone[1] + dy * i
        dest_z = max(drop_zone[2], 0.02)

        result = pick_and_place(robot, obj, dest_x, dest_y, dest_z)
        if result["status"] != "success":
            return {"status": "failed", "reason": f"failed on object {i+1}: {result['reason']}"}

    return {"status": "success", "moved_count": len(matching)}


def find_object(
    color: Optional[str] = None,
    category: Optional[str] = None,
    name_contains: Optional[str] = None,
) -> Optional[SceneObject]:
    """
    在场景中查找目标物体（按颜色/类别/名称匹配）。

    Args:
        color: 颜色名 (如 "red", "blue") — 匹配 appearance.color_candidates
        category: 类别名 (如 "cube", "cup") — 匹配 category_candidates
        name_contains: 名称子串 (如 "红色", "方块")

    Returns:
        找到的第一个匹配物体，未找到返回 None
    """
    objects = get_scene_objects()
    for obj in objects:
        if name_contains and name_contains in obj.name:
            return obj
        if color and obj.color and color.lower() in obj.color.lower():
            return obj
        if category and obj.label and category.lower() in obj.label.lower():
            return obj
    return None


def scan_table(
    robot,
    z: float = 0.15,
    step: float = 0.05,
) -> Dict[str, Any]:
    """
    扫描桌面：末端执行器在工作空间内做 S 形扫描，
    配合 get_scene_objects() 刷新环境感知。

    Args:
        robot: ExecutionWrapper 实例
        z: 扫描高度
        step: 步进间距

    Returns:
        {"status": "success", "objects_found": N}
    """
    safe_z = max(z, 0.02)
    waypoints = [
        (-0.15, -0.15, safe_z), (0.15, -0.15, safe_z),
        (0.15, 0.0, safe_z), (-0.15, 0.0, safe_z),
        (-0.15, 0.15, safe_z), (0.15, 0.15, safe_z),
    ]

    for wx, wy, wz in waypoints:
        if not robot.move_to_pose(wx, wy, wz):
            return {"status": "failed", "reason": f"scan failed at ({wx:.3f}, {wy:.3f})"}

    objects = get_scene_objects()
    return {"status": "success", "objects_found": len(objects)}


def approach_safely(
    robot,
    x: float,
    y: float,
    z: float,
) -> Dict[str, Any]:
    """
    安全检查后接近目标位姿。

    Args:
        robot: ExecutionWrapper 实例
        x, y, z: 目标坐标

    Returns:
        {"status": "success"} 或 {"status": "failed", "reason": "..."}
    """
    safe_z = max(z + APPROACH_OFFSET, SAFE_Z)

    # 碰撞预判
    if not robot.check_collision(x, y, z):
        return {"status": "failed", "reason": "collision risk detected"}

    # 先飞到上方
    if not robot.move_to_pose(x, y, safe_z):
        return {"status": "failed", "reason": "approach failed"}

    # 再降到目标
    if not robot.move_to_pose(x, y, max(z, 0.02)):
        return {"status": "failed", "reason": "final approach failed"}

    return {"status": "success"}


def retreat_safely(robot, safe_z: float = SAFE_Z) -> Dict[str, Any]:
    """
    安全退回到默认高度。

    Args:
        robot: ExecutionWrapper 实例
        safe_z: 安全高度

    Returns:
        {"status": "success"}
    """
    state = robot.get_robot_state()
    cx, cy, _ = state.end_effector_pose[0], state.end_effector_pose[1], state.end_effector_pose[2]
    robot.move_to_pose(cx, cy, safe_z)
    return {"status": "success"}


# ============================================================
# 动作清单（供 code_loader 注入命名空间）
# ============================================================
ACTION_LIBRARY = {
    # 基础抓取
    "pick_up": pick_up,
    "place_at": place_at,
    "pick_and_place": pick_and_place,
    # 移动
    "move_home": move_home,
    "approach_safely": approach_safely,
    "retreat_safely": retreat_safely,
    # 操作
    "push": push,
    "stack": stack,
    # 感知
    "find_object": find_object,
    "scan_table": scan_table,
    "sort_by_color": sort_by_color,
}

# 安全常量也暴露给策略代码
ACTION_CONSTANTS = {
    "SAFE_Z": SAFE_Z,
    "PLACE_Z": PLACE_Z,
    "DEFAULT_FORCE": DEFAULT_FORCE,
    "DEFAULT_WIDTH": DEFAULT_WIDTH,
    "DEFAULT_SPEED": DEFAULT_SPEED,
}


# ============================================================
# 自检
# ============================================================
if __name__ == "__main__":
    from isaac.exec_wrapper import ExecutionWrapper
    from isaac.get_scene_json import get_scene_objects

    print("=" * 60)
    print("动作封装库自检")
    print("=" * 60)

    robot = ExecutionWrapper()
    objects = get_scene_objects()

    # 1. find_object
    print("\n[1] find_object(color='red')")
    target = find_object(color="red")
    print(f"    结果: {target.name if target else 'Not found'}")

    # 2. pick_and_place
    if target:
        print(f"\n[2] pick_and_place({target.name}, dest=(0.2, 0.0))")
        result = pick_and_place(robot, target, 0.2, 0.0)
        print(f"    结果: {result}")

    # 3. scan_table
    print("\n[3] scan_table()")
    result = scan_table(robot)
    print(f"    结果: {result}")

    # 4. move_home
    print("\n[4] move_home()")
    result = move_home(robot)
    print(f"    结果: {result}")

    # 5. sort_by_color
    print("\n[5] sort_by_color('blue', drop_zone=(0.2, -0.1, 0.03))")
    result = sort_by_color(robot, "blue", (0.2, -0.1, 0.03))
    print(f"    结果: {result}")

    print("\n所有动作封装自检完成!")
