# 机器人元 API 白皮书与函数说明书
> 同学 C & B 联合编写 — 物理仿真与元 API 底层实现规范

---

## 1. 概述

本文档定义了 Isaac Sim 环境下机械臂操作的元 API（Meta API）层，是上层策略代码生成的底层契约。

---

## 2. 核心 API 函数

### 2.1 感知类 (Perception)

| 函数签名 | 返回值 | 说明 |
|---|---|---|
| `get_scene_objects()` | `List[SceneObject]` | 获取场景中所有物体的 3D 坐标与 Bounding Box |
| `get_robot_state()` | `RobotState` | 获取当前机械臂关节角度、末端执行器位姿 |
| `get_gripper_state()` | `GripperState` | 获取夹爪开合状态与力反馈 |

### 2.2 运动控制类 (Motion Control)

| 函数签名 | 返回值 | 说明 |
|---|---|---|
| `move_to_pose(x, y, z, roll, pitch, yaw)` | `bool` | 逆运动学解算并执行到位 |
| `move_joints(joint_angles)` | `bool` | 直接关节空间运动 |
| `open_gripper(width)` | `bool` | 张开夹爪到指定宽度 |
| `close_gripper(force)` | `bool` | 闭合夹爪，施加指定力 |

### 2.3 安全断言

所有底层执行需满足：
- **末端执行器 Z 轴安全高度**: `assert z >= 0.02` (距桌面最低 2cm)
- **关节限位检查**: 每次运动前校验关节角度在硬件限位内
- **碰撞检测**: 路径规划时检查与场景物体的碰撞

---
## 3. 坐标系统

- 世界坐标系原点：桌面中心
- X 轴：桌面长边方向
- Y 轴：桌面短边方向
- Z 轴：垂直桌面向上
- 单位：米 (m)

---
## 4. 更新日志

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-07-14 | v1.0 | 初始版本，定义核心 API |
