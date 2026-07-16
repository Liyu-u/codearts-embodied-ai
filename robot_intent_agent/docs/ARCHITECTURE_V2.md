# Robot Intent Agent v2.0 — 架构升级设计

> **原则**: 不推翻现有架构，沿 **Task Compiler → Robot IR → Execution Feedback → Self Improvement** 方向增强
>
> **核心升级**: Hybrid LLM+Rule Planner | RobotTaskIR 2.0 | Skill Feasibility | Vector Memory | Feedback Loop | Uncertainty Scene
>
> 状态: 设计方案 | 日期: 2026-07-16

---

## 零、升级总览

### v1.0 → v2.0 变化对照

| 模块 | v1.0 | v2.0 | 升级原因 |
|------|------|------|---------|
| **Planner** | 纯规则 | LLM + Rule Validator | 长任务/新任务泛化不足 |
| **IR** | Task-Skill 两级 | Task-Skill-Motion-Safety-Opt 五级 | 缺少执行层语义 |
| **Feasibility** | 默认可执行 | 独立评估模块 | 真实机器人可达性判断 |
| **Memory** | Keyword | Embedding + Vector DB + Rerank | 语义检索 + 经验学习 |
| **Feedback** | 无 | Monitor → Reflect → Memory Update | 缺乏自主改进闭环 |
| **Scene** | 确定性 | 不确定性建模 + VLM Hybrid | 感知不可靠的现实 |

### 不变的部分（v1.0 优势保留）

- Constraint Graph（约束一等公民）
- Memory-driven Planning（三层记忆分治）
- Semantic Scene Graph（11 种空间关系）
- 五步流水线架构
- Pydantic 类型系统
- Safety-first 原则

---

## 一、升级后总体架构

