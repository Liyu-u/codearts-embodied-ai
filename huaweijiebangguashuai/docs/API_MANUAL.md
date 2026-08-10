# 元 API 使用手册 v1.0

> 同学 C（吴昌庆）| 2026-07-17 | 适用于队友 A、B、D

---

## 一、你可以用哪些 API

| 元 API | 说明 | 参数 | 返回值 |
|---|---|---|---|
| `get_scene_objects()` | 获取场景中所有可抓取物体 | 无 | `List[SceneObject]` |
| `move_to_pose(x, y, z, roll, pitch, yaw)` | 末端执行器飞到目标 6D 位姿 | xyz 单位米, 姿态角度 | `bool` |
| `move_joints(joint_angles)` | 直接驱动 7 个关节 | 7 元素 float 列表 | `bool` |
| `move_linear(dx, dy, dz, speed)` | 笛卡尔直线运动 | 位移量+速度(m/s) | `bool` |
| `open_gripper(width)` | 张开夹爪 | 开度 0.0~0.1m | `bool` |
| `close_gripper(force)` | 闭合夹爪抓取 | 力 0~10N | `bool` |
| `check_collision(x, y, z)` | 预判目标位姿是否碰撞 | 目标坐标 | `bool` (True=安全) |
| `verify_grasp(threshold)` | 力反馈确认是否抓稳 | 力阈值 N | `bool` (True=抓住) |
| `get_robot_state()` | 获取机械臂当前状态 | 无 | `RobotState` |

### SceneObject 结构 (perception_observation v1.0.0)

```python
SceneObject:
    .object_id            # "obj_001"
    .name                 # "红色方块" (向后兼容)
    .category_candidates  # [Candidate("cube", 0.95), ...]  类别+置信度
    .pose                 # Pose(position, orientation)
      .position           # Position3D(x, y, z)  单位: 米
      .orientation        # Orientation(x, y, z, w)  四元数
    .geometry             # Geometry(type="oriented_bbox_3d", size_3d=(w,h,d))
    .appearance           # Appearance(color/shape/texture_candidates)
      .color_candidates   # [Candidate("red", 0.95), ...]
      .shape_candidates   # [Candidate("cubic", 0.92), ...]
      .texture_candidates # [Candidate("matte", 0.80), ...]
    .tracking             # Tracking(track_age_frames, velocity, velocity_confidence)

# 向后兼容属性（策略代码仍可使用）:
    .position  # → (x, y, z) 元组
    .bbox      # → (w, h, d) 元组
    .color     # → 最高置信度颜色名 "red"
    .label     # → 最高置信度类别名 "cube"
```

### HTTP 返回的完整 JSON 格式

见 [docs/samples/scene_state_sample.json](docs/samples/scene_state_sample.json)

### 安全红线（评审判定依据）

| 约束 | 值 |
|---|---|
| Z 轴最低高度 | >= 0.02m |
| 关节角度范围 | [-2.9, 2.9] rad |
| 夹爪力范围 | (0, 10.0] N |
| 夹爪开度范围 | [0.0, 0.1] m |
| 直线运动速度 | <= 0.1 m/s |

---

## 二、队友 A（意图解析）怎么用

### HTTP 方式
```
GET http://<服务器IP>:8000/api/scene/current
```

返回格式见 [docs/samples/scene_state_sample.json](docs/samples/scene_state_sample.json) — perception_observation v1.0.0。

**用途**：拿到场景中所有物体的 6D 位姿 + 分类/颜色/形状候选列表（带置信度），匹配用户口语指令中的目标物体。

---

## 三、队友 B（CodeArts 策略生成）怎么用

### 3.1 你可以用哪些函数

CodeArts 生成的 Python 代码中可直接调用以下函数（已注入命名空间，无需 import）：

#### 元 API（底层原子操作）

| 元 API | 说明 |
|---|---|
| `move_to_pose(x, y, z, roll=0, pitch=0, yaw=0)` | 末端飞到 6D 位姿 |
| `move_joints(joint_angles)` | 直接驱动 7 关节 |
| `move_linear(dx, dy, dz, speed)` | 笛卡尔直线运动 |
| `open_gripper(width)` | 张开夹爪 (0.0~0.1m) |
| `close_gripper(force)` | 闭合夹爪 (0~10N) |
| `get_scene_objects()` | 获取场景所有物体 |
| `get_robot_state()` | 获取机械臂状态 |
| `check_collision(x, y, z)` | 碰撞预判 |
| `verify_grasp(threshold)` | 验证是否抓稳 |

#### 动作库（高层封装动作，推荐优先使用）

| 动作 | 说明 | 示例 |
|---|---|---|
| `pick_up(robot, target)` | 完整抓取流程 | `pick_up(robot, obj)` |
| `place_at(robot, x, y, z)` | 放置到坐标 | `place_at(robot, 0.2, 0.0, 0.03)` |
| `pick_and_place(robot, target, x, y, z)` | 抓取+放置 | `pick_and_place(robot, obj, 0.2, 0.0)` |
| `move_home(robot)` | 回初始位姿 | `move_home(robot)` |
| `approach_safely(robot, x, y, z)` | 安全检查后接近 | `approach_safely(robot, 0.1, 0.1, 0.05)` |
| `retreat_safely(robot)` | 退回安全高度 | `retreat_safely(robot)` |
| `push(robot, target, dx, dy)` | 推动物体 | `push(robot, obj, 0.05, 0.0)` |
| `stack(robot, top, bottom)` | 堆叠物体 | `stack(robot, block_a, block_b)` |
| `find_object(color, category, name)` | 查找目标物体 | `find_object(color="red")` |
| `scan_table(robot)` | 扫描桌面 | `scan_table(robot)` |
| `sort_by_color(robot, color, zone)` | 按颜色分类 | `sort_by_color(robot, "blue", (0.2,0,0.03))` |

