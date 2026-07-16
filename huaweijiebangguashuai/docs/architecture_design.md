# 言出必行：具身智能机械臂操作系统 — 系统架构与流程原理

> 华为揭榜挂帅 · 基于 CodeArts 代码智能体的具身指令生成系统
>
> 版本: v1.0 | 日期: 2026-07-15

---

## 一、系统总览

### 1.1 一句话概括

**操作者说一句话 → 系统自动理解意图 → 生成控制代码 → 仿真中真实执行 → 出错自动反思修复。**

### 1.2 核心思想

```
Natural Language + Environment State + Robot Knowledge → Constraint-aware Robot Task IR → Robot Control Code
```

传统机器人编程需要工程师手工编写每个动作的控制指令。本系统将这个过程自动化：大模型（CodeArts）读懂口语意图，自动生成 Python 代码，驱动仿真中的机械臂完成物理动作。如果执行失败，系统能自动诊断原因并重写代码。

### 1.3 系统闭环全景

```
                          ┌─────────────────────────────────────────────────┐
                          │                                                 │
    "请把红色药瓶递给我"    │                                                 │
          │               │                                                 │
          v               │                                                 │
  ┌───────────────┐       │                                                 │
  │ 意图感知部件    │       │        ┌──────────────────────────────────┐     │
  │               │       │        │                                  │     │
  │ NL → Memory   │       │        │  反思闭环 (Reflexion Loop)        │     │
  │   → Scene     │       │        │                                  │     │
  │   → Planner   │       │        │  error_report.json               │     │
  │   → Constraint│       │        │       │                          │     │
  │   → IR        │       │        │       v                          │     │
  └───────┬───────┘       │        │  CodeArts 重新生成策略代码         │     │
          │               │        │       │                          │     │
          v               │        │       v                          │     │
  ┌───────────────┐       │        │  安全校验 (code_validator)        │     │
  │ CodeArts      │       │        │       │                          │     │
  │ 策略代码生成   │       │        │       v                          │     │
  └───────┬───────┘       │        │  重新下发给仿真执行                │     │
          │               │        └──────────────────────────────────┘     │
          v               │                         ↑                       │
  ┌───────────────┐       │              执行失败时触发                      │
  │ 安全校验       │       │                                                 │
  │ code_validator│       │                                                 │
  └───────┬───────┘       │                                                 │
          │               │                                                 │
          v               │                                                 │
  ┌───────────────┐       │                                                 │
  │ Isaac Sim     │       │                                                 │
  │ 物理仿真执行   │───→ 成功 ✅ ──→ 返回结果给前端                            │
  └───────┬───────┘       │                                                 │
          │               │                                                 │
          │ 失败 🚨       │                                                 │
          v               │                                                 │
  ┌───────────────┐       │                                                 │
  │ TraceProbe    │       │                                                 │
  │ 异常探针       │─────────────────────────────────────────────────────────┘
  │ 截获现场坐标   │
  │ 生成错误报告   │
  └───────────────┘
```

---

## 二、分层架构

整个系统分为 **四层**：

