# Manual Test Cases — Embodied AI Intent Reasoning Hub

> **版本**: 2.0.0 | **日期**: 2026-07-19 | **总用例数**: 22

---

## 概述

本文档包含 22 个可执行功能测试用例，覆盖具身智能意图推理中枢的完整管线：

- **正常用例 (TC_001 – TC_008)**: 8 个主用例 + 2 个子用例，覆盖基础抓取、参数保真、安全截断、运算符约束、多对象消歧、运动物体、约束冲突
- **异常输入 (TC_009_01 – TC_009_09)**: 9 个异常输入用例
- **API 异常 (TC_010_01 – TC_010_06)**: 6 个 API/模型异常回退用例

每个用例均提供：
1. 用例编号和名称
2. 测试目的
3. 完整的环境感知 JSON（可直接复制粘贴）
4. 自然语言指令
5. 选择的规划引擎
6. 操作步骤
7. 预期页面结果
8. Developer JSON 关键断言
9. 失败时优先检查的模块

---

## 操作方法

### 通过 Gradio 页面测试

1. 启动 Gradio: `cd robot_intent_agent && python demo/web_ui.py`
2. 打开浏览器访问 `http://localhost:7860`
3. 从 **选择预设场景** 下拉框中选择测试用例
4. 下拉框会**同时填充**自然语言指令和环境感知 JSON
5. 选择规划引擎（默认 纯规则引擎）
6. 点击 **运行推理**
7. 对照下方的预期结果检查 6 个输出面板

### 通过自动化测试

```bash
cd robot_intent_agent
pytest tests/test_reasoning_cases.py -v
pytest tests/test_reasoning_cases.py -v -k "TC_003"
```

---

## 正常用例

---

### TC_001: 基础单物体抓取 — 塑料杯

**测试目的**: 验证基础目标绑定、属性推理和动作序列生成能力。