```
                         ┌─────────────────────────┐
                         │    User Instruction      │
                         │  "把红色药瓶递给老人，    │
                         │   轻一点，别碰水杯"      │
                         └────────────┬────────────┘
                                      │
                                      v
┌─────────────────────────────────────────────────────────────────────┐
│                    INTENT UNDERSTANDING LAYER                        │
│                                                                     │
│  ┌──────────────────────┐        ┌──────────────────────────────┐  │
│  │   Memory System v2   │        │    Scene Understanding v2    │  │
│  │                      │        │                              │  │
│  │ Query → Embedding    │        │ VLM + Geometric Hybrid       │  │
│  │ → VectorDB → Rerank  │        │ Uncertainty Modeling         │  │
│  │ → Memory Injection   │        │ Dynamic Affordance           │  │
│  └──────────┬───────────┘        └──────────────┬───────────────┘  │
│             │                                   │                  │
│             └───────────────┬───────────────────┘                  │
│                             │                                      │
│                             v                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │               Hybrid Task Planner v2                          │  │
│  │                                                               │  │
│  │  User Instruction ──→ LLM Task Decomposer                     │  │
│  │                           │                                   │  │
│  │                           v                                   │  │
│  │                    Candidate Behavior Tree(s)                  │  │
│  │                           │                                   │  │
│  │                           v                                   │  │
│  │  ┌─────────────────────────────────────────────────────┐     │  │
│  │  │ Rule Validator                                       │     │  │
│  │  │  · BT Structure Check  · Skill Availability         │     │  │
│  │  │  · Safety Compliance   · Constraint Feasibility     │     │  │
│  │  └─────────────────────────────────────────────────────┘     │  │
│  │                           │                                   │  │
│  │                           v                                   │  │
│  │                 Validated Behavior Tree                       │  │
│  └──────────────────────────────┬───────────────────────────────┘  │
│                                 │                                  │
│                                 v                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Skill Feasibility Estimator                      │  │
│  │                                                               │  │
│  │  For each skill in BT:                                        │  │
│  │    Scene + Robot State + Skill + Constraints                  │  │
│  │         │                                                     │  │
│  │         v                                                     │  │
│  │    {feasibility_score, failure_risk, alternative_skills}      │  │
│  └──────────────────────────────┬───────────────────────────────┘  │
│                                 │                                  │
│                                 v                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Constraint Compiler (v1.0 保留 + 增强)           │  │
│  │                                                               │  │
│  │  Layer 0: Safety Mandatory (不可变)                           │  │
│  │  Layer 1: Rule + Memory + Scene Constraints                   │  │
│  │  Layer 2: LLM-inferred Constraints (新增)                     │  │
│  │  Layer 3: Feasibility-as-Constraint (新增)                    │  │
│  │  Layer 4: BT Alignment + Dedup + Conflict Resolution          │  │
│  └──────────────────────────────┬───────────────────────────────┘  │
│                                 │                                  │
│                                 v                                  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  RobotTaskIR 2.0 Generator                    │  │
│  │                                                               │  │
│  │  五层结构:                                                     │  │
│  │    Task Layer    — 任务元数据 + 子任务分解                     │  │
│  │    Skill Layer   — 技能序列 + 约束 + 可行性                    │  │
│  │    Motion Layer  — 轨迹需求 + 运动规划器选择                    │  │
│  │    Safety Layer  — 全局安全约束 + 验证条件                      │  │
│  │    Optimization Layer — 可调参数边界                            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │
                                  v
┌─────────────────────────────────────────────────────────────────────┐
│                      EXECUTION & FEEDBACK                            │
│                                                                     │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │ CodeArts     │    │ Motion       │    │ Execution Monitor    │  │
│  │ Code Gen     │    │ Planner      │    │                      │  │
│  │              │    │ (RRT*/IK)    │    │ Sensor Feedback      │  │
│  └──────┬───────┘    └──────┬───────┘    │ Error Detection      │  │
│         │                   │            │ State Tracking       │  │
│         v                   v            └──────────┬───────────┘  │
│  ┌──────────────────────────────────────┐            │              │
│  │         Robot Execution              │            │              │
│  │    Isaac Sim / ROS2 / Real Robot     │            │              │
│  └──────────────────┬───────────────────┘            │              │
│                     │                                │              │
│                     v                                v              │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Reflection Engine                            │  │
│  │                                                               │  │
│  │  Failure Report → Root Cause Analysis → Recovery Strategy     │  │
│  │       │                                                       │  │
│  │       ├──→ Constraint Compiler (tighten constraints)          │  │
│  │       ├──→ Memory (update experience)                         │  │
│  │       └──→ Planner (re-plan with new constraints)             │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Memory Update Loop                            │  │
│  │                                                               │  │
│  │  Execution → Outcome → Reflection → Update Memory             │  │
│  │    (success)  → 经验强化 (boost access_count)                  │  │
│  │    (failure)  → 经验衰减 (reduce confidence)                  │  │
│  │    (recovery) → 新增恢复经验 (recovery skill params)           │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、六大升级模块详细设计

---

### 升级 1: Hybrid LLM + Rule Planner

#### 问题诊断

v1.0 纯规则 Planner 的局限性：
- `"把药瓶递给老人，但如果她在睡觉就放在床头柜上"` → 条件分支无法解析
- `"先把桌上的杂物清理干净，再把药瓶放上去"` → 子任务拆解失败
- 新技能组合（如"一边倒水一边递药"）→ 模板不覆盖

#### 升级方案

```
                 User Instruction
                       │
           ┌───────────┴───────────┐
           │                       │
           v                       v
    ┌──────────────┐      ┌──────────────────┐
    │ Rule Parser  │      │   LLM Planner    │
    │ (v1.0 保留)  │      │   (新增)         │
    │              │      │                  │
    │ 简单指令     │      │ 复杂/长指令      │
    │ 确定性高     │      │ 子任务拆解       │
    │ 零延迟       │      │ 新技能组合       │
    └──────┬───────┘      └────────┬─────────┘
           │                       │
           │  ┌────────────────────┘
           │  │
           v  v
    ┌──────────────────────────────────────────┐
    │        Candidate Behavior Tree(s)         │
    │                                          │
    │  LLM 可输出 1-3 个候选 BT,               │
    │  每个附带 confidence score               │
    └────────────────────┬─────────────────────┘
                         │
                         v
    ┌──────────────────────────────────────────┐
    │           Rule Validator                  │
    │                                          │
    │  ✅ BT Structure: 是否合法递归结构        │
    │  ✅ Skill Check:   技能是否在SkillCatalog │
    │  ✅ Safety Gate:   是否违反硬安全约束     │
    │  ✅ Constraint:    参数是否在物理范围内   │
    │  ✅ Feasibility:   运动学是否可达         │
    └────────────────────┬─────────────────────┘
                         │
                         v
                 Validated Behavior Tree
```

#### LLM Planner 接口

```python
from abc import ABC, abstractmethod
from typing import List, Optional

class LLMTaskPlanner(ABC):
    """
    LLM 任务规划器接口。

    负责: 复杂长指令理解、子任务拆解、候选 BT 生成
    不负责: 安全验证（交给 Rule Validator）
    """

    @abstractmethod
    def decompose(
        self,
        instruction: str,
        scene_summary: str,
        memory_summary: str,
    ) -> List[SubTask]:
        """将长指令拆解为子任务序列"""
        ...

    @abstractmethod
    def generate_candidates(
        self,
        sub_tasks: List[SubTask],
        scene_graph: dict,
        skill_catalog: dict,
    ) -> List[CandidateBT]:
        """为每个子任务生成 1-3 个候选 Behavior Tree"""
        ...


@dataclass
class SubTask:
    """子任务"""
    id: str
    description: str              # "先拿起药瓶"
    action_type: str              # pick_and_place
    target: str                   # "药瓶"
    dependencies: List[str]       # 依赖的前置子任务 ID


@dataclass
class CandidateBT:
    """候选行为树"""
    behavior_tree: dict           # BT JSON
    confidence: float             # 0.0 - 1.0
    rationale: str                # LLM 解释为什么这样规划