```
┌─────────────────────────────────────────────────────────────┐
│                    第 4 层: 人机交互                          │
│                                                             │
│  Gradio Web UI (src/ui/app.py)                              │
│  · 自然语言输入框                                           │
│  · 任务预设下拉菜单 (7 个预制场景)                            │
│  · JSON 高亮渲染器                                          │
│  · 状态卡片                                                 │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (FastAPI)
┌──────────────────────────v──────────────────────────────────┐
│                    第 3 层: 中枢路由                          │
│                                                             │
│  FastAPI Server (src/backend/server.py)                     │
│  · 承接前端请求                                             │
│  · 调用意图感知 + 策略生成                                   │
│  · 下发指令到仿真引擎                                        │
│  · 接收异常触发反思                                          │
└───────┬───────────────────┬──────────────────┬──────────────┘
        │                   │                  │
        v                   v                  v
┌─────────────────────────────────────────────────────────────┐
│                    第 2 层: 智能核心                          │
│                                                             │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ 意图感知部件      │  │ CodeArts     │  │ 反思自愈       │  │
│  │ robot_intent_    │  │ 策略代码生成  │  │ Reflexion      │  │
│  │ agent/           │  │              │  │                │  │
│  │                  │  │ Prompt:      │  │ Prompt:        │  │
│  │ NL → Scene →    │  │ codearts_    │  │ reflexion_     │  │
│  │ BT → Constraint  │  │ system_      │  │ prompt.md      │  │
│  │ → RobotTaskIR    │  │ prompt.md    │  │                │  │
│  └────────┬─────────┘  └──────┬───────┘  └───────┬────────┘  │
│           │                   │                   │           │
│           v                   v                   v           │
│  ┌──────────────────────────────────────────────────────┐    │
│  │         code_validator.py  (安全校验层)               │    │
│  │  · 语法检查 (AST)                                     │    │
│  │  · 黑名单拦截 (os, subprocess, eval, exec...)        │    │
│  │  · 白名单校验 (只允许元 API 函数)                     │    │
│  │  · 安全断言检查 (assert z >= 0.02)                   │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           v
┌─────────────────────────────────────────────────────────────┐
│                    第 1 层: 物理执行                          │
│                                                             │
│  Isaac Sim 6.0.1 + Franka Panda 7-DOF                      │
│                                                             │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ exec_wrapper.py  │  │ get_scene_   │  │ trace_probe.py│  │
│  │                 │  │ json.py      │  │               │  │
│  │ move_to_pose()  │  │              │  │ 碰撞/抓空/超限 │  │
│  │ move_joints()   │  │ 感知场景物体  │  │ 异常捕获       │  │
│  │ open_gripper()  │  │ 提取3D坐标   │  │ 现场快照       │  │
│  │ close_gripper() │  │ 导出scene_   │  │ error_report   │  │
│  │ move_linear()   │  │ state.json   │  │ .json生成      │  │
│  │                 │  │              │  │               │  │
│  │ assert z>=0.02  │  │              │  │               │  │
│  │ assert force<=10│  │              │  │               │  │
│  └─────────────────┘  └──────────────┘  └───────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、意图感知部件 (robot_intent_agent) 详细原理

意图感知是整个系统的**前端大脑**。它解决的是一个关键问题：**如何把模糊的口语指令变成下游 AI 能精确消费的结构化数据？**

### 3.1 为什么需要意图感知

LLM（大语言模型）虽然能理解自然语言，但直接让 LLM 生成机器人控制代码存在几个致命问题：

1. **幻觉**：LLM 可能生成不存在的函数名或参数
2. **不安全**：LLM 可能忽略物理安全约束（如 Z 轴高度底线）
3. **不可控**：同样的输入可能产生完全不同的输出格式
4. **无法验证**：生成的代码是否正确，缺乏中间验证层

意图感知部件通过在 LLM 调用前建立**结构化理解层**来解决这些问题：

```
       传统方式:  口语 ──→ LLM ──→ Python 代码  (不可控、不安全)

       我们的方式: 口语 ──→ [意图感知] ──→ 结构化 IR ──→ LLM ──→ 代码
                         (可控、可验证)    (标准格式)    (精确输入)
```

### 3.2 五步流水线原理

每一步解决一个特定的子问题，层层递进：

```
Step 3: Memory     → "这个用户是谁？有什么习惯？历史上怎么做类似的？"
Step 4: Scene      → "周围有什么东西？它们在哪里？空间关系怎样？"
Step 5: Planner    → "应该先做什么、后做什么？"
Step 6: Constraint → "每个动作受什么限制？什么绝对不能违反？"
Step 7: IR         → "把所有信息打包成下游能直接用的格式"
```

#### Step 3: Memory Retrieval — 记忆检索

**要解决的问题**：同一个"递药瓶"指令，对成年人和对老人，执行方式完全不同。

**原理**：
```
输入 → 三层 Memory 并行检索 → 带权重的搜索结果

UserMemory:
  检索 "老人" → {hand: "left", grip: "gentle"}
  匹配策略: 关键词子串 + 优先级 (HIGH>MEDIUM>LOW) + 访问频率加权

SkillMemory:
  检索 "药瓶 grasp" → {skill: "gentle_grasp", force_n: 2.5, success: true}
  匹配策略: 技能名精确匹配 (10分) + 物体名匹配 (8分) + 成功经验加权 (5分)

EnvironmentMemory:
  检索 "桌面" → {table_height_m: 0.72}
  匹配策略: 场景名匹配 (12分) + 物体匹配 (8分)

结果排序:
  score = 关键词匹配分 + 优先级加权 + 访问频率 - 时间衰减 (预留)