**环境感知 JSON**:

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc001",
  "scene_id": "scene_tc001",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_001",
      "category_candidates": [
        {"name": "container", "score": 0.93},
        {"name": "cup", "score": 0.04}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.12, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.07, "height": 0.10, "depth": 0.07}
      },
      "appearance": {
        "color": "white",
        "material": "plastic",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.96,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制环境感知 JSON 到输入框
2. 输入自然语言指令
3. 选择 纯规则引擎
4. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 意图理解 | 任务目标: container, 动作类型: 抓取并放置 |
| 物体属性推理 | 材料: plastic, 易碎等级: L1·较敏感, 推荐抓力: 2.0N, 硬安全上限: 8.0N, 可抓取: ✅ 是, 可移动: ✅ 是 |
| 安全约束裁决 | 状态: 无冲突, 硬性约束 >= 5 条 |
| RobotTaskIR | 技能: Reach, Grasp, MoveTo, Release, 约束: fragile=false |
| 动作序列 | 1. Reach 2. Grasp 3. MoveTo 4. Release |
| 决策链路 | NL_PARSE → SCENE_GROUNDING → MEMORY_RETRIEVAL → CONSTRAINT_REASONING → CONFLICT_RESOLUTION → TASK_COMPILATION |

**Developer JSON 关键断言**:
- `skills.Grasp.constraints.force.max_force_n.value` = 8.0
- `skills.Grasp.constraints.fragile` = false
- `task_metadata.raw_instruction` = "把杯子拿过来"
- `ir_version` = "3.0.0"

**失败时优先检查的模块**: `property_inference/property_mapper.py`, `scene_builder/semantic_scene_builder.py`, `planner/behavior_tree_generator.py`

---

### TC_002: 参数保真 — 盒子移动指定力度和速度

**测试目的**: 验证用户指定的抓力、速度参数不会在各模块间丢失。

**环境感知 JSON**:

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc002",
  "scene_id": "scene_tc002",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_box_001",
      "category_candidates": [
        {"name": "box", "score": 0.91},
        {"name": "container", "score": 0.05}
      ],
      "pose": {
        "position": {"x": 0.30, "y": 0.05, "z": 0.05},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.10, "height": 0.08, "depth": 0.10}
      },
      "appearance": {
        "color": "brown",
        "material": "cardboard",
        "texture": "rough"
      },
      "affordances": ["graspable", "movable"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.95,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `用5N力量把盒子以0.2m/s速度放到桌子上`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制 JSON 到环境感知输入框
2. 输入自然语言指令
3. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 意图理解 | 动作类型: 抓取并放置, 有目标物体和目的地 |
| 物体属性推理 | 材料: plastic (ontology: box→generic_container), max_force=8.0N |
| 安全约束裁决 | 用户原始要求: 5N, 最终执行: 5N (无冲突, 在安全范围内) |
| RobotTaskIR | Grasp.params.force_n.value = 5.0, MoveTo.params.velocity_ms.value = 0.2 |

**Developer JSON 关键断言**:
- `skills.Grasp.params.force_n.value` = 5.0
- `skills.MoveTo.params.velocity_ms.value` = 0.2
- `raw_requested_force` = 5.0
- `raw_requested_vel` = 0.2
- 参数不得在各模块间丢失

**失败时优先检查的模块**: `planner/behavior_tree_generator.py` (参数提取), `constraint/constraint_compiler.py`, `ir/ir_generator.py`

---

### TC_003: 玻璃杯高抓力安全截断

**测试目的**: 验证易碎物体推理、安全上限和 Min-Clamping 裁决。

**环境感知 JSON**:

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc003",
  "scene_id": "scene_tc003",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_001",
      "category_candidates": [
        {"name": "cup", "score": 0.93},
        {"name": "container", "score": 0.04}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.12, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.06, "height": 0.12, "depth": 0.06}
      },
      "appearance": {
        "color": "transparent",
        "material": "glass",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable", "fragile"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.96,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `快点！用50N力量把玻璃杯抓过来！`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 从下拉框选择 "TC_003: 易碎玻璃杯高抓力安全截断"
2. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 意图理解 | 任务目标: cup, 动作类型: 抓取并放置 |
| 物体属性推理 | 材料: glass, 易碎等级: L3·精密仪器 (或 L2·易碎), 硬安全上限: ≤3.0N, 可抓取: ✅ 是 |
| 安全约束裁决 | **用户原始要求: 50.0N** → 裁决链: 50N→10N(硬件上限)→3N(易碎上限) → **最终执行: ≤3.0N** |
| RobotTaskIR | 抓取动作为 GentleGrasp 或等效安全抓取, 约束: fragile=true, max_force_n ≤ 3.0N |
| 动作序列 | 1. Reach 2. GentleGrasp 3. MoveTo |
| 决策链路 | CONFLICT_RESOLUTION 节点必须出现 force_n 冲裁记录 |

**Developer JSON 关键断言**:
- `resolved_force` ≤ 3.0（不得显示 50N）
- `override_ledger` 包含 force_n 冲突记录: user_request=50N → clamped=≤3.0N
- `force_clamp.candidates` 包含 [50.0, 10.0, 2.0]
- `force_clamp.selected` ≤ 3.0
- `skills.GentleGrasp.constraints.force.max_force_n.value` ≤ 3.0
- `skills.GentleGrasp.constraints.fragile` = true
- 所有动作必须使用同一个 entity_id

**失败时优先检查的模块**: `property_inference/ontology/ontology_loader.py` (cup→glass_cup alias), `constraint/constraint_compiler.py` (min-clamping), `planner/behavior_tree_generator.py` (GentleGrasp 选择)

---

### TC_004: 运算符约束 — 不超过解析为 max 而非 exact

**测试目的**: 验证"不超过"关键词被正确解析为最大值约束，而非精确值要求。

**环境感知 JSON**: 同 TC_003（单玻璃杯，category="cup"）

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc004",
  "scene_id": "scene_tc004",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_001",
      "category_candidates": [
        {"name": "cup", "score": 0.93},
        {"name": "container", "score": 0.04}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.12, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.06, "height": 0.12, "depth": 0.06}
      },
      "appearance": {
        "color": "transparent",
        "material": "glass",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable", "fragile"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.96,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `用不超过2N的力量抓住杯子`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制 JSON 到输入框
2. 输入自然语言指令
3. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 安全约束裁决 | 用户原始要求: 2.0N, 最终执行: ≤2.0N |
| RobotTaskIR | max_force_n ≤ 2.0N (上限约束) |

**Developer JSON 关键断言**:
- `requested_force_n` = 2.0
- `resolved_force` ≤ 2.0
- "不超过" 不得被解释为"必须恰好 2.0N"
- 最终抓力可以在 [0.1, 2.0] 范围内

**失败时优先检查的模块**: `planner/behavior_tree_generator.py` (NL 解析), `constraint/constraint_compiler.py`

---

### TC_005: 目标、目的地和障碍物 — 角色不混淆

**测试目的**: 验证 target、destination 和 avoid_object 三个角色正确区分，不混淆。

**环境感知 JSON**:

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc005",
  "scene_id": "scene_tc005",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_001",
      "category_candidates": [
        {"name": "cup", "score": 0.93},
        {"name": "container", "score": 0.04}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.12, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.06, "height": 0.12, "depth": 0.06}
      },
      "appearance": {
        "color": "transparent",
        "material": "glass",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable", "fragile"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.96,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    },
    {
      "object_id": "obj_tray_001",
      "category_candidates": [
        {"name": "container", "score": 0.88},
        {"name": "tray", "score": 0.07}
      ],
      "pose": {
        "position": {"x": -0.20, "y": 0.25, "z": 0.02},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.30, "height": 0.02, "depth": 0.20}
      },
      "appearance": {
        "color": "gray",
        "material": "plastic",
        "texture": "smooth"
      },
      "affordances": ["movable"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.92,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    },
    {
      "object_id": "obj_box_001",
      "category_candidates": [
        {"name": "box", "score": 0.90},
        {"name": "wooden_block", "score": 0.05}
      ],
      "pose": {
        "position": {"x": 0.18, "y": 0.10, "z": 0.06},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.12, "height": 0.12, "depth": 0.12}
      },
      "appearance": {
        "color": "brown",
        "material": "cardboard",
        "texture": "rough"
      },
      "affordances": ["graspable", "movable"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.94,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [
    {
      "subject": "obj_cup_001",
      "predicate": "blocking",
      "object": "obj_box_001",
      "confidence": 0.85,
      "metadata": {"description": "box blocks path to cup"}
    }
  ],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `绕开盒子，把杯子放到托盘上`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制 JSON 到输入框
2. 输入自然语言指令
3. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 意图理解 | 规避物体: 盒子 |
| 安全约束裁决 | 碰撞避免约束包含 ob_box_001 或 盒子 |
| RobotTaskIR | 技能包含 PlanPath, constraints.avoid 包含 box |
| 动作序列 | 1. PlanPath 2. Reach 3. GentleGrasp 4. MoveTo 5. Release |

**Developer JSON 关键断言**:
- target ≠ destination ≠ avoid_object
- `skills.PlanPath.params.avoid_obstacles` 包含 "盒子"
- `avoid_objs` 列表非空
- PlanPath 必须在抓取之前执行
- 不能将障碍物当作目标

**失败时优先检查的模块**: `scene_builder/semantic_scene_builder.py` (relations/blocking), `planner/behavior_tree_generator.py` (avoid 提取), `ir/ir_generator.py`

---

### TC_006: 运动物体 — Motion Gate 与安全等待

**测试目的**: 验证运动物体检测、WaitUntilStable 插入和安全拒绝逻辑。

**环境感知 JSON**:

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc006",
  "scene_id": "scene_tc006",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_001",
      "category_candidates": [
        {"name": "cup", "score": 0.91},
        {"name": "container", "score": 0.05}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.12, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.06, "height": 0.12, "depth": 0.06}
      },
      "appearance": {
        "color": "transparent",
        "material": "glass",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable", "fragile"],
      "tracking": {
        "state": "moving",
        "confidence": 0.96,
        "velocity": {"x": 0.15, "y": 0.02, "z": 0.0},
        "velocity_confidence": 0.92
      }
    }
  ],
  "relations": [],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `抓住正在移动的杯子`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制 JSON 到输入框