```

#### LLM Prompt 结构

```markdown
## System Prompt

You are a robot task planner. Given a user instruction, scene summary,
and memory context, generate a Behavior Tree.

## Rules
1. Use ONLY skills from the Skill Catalog: {skill_catalog}
2. Output valid Behavior Tree JSON conforming to the schema below
3. For each action, explain your reasoning in the "annotation" field
4. If uncertain, generate 2-3 alternative plans with confidence scores
5. NEVER generate Python code. Output Behavior Tree JSON only.

## Context
- Instruction: {instruction}
- Scene: {scene_summary}
- Memory: {memory_summary}

## Output Schema
{schema}
```

#### Rule Validator 机制

```python
class RuleValidator:
    """对 LLM 生成的 Behavior Tree 进行合法性校验"""

    VALIDATION_RULES = [
        "bt_structure",        # 递归结构合法
        "skill_exists",        # 每个 Action 的 skill_name 在 SkillCatalog
        "safety_gate",         # 不违反 z≥0.02, force≤10N 等
        "param_range",         # 参数在物理约束范围内
        "no_forbidden_ops",    # 无 os.system, eval 等
    ]

    def validate(
        self, bt: BehaviorTree, context: ValidationContext
    ) -> ValidationResult:
        """
        返回: {valid: bool, violations: List, fixed_bt: Optional[BehaviorTree]}
        如果可以自动修复 → 返回 fixed_bt
        如果不可修复 → 返回 violations，触发 LLM 重新生成
        """
```

#### 选择策略

```
简单指令 (规则覆盖) → Rule Parser (零延迟)
    "把药瓶递给我"  → 直接模板匹配

中等指令 (规则部分覆盖) → Rule + LLM 补充
    "把药瓶递给老人，轻一点" → 规则做主干，LLM 补充条件

复杂长指令 → LLM 全权处理
    "先清理桌面杂物，再拿药瓶，如果老人醒着就递过去，
     否则放床头柜，最后把水杯也拿过去"
    → LLM 拆解为 3-4 个子任务，分别生成 BT
```

---

### 升级 2: RobotTaskIR 2.0 — 五层中间表示

#### 设计理念

类似 LLVM IR：高级语言（自然语言）→ IR → 不同机器人后端（Isaac Sim / ROS2 / 真机）。

```
自然语言
    ↓
RobotTaskIR 2.0   ← 五层结构, 每层独立可消费
    ↓
┌─────────────────┬──────────────────┬──────────────────┐
│ CodeArts        │ RRT* / TrajOpt   │ Safety Monitor   │
│ (代码生成)       │ (运动规划)       │ (运行时验证)      │
└─────────────────┴──────────────────┴──────────────────┘
```

#### 五层 Schema

```json
{
  "ir_version": "2.0.0",

  // ═══════════════════════════════════════
  // Layer 1: Task Layer — 任务描述
  // ═══════════════════════════════════════
  "task": {
    "task_id": "task-001",
    "raw_instruction": "请把红色药瓶递给老人",
    "language": "zh",
    "sub_tasks": [
      {
        "id": "sub-1",
        "description": "取出药瓶",
        "dependencies": [],
        "priority": "required"
      }
    ],
    "user_context": { "hand_preference": "left" }
  },

  // ═══════════════════════════════════════
  // Layer 2: Skill Layer — 技能序列
  // ═══════════════════════════════════════
  "skills": {
    "Grasp": {
      "skill_name": "GentleGrasp",
      "target": "红色药瓶",
      "target_id": "obj-001",
      "object": {
        "position": {
          "value": [0.15, 0.05, 0.03],
          "confidence": 0.87,
          "uncertainty_m": 0.02
        },
        "affordances": ["graspable", "fragile"],
        "affordance_confidence": 0.92
      },
      "constraints": {
        "force": { "max_n": 2.5, "min_n": 0.1 },
        "fragile": true
      },
      "feasibility": {
        "score": 0.92,
        "reachable": true,
        "grasp_quality": 0.85,
        "failure_risk": "low"
      },
      "preconditions": [
        "gripper_empty",
        "target_visible",
        "path_clear"
      ],
      "expected_effects": [
        "target_in_hand",
        "gripper_closed"
      ]
    }
  },

  // ═══════════════════════════════════════
  // Layer 3: Motion Layer — 运动需求
  // ═══════════════════════════════════════
  "motion": {
    "planner": "RRTStar",
    "collision_check": true,
    "trajectory_constraints": {
      "max_velocity_ms": 0.10,
      "max_acceleration_ms2": 0.5,
      "max_jerk_ms3": 1.0
    },
    "waypoints": [
      {
        "skill": "Reach",
        "goal_pose": {
          "position": [0.15, 0.05, 0.13],
          "orientation": [0, 0, 0, 1],
          "tolerance_m": 0.005
        }
      }
    ],
    "ik_preferences": {
      "solver": "TRAC_IK",
      "null_space_behavior": "joint_center"
    }
  },

  // ═══════════════════════════════════════
  // Layer 4: Safety Layer — 安全约束
  // ═══════════════════════════════════════
  "safety": {
    "global": [
      { "type": "z_floor", "value": 0.02, "unit": "m", "priority": "HARD" },
      { "type": "joint_limit", "value": 2.9, "unit": "rad", "priority": "HARD" },
      { "type": "gripper_force_max", "value": 10.0, "unit": "N", "priority": "HARD" },
      { "type": "workspace", "x": [-0.5, 0.5], "y": [-0.5, 0.5], "z": [0.02, 0.5], "priority": "HARD" },
      { "type": "human_proximity", "radius_m": 0.3, "max_velocity_ms": 0.10, "priority": "HARD" }
    ],
    "per_skill": {
      "Grasp": [
        { "type": "force_limit", "max_n": 2.5 },
        { "type": "collision_avoid", "obstacle": "水杯", "min_distance_m": 0.05 }
      ]
    },
    "runtime_checks": [
      "grasp_verification",
      "collision_monitoring",
      "force_feedback_loop"
    ]
  },

  // ═══════════════════════════════════════
  // Layer 5: Optimization Layer — 优化空间
  // ═══════════════════════════════════════
  "optimization": {
    "objectives": ["max_safety", "min_time"],
    "tunable_params": {
      "force_n": { "range": [0.1, 2.5], "current": 2.5 },
      "velocity_ms": { "range": [0.05, 0.10], "current": 0.10 },
      "z_offset_m": { "range": [0.02, 0.10], "current": 0.05 }
    },
    "trade_offs": {
      "speed_vs_safety": "prefer_safety",
      "force_vs_grasp_quality": "prefer_gentle"
    }
  },

  // ═══════════════════════════════════════
  // 元数据
  // ═══════════════════════════════════════
  "metadata": {
    "planner": "HybridPlanner(LLM+Rule)",
    "generated_at": "2026-07-16T10:30:00Z",
    "behavior_tree": { /* 完整行为树 */ },
    "scene": { /* 语义场景图 */ },
    "memory_context": { /* 记忆上下文 */ }
  }
}
```

#### 层次间依赖关系

```
Task Layer ──────→ 定义"做什么" (What)
    │
    v
