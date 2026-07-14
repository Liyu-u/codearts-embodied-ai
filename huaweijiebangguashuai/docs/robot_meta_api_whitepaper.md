# 🤖 机器人元 API 白皮书与函数说明书 v1.0

> **主笔**: 同学 C (吴昌庆) | **协定**: 同学 B
> **用途**: 定义 Isaac Sim 6.0.1 + Franka Panda 机械臂的全部元 API 契约
> **原则**: 此乃技术宪法，敲定后不得私自修改

---

## 一、核心 API 函数表 (8 个核心动词)

### 1.1 感知类 (Perception)

| # | 函数签名 | 返回值 | 说明 |
|---|---|---|---|
| P1 | `get_scene_objects()` | `List[SceneObject]` | 获取所有物体名称、3D 坐标、BBox 尺寸 |
| P2 | `get_robot_state()` | `RobotState` | 获取 Franka Panda 7DOF 关节角 + 末端 6D 位姿 |
| P3 | `get_gripper_state()` | `GripperState` | 获取夹爪宽度 (m) 和力反馈 (N) |

### 1.2 运动控制类 (Motion Control)

| # | 函数签名 | 参数类型 | 返回值 | 说明 |
|---|---|---|---|---|
| M1 | `move_to_pose(x, y, z, roll, pitch, yaw)` | `float×6` | `bool` | **IK 解算** 驱动末端到目标 6D 位姿 |
| M2 | `move_joints(joint_angles)` | `List[float]×7` | `bool` | 直接驱动 7 关节角 |
| M3 | `open_gripper(width)` | `float` (0.0~0.1) | `bool` | 张开夹爪 |
| M4 | `close_gripper(force)` | `float` (0.1~10.0) | `bool` | 闭合夹爪并施加抓取力 |
| M5 | `move_linear(dx, dy, dz, speed)` | `float×4` | `bool` | 笛卡尔直线运动 (不改变姿态) |

### 1.3 逻辑判断类 (Logic)

| # | 函数签名 | 参数 | 返回值 | 说明 |
|---|---|---|---|---|
| L1 | `check_collision(pose)` | `Pose` | `bool` | 预判目标位姿是否碰撞 |
| L2 | `verify_grasp(threshold)` | `float` | `bool` | 力反馈判断是否抓住物体 |

---

## 二、坐标系统

| 项目 | 规范 |
|---|---|
| 原点 | 桌面中心 |
| X | 长边方向 (前→后) |
| Y | 短边方向 (左→右) |
| Z | 垂直桌面向上 |
| 单位 | 米 (m)，保留 4 位小数 |
| 姿态 | 弧度 (rad)，[-π, π] |

---

## 三、物理安全红线

```python
# === 必须内置到 exec_wrapper.py 的断言 ===
assert z >= 0.02,      f"[SAFETY] Z={z:.4f} < 0.02m 最低安全高度!"
assert -2.9 <= a <= 2.9 for a in joints, f"[SAFETY] 关节角度超出限位!"
assert 0.0 < force <= 10.0, f"[SAFETY] 夹爪力 {force}N 超出允许范围!"
```

---

## 四、数据结构

```python
@dataclass
class SceneObject:
    name: str              # 物体名称
    position: (x, y, z)    # 世界坐标 (m)
    bbox: (w, h, d)        # Bounding Box (m)
    color: str | None      # 颜色标签
    label: str | None      # 语义标签
```

---

## 五、版本记录

| 日期 | 版本 | 变更人 | 变更说明 |
|---|---|---|---|
| 2026-07-14 | v1.0 | 同学 C | 初始发布: 8 核心动词 + 3 感知 + 2 逻辑 + 安全红线 |