2. 输入自然语言指令
3. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 意图理解 | 任务目标: cup |
| 安全约束裁决 | 运动安全: 目标正在移动 (0.15m/s) |
| 动作序列 | **1. WaitUntilStable** 2. Reach 3. GentleGrasp 4. MoveTo |

**Developer JSON 关键断言**:
- `target_moving` = true
- `target_speed` > 0.01
- `execution_ready` = true (有稳定等待)
- 动作序列第一项为 WaitUntilStable
- `WaitUntilStable` params 包含 `timeout_s`, `max_speed_mps`, `required_consecutive_frames`

**失败时优先检查的模块**: `scene_builder/semantic_scene_builder.py` (tracking 解析), `demo/web_ui.py` (motion safety 逻辑)

---

### TC_007a: 多对象消歧 — 空间参照成功绑定

**测试目的**: 验证空间描述（右边）和材料描述（玻璃）被用于消歧，正确绑定到目标对象。

**环境感知 JSON**:

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc007",
  "scene_id": "scene_tc007",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_left",
      "category_candidates": [
        {"name": "container", "score": 0.91},
        {"name": "cup", "score": 0.05}
      ],
      "pose": {
        "position": {"x": 0.35, "y": -0.20, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.07, "height": 0.10, "depth": 0.07}
      },
      "appearance": {
        "color": "blue",
        "material": "plastic",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.95,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    },
    {
      "object_id": "obj_cup_right",
      "category_candidates": [
        {"name": "cup", "score": 0.93},
        {"name": "container", "score": 0.04}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.20, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.06, "height": 0.12, "depth": 0.06}
      },
      "appearance": {
        "color": "transparent",
        "material": "glass",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable", "fragile"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.96,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [
    {
      "subject": "obj_cup_left",
      "predicate": "left_of",
      "object": "obj_cup_right",
      "confidence": 0.90,
      "metadata": {"axis_delta_m": 0.40}
    }
  ],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `把右边的玻璃杯拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制 JSON 到输入框
2. 输入自然语言指令
3. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 意图理解 | 任务目标应绑定到 obj_cup_right（玻璃+右边） |
| 物体属性推理 | 材料: glass, 易碎等级: L3 |

**Developer JSON 关键断言**:
- 绑定到 obj_cup_right
- 不能错误绑定到 obj_cup_left
- material = glass
- fragility 约束生效

**失败时优先检查的模块**: `scene_builder/semantic_scene_builder.py` (空间关系), `planner/behavior_tree_generator.py` (消歧逻辑)

---

### TC_007b: 多对象消歧 — 歧义检测

**测试目的**: 验证当存在多个同类别物体且指令未提供足够消歧信息时，系统能检测到歧义。

**环境感知 JSON**: 同 TC_007a

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 使用与 TC_007a 相同的 JSON
2. 输入指令 把杯子拿过来（无消歧信息）
3. 点击 运行推理

**预期页面结果**:
- 不应默认选择第一个物体
- 系统应检测到存在两个候选 (container + cup)
- 应提示歧义或需要进一步信息

**Developer JSON 关键断言**:
- 不能默认选择第一个对象
- 应检测到歧义（两个杯子）

**失败时优先检查的模块**: `planner/behavior_tree_generator.py` (消歧), `scene_builder/semantic_scene_builder.py`

---

### TC_008: 用户约束冲突 — 不可满足条件检测

**测试目的**: 验证当用户约束与安全上限矛盾时，系统能正确检测并裁决。

**环境感知 JSON**: 同 TC_003（单玻璃杯，category="cup"，glass, fragility=3, max_force=2.0N）

```json
{
  "schema_version": "1.0.0",
  "message_type": "perception_observation",
  "observation_id": "obs_tc008",
  "scene_id": "scene_tc008",
  "timestamp": 1723456789123,
  "clock_domain": "unix_utc",
  "coordinate_system": "robot_base",
  "source": {
    "module": "perception_pipeline",
    "pipeline_version": "1.0.0",
    "sensor_ids": ["camera_front", "depth_front"]
  },
  "objects": [
    {
      "object_id": "obj_cup_001",
      "category_candidates": [
        {"name": "cup", "score": 0.93},
        {"name": "container", "score": 0.04}
      ],
      "pose": {
        "position": {"x": 0.35, "y": 0.12, "z": 0.075},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
      },
      "geometry": {
        "type": "oriented_bbox_3d",
        "size": {"width": 0.06, "height": 0.12, "depth": 0.06}
      },
      "appearance": {
        "color": "transparent",
        "material": "glass",
        "texture": "smooth"
      },
      "affordances": ["graspable", "movable", "fragile"],
      "tracking": {
        "state": "stationary",
        "confidence": 0.96,
        "velocity": {"x": 0.0, "y": 0.0, "z": 0.0},
        "velocity_confidence": 0.0
      }
    }
  ],
  "relations": [],
  "robot_state": {
    "gripper": {"is_open": true, "has_object": false}
  }
}
```

**自然语言指令**: `必须使用8N抓住杯子，同时抓力绝不能超过2N`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**:
1. 复制 JSON 到输入框
2. 输入自然语言指令
3. 点击 运行推理

**预期页面结果**:

| 面板 | 预期内容 |
|------|----------|
| 安全约束裁决 | 用户原始要求: 8.0N → 裁决链显示冲突 → 最终执行: ≤2.0N |
| 决策链路 | CONFLICT_RESOLUTION 节点明确记录 force_n 冲突 |

**Developer JSON 关键断言**:
- `raw_requested_force` = 8.0（从第一个数字提取）
- `resolved_force` ≤ 2.0（安全约束生效）
- `override_ledger` 必须包含 force_n 冲突记录
- 冲突裁决规则为 Min-Clamping
- 不能在冲突时静默执行 8N

**失败时优先检查的模块**: `constraint/constraint_compiler.py` (conflict detection / min-clamping), `planner/behavior_tree_generator.py`

---

## 异常输入用例

---

### TC_009_01: 非法 JSON 字符串

**测试目的**: 验证 JSON 解析错误被优雅捕获。

**环境感知 JSON 原始输入**:
```
{objects: [}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 在环境感知 JSON 框中输入非法 JSON 字符串，点击运行推理。

**预期结果**: 系统必须捕获 JSON 解析错误，返回错误提示，不应崩溃。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run / JSON parsing)

