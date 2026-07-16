# 意图理解 Agent — 架构设计与流程原理

> Robot Intent Agent v1.0
>
> 自然语言 + 环境感知 + 机器人知识 → 统一可执行中间表示 (Robot Task IR)

---

## 一、模块定位

意图理解 Agent 是整个机器人控制系统的**前端大脑**。它的唯一职责是：

> **把模糊的口语指令变成下游 AI（CodeArts）能精确消费的结构化 IR。**

```
                    整体系统中的地位

  用户口语 ──→ [意图理解 Agent] ──→ RobotTaskIR ──→ CodeArts ──→ Python代码
                  ▲                                        │
                  │                                        │
           环境感知数据                               Isaac Sim 执行
           历史操作经验                                      │
                  │                                  失败时反馈
                  └────────────────────────────────────────┘
```

### 边界清晰：做什么 vs 不做什么

**本模块负责**：

- 理解用户自然语言指令的意图
- 结合环境感知数据理解物理场景
- 结合历史记忆理解用户偏好
- 规划任务步骤（行为树）
- 编译执行约束（物理 + 安全 + 空间）
- 输出统一的 RobotTaskIR

**本模块不负责**：

- 生成 Python 控制代码 → 交给 CodeArts
- 控制机械臂运动 → 交给 Isaac Sim
- ROS 通信 → 交给 backend
- 异常捕获 → 交给 monitor

---

## 二、输入与输出

### 2.1 输入：三个信息源

```
┌──────────────────────────────────────────────────────────┐
│                      输入层                              │
├──────────────┬──────────────────┬────────────────────────┤
│ 自然语言指令  │  环境感知数据      │  机器人知识与历史记忆   │
│              │                  │                        │
│ 自由中文文本  │ RawObjectPercept │ MemoryRetriever        │
│              │ 列表             │ 三层存储检索            │
│              │                  │                        │
│ "请把桌上的   │ · 物体名称       │ UserMemory:            │
│  红色药瓶递给 │ · 3D世界坐标     │   用户偏好 (左手/轻柔)   │
│  我，轻一点， │ · BBox尺寸       │                        │
│  别碰水杯"   │ · 颜色/材质      │ SkillMemory:           │
│              │                  │   历史技能参数          │
│              │                  │   (药瓶→2.5N)          │
│              │                  │                        │
│              │                  │ EnvironmentMemory:     │
│              │                  │   环境先验              │
│              │                  │   (桌面高0.72m)        │
└──────────────┴──────────────────┴────────────────────────┘
```

### 2.2 输出：RobotTaskIR

```json
{
  "ir_version": "1.0.0",
  "task_metadata": {
    "task_id": "task-pick_and_place",
    "raw_instruction": "请把桌上的红色药瓶递到我手上...",
    "language": "zh",
    "user_context": { "grip_style": "gentle" }
  },
  "precondition_assertions": [
    { "assertion": "z >= 0.02" },
    { "assertion": "gripper_force <= 10.0" },
    { "assertion": "is_gripper_empty" }
  ],
  "skills": {
    "Grasp": {
      "target": "红色药瓶",
      "params": { "force_n": 3.0 },
      "constraints": {
        "force": { "max_force_n": 2.5 },
        "fragile": true,
        "avoid": ["水杯"]
      },
      "object": {
        "position": { "x": 0.15, "y": 0.05, "z": 0.03 },
        "affordances": ["graspable", "fragile", "movable"]
      }
    },
    "MoveTo": {
      "target": "红色药瓶",
      "constraints": {
        "velocity": { "max_linear_ms": 0.10 },
        "avoid": ["水杯"]
      }
    }
  },
  "optimization_space": {
    "force_range_n": [0.1, 2.5],
    "velocity_range_ms": [0.05, 0.10],
    "targets": ["max_safety", "min_time"]
  },
  "behavior_tree": { /* 完整递归行为树 */ },
  "scene": { /* 语义场景图 */ },
  "memory_context": { /* 约束统计 + 用户偏好 */ }
}
```

---

## 三、五步流水线

```
  Input: "请把红色药瓶递给我，轻一点，别碰水杯"
         + [药瓶(x=0.15), 水杯(x=0.08)]
         + Memory Items
    │
    ├── Step 3: Memory Retrieval        (为什么这样做？)
    │
    ├── Step 4: Scene Building          (周围有什么？)
    │
    ├── Step 5: Task Planning           (怎么做？)
    │
    ├── Step 6: Constraint Compilation  (受什么限制？)
    │
    └── Step 7: IR Generation           (打包统一输出)
```