Skill Layer ─────→ 定义"怎么做" (How)
    │
    v
Motion Layer ────→ 定义"怎么动" (How to move)
    │
    v
Safety Layer ────→ 定义"不能做什么" (Must NOT)
    │
    v
Optimization Layer → 定义"可以调什么" (Can tune)
```

每层可被不同的下游模块独立消费：
- **CodeArts** 消费 Skill Layer → 生成 Python 代码
- **RRT\*/TrajOpt** 消费 Motion Layer → 轨迹规划
- **Safety Monitor** 消费 Safety Layer → 运行时验证
- **Hyperparameter Tuner** 消费 Optimization Layer → 参数搜索

---

### 升级 3: Skill Feasibility Estimator

#### 设计动机

v1.0 默认"规划出的技能一定可执行"。真实机器人需要判断：
- IK 是否可解？（目标位姿在工作空间内吗？）
- 是否被遮挡？（视线/路径受阻？）
- 抓取质量如何？（物体形状、材质、尺寸？）

#### 模块设计

```python
@dataclass
class FeasibilityResult:
    skill_name: str
    target: str
    score: float                    # 0.0 - 1.0
    reachable: bool                 # IK 可解
    graspable: bool                 # 抓取可行
    collision_free: bool            # 路径无碰撞
    failure_risk: str               # "low" | "medium" | "high"
    failure_reasons: List[str]      # 如果不可行, 原因列表
    suggested_alternatives: List[str]  # 可行替代方案


class SkillFeasibilityEstimator:
    """
    评估每个 BT Action 的可执行性。

    评估维度:
        1. Reachability    — 目标位姿是否在 workspace 内
        2. Occlusion       — 目标是否被遮挡
        3. Grasp Quality   — 物体是否可抓取 (形状/材质)
        4. Payload         — 物体是否超重
        5. Clearance       — 路径是否有足够空间
        6. Uncertainty     — 感知不确定性下的成功概率
    """

    def __init__(self, robot_model: RobotModel):
        self.robot = robot_model

    def evaluate(
        self,
        skill_name: str,
        target: SceneObject,
        constraints: List[ConstraintNode],
        scene: SemanticSceneGraph,
    ) -> FeasibilityResult:
        """
        综合评估。
        """
        scores = {
            "reachability": self._check_reachability(target),
            "occlusion": self._check_occlusion(target, scene),
            "grasp_quality": self._check_grasp_quality(target, skill_name),
            "clearance": self._check_clearance(target, scene),
            "uncertainty": self._uncertainty_penalty(target),
        }
        score = sum(scores.values()) / len(scores)
        return FeasibilityResult(score=score, ...)

    def _check_reachability(self, target: SceneObject) -> float:
        """IK 求解: 在工作空间内 → 1.0, 边缘 → 0.5, 外部 → 0.0"""
        pos = target.position
        if not self.robot.workspace_contains(pos):
            return 0.0
        # 置信度随距离增加而衰减
        dist = np.linalg.norm([pos.x, pos.y, pos.z])
        return max(0.0, 1.0 - dist / self.robot.max_reach)

    def _check_grasp_quality(
        self, target: SceneObject, skill_name: str
    ) -> float:
        """基于物体属性评估抓取质量"""
        score = 1.0
        if target.bbox.width > 0.08:  # 太宽
            score -= 0.3
        if "slippery" in target.attributes:
            score -= 0.3
        if "irregular" in target.attributes:
            score -= 0.2
        return max(0.1, score)

    def _uncertainty_penalty(self, target: SceneObject) -> float:
        """感知不确定性惩罚"""
        return target.position_confidence or 0.8
