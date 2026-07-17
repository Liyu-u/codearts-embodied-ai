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

### SceneObject 结构

```python
SceneObject:
    .name      # "红色方块"
    .position  # (0.15, 0.05, 0.03)  单位: 米
    .bbox      # (0.04, 0.04, 0.04)  宽 x 高 x 深
    .color     # "#FF0000"
    .label     # "cube"
```

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

返回示例：
```json
{
  "scene_id": "scene-21cfdc01",
  "timestamp": "2026-07-17T15:24:14",
  "objects": [
    {
      "name": "红色方块",
      "position": {"x": 0.15, "y": 0.05, "z": 0.03},
      "bbox": {"width": 0.04, "height": 0.04, "depth": 0.04},
      "color": "#FF0000",
      "label": "cube"
    }
  ]
}
```

**用途**：拿到场景中所有物体列表，匹配用户口语指令中的目标物体（"红色方块""蓝色杯子"等）。

---

## 三、队友 B（CodeArts 策略生成）怎么用

你生成的 Python 代码需要满足：

1. **必须有 `def task_main():` 入口函数**
2. **函数内部调用上面的元 API**（不要 import os/sys 等危险模块）
3. **返回值必须是 dict**: `{"status": "success"}` 或 `{"status": "failed", "reason": "..."}`

### 代码示例（抓取红色方块并放置）

```python
def task_main():
    objects = get_scene_objects()
    target = None
    for obj in objects:
        if "红色" in obj.name:
            target = obj
            break

    if target is None:
        return {"status": "failed", "reason": "no red object"}

    px, py, pz = target.position
    safe_z = max(pz + 0.15, 0.02)   # 安全高度

    move_to_pose(px, py, safe_z)     # 飞到物体上方
    open_gripper(0.08)               # 张开夹爪
    move_to_pose(px, py, pz + 0.003) # 下降抓取
    close_gripper(5.0)               # 5N力闭合

    if not verify_grasp(0.5):
        return {"status": "failed", "reason": "grasp failed"}

    move_to_pose(px, py, safe_z)     # 抬升
    move_to_pose(0.2, 0.0, safe_z)   # 平移到目标
    move_to_pose(0.2, 0.0, 0.03)     # 放置
    open_gripper(0.08)
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
  get_scene_json.py  # 场景感知（双模式）
  code_loader.py     # 策略代码加载器
  run_simulation.py  # Isaac Sim 仿真入口
src/agent/
  code_validator.py  # 代码安全校验（三层防护）
src/backend/
  server.py          # FastAPI 中转服务器
src/monitor/
  trace_probe.py     # 运行时探针（闭环反馈）
```