### 3.1 Step 3: Memory Retrieval — 记忆检索

**核心问题**：同一个"递药瓶"指令，对年轻人和对老人，执行方式完全不同。

**流程**：

```
自然语言指令 ──→ MemoryRetriever.search("老人递药轻一点", top_k=5)
                      │
        ┌─────────────┼─────────────┐
        v             v             v
   UserMemory    SkillMemory   EnvironmentMemory
        │             │             │
        v             v             v
  关键词匹配     技能名+物体匹配   场景名匹配
  + 优先级加权   + 成功经验加权    + 关键词匹配
  + 访问频率     + 最近优先       + 优先级
        │             │             │
        └─────────────┼─────────────┘
                      v
              合并 + 全局排序
          (高优先级 > 高匹配 > 高访问频率)
                      │
                      v
    ┌─────────────────────────────────────┐
    │ 结果:                               │
    │  { key: "grip_style", value: "gentle" }  │
    │  { key: "hand_preference", value: "left" }│
    │  { skill: "gentle_grasp", force_n: 2.5 } │
    └─────────────────────────────────────┘
```

**设计原理**：

- **三层分离**：用户偏好、技能经验、环境先验是三种完全不同来源的知识，分开存储可以独立更新
- **无外部依赖**：当前使用 in-memory 关键词匹配，FAISS 向量检索接口已预留
- **去重策略**：同 key 覆盖（用户偏好）、同 skill+object 覆盖（技能经验）

### 3.2 Step 4: Scene Building — 语义场景构建

**核心问题**：知道"药瓶在(0.15, 0.05, 0.03)"不够，还需要知道"水杯挡在药瓶前面"。

**流程**：

```
RawObjectPercept 列表
    │
    ├── 1. 物体实例化
    │   · 自动推断语义标签: "药"→medicine_bottle, "杯"→cup
    │   · 自动推断可供性 (Affordance):
    │     "瓶"→[GRASPABLE, FRAGILE, MOVABLE]
    │     "杯"→[GRASPABLE, CONTAINER, MOVABLE]
    │   · 自动推断属性: plastic, glass, ceramic
    │
    ├── 2. 空间关系推理 (SpatialReasoner)
    │   对所有物体两两配对，基于几何规则推断 11 种空间关系:
    │
    │   方向关系:
    │   ┌────────────┬─────────────────────────────────┐
    │   │ left_of    │ X轴差 > 2cm, 双向输出           │
    │   │ right_of   │ (A左B → B右A 同时生成)         │
    │   │ above      │ Z轴中点差 > 1cm                  │
    │   │ below      │                                 │
    │   │ in_front_of│ Y轴差 > 2cm                     │
    │   │ behind     │                                 │
    │   ├────────────┼─────────────────────────────────┤
    │   │ near       │ 欧氏距离 < 10cm                 │
    │   │ blocking   │ robot→target 连线的垂距         │
    │   │            │ < object_radius (几何判定)       │
    │   │ supporting │ Z重叠 + XY投影重叠              │
    │   │ inside     │ BBox 包含关系 (预留)            │
    │   └────────────┴─────────────────────────────────┘
    │
    └── 3. 输出 SemanticSceneGraph
       {
         objects: [药瓶(0.15,0.05), 水杯(0.08,0.03)],
         relations: [
           水杯 left_of 药瓶,
           药瓶 right_of 水杯,
           水杯 blocking 药瓶,    ← 关键: 影响后续规划
           水杯 near 药瓶
         ],
         robot_state: { gripper: empty, is_homed: true }
       }
```

**blocking（阻挡）判定原理**：

```
         robot (0, 0)
            │
            │  视线方向
            │
            ├─── obstacle (0.08, 0.03)  ← 垂距 < radius → BLOCKING
            │
            │
            v
         target (0.15, 0.05)

  判定步骤:
  1. 计算 obstacle 到 robot→target 连线的垂直距离
  2. 如果垂距 < (obstacle 的半径 + 2cm) → blocking
  3. 如果 obstacle 在 robot 后方 (dot_product ≤ 0) → 不阻挡
```

**为什么用规则而不是 LLM**：空间关系是纯几何计算，用规则比 LLM 更精确、更快、可复现。关系推理的置信度来源于几何精度，不是 LLM 的概率输出。

### 3.3 Step 5: Task Planning — 任务规划

**核心问题**：从"递给我"三个字，推断出"Reach → Grasp → MoveTo → Release"一连串动作。

**流程**：