```

#### 与 Constraint Compiler 的关系

Feasibility 结果可以**转化为约束**注入 Constraint Graph：

```python
# Feasibility → Constraint 转化
if feasibility.score < 0.5:
    # 不可行 → 硬约束阻止执行
    cg.add_hard(BlockExecutionConstraint(
        skill=skill_name,
        reason=feasibility.failure_reasons[0],
        alternatives=feasibility.suggested_alternatives,
    ))
elif feasibility.score < 0.7:
    # 低可行性 → 软约束标记风险
    cg.add_soft(RiskWarningConstraint(
        skill=skill_name,
        risk=feasibility.failure_risk,
    ))
```

---

### 升级 4: Vector Memory + Experience Learning

#### 架构升级

```
                    v1.0                          v2.0
            ┌──────────────┐            ┌──────────────────────┐
  Query ──→ │ Keyword Match│    Query ──→│ Embedding Model      │
            │ (子串+正则)   │            │ (sentence-transformer)│
            └──────┬───────┘            └──────────┬───────────┘
                   │                               │
                   v                               v
            ┌──────────────┐            ┌──────────────────────┐
            │ In-Memory    │            │ Vector Database       │
            │ Dict Store   │            │ (FAISS / Chroma)      │
            └──────────────┘            └──────────┬───────────┘
                                                   │
                                                   v
                                          ┌──────────────────────┐
                                          │ Cross-Encoder Rerank │
                                          │ (relevance scoring)  │
                                          └──────────┬───────────┘
                                                     │
                                                     v
                                          ┌──────────────────────┐
                                          │ Memory Injection     │
                                          │ (v1.0 格式兼容)       │
                                          └──────────────────────┘
```

#### 新增 Experience Update Loop

```
            ┌─────────────────────────────────────────┐
            │         Experience Update Loop           │
            │                                         │
            │  Execution Complete                      │
            │       │                                 │
            │       v                                 │
            │  ┌──────────┐                           │
            │  │ Outcome? │                           │
            │  └────┬─────┘                           │
            │       │                                 │
            │  ┌────┴────┐                            │
            │  │         │                            │
            │  v         v                            │
            │ SUCCESS   FAILURE                       │
            │  │         │                            │
            │  v         v                            │
            │ 经验强化   经验衰减                       │
            │ · boost   · reduce confidence            │
            │   access  · 分析失败原因                  │
            │   _count  · 提取修正参数                  │
            │           · 存储为 recovery experience    │
            │                                         │
            │           v                             │
            │      Reflection Engine                   │
            │           │                             │
            │           v                             │
            │      Update Memory                       │
            │      (User/Skill/Environment)            │
            └─────────────────────────────────────────┘
```

#### Memory Item v2.0 增强字段

```python
@dataclass
class MemoryItemV2:
    # v1.0 字段保留
    id: str
    memory_type: MemoryType
    key: str
    value: Any

    # === v2.0 新增字段 ===
    embedding: Optional[List[float]] = None     # 向量表示
    confidence: float = 1.0                      # 置信度
    success_rate: float = 1.0                    # 历史成功率
    failure_count: int = 0                       # 失败次数
    last_outcome: Optional[str] = None           # "success" | "failure"
    decay_factor: float = 1.0                    # 时间衰减因子
    conditions: Dict[str, Any] = field(...)      # 适用条件 (材质/重量范围等)
    source: str = "rule"                         # "rule" | "llm" | "experience"
```

---

### 升级 5: Execution Feedback Loop

#### 闭环原理

```
                        ┌──────────────────────────────┐
                        │       Observe                 │
                        │  (Sensor, Camera, Force)      │
                        └──────────────┬───────────────┘
                                       │
                        ┌──────────────v───────────────┐
                        │       Understand              │
                        │  (Intent Agent v2)            │
                        └──────────────┬───────────────┘
                                       │
                        ┌──────────────v───────────────┐
                        │       Plan                    │
                        │  (Hybrid Planner)             │
                        └──────────────┬───────────────┘
                                       │
         ┌─────────────────────────────┼─────────────────────────────┐
         │                             │                             │
         v                             v                             v
   ┌──────────┐              ┌──────────────┐            ┌──────────────┐
   │ Execute  │    ──失败──→  │   Monitor    │   ──成功──→ │   Complete   │
   └──────────┘              │              │            └──────────────┘
                             │ Failure Report│
                             │ {             │
                             │   skill,      │
                             │   reason,     │
                             │   suggestion, │
                             │   snapshot    │
                             │ }             │
                             └──────┬───────┘
                                    │
                             ┌──────v───────┐
                             │   Reflect    │
                             │              │
                             │ Root Cause   │
                             │ Analysis     │
                             └──────┬───────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    v               v               v
             Constraint        Memory           Planner
             (加严约束)        (更新经验)        (重规划)