---

### TC_009_02: objects 为 dict 而非 list

**测试目的**: 验证类型错误检测。

**环境感知 JSON 原始输入**:
```json
{"objects": {"key": "value"}}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON，点击运行推理。

**预期结果**: 应报错，不应将 dict 当作单元素 list 处理。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run / type checking)

---

### TC_009_03: category_candidates 为字符串

**测试目的**: 验证嵌套类型错误被检测。

**环境感知 JSON 原始输入**:
```json
{"objects": [{"object_id": "obj_001", "category_candidates": "cup"}]}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON，点击运行推理。

**预期结果**: 应检测到 category_candidates 类型错误，不应将字符串当作 list 遍历每个字符。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run / category parsing)

---

### TC_009_04: objects 中包含非 dict 元素

**测试目的**: 验证混合类型数组的容错处理。

**环境感知 JSON 原始输入**:
```json
{"objects": [{"object_id": "obj_001", "category_candidates": [{"name": "cup", "score": 0.9}]}, "not_an_object"]}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON，点击运行推理。

**预期结果**: 应跳过 "not_an_object" 字符串元素，至少处理第一个有效对象，不应崩溃。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run / object iteration)

---

### TC_009_05: 空 objects 数组

**测试目的**: 验证空场景的优雅处理。

**环境感知 JSON 原始输入**:
```json
{"objects": []}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON，点击运行推理。