```
"请把红色药瓶递给我，轻一点，别碰水杯"
    │
    v
┌────────────────────────────────────────────────┐
│  InstructionParser (规则解析器, 不调用 LLM)     │
│                                                │
│  动作分类: "递" → pick_and_place               │
│  目标提取: "把...药瓶..." → "红色药瓶"          │
│  修饰语:   "轻一点" → {grip_style: gentle}     │
│  规避对象: "别碰...水杯" → ["水杯"]            │
└────────────────────┬───────────────────────────┘
                     │
                     v
┌────────────────────────────────────────────────┐
│  BTComposer (行为树组装器)                      │
│                                                │
│  1. 查技能模板:                                 │
│     pick_and_place → [Reach, Grasp, MoveTo, Release]  │
│                                                │
│  2. Memory 注入:                                │
│     grip_style=gentle → Grasp 替换为 GentleGrasp│
│     force_n=2.5 → Grasp.params.force_n = 2.5  │
│                                                │
│  3. Scene 注入:                                 │
│     水杯 blocking 药瓶 → 前置: CheckClear(水杯)  │
│                       → 动作: Avoid(水杯)       │
│                                                │
│  4. 默认前置条件:                               │
│     CheckGripperEmpty                          │
│     CheckVisible(药瓶)                         │
└────────────────────┬───────────────────────────┘
                     │
                     v
         BehaviorTree (Sequence)
         ┌─────────────────────────┐
         │ CONDITION: CheckClear(水杯)   │
         │ CONDITION: CheckGripperEmpty  │
         │ CONDITION: CheckVisible(药瓶) │
         ├─────────────────────────┤
         │ ACTION: Avoid(水杯)          │
         │ ACTION: Reach(红色药瓶)      │
         │ ACTION: GentleGrasp(红色药瓶)│
         │ ACTION: MoveTo(用户)         │
         │ ACTION: Release(红色药瓶)    │
         └─────────────────────────┘
```

**Planner 不生成 Python 代码**：它只生成任务逻辑树。代码生成留给 CodeArts。

**为什么先规则后 LLM**：
- 规则确定性高（同样的输入永远产生同样的输出）
- LLM 接口已预留（`llm_planner.py`），未来可替换
- 对于常见指令（拿/放/推/叠），规则覆盖率达 90%+

### 3.4 Step 6: Constraint Compilation — 约束编译

**核心问题**：从"机器人应该做什么"升级到"机器人必须满足什么条件才能做"。

**这是意图理解最关键的升级**。没有约束编译，"轻一点"只是 Grasp 的一个参数。有了约束编译，"轻一点"成为一条**硬约束**：`force(药瓶) ∈ (0.1, 3.0]N`，永远不能被绕过。

**流程**：

```
┌─────────────────────────────────────────────────────────┐
│              HybridConstraintCompiler                    │
│                                                         │
│  Layer 0: SAFETY MANDATORY (不可绕过，不可降级)           │
│  ┌──────────────────────────────────────────────────┐  │
│  │ z >= 0.02m               ← Z 轴防撞              │  │
│  │ joint ∈ [-2.9, 2.9] rad  ← 关节硬件限位          │  │
│  │ force <= 10.0N           ← 夹爪力上限             │  │
│  │ workspace: x,y∈[-0.5,0.5], z∈[0.02,0.5]         │  │
│  │ human proximity: <0.3m → velocity≤0.10m/s        │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  Layer 1: RULE ENGINE (NL修饰语 + 场景 + 物体属性)       │
│  ┌──────────────────────────────────────────────────┐  │
│  │ 来源            │ 约束                            │  │
│  │ "轻一点"        │ force(药瓶) ≤ 3.0N             │  │
│  │ "慢一点"        │ velocity ≤ 0.10 m/s            │  │
│  │ "别碰水杯"      │ distance(水杯) ≥ 0.05m         │  │
│  │ Scene blocking  │ distance(水杯) ≥ 0.05m         │  │
│  │ fragile物体     │ force ≤ 3.0N                   │  │
│  │ Memory注入      │ {force_n:2.5, velocity_ms:0.10}│  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  Layer 2: BT ALIGNMENT (约束绑定到技能节点)              │
│  ┌──────────────────────────────────────────────────┐  │
│  │ 约束类型          │ 绑定到 BT 技能                 │  │
│  │ force_limit       │ Grasp, GentleGrasp           │  │
│  │ velocity_limit    │ MoveTo, Reach, Push           │  │
│  │ collision_avoid   │ 全部 Action (全局)            │  │
│  │ z_axis_floor      │ 全部 Action (全局)            │  │
│  │ release_height    │ Release                      │  │
│  │ human_proximity   │ MoveTo                       │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  Layer 3: DEDUP + CONFLICT DETECTION                   │
│  ┌──────────────────────────────────────────────────┐  │
│  │ · 同类型+同目标+同技能 → 保留最严格的约束          │  │
│  │ · min_force < max_force 校验                     │  │
│  │ · HARD 约束不可被 SOFT 覆盖                       │  │
│  └──────────────────────────────────────────────────┘  │
│                                                         │
│  输出: ConstraintGraph                                   │
│  ┌──────────────────────────────────────────────────┐  │
│  │ Grasp:                                           │  │
│  │   [HARD] force ∈ (0.1, 2.5] N (取最严格)        │  │
│  │   [HARD] gripper_force ≤ 10.0N                   │  │
│  │                                                  │  │
│  │ MoveTo:                                          │  │
│  │   [SOFT] velocity ≤ 0.10 m/s                    │  │
│  │   [HARD] distance(水杯) ≥ 0.05m                  │  │
│  │   [HARD] z ≥ 0.02m                              │  │
│  │                                                  │  │
│  │ _global:                                         │  │
│  │   [HARD] joint_limits, workspace_bounds,         │  │
│  │          human_proximity                         │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**安全优先原则**：

```
约束冲突时的优先级:
  Safety (HARD)  >  User Preference (SOFT)
  Physics (HARD)  >  Task Requirement
  Fragile Object  >  Speed Preference