```

#### Execution Monitor 接口

```python
@dataclass
class FailureReport:
    task_id: str
    failed_skill: str
    reason: str                     # "collision" | "grasp_failed" | "ik_failed"
    detail: str                     # "force 2.5N insufficient for 80g object"
    snapshot: ExecutionSnapshot     # 失败瞬间状态
    suggestion: str                 # "increase force to 5.0N"
    suggested_constraint_update: Dict[str, Any]
    suggested_memory_update: Dict[str, Any]


class ExecutionMonitor:
    """
    运行时监控器。挂在执行引擎旁路, 监听异常。
    """

    def on_skill_start(self, skill_name: str, state: RobotState): ...

    def on_skill_complete(
        self, skill_name: str, success: bool, state: RobotState
    ): ...

    def on_exception(self, exception: Exception, state: RobotState) -> FailureReport:
        """异常发生时: 捕获现场 → 分析根因 → 生成修复建议"""
        ...

    def generate_failure_report(self) -> FailureReport:
        """组装完整失败报告"""
        ...


class ReflectionEngine:
    """
    反思引擎。接收 FailureReport, 分发到:
    - Constraint Compiler (加严约束)
    - Memory (更新经验)
    - Planner (重规划)
    """

    def reflect(self, report: FailureReport) -> ReflectionResult:
        # 1. 约束更新
        if "force" in report.reason:
            new_constraint = ForceConstraint(
                max_force_n=report.suggested_constraint_update["force_n"]
            )
        # 2. 记忆更新
        memory_update = MemoryItem(
            skill=report.failed_skill,
            success=False,
            failure_reason=report.reason,
            suggested_params=report.suggested_constraint_update,
        )
        # 3. 重规划触发
        re_plan_signal = RePlanRequest(
            original_task=report.task_id,
            added_constraints=[new_constraint],
            memory_context=[memory_update],
        )
        return ReflectionResult(constraint=new_constraint, memory=memory_update, re_plan=re_plan_signal)
```

---

### 升级 6: Uncertainty-Aware Scene Builder

#### 为什么需要不确定性建模

真实感知系统（RGB-D 相机、VLM）的输出**不是完美的**：
- 部分遮挡 → 位置估计有误差
- 光照变化 → 颜色识别不可靠
- 相似物体 → 语义标签混淆
- 透明物体 → 深度相机失效

#### 升级后的 SceneObject

```python
@dataclass
class UncertainValue:
    value: Any                      # 最佳估计值
    confidence: float               # 0.0 - 1.0
    uncertainty: float              # 标准差/误差范围
    source: str                     # "camera" | "vlm" | "rule" | "prior"


class SceneObjectV2(SceneObject):
    # v1.0 字段保留
    name: str
    position: Position
    bbox: BoundingBox
    affordances: List[Affordance]

    # === v2.0: 不确定性字段 ===
    position_confidence: float = 1.0
    position_uncertainty_m: float = 0.0
    label_confidence: float = 1.0
    affordance_confidence: float = 1.0
    occlusion_ratio: float = 0.0       # 0=完全可见, 1=完全遮挡
    last_observed_at: Optional[str] = None
    state: str = "static"              # "static" | "moving" | "unknown"
```

#### VLM + Rule Hybrid Affordance

```
                    Object Image / Description
                              │
              ┌───────────────┴───────────────┐
              │                               │
              v                               v
    ┌──────────────────┐           ┌──────────────────┐
    │   VLM Inference  │           │  Rule Check       │
    │                  │           │                  │
    │ "这个透明物体      │           │ BBox width=0.07, │
    │  看起来像杯子,     │           │ height=0.12      │
    │  可以抓取,         │           │ → 符合杯子尺寸    │
    │  但是易碎"        │           │ → 可以抓取        │
    │                  │           │ → 形状不支持堆叠  │
    │ confidence: 0.85 │           │ confidence: 1.0  │
    └────────┬─────────┘           └────────┬─────────┘
             │                              │
             └──────────────┬───────────────┘
                            │
                            v
              ┌────────────────────────────────┐
              │     Affordance Fusion           │
              │                                │
              │  fused = w_vlm * vlm_score      │
              │        + w_rule * rule_score    │
              │                                │
              │  if VLM confidence < 0.5:       │
              │      fallback to Rule           │
              │  if Rule rejects VLM:           │
              │      flag as "inconsistent"     │
              └────────────────┬───────────────┘
                               │
                               v
              Final Affordances:
                graspable: 0.92
                fragile:   0.78
                container: 0.95
                stackable: 0.05  ← Rule rejected VLM