**预期结果**: 应优雅处理空场景，不应崩溃，应返回空结果或提示无目标。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run), `scene_builder/semantic_scene_builder.py`

---

### TC_009_06: 缺少目标物体

**测试目的**: 验证目标不在场景中的处理。

**环境感知 JSON 原始输入**:
```json
{"objects": [{"object_id": "obj_001", "category_candidates": [{"name": "wooden_block", "score": 0.9}], "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}, "geometry": {"type": "oriented_bbox_3d", "size": {"width": 0.05, "height": 0.05, "depth": 0.05}}}]}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON（场景中只有积木，没有杯子），点击运行推理。

**预期结果**: 目标"杯子"不在场景中，系统应继续运行但不将积木误认为杯子。

**失败时优先检查的模块**: `scene_builder/semantic_scene_builder.py` (target matching), `planner/behavior_tree_generator.py` (grounding)

---

### TC_009_07: 缺少必要的位置信息

**测试目的**: 验证缺失字段的默认值回退。

**环境感知 JSON 原始输入**:
```json
{"objects": [{"object_id": "obj_001", "category_candidates": [{"name": "cup", "score": 0.9}]}]}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON（缺少 position, geometry 等），点击运行推理。

**预期结果**: 应使用默认值 (0, 0, 0.03)，不应崩溃。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run / position fallback)

---

### TC_009_08: objects 为 null

**测试目的**: 验证 null 值的优雅降级。

**环境感知 JSON 原始输入**:
```json
{"objects": null}
```

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 输入以上 JSON，点击运行推理。

**预期结果**: 应检测 null 并优雅处理，返回空场景结果，不应崩溃。

**失败时优先检查的模块**: `demo/web_ui.py` (Pipeline.run / null handling)

---

### TC_009_09: 完全空字符串

**测试目的**: 验证空输入被提前拦截。

**环境感知 JSON 原始输入**: (留空，不输入任何内容)

**自然语言指令**: `把杯子拿过来`

**规划引擎**: 纯规则引擎 (极速)