示例:
  "轻一点"(force≤3N)  +  "用力抓"(force≥5N)
  → 冲突检测: min_force(5) >= max_force(3) → WARNING
  → 保留更安全的: max_force = 3.0 (fragile物体优先)
```

### 3.5 Step 7: IR Generation — 统一中间表示

**核心问题**：Steps 3-6 产生四种异构产物，下游 CodeArts 无法直接消费。

**流程**：

```
Memory Items ──────┐
Scene Graph ───────┤
Behavior Tree ─────┼──→ RobotTaskIRGenerator.generate()
Constraint Graph ──┘
                          │
                          v
                  RobotTaskIR (Pydantic Model)
                          │
                  model_dump_json()
                          │
                          v
                     JSON 字符串
                          │
        ┌─────────────────┼─────────────────┐
        v                 v                 v
   CodeArts          Isaac Sim          TraceCoder
   (代码生成)         (物理执行)          (异常反思)
```

**编译规则**：

- 每个 BT Action → IR Skill `{target, params, constraints, object}`
- 每个 ConstraintNode → 绑定到 IR Skill 的 `constraints` 字段
- Safety 约束 → 提取到顶层 `precondition_assertions`
- 所有 force/velocity 约束 → 提取优化边界 `optimization_space`
- Memory 偏好 → 注入 `user_context`
- Scene 物体信息 → 注入 `skills[name].object`

---

## 四、数据流全景

```
  Input                     Pipeline                        Output
─────────    ────────────────────────────────────────    ─────────

NL Text ──→ Step 3: Memory.search("老人递药")
               │
               ├→ UserMemory: 关键词+优先级
               ├→ SkillMemory: skill+object精确匹配
               └→ EnvMemory:   场景名匹配
               │
               v
               [MemoryItem × 3]

Raw Data ──→ Step 4: Scene.build(raw_objects)
               │
               ├→ .to_scene_object(): 实例化+label+affordance
               ├→ SpatialReasoner: 两两配对, 11种关系
               └→ blocking: 几何判定 robot→target视线
               │
               v
               SemanticSceneGraph {objects, relations, robot_state}

Memory ────→ Step 5: Planner.plan(instruction, scene, memory)
Scene          │
               ├→ InstructionParser: 动作分类+目标+修饰语+规避
               ├→ 技能模板: pick_and_place → [Reach, Grasp, MoveTo, Release]
               ├→ Memory注入: Gripper→GentleGrasp, force_n=2.5
               ├→ Scene注入: blocking→CheckClear+Avoid
               └→ BTComposer: 组装Sequence根节点
               │
               v
               BehaviorTree {Sequence[Conds + Avoid + 4 Actions]}

BT ────────→ Step 6: Compiler.compile(instruction, bt, scene, memory)
Scene          │
Memory         ├→ Layer 0: SafetyConstraint.mandatory_set()
               ├→ Layer 1: RuleEngine.extract() 5 sources
               ├→ Layer 2: _align_with_bt() 绑定
               └→ Layer 3: _deduplicate() + _resolve_conflicts()
               │
               v
               ConstraintGraph {9 nodes: 6 hard + 1 soft, BT-aligned}