```

---

## 三、数据流全景（单条指令的完整旅程）

```
"请把桌上的红色药瓶递给老人，轻一点，别碰水杯"
    │
    v
┌──────────────────────────────────────────────────────────────────┐
│ Phase 1: OBSERVE + UNDERSTAND                                   │
│                                                                  │
│  Memory (v2):                                                    │
│    Embedding(指令) → VectorDB.search(top_k=5) → Rerank →         │
│    {grip_style: gentle, force_n: 2.5, user: elderly}            │
│                                                                  │
│  Scene (v2):                                                     │
│    VLM → 药瓶(graspable=0.92, fragile=0.85)                     │
│        → 水杯(container=0.95, position_conf=0.87)               │
│    Rule → 水杯 blocking 药瓶 (geometric, confidence=1.0)         │
│    融合 → SemanticSceneGraph(uncertainty-aware)                   │
└──────────────────────────────────────────────────────────────────┘
    │
    v
┌──────────────────────────────────────────────────────────────────┐
│ Phase 2: PLAN                                                    │
│                                                                  │
│  LLM Planner:                                                    │
│    Prompt(instruction + scene_summary + memory_summary)          │
│    → decompose: [sub1: 取药瓶, sub2: 避免水杯, sub3: 递给老人]    │
│    → 每个 sub 生成 CandidateBT (1-3 alternatives)                 │
│                                                                  │
│  Rule Validator:                                                 │
│    ✅ BT structure valid                                         │
│    ✅ All skills in SkillCatalog                                  │
│    ✅ No safety violations                                       │
│    ⚠️ GentleGrasp force 2.5N < 3.0N (fragile limit) → OK        │
│    → Validated BT                                                │
└──────────────────────────────────────────────────────────────────┘
    │
    v
┌──────────────────────────────────────────────────────────────────┐
│ Phase 3: FEASIBILITY CHECK                                       │
│                                                                  │
│  For each skill:                                                 │
│    Reach(药瓶):   IK reachable ✅, occlusion=0.3 ⚠️, score=0.85  │
│    GentleGrasp:   grasp_quality=0.92 ✅, payload OK ✅, score=0.92│
│    MoveTo(user):  path blocked by 水杯 ❌, score=0.0             │
│                   → add Avoid(水杯) before MoveTo                │
│                   → re-check: score=0.88 ✅                      │
└──────────────────────────────────────────────────────────────────┘
    │
    v
┌──────────────────────────────────────────────────────────────────┐
│ Phase 4: COMPILE (Constraint + IR)                               │
│                                                                  │
│  Constraint Compiler:                                            │
│    Layer 0: Safety (z≥0.02, joint, force≤10N, workspace, human)  │
│    Layer 1: Rule (force≤3.0N, velocity≤0.10, avoid水杯)          │
│    Layer 2: LLM-inferred ("elderly → slow motion")               │
│    Layer 3: Feasibility-as-Constraint (Avoid required before Mv) │
│    → ConstraintGraph (12 nodes, 10 HARD, 1 SOFT, 1 FEASIBILITY) │
│                                                                  │
│  IR Generator 2.0:                                               │
│    Task Layer    → sub_tasks [{取药瓶, 避免水杯, 递给老人}]       │
│    Skill Layer   → {Reach, GentleGrasp, Avoid, MoveTo, Release}  │
│    Motion Layer  → {RRTStar, v≤0.10, a≤0.5, waypoints[...]}    │
│    Safety Layer  → {global:[6], per_skill:{...}}                 │
│    Optimization  → {force:[0.1,2.5], velocity:[0.05,0.10]}      │
│    → RobotTaskIR 2.0 JSON                                        │
└──────────────────────────────────────────────────────────────────┘
    │
    v
┌──────────────────────────────────────────────────────────────────┐
│ Phase 5: EXECUTE + MONITOR + REFLECT                             │
│                                                                  │
│  CodeArts: IR → Python code                                      │
│  RRT*:      Motion Layer → trajectory                            │
│  Isaac Sim: Execute                                               │
│                                                                  │
│  Monitor:                                                        │
│    Reach ✅ → Grasp: force_feedback=2.3N < expected 2.5N ⚠️      │
│    → VerifyGrasp: success ✅                                      │
│    → MoveTo → Release ✅                                          │
│                                                                  │
│  Success → Reflect:                                              │
│    Memory: GentleGrasp(药瓶) success_rate ↗ from 0.9 to 0.93     │
│    Constraint: force_n 2.5 was sufficient, keep boundary          │
│                                                                  │
│  (If Grasp failed) → Reflect:                                    │
│    Failure Report: {skill:Grasp, reason:insufficient_force,       │
│                     suggestion:increase_to_5N}                   │
│    → Constraint: force_limit(药瓶) min_n ↗ from 0.1 to 5.0       │
│    → Memory: GentleGrasp(药瓶) success_rate ↘, add failure case   │
│    → Re-plan: with tightened constraints                         │
│    → Re-execute                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 四、面向论文/比赛的创新点包装