```

**为什么设计成三层**：用户偏好、技能经验、环境先验是三种完全不同来源的知识，分开存储可以独立更新（换用户只刷新 UserMemory，换环境只刷新 EnvironmentMemory）。

#### Step 4: Scene Building — 场景语义理解

**要解决的问题**：只知道"药瓶在(0.15, 0.05, 0.03)"是不够的，还需要知道"水杯挡在药瓶前面"。

**原理**：
```
RawObjectPercept 列表
    │
    ├── 物体实例化
    │   自动推断语义标签: "药"→medicine_bottle, "杯"→cup
    │   自动推断可供性 (Affordance): "瓶"→[GRASPABLE, FRAGILE]
    │   自动推断材质属性: plastic, glass, ceramic
    │
    ├── 空间关系推理 (两两配对)
    │   ｜谓词          ｜ 判定规则                    ｜
    │   ｜left_of       ｜ X轴差 > 2cm                ｜
    │   ｜right_of      ｜ 双向输出 (A左B → B右A)     ｜
    │   ｜above/below   ｜ Z轴中点比较                 ｜
    │   ｜near          ｜ 欧氏距离 < 10cm             ｜
    │   ｜blocking      ｜ 几何: robot→target连线的垂距 ｜
    │   ｜supporting    ｜ Z重叠 + XY投影重叠           ｜
    │
    └── 输出: SemanticSceneGraph
```

**blocking（阻挡）的判定是空间推理的核心**：

```
robot(0,0) ────────────────→ target(0.15, 0.05)
                │
                │  垂距
                │
            obstacle(0.08, 0.03)

判定: 垂距 < obstacle_radius  →  BLOCKING
```

#### Step 5: Task Planning — 任务规划

**要解决的问题**：从"递给我"这三个字，推断出"先移动到物体上方 → 抓取 → 移动到用户 → 释放"这一连串动作。

**原理**：
```
中文指令解析 (纯规则, 不调用 LLM)
    │
    ├── 动作分类:  "递/拿/取" → pick_and_place
    │             "推/挪"   → push
    │             "摞/叠"   → stack
    │
    ├── 目标提取:  "把...药瓶..." → "红色药瓶"
    │
    ├── 修饰语:    "轻一点" → grip_style=gentle
    │             "慢一点" → velocity=0.10
    │
    └── 规避:      "别碰...水杯" → avoid=["水杯"]

    │
    v
行为树组装 (Behavior Tree)
    │
    ├── 技能模板: pick_and_place → [Reach, Grasp, MoveTo, Release]
    │
    ├── Memory 注入:
    │   grip_style=gentle → Grasp 替换为 GentleGrasp
    │   force_n=2.5 → 注入 Grasp.params
    │
    ├── Scene 注入:
    │   水杯阻挡药瓶 → 前置条件节点: CheckClear(水杯)
    │                 动作节点插入: Avoid(水杯)
    │
    └── 输出: BehaviorTree (Sequence 根节点)
```

**注意**：Planner **不生成 Python 代码**，只生成任务逻辑树。代码生成留给 CodeArts。

#### Step 6: Constraint Compilation — 约束编译

**要解决的问题**：从"机器人应该做什么"升级到"机器人必须满足什么条件才能做"。

**这是系统最关键的升级**。没有约束编译，"轻一点"只是 Grasp 技能的一个参数字段。有了约束编译，"轻一点"变成了一条**硬约束**：`force(药瓶) ∈ (0.1, 3.0]N`，绑在 Grasp 节点上，任何下游模块都不能违反。

**原理**：
```
四层递进编译:

Layer 0: 安全红线 (不可绕过, 不可降级)
    z ≥ 0.02m           ← 防止机械臂撞击桌面
    joint ∈ ±2.9 rad    ← Franka Panda 硬件限位
    force ≤ 10N          ← 夹爪最大力
    workspace bounds     ← 工作空间边界
    human proximity      ← 接近用户时降速

Layer 1: 规则引擎
    "轻一点"       → force(药瓶) ≤ 3.0N
    "慢一点"       → velocity ≤ 0.10 m/s
    "别碰水杯"     → distance(水杯) ≥ 0.05m
    Scene blocking → distance(水杯) ≥ 0.05m
    fragile物体    → force ≤ 3.0N
    Memory注入     → {force_n: 2.5, velocity_ms: 0.10}

Layer 2: 行为树对齐
    force_limit      → 绑定到 Grasp, GentleGrasp
    velocity_limit   → 绑定到 MoveTo, Reach, Push
    collision_avoid  → 绑定到全部 Action (全局)
    release_height   → 绑定到 Release
    z_axis_floor     → 绑定到全部 Action (全局)