**操作步骤**: 不输入环境感知 JSON，直接点击运行推理。

**预期结果**: Gradio run() 早期检查应拦截空输入，返回"请输入环境感知 JSON"。

**失败时优先检查的模块**: `demo/web_ui.py` (run() input validation)

---

## API/模型异常用例

> TC_010 系列需要 mock 环境或实际配置 DeepSeek API Key 才能执行。自动化测试中通过 unittest.mock 覆盖。

### TC_010_01: API Key 为空 — 回退规则引擎

**环境感知 JSON**: 任意有效 JSON（如 TC_001）
**自然语言指令**: `把杯子拿过来`
**规划引擎**: DeepSeek-V3 (AI 推理)
**API Key**: (留空)

**预期结果**: 自动回退到规则引擎，planner_name 包含 "Rule Engine"，不崩溃。

---

### TC_010_02 – TC_010_06: 各类 API 异常回退

| 用例 | 模拟场景 | 预期行为 |
|------|----------|----------|
| TC_010_02 | HTTP 401 认证失败 | 回退规则引擎 |
| TC_010_03 | HTTP 429 频率限制 | 回退规则引擎 |
| TC_010_04 | 网络超时 | 回退规则引擎 |
| TC_010_05 | 网络异常 | 回退规则引擎 |
| TC_010_06 | 空响应 | 回退规则引擎 |

**失败时优先检查的模块**: `planner/llm_planner.py` (API call), `demo/web_ui.py` (_plan fallback)

---

## 附录

### 环境感知 JSON Schema 字段说明

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `schema_version` | string | 固定 `"1.0.0"` |
| `message_type` | string | 固定 `"perception_observation"` |
| `observation_id` | string | 观测唯一 ID |
| `scene_id` | string | 场景 ID |
| `timestamp` | integer | Unix UTC 毫秒时间戳 |
| `clock_domain` | string | `"unix_utc"` |
| `coordinate_system` | string | `"robot_base"` |
| `source.module` | string | 感知模块名 |
| `source.sensor_ids` | string[] | 传感器 ID 列表 |
| `objects[].object_id` | string | 物体唯一 ID |
| `objects[].category_candidates[].name` | string | 类别名（用于 ontology 查询） |
| `objects[].category_candidates[].score` | float | 类别置信度 [0, 1] |
| `objects[].pose.position.x/y/z` | float | 3D 位置 (m) |
| `objects[].pose.orientation` | object | 四元数朝向 |
| `objects[].geometry.type` | string | 几何类型（`"oriented_bbox_3d"`） |
| `objects[].geometry.size.width/height/depth` | float | BBox 尺寸 (m) |
| `objects[].appearance.color` | string | 颜色描述 |
| `objects[].appearance.material` | string | 材料（辅助推理） |
| `objects[].appearance.texture` | string | 纹理描述 |
| `objects[].affordances` | string[] | 可供性标签 |
| `objects[].tracking.state` | string | `"stationary"` 或 `"moving"` |
| `objects[].tracking.confidence` | float | 跟踪置信度 |
| `objects[].tracking.velocity.x/y/z` | float | 速度分量 (m/s) |
| `objects[].tracking.velocity_confidence` | float | 速度估计置信度 |
| `relations[]` | array | 空间关系列表 |
| `robot_state.gripper.is_open` | bool | 夹爪是否张开 |
| `robot_state.gripper.has_object` | bool | 是否持有物体 |

### Category → Ontology 映射参考

| category_candidates[0].name | Ontology 匹配 | 材料 | 易碎等级 | max_force |
|----------------------------|--------------|------|---------|-----------|
| `glass_cup` | exact | glass | 3 (PRECISION) | 2.0N |
| `cup` | alias → glass_cup | glass | 3 | 2.0N |
| `plastic_cup` | exact | plastic | 1 (SENSITIVE) | 8.0N |
| `container` | alias → generic_container | plastic | 1 | 8.0N |
| `box` | alias → generic_container | plastic | 1 | 8.0N |
| `medicine_bottle` | exact | plastic | 1 | 5.0N |
| `wooden_block` | exact | wood | 0 (NORMAL) | 10.0N |
| `block` | alias → wooden_block | wood | 0 | 10.0N |
| `power_supply` | exact | metal | 0 | 50.0N |
| `wafer_box` | exact | plastic | 4 (ULTRA_PRECISION) | 1.5N |
| 未知名称 | none | unknown | 0 (default) | 10.0N (default) |