BT ────────→ Step 7: Generator.generate(instruction, bt, cg, scene, memory)
CG               │
Scene            ├→ _build_metadata(): task_id + user_context
Memory           ├→ _build_skills(): BT actions + CG constraints + scene objects
                 ├→ _build_optimization(): force_range + velocity_range
                 └→ _build_memory_context(): constraint_summary + preferences
                 │
                 v
                 RobotTaskIR {ir_version, metadata, skills, optimization, ...}
                 │
                 v
                 model_dump_json() → 标准JSON → CodeArts / IsaacSim / TraceCoder
```

---

## 五、代码量与测试

```
总代码: ~4,500 行 Python + ~500 行 Markdown

robot_intent_agent/
├── config/           1 file   (Pydantic Settings)
├── schemas/          4 files  (Scene, BT, Constraint, IR 数据模型)
├── memory/           5 files  (三层 Memory + Retriever)
├── scene_builder/    1 file   (场景构建器 + 空间推理引擎)
├── planner/          4 files  (抽象接口 + 技能库 + 规则规划器 + LLM预留)
├── constraint/       5 files  (约束图 + 3种约束工厂 + 规则引擎 + 编译器)
├── ir/               1 file   (IR 生成器)
├── integration_tests/4 files  (端到端场景测试)
├── tests/            5 files  (单元测试)
└── demo/             1 file   (CLI 演示)

测试: 136 passed in 0.29s
  单元测试:   127
  集成测试:     9
```

---

## 六、设计决策与原则

### 6.1 不调用 LLM 做确定性计算

空间关系推理、关键词解析、物理约束计算 → 全部用规则引擎。LLM 接口只在 Planner 层预留（`llm_planner.py`），用于处理规则覆盖不到的复杂指令。

### 6.2 安全永远优先

Safety 约束在 Layer 0 注入，优先级 HARD，不可降级、不可绕过、不可被其他约束覆盖。即使 LLM 生成的约束与之冲突，去重阶段也保留 Safety 约束。

### 6.3 所有接口都是强类型

模块间传递 Pydantic 模型或 dataclass，不传裸 dict。下游模块通过类型系统就能知道输入格式，不需要读文档。

### 6.4 可替换性

每一层都有清晰的抽象接口：
- `MemoryInterface` → 可替换为 FAISS
- `TaskPlannerInterface` → 可替换为 LLM
- `RawObjectPercept` → 可替换为真实 VLM 输出

### 6.5 渐进式复杂度

"递给我"这种简单指令也走完整五步流水线，但每步的计算量都很轻（规则匹配、几何计算）。复杂场景（多物体、冲突约束）由同一套框架处理，只是规则引擎激活更多分支。

---

## 七、关键创新点

### 创新 1: 约束图（Constraint Graph）

传统方法把约束作为技能参数（`Grasp(force=3.0)`），约束图把约束提升为**一等公民**：

- 约束有独立的类型系统（Spatial / Physical / Safety / Temporal / Interaction）
- 约束有优先级（HARD / SOFT）
- 约束绑定到特定的 BT 节点
- 约束可以冲突检测和自动调解

### 创新 2: 记忆驱动的规划

不是简单地把用户指令喂给 Planner，而是先用 Memory 检索出用户偏好和历史经验，再注入规划过程。这使得同一个"递药瓶"指令对老人和对年轻人产生完全不同的执行参数。

### 创新 3: 空间关系作为约束源

Scene Builder 不只是输出物体列表，而是推理出 blocking 等空间关系，这些关系自动转化为碰撞避免约束。下游 Planner 不需要理解几何，只需要看"有没有 Blocking 关系"。

---

## 八、与上层系统的接口

```
                            robot_intent_agent
                                   │
                                   │ RobotTaskIR (JSON)
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          v                        v                        v
  ┌───────────────┐    ┌───────────────────┐    ┌──────────────────┐
  │ CodeArts      │    │ Isaac Sim         │    │ TraceCoder       │
  │ Adapter       │    │ Executor          │    │ Adapter          │
  │               │    │                   │    │                  │
  │ 解析 IR       │    │ 解析 skills       │    │ 接收 error_report│
  │ → CaP 模板    │    │ → exec_wrapper    │    │ + IR 上下文      │
  │ → Python 代码 │    │ → 物理执行        │    │ → Reflexion重写  │
  └───────────────┘    └───────────────────┘    └──────────────────┘
```