Layer 3: 去重 + 冲突检测
    同类型+同目标+同技能 → 保留最严格的约束
    min_force >= max_force → 报告冲突
```

#### Step 7: IR Generation — 统一输出

**要解决的问题**：Steps 3-6 产生了四种异构产物（记忆列表、场景图、行为树、约束图），下游 CodeArts 无法直接消费这么多种格式。

**原理**：IR Generator 是"编译器后端"，把所有中间产物翻译成一种统一的格式：

```
Memory Items ──────┐
Scene Graph ───────┤
Behavior Tree ─────┼──→ RobotTaskIR ──→ JSON ──→ CodeArts
Constraint Graph ──┘

编译规则:
  · 每个 BT Action → IR Skill (目标 + 参数 + 约束 + 物体信息)
  · 每个 ConstraintNode → 绑定到对应 IR Skill 的 constraints 字段
  · Safety 约束 → 提取到顶层 precondition_assertions
  · 所有 force/velocity 约束 → 提取优化边界 (optimization_space)
  · Memory 偏好 → 注入 user_context
```

---

## 四、安全设计

系统的安全性通过**四道防线**保证：

```
第 1 道: Schema 验证 (Pydantic)
    所有模块间传递的数据都是强类型 Pydantic 模型。
    "把字典当接口"的方式在整个系统中被禁止。

第 2 道: 安全断言 (exec_wrapper.py)
    assert z >= 0.02     → 机械臂 Z 轴底线
    assert force <= 10   → 最大夹爪力
    assert joint ∈ ±2.9  → 关节硬件限位
    这些断言在 ISAC SIM 层强制执行，CodeArts 生成的代码无法绕过。

第 3 道: 代码校验 (code_validator.py)
    黑名单拦截: os, subprocess, eval, exec, __import__...
    白名单放行: move_to_pose, close_gripper, get_scene_objects...
    语法检查: AST 解析
    所有通过 CodeArts 生成的代码必须通过校验才能执行。

第 4 道: 运行时探针 (trace_probe.py)
    监听: 碰撞、抓空、IK失败、关节超限
    截获: 异常瞬间的三维坐标、关节角、夹爪状态
    输出: error_report.json → 触发 Reflexion 闭环
```

---

## 五、数据契约

系统中各个模块之间的接口契约：

| 模块 | 输出 | 格式 | 消费者 |
|------|------|------|--------|
| Intent Agent | RobotTaskIR | Pydantic → JSON | CodeArts / Executor |
| CodeArts | Python 代码 | str | code_validator |
| code_validator | 校验结果 | dict{passed, violations} | Executor |
| exec_wrapper | 执行状态 | bool + 状态数据 | trace_probe |
| trace_probe | error_report | JSON → logs/ | Reflexion → CodeArts |
| get_scene_json | scene_state | JSON → logs/ | Intent Agent |

**关键原则**: 所有模块间通信使用 Pydantic 模型或 JSON Schema，不传递裸字典、不传递裸字符串（除非是代码文本本身）。

---

## 六、项目文件总览

```
codearts-embodied-ai/
│
├── README.md                          # 项目总说明
├── 开发说明.md                         # Sprint 1 + 团队分工
│
├── huaweijiebangguashuai/             # 主工程
│   ├── docs/                          # 设计文档
│   │   ├── intent_schema_v1.json      # 意图 JSON Schema
│   │   ├── robot_meta_api_whitepaper.md  # 元 API 白皮书
│   │   ├── sprint1_plan.md            # Sprint 规划
│   │   ├── architecture_design.md     # 本文档
│   │   └── weekly_reports/            # 周报
│   │
│   ├── prompts/                       # LLM 提示词
│   │   ├── intent_parser_prompt.md    # 意图解析 (3 Few-shot)
│   │   ├── codearts_system_prompt.md  # CaP 策略生成 (4 Few-shot)
│   │   └── reflexion_prompt.md        # 反思自愈模板
│   │
│   ├── src/                           # 核心源码
│   │   ├── ui/app.py                  # Gradio 前端
│   │   ├── agent/                     # 策略生成 + 校验
│   │   ├── isaac/                     # Isaac Sim 元 API
│   │   ├── backend/server.py          # FastAPI 中枢
│   │   └── monitor/                   # 异常探针
│   │
│   ├── tests/                         # 测试
│   └── logs/                          # 运行时日志 (黑匣子)
│
└── Intent-Understanding-.../          # 意图感知子模块
    └── robot_intent_agent/            # (前文详细描述)
```