### 创新点 1: Constraint-Aware Task IR（约束感知任务中间表示）

> **定位**: 传统方法将约束作为 LLM prompt 中的自然语言或代码注释。本系统将物理约束、安全约束、用户偏好提升为**一等公民**，在 IR 中拥有独立的 Safety Layer 和 Optimization Layer。这使得不同下游模块（CodeArts、Motion Planner、Safety Monitor）可以独立消费不同层的语义。

**关键词**: Constraint Graph, Robot IR, Safety-first Architecture, LLVM-like for Robotics

### 创新点 2: Hybrid LLM + Rule Architecture（LLM 生成 + 规则验证）

> **定位**: 不是简单地用 LLM 替换规则引擎，而是 LLM 负责"创造性"（长任务拆解、新技能组合），规则引擎负责"正确性"（安全验证、物理约束、BT 合法性）。LLM 输出的每个候选 BT 都经过多层规则验证，不可修复的送回 LLM 重新生成。

**关键词**: Hybrid Planning, LLM + Symbolic AI, Safe RL/Planning, Verifiable LLM Output

### 创新点 3: Experience-Driven Self-Improvement Loop（经验驱动的自主改进闭环）

> **定位**: 系统在执行后不丢弃经验数据。成功 → 强化记忆；失败 → 反思故障根因 → 更新约束 → 更新记忆 → 触发重规划。形成一个从"执行反馈"到"策略改进"的完整闭环，符合 Embodied AI 的"终生学习"方向。

**关键词**: Lifelong Learning, Experience Loop, Self-Improving Robot, Execution Feedback

### 创新点 4: Uncertainty-Aware Scene Understanding（不确定性感知的场景理解）

> **定位**: 将感知不确定性显式建模到 Scene Graph 中（位置置信度、标签置信度、可供性置信度）。下游模块（Feasibility Estimator、Constraint Compiler）根据不确定性自适应调整行为（高不确定性 → 降速、增大安全距离）。

**关键词**: Uncertainty Modeling, VLM + Geometric Fusion, Probabilistic Scene Graph

### 创新点 5: Five-Layer Robot IR（五层机器人中间表示）

> **定位**: 借鉴 LLVM IR 的"多级中间表示"思想，将 RobotTaskIR 设计为五层：Task → Skill → Motion → Safety → Optimization。每层有明确的语义边界和消费者。不同机器人平台（Isaac Sim / ROS2 / 真机）只需实现不同的后端，共享同一套 IR。

**关键词**: Multi-Level IR, Compiler Architecture for Robotics, Robot Code Generation

### 论文标题建议

- *"Constraint-Aware Task IR: A Compiler Approach to Safe Robot Programming from Natural Language"*
- *"From 'Be Gentle' to force ≤ 3.0N: Compiling Natural Language Constraints into Verifiable Robot Intermediate Representation"*
- *"Hybrid LLM+Rule Planning with Execution Feedback for Lifelong Robot Skill Learning"*

---

## 五、实施路线图

| Phase | 内容 | 依赖 | 预计工期 |
|-------|------|------|---------|
| **Phase 1** | RobotTaskIR 2.0 Schema 实现 | v1.0 schemas | 1 周 |
| **Phase 2** | Scene Builder v2 (Uncertainty + VLM Hybrid) | Phase 1 | 1 周 |
| **Phase 3** | Memory v2 (Embedding + VectorDB) | Phase 1 | 1 周 |
| **Phase 4** | Hybrid Planner (LLM + Validator) | Phase 1, 2, 3 | 2 周 |
| **Phase 5** | Skill Feasibility Estimator | Phase 1, 2 | 1 周 |
| **Phase 6** | Execution Monitor + Reflection | Phase 1, 4, 5 | 2 周 |
| **Phase 7** | Experience Update Loop | Phase 3, 6 | 1 周 |
| **Phase 8** | 端到端集成 + 论文撰写 | Phase 1-7 | 2 周 |

---

## 六、与 v1.0 的兼容性

v1.0 的以下模块**不需要修改**，v2.0 是在其之上的增强：

- `schemas/scene.py` — 保留，新增 `SceneObjectV2` 继承
- `schemas/behavior_tree.py` — 完全不变
- `schemas/constraint.py` — 保留，新增 Feasibility-as-Constraint
- `memory/base.py` — `MemoryInterface` 保留，新增 `VectorMemoryBackend`
- `planner/base.py` — `TaskPlannerInterface` 保留，新增 `LLMTaskPlanner`
- `constraint/base.py` — `ConstraintGraph` 保留，新增 Layer 2/3

**所有 v1.0 测试（136 个）在 v2.0 升级后仍应通过。**
