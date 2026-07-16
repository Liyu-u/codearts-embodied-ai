# 意图理解 Agent — 完整参考手册

> **Robot Intent Agent v1.0**
>
> 本文档涵盖模块的组成结构、工作原理、使用方法、设计创新四个方面，
> 面向开发者、论文审稿人、比赛评委。

---

## 目录

1. [模块定位](#一模块定位)
2. [组成结构](#二组成结构)
3. [工作原理](#三工作原理)
4. [使用方法](#四使用方法)
5. [设计创新](#五设计创新)
6. [数据契约](#六数据契约)
7. [v2.0 升级路线](#七v20-升级路线)

---

## 一、模块定位

### 1.1 在系统中的位置

```
                    具身智能机械臂操作系统
        ┌────────────────────────────────────────────┐
        │                                            │
        v                                            │
  ┌──────────┐    ┌──────────────┐    ┌──────────────┤
  │ 用户口语  │ → │ 意图理解Agent │ → │ CodeArts代码生成│
  └──────────┘    │  (本模块)    │    └──────┬───────┤
                  └──────────────┘           │       │
                                             v       │
                                ┌──────────────┐     │
                                │ Isaac Sim执行 │     │
                                └──────┬───────┘     │
                                       │             │
                                ┌──────v───────┐     │
                                │ 异常反思重试  │─────┘
                                └──────────────┘
```

### 1.2 一句话定义

**把模糊的口语指令变成下游 AI 能精确消费的结构化中间表示（RobotTaskIR）。**

### 1.3 做什么 vs 不做什么

| 负责 | 不负责 |
|------|--------|
| 理解自然语言意图 | 生成 Python 控制代码 → CodeArts |
| 构建语义场景图（空间关系推理） | 控制机械臂运动 → Isaac Sim |
| 规划任务步骤（行为树） | ROS 通信 → backend |
| 编译执行约束（物理/安全/空间） | 相机感知 → VLM 模块 |
| 输出统一 RobotTaskIR JSON | 异常捕获 → monitor |

---

## 二、组成结构

### 2.1 目录总览

```
robot_intent_agent/
│
├── main.py                         # 入口 (--version / --instruction / --api)
│
├── config/settings.py              # Pydantic Settings 全局配置
│
├── schemas/                        # 数据模型层 (Pydantic v2)
│   ├── scene.py                    # SceneObject, SpatialRelation, SemanticSceneGraph
│   ├── behavior_tree.py            # BTNode (递归), BehaviorTree
│   ├── constraint.py               # 6 种约束模型 + ConstraintSet
│   ├── robot_task_ir.py            # RobotTaskIR (顶层统一 IR)
│   └── json_schema/                # 自动生成的 JSON Schema (4 个)
│
├── memory/                         # Step 3: 记忆检索
│   ├── base.py                     # MemoryItem, MemoryInterface (抽象)
│   ├── user_memory.py              # UserMemory (偏好记忆)
│   ├── skill_memory.py             # SkillMemory (技能经验)
│   ├── environment_memory.py       # EnvironmentMemory (环境先验)
│   ├── retriever.py                # MemoryRetriever (统一检索 + FAISS 预留)
│   └── ENHANCEMENTS.md             # v1.1 升级备忘
│
├── scene_builder/                  # Step 4: 场景构建
│   ├── semantic_scene_builder.py   # RawObjectPercept, SpatialReasoner, SemanticSceneBuilder
│   └── ENHANCEMENTS.md
│
├── planner/                        # Step 5: 任务规划
│   ├── base.py                     # TaskPlannerInterface (抽象)
│   ├── skill_catalog.py            # SkillCatalog (9 个原子技能)
│   ├── behavior_tree_generator.py  # BehaviorTreeGenerator (规则规划器)
│   └── llm_planner.py              # LLMPlanner (Mock 预留)
│
├── constraint/                     # Step 6: 约束编译
│   ├── base.py                     # ConstraintNode, ConstraintGraph
│   ├── spatial_constraint.py       # 空间约束工厂 (3 种)
│   ├── physical_constraint.py      # 物理约束工厂 (5 种)
│   ├── safety_constraint.py        # 安全约束工厂 (6 条红线)
│   ├── rule_engine.py              # ConstraintRuleEngine (NL→约束)
│   └── constraint_compiler.py      # HybridConstraintCompiler (编排器)
│
├── ir/                             # Step 7: IR 生成
│   └── ir_generator.py             # RobotTaskIRGenerator
│
├── demo/cli_demo.py                # CLI 演示 (3 个预设任务)
│
├── integration_tests/              # 端到端场景测试 (4 文件)
│   ├── test_full_pipeline.py       # 药瓶递送
│   ├── test_multi_object_task.py   # 多物体区分
│   ├── test_failure_recovery.py    # 零 Memory 降级
│   └── test_constraint_conflict.py # 矛盾指令
│
├── tests/                          # 单元测试 (5 文件, 127 tests)
│   ├── test_memory.py              # 30 tests
│   ├── test_scene_builder.py       # 19 tests
│   ├── test_planner.py             # 35 tests
│   ├── test_constraint.py          # 30 tests
│   └── test_ir_generator.py        # 13 tests
│
├── docs/                           # 文档
│   ├── ARCHITECTURE.md             # v1.0 架构设计
│   ├── ARCHITECTURE_V2.md          # v2.0 升级设计
│   └── COMPLETE_REFERENCE.md       # 本文档
│
└── prompts/                        # LLM 提示词模板
    ├── planner_prompt.md
    └── constraint_prompt.md
```

### 2.2 代码量统计

```
总代码: ~4,500 行 Python + ~500 行 Markdown + 136 个测试用例

schemas/             4 文件   ~700 行   数据模型定义
memory/              5 文件   ~600 行   三层记忆 + 检索
scene_builder/       1 文件   ~250 行   场景构建 + 空间推理
planner/             4 文件   ~600 行   技能库 + 规划器
constraint/          5 文件   ~650 行   约束图 + 编译器
ir/                  1 文件   ~280 行   IR 生成器
tests/               5 文件   ~900 行   单元测试
integration_tests/   4 文件   ~350 行   集成测试
demo/                 1 文件   ~180 行   CLI 演示
```

### 2.3 模块间依赖关系

```
                    schemas/  (被所有模块依赖)
                   /   |   \
                  /    |    \
          memory/  scene_builder/  planner/ (依赖 memory+scene)
                           \         /
                            \       /
                         constraint/ (依赖 planner+scene+memory)
                              |
                           ir/  (依赖所有上述模块)
```

---

## 三、工作原理

### 3.1 五步流水线

```
  输入: "请把红色药瓶递给我，轻一点，别碰水杯"
       + Scene RawObjects
       + Memory Items
    │
    ├── Step 3: Memory Retrieval
    │   核心问题: "这个用户是谁？有什么习惯？历史上怎么做类似的？"
    │   输出: List[MemoryItem] — 用户偏好 + 技能经验 + 环境先验
    │
    ├── Step 4: Scene Building
    │   核心问题: "周围有什么东西？在哪里？空间关系怎样？"
    │   输出: SemanticSceneGraph — 物体列表 + 11 种空间关系
    │
    ├── Step 5: Task Planning
    │   核心问题: "应该先做什么、后做什么？"
    │   输出: BehaviorTree — Sequence 根节点的递归行为树
    │
    ├── Step 6: Constraint Compilation
    │   核心问题: "每个动作受什么限制？什么绝对不能违反？"
    │   输出: ConstraintGraph — 绑定到 BT 技能的约束集
    │
    └── Step 7: IR Generation
        核心问题: "怎么打包成下游能直接用的格式？"
        输出: RobotTaskIR — 统一 JSON 中间表示
```

### 3.2 Step 3: Memory Retrieval — 记忆检索

**三层分治架构**：

```
                  MemoryRetriever
                        │
        ┌───────────────┼───────────────┐
        v               v               v
   UserMemory      SkillMemory     EnvironmentMemory
   (用户偏好)       (技能经验)       (环境先验)
        │               │               │
   "左手接物"      "药瓶→2.5N"      "桌面高0.72m"
   "轻柔抓取"      "水杯→1.5N"      "工作半径0.5m"
```

**检索算法（当前 in-memory, FAISS 接口已预留）**：

```
UserMemory.search(query):
  1. 关键词子串匹配 (query 整体在 search_text 中 → +10)
  2. 分词 token 匹配 (每个 token → +3)
  3. 优先级加权 (HIGH:+5, MEDIUM:+2, LOW:0)
  4. 访问频率加权 (min(access_count, 5))
  5. 按总分降序 → 返回 top_k

SkillMemory.search(query):
  1. 技能名精确匹配 (skill in query → +10)
  2. 物体名匹配 (object in query → +8)
  3. 关键词匹配 (tokens → +3 each)
  4. 成功经验加权 (+5 for success)
  5. 去重: 同 skill+object → 保留最新

EnvironmentMemory.search(query):
  1. 场景名匹配 (environment in query → +12)
  2. 物体名匹配 (+8)
  3. 关键词匹配 (+2 each)
```

### 3.3 Step 4: Scene Building — 场景构建

**物体实例化**（中英文关键词自动推断）：

```
RawObjectPercept → SceneObject:
  "红色药瓶" → name="红色药瓶"
            → label="medicine_bottle" (中文"药"关键词匹配)
            → affordances=[GRASPABLE, FRAGILE, MOVABLE]
            → attributes={color:"red", material:"plastic"}

  "玻璃水杯" → name="玻璃水杯"
            → label="cup" (中文"杯"关键词匹配)
            → affordances=[GRASPABLE, CONTAINER, MOVABLE]
            → attributes={color:"transparent", material:"glass"}
```

**11 种空间关系推理（纯几何规则）**：

| 谓词 | 判定规则 | 置信度公式 |
|------|---------|-----------|
| `left_of` | X轴差 > 2cm, 双向输出 | `1.0 - distance*2` |
| `right_of` | X轴差 > 2cm (双向) | `1.0 - distance*2` |
| `above` | Z 轴中点差 > 1cm | `1.0 - distance*2` |
| `below` | Z 轴中点差 > 1cm | `1.0 - distance*2` |
| `in_front_of` | Y 轴差 > 2cm | `1.0 - distance*2` |
| `behind` | Y 轴差 > 2cm | `1.0 - distance*2` |
| `near` | 欧氏距离 < 10cm | `1.0 - distance*2` |
| `blocking` | robot→target 视线垂距 < object_radius | **0.85 (固定)** |
| `supporting` | Z 重叠 + XY 投影重叠 | 1.0 |
| `inside` | BBox 包含关系 (预留) | - |

**blocking（阻挡）判定 — 几何原理**:

```
     robot(0,0) ──────连线──────→ target(0.15, 0.05)
                    │
                    │ 垂直距离
                    │
              obstacle(0.08, 0.03)

  步骤:
  1. 计算 obstacle 在 robot→target 向量上的投影参数 t
  2. 如果 t < 0 → obstacle 在 robot 后方 → 不阻挡
  3. 计算 obstacle 到连线的垂直距离 d
  4. 如果 d < (obstacle_radius + 2cm) → BLOCKING
  5. obstacle_radius = max(bbox.width, bbox.depth) / 2 + 0.02
```

### 3.4 Step 5: Task Planning — 任务规划

**规则指令解析器（不调用 LLM）**：

```
"请把红色药瓶递给我，轻一点，别碰水杯"

  动作分类:   "递/拿/取" → pick_and_place
  目标提取:   "把...药瓶..." → "红色药瓶"
  修饰语:     "轻一点" → {force_n: 3.0, grip_style: gentle}
  规避对象:   "别碰...水杯" → ["水杯"]
```

**行为树组装流程**：

```
  1. 查技能模板:
     pick_and_place → [Reach, Grasp, MoveTo, Release]

  2. Memory 注入:
     grip_style=gentle → Grasp 替换为 GentleGrasp
     force_n=2.5       → 注入 Grasp.params

  3. Scene 注入:
     水杯 blocking 药瓶 → 前置条件: CheckClear(水杯)
                       → 动作插入: Avoid(水杯)

  4. 默认前置条件:
     CheckGripperEmpty (每次执行前)
     CheckVisible(药瓶)

  5. 组装 Sequence:
     Sequence[CheckClear(水杯), CheckGripperEmpty, CheckVisible(药瓶),
              Avoid(水杯), Reach, GentleGrasp, MoveTo, Release]
```

**9 个原子技能**：

| 技能 | 前置条件 | 效果 | 安全说明 |
|------|---------|------|---------|
| Reach | gripper_ready, target_in_view, path_clear | end_effector_at_safe_height | z ≥ 0.02m |
| Grasp | end_effector_at_height, gripper_open | target_in_hand | verify after close |
| GentleGrasp | 同 Grasp | 同 Grasp (低力) | force ≤ 3.0N |
| MoveTo | target_in_hand, path_clear | target_at_destination | lift before lateral |
| Release | at_destination, stable | object_released | verify stability |
| Push | end_effector_near_target | target_moved | not fragile |
| Stack | target_in_hand, surface_clear | stacked | verify stability |
| Pour | target_in_hand, is_container | contents_transferred | tilt slowly |
| Avoid | obstacle_known | path_clear | d ≥ 0.05m |
| Inspect | camera_available | state_confirmed | pre-critical actions |

### 3.5 Step 6: Constraint Compilation — 约束编译

**核心升级**: 从"机器人应该做什么"→"机器人必须满足什么条件才能做"。

**四层递进编译**：

```
  Layer 0: Safety Mandatory (不可绕过, 不可降级)
    z ≥ 0.02m                 ← 防止机械臂撞桌面
    joint ∈ [-2.9, 2.9] rad   ← Franka Panda 硬件限位
    force ≤ 10.0N             ← 夹爪力上限
    workspace bounds          ← 工作空间边界
    human proximity           ← 人机安全距离 (0.3m内降速)

  Layer 1: Rule Engine (NL修饰语 + 场景 + 物体属性)
    "轻一点"     → force(药瓶) ≤ 3.0N
    "慢一点"     → velocity ≤ 0.10 m/s
    "别碰水杯"   → distance(水杯) ≥ 0.05m
    Scene blocking → distance(水杯) ≥ 0.05m
    物体 fragile   → force ≤ 3.0N
    Memory 注入   → {force_n: 2.5, velocity_ms: 0.10}

  Layer 2: BT Alignment (约束绑定到具体技能)
    force_limit     → Grasp, GentleGrasp
    velocity_limit  → MoveTo, Reach, Push
    collision_avoid → 全部 Action (全局)
    z_axis_floor    → 全部 Action (全局)
    release_height  → Release

  Layer 3: Dedup + Conflict Resolution
    同类型+同目标+同技能 → 保留最严格的约束
    min_force >= max_force → 冲突报告
    HARD 不可被 SOFT 覆盖
```

**约束种类（6 种）**：

```
ForceConstraint       : force ∈ (min, max] N
VelocityConstraint    : velocity ≤ max m/s
CollisionConstraint   : distance(obstacle) ≥ min m
HeightConstraint      : z ∈ [min, max] m
TemporalConstraint    : before / after 时序
PreferenceConstraint  : 用户偏好 (SOFT)
```

### 3.6 Step 7: IR Generation — 统一输出

**编译规则**：

```
  输入: BehaviorTree + ConstraintGraph + SemanticSceneGraph + MemoryItems

  编译:
    每个 BT Action → IR Skill {target, params, constraints, object_info}
    每个 ConstraintNode → 绑定到对应 skill 的 constraints 字段
    Safety 约束        → 顶层 precondition_assertions
    力/速度约束         → optimization_space 边界
    Memory 偏好         → user_context
    Scene 物体信息       → skills[name].object

  输出: RobotTaskIR (Pydantic Model → JSON)
```

**IR 结构（6 个顶层字段）**：

```
RobotTaskIR
├── ir_version: "1.0.0"
├── task_metadata     → 原始指令 + 语言 + 用户上下文
├── preconditions     → 执行前必须满足的断言列表
├── skills            → {技能名: {target, params, constraints, object}}
├── optimization_space → force_range, velocity_range, z_range, targets
├── behavior_tree     → 完整递归行为树
├── scene             → 语义场景图
└── memory_context    → 记忆上下文 + 约束统计摘要
```

---

## 四、使用方法

### 4.1 环境要求

```bash
Python 3.11+
pip install -r ../../requirements.txt  # fastapi, pydantic, pytest, numpy...
```

### 4.2 快速测试（136 个用例）

```bash
cd D:\CodeArts_embodied_ai\Intent-Understanding-Natural-Language-Context-Awareness

# 全量测试 (0.3 秒)
python -m pytest robot_intent_agent/tests/ robot_intent_agent/integration_tests/ -v

# 单独模块
python -m pytest robot_intent_agent/tests/test_memory.py -v
python -m pytest robot_intent_agent/tests/test_scene_builder.py -v
python -m pytest robot_intent_agent/tests/test_planner.py -v
python -m pytest robot_intent_agent/tests/test_constraint.py -v
python -m pytest robot_intent_agent/tests/test_ir_generator.py -v

# 集成测试
python -m pytest robot_intent_agent/integration_tests/ -v
```

### 4.3 CLI Demo（3 个预设任务）

```bash
# 运行全部
python robot_intent_agent/demo/cli_demo.py --task all

# 单个任务
python robot_intent_agent/demo/cli_demo.py --task medicine   # 药瓶递送
python robot_intent_agent/demo/cli_demo.py --task water      # 老人喝水
python robot_intent_agent/demo/cli_demo.py --task fragile    # 脆弱物品
```

### 4.4 编程调用

```python
import sys
sys.path.insert(0, r"D:\CodeArts_embodied_ai\Intent-Understanding-Natural-Language-Context-Awareness")

from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator

# ── 准备输入 ──
instruction = "请把红色药瓶递给我，轻一点，别碰水杯"

# Memory
retriever = MemoryRetriever()
retriever.add_user_preference("grip_style", "gentle")
retriever.add_skill_experience("gentle_grasp", "红色药瓶",
                                params={"force_n": 2.5}, success=True)
mem = [i.to_dict() for i in retriever.search("轻一点", top_k=3)]

# Scene
scene = SemanticSceneBuilder().build([
    RawObjectPercept(name="红色药瓶", x=0.15, y=0.05, z=0.03,
                     width=0.03, height=0.08, depth=0.03, color="red"),
    RawObjectPercept(name="水杯", x=0.08, y=0.03, z=0.06,
                     width=0.07, height=0.12, depth=0.07, color="transparent"),
])

# ── 五步管线 ──
bt  = BehaviorTreeGenerator().plan(instruction, scene=scene, memory_context=mem)
cg  = HybridConstraintCompiler().compile(instruction, bt, scene=scene, memory_context=mem, target="红色药瓶")
ir  = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene, memory_context=mem)

# ── 使用输出 ──
print(ir.summary())                             # 可读摘要
print(ir.model_dump_json(indent=2)[:500])       # JSON 序列化 (给 CodeArts)

# 提取具体信息
grasp_constraints = ir.skills["Grasp"]["constraints"]
print(grasp_constraints["force"])               # {'max_force_n': 2.5}
print(grasp_constraints["fragile"])             # True
print(grasp_constraints["avoid"])               # ['水杯']

# 优化边界
opt = ir.optimization_space
print(opt.force_range_n)                        # (0.1, 2.5)
print(opt.velocity_range_ms)                    # (0.05, 0.1)
```

### 4.5 自定义新任务

```python
# 1. 定义场景物体
my_objects = [
    RawObjectPercept(name="蓝色盒子", x=0.20, y=-0.10, z=0.04,
                     width=0.06, height=0.06, depth=0.06, color="blue"),
    RawObjectPercept(name="障碍物", x=0.10, y=-0.05, z=0.05,
                     width=0.04, height=0.10, depth=0.04),
]

# 2. 定义用户偏好
retriever = MemoryRetriever()
retriever.add_user_preference("speed_preference", "slow")

# 3. 构建场景
scene = SemanticSceneBuilder().build(my_objects)

# 4. 生成 IR
bt = BehaviorTreeGenerator().plan("把蓝色盒子移到右边，慢一点，别碰障碍物",
                                   scene=scene,
                                   memory_context=[i.to_dict() for i in retriever.search("slow", 3)])
cg = HybridConstraintCompiler().compile("把蓝色盒子移到右边，慢一点，别碰障碍物",
                                         bt, scene=scene, target="蓝色盒子")
ir = RobotTaskIRGenerator().generate("把蓝色盒子移到右边，慢一点，别碰障碍物",
                                      bt, cg, scene=scene)
# ir 现在可以发给 CodeArts 生成代码
```

---

## 五、设计创新

### 创新 1: Constraint Graph — 约束作为一等公民

**传统方法**：
```python
grasp(force=3.0)  # "轻一点" 只是一个参数
```

**本系统**：
```python
# "轻一点" 变成一条不可绕过的 HARD 约束
ConstraintNode(
    category=PHYSICAL,
    constraint_type="force_limit",
    expression="force <= 3.0N",
    applies_to_skill="Grasp",
    priority=HARD,
)
```

- 约束有独立的类型系统（Spatial / Physical / Safety / Temporal / Interaction）
- 约束有优先级（HARD → 不可违反 vs SOFT → 尽量满足）
- 约束绑定到具体的 BehaviorTree 节点
- 约束可以冲突检测和自动调解（取最严格的）
- **任何下游模块都不能绕过 HARD 约束**

### 创新 2: Memory-Driven Planning — 记忆驱动的规划

同一个"递药瓶"指令对不同用户产生完全不同的执行参数：

```
  年轻人: force=7.0N, velocity=0.25 m/s, hand=right
  老人:   force=2.5N, velocity=0.10 m/s, hand=left
```

Memory 不是 prompt 文本，而是结构化的记忆条目，通过加权检索匹配到当前上下文，注入到 Planner 和 Constraint Compiler。

### 创新 3: Rule + LLM Hybrid Architecture — 规则确定性 + LLM 灵活性

**不是 LLM 套壳**。当前 v1.0 是纯规则（136 tests, 0.3s），v2.0 将引入 LLM：
- LLM 负责"创造性"（长任务拆解、新技能组合）
- Rule Validator 负责"正确性"（安全检查、参数范围、BT 合法性）
- 不可修复的 LLM 输出 → 送回重新生成
- 可修复的 → 自动修正后使用

### 创新 4: RobotTaskIR — LLVM IR 思想的机器人版本

```
自然语言 (高级语言)
    ↓
RobotTaskIR (中间表示)
    ↓
┌──────────┬──────────┬──────────┐
│ CodeArts │ RRT*     │ Safety   │  ← 不同后端
│ (代码)   │ (运动)   │ (验证)   │
└──────────┴──────────┴──────────┘
```

- 与平台无关（Isaac Sim / ROS2 / 真机共享同一 IR）
- 强类型（Pydantic 模型，编译期验证）
- 可序列化（JSON，任何语言可解析）

### 创新 5: Safety-First — 四道安全防线

```
  第 1 道: Schema 验证 (Pydantic, 编译期)
  第 2 道: 安全断言 (exec_wrapper.py, 运行时)
  第 3 道: 代码校验 (code_validator.py, 黑白名单)
  第 4 道: 异常探针 (trace_probe.py, 执行态)
```

安全约束在 Constraint Compiler 的 **Layer 0** 注入，标记为 HARD，任何下游模块（包括 LLM）都不能修改或移除。

---

## 六、数据契约

### 6.1 模块间通信格式

| 步骤 | 输出类型 | 消费者 |
|------|---------|--------|
| Memory | `List[MemoryItem]` (dataclass) | Planner, Constraint |
| Scene | `SemanticSceneGraph` (Pydantic) | Planner, Constraint, IR |
| Planner | `BehaviorTree` (Pydantic) | Constraint, IR |
| Constraint | `ConstraintGraph` (dataclass) | IR |
| IR | `RobotTaskIR` (Pydantic → JSON) | CodeArts, Executor |

### 6.2 下游消费 RobotTaskIR 的方式

```python
# CodeArts 消费 Skill Layer
ir = RobotTaskIR.model_validate_json(json_str)
for skill_name, skill_data in ir.skills.items():
    generate_code(skill_data["target"], skill_data["constraints"])

# Motion Planner 消费技能参数
reach_params = ir.skills["Reach"]["object"]["position"]
plan_trajectory(from=current_pose, to=reach_params)

# Safety Monitor 消费顶层断言
for assertion in ir.precondition_assertions.assertions:
    assert eval(assertion.assertion)  # "z >= 0.02"
```

---

## 七、v2.0 升级路线

### 7.1 六大升级方向

| # | 升级 | 从 → 到 | 优先级 |
|---|------|---------|--------|
| 1 | Planner | 纯规则 → LLM生成 + Rule验证 | P0 |
| 2 | IR | Task-Skill 两级 → 五层 (Task-Skill-Motion-Safety-Opt) | P0 |
| 3 | Feasibility | 默认可执行 → 独立评估 (可达性/遮挡/抓取) | P1 |
| 4 | Memory | Keyword → Embedding + VectorDB + Rerank | P1 |
| 5 | Feedback | 无闭环 → Observe→Execute→Monitor→Reflect→Update | P0 |
| 6 | Scene | 确定性 → 不确定性建模 + VLM Hybrid | P1 |

### 7.2 不变的核心

- Constraint Graph（约束一等公民）
- Memory-Driven Planning（三层记忆分治）
- Safety-First（四道防线）
- Pydantic 类型系统
- 五步流水线架构
- 136 个 v1.0 测试（v2.0 仍须全部通过）