#### 安全常量

| 常量 | 值 | 说明 |
|---|---|---|
| `SAFE_Z` | 0.20 | 安全抬升高度 (m) |
| `PLACE_Z` | 0.03 | 放置高度 (m) |
| `DEFAULT_FORCE` | 5.0 | 默认夹爪力 (N) |
| `DEFAULT_WIDTH` | 0.08 | 默认夹爪开度 (m) |

### 3.2 代码要求

1. **必须有 `def task_main():` 入口函数**
2. **函数内部调用上述 API**（不要 import os/sys 等危险模块）
3. **返回值必须是 dict**: `{"status": "success"}` 或 `{"status": "failed", "reason": "..."}`
4. **第一个参数是 robot**（动作库函数）

### 3.3 代码示例

#### 示例 1：用动作库（推荐，3 行搞定）

```python
def task_main():
    target = find_object(color="red")
    if not target:
        return {"status": "failed", "reason": "no red object"}
    return pick_and_place(robot, target, 0.2, 0.0)
```

#### 示例 2：用元 API（精细控制）

```python
def task_main():
    objects = get_scene_objects()
    target = None
    for obj in objects:
        if obj.color and "red" in obj.color:
            target = obj
            break

    if target is None:
        return {"status": "failed", "reason": "no red object"}

    px, py, pz = target.pose.position.x, target.pose.position.y, target.pose.position.z
    safe_z = max(pz + 0.15, 0.02)

    move_to_pose(px, py, safe_z)
    open_gripper(0.08)
    move_to_pose(px, py, pz + 0.003)
    close_gripper(5.0)

    if not verify_grasp(0.5):
        return {"status": "failed", "reason": "grasp failed"}

    move_to_pose(px, py, safe_z)
    move_to_pose(0.2, 0.0, safe_z)
    move_to_pose(0.2, 0.0, 0.03)
    open_gripper(0.08)
    return {"status": "success"}
```

#### 示例 3：多种复杂动作

```python
def task_main():
    # 按颜色分类
    result = sort_by_color(robot, "blue", (0.2, -0.1, 0.03))
    if result["status"] != "success":
        return result

    # 堆叠两个方块
    red_block = find_object(color="red")
    green_cyl = find_object(color="green")
    if red_block and green_cyl:
        return stack(robot, red_block, green_cyl)

    # 推动三角
    triangle = find_object(name_contains="三角")
    if triangle:
        result = push(robot, triangle, 0.05, 0.0)
        return result

    move_home(robot)
    return {"status": "success"}
```

### 提交方式

**方式1: HTTP POST**
```
POST http://<服务器IP>:8000/api/code/execute
Content-Type: application/json

{"code": "def task_main():\n    ..."}
```

**方式2: Python 直接调用**
```python
from isaac.code_loader import execute_strategy_code
from isaac.exec_wrapper import ExecutionWrapper

robot = ExecutionWrapper()
result = execute_strategy_code(your_code_string, robot)
# result = {"success": True, "message": "策略执行成功", "result": {...}}
```

---

## 四、队友 D（监控探针）怎么用

```python
from monitor.trace_probe import TraceProbe

probe = TraceProbe(task_id="task-001")

# 在 exec_wrapper 的每个运动函数中插入探针
probe.record("COLLISION_DETECTED", "路径与蓝色杯子干涉!",
             position={"x": 0.02, "y": -0.03, "z": 0.04},
             joints=[0.1, -0.6, 0.2, -1.5, 0.0, 0.8, 0.3])

if probe.has_errors():
    probe.dump_error_report()  # 生成 logs/error_report_task-001.json
```

### HTTP 接口
```
GET http://<服务器IP>:8000/api/monitor/errors
```
返回最近 10 条异常报告。

---

## 五、你自己（同学 C）怎么用

### 启动服务器
```bash
cd huaweijiebangguashuai
python src/backend/server.py
```
然后打开 http://localhost:8000/docs 查看 Swagger 交互式文档。

### 跑完整 Demo
```bash
python demo_api_usage.py
```

### 跑单元测试
```bash
python -m pytest tests/ -v   # 55 tests, 0.08s
```

### 在 Isaac Sim 3D 中运行（需要 conda isaacsim 环境）
```bash
set OMNI_KIT_ACCEPT_EULA=YES
isaacsim.exe --/app/headless=true --/renderer/type=fabric --exec src/isaac/run_simulation.py
```

### 代码结构
```
src/isaac/
  exec_wrapper.py    # 机械臂底层执行器（双模式: Kit/Mock）
  get_scene_json.py  # 场景感知（双模式, perception_observation v1.0.0）
  action_library.py  # 常见动作封装库（12 个高层动作）
  code_loader.py     # 策略代码加载器（注入元API+动作库）
  scene_builder.py   # 三标准测试场景构建器（Kit/Mock 双模式）
  run_simulation.py  # Isaac Sim 仿真入口（支持 --scene 参数）
  scenes/            # 标准测试场景文件 (.usda)
    stacking_cubes.usda   # 场景1: 3方块堆叠
    cup_lineup.usda       # 场景2: 5杯子排列
    color_sorting.usda    # 场景3: 6方块颜色分类
src/agent/
  code_validator.py  # 代码安全校验（三层防护）
src/backend/
  server.py          # FastAPI 中转服务器
src/monitor/
  trace_probe.py     # 运行时探针（闭环反馈）
```
