"""
Hybrid Constraint Compiler — 混合约束编译器

架构:
    User Instruction
        │
        v
    Rule Instruction Parser
        │
        v
    +---------------------------+
    | Hybrid Constraint Compiler |
    +---------------------------+
       │          │          │
       v          v          v
    Spatial   Physical   Safety
    Constraint Constraint Constraint
       │          │          │
       └──────────┼──────────┘
                  v
          Constraint Graph
                  │
                  v
          Behavior Tree (enriched with constraints)

工作流程:
    1. 注入安全红线 (SafetyConstraint.mandatory_set) — 不可绕过
    2. Rule Engine 提取 NL + Scene 约束
    3. 绑定约束到 BehaviorTree 技能节点
    4. 输出 ConstraintGraph
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import (
    ConstraintGraph,
    ConstraintNode,
    ConstraintCategory,
    ConstraintPriority,
    ConstraintStatus,
)
from .rule_engine import ConstraintRuleEngine
from .safety_constraint import SafetyConstraint
from .spatial_constraint import SpatialConstraint
from .physical_constraint import PhysicalConstraint

from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.behavior_tree import BehaviorTree, BTNode, BTNodeType, SkillAction
from robot_intent_agent.planner.skill_catalog import SkillCatalog


class HybridConstraintCompiler:
    """
    混合约束编译器 — Step 6 核心。

    将 NL + Scene + Memory + Safety Rules
    → 编译为可执行 ConstraintGraph。

    用法:
        compiler = HybridConstraintCompiler()
        graph = compiler.compile(
            instruction="请把红色药瓶递给我，轻一点，别碰水杯",
            behavior_tree=bt,
            scene=scene_graph,
            memory_context=memory_items,
        )
        # graph.bind_to_skills() → {"Grasp": [force_limit, ...], "MoveTo": [...]}
    """

    def __init__(self):
        self.engine = ConstraintRuleEngine()
        self.catalog = SkillCatalog()

    # ============================================================
    # 主入口
    # ============================================================

    def compile(
        self,
        instruction: str,
        behavior_tree: BehaviorTree,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
        target: str = "",
    ) -> ConstraintGraph:
        """
        编译完整 ConstraintGraph。

        Args:
            instruction:    用户自然语言指令
            behavior_tree:  已规划的行为树 (来自 Step 5)
            scene:          语义场景图 (来自 Step 4)
            memory_context: Memory 检索结果 (来自 Step 3)
            target:         主目标物体名

        Returns:
            ConstraintGraph — 绑定到 BT 技能节点的约束集
        """
        graph = ConstraintGraph(
            task_id=behavior_tree.task_id,
            metadata={
                "instruction": instruction,
                "planner": behavior_tree.metadata.get("planner", "unknown"),
            },
        )

        # ══════════════════════════════════════════
        # 第 0 层: 安全红线 (不可绕过)
        # ══════════════════════════════════════════
        safety_set = SafetyConstraint.mandatory_set(target)
        graph.add_all(safety_set)

        # ══════════════════════════════════════════
        # 第 1 层: Rule Engine 提取约束
        # ══════════════════════════════════════════
        rule_constraints = self.engine.extract(
            instruction=instruction,
            scene=scene,
            target=target,
            memory_context=memory_context or [],
        )
        graph.add_all(rule_constraints)

        # ══════════════════════════════════════════
        # 第 2 层: 与 BehaviorTree 对齐
        # ══════════════════════════════════════════
        self._align_with_bt(graph, behavior_tree)

        # ══════════════════════════════════════════
        # 第 3 层: 去重 + 冲突检测
        # ══════════════════════════════════════════
        self._deduplicate(graph)
        self._resolve_conflicts(graph)

        return graph

    # ============================================================
    # BT 对齐 — 将约束绑定到具体技能节点
    # ============================================================

    def _align_with_bt(
        self, graph: ConstraintGraph, bt: BehaviorTree
    ) -> None:
        """
        将未绑定技能的全局约束，自动绑定到 BT 中的对应 Action 节点。

        规则:
            - force_limit   → 绑定到所有 Grasp 节点
            - velocity_limit → 绑定到所有 MoveTo/Reach 节点
            - collision_avoid → 绑定到所有节点
            - z_axis_floor  → 绑定到所有节点 (全局)
        """
        bt_skills = {a.skill_name for a in bt.root.flatten_actions()}

        # 默认绑定规则
        skill_bindings: Dict[str, List[str]] = {
            "force_limit":      ["Grasp", "GentleGrasp"],
            "velocity_limit":   ["MoveTo", "Reach", "Push"],
            "collision_avoid":  [],   # 空 = 全局
            "z_axis_floor":     [],   # 空 = 全局
            "joint_limits":     [],
            "max_gripper_force": ["Grasp", "GentleGrasp"],
            "workspace_bounds": [],
            "human_proximity":  ["MoveTo"],
            "release_height":   ["Release"],
            "gripper_width":    ["Grasp", "GentleGrasp", "Release"],
        }

        for node in graph.nodes:
            # 如果已经有绑定的技能且在 BT 中存在 → 保持
            if node.applies_to_skill and node.applies_to_skill in bt_skills:
                continue

            # 否则按类型自动绑定
            bind_skills = skill_bindings.get(node.constraint_type, [])
            if bind_skills:
                # 绑定到第一个匹配的 BT 技能
                for skill in bind_skills:
                    if skill in bt_skills:
                        node.applies_to_skill = skill
                        break

    # ============================================================
    # 去重
    # ============================================================

    def _deduplicate(self, graph: ConstraintGraph) -> None:
        """
        合并重复约束。

        规则: 同类型 + 同目标 + 同技能 → 保留最严格的
        """
        seen: Dict[str, ConstraintNode] = {}

        for node in graph.nodes:
            key = f"{node.constraint_type}:{node.target}:{node.applies_to_skill}"
            if key in seen:
                existing = seen[key]
                # 保留更严格的 (取更小的上限)
                if node.constraint_type == "force_limit":
                    new_max = node.params.get("max_force_n", 10.0)
                    old_max = existing.params.get("max_force_n", 10.0)
                    if new_max < old_max:
                        seen[key] = node
                elif node.constraint_type == "velocity_limit":
                    new_v = node.params.get("max_linear_ms", 0.3)
                    old_v = existing.params.get("max_linear_ms", 0.3)
                    if new_v < old_v:
                        seen[key] = node
                elif node.priority == ConstraintPriority.HARD:
                    if existing.priority != ConstraintPriority.HARD:
                        seen[key] = node
            else:
                seen[key] = node

        graph.nodes = list(seen.values())

    # ============================================================
    # 冲突检测
    # ============================================================

    def _resolve_conflicts(self, graph: ConstraintGraph) -> None:
        """
        检测并报告约束冲突。

        当前: 警告级别 (future: 自动调解)
        """
        violations = []

        # 检查: force_limit 是否有冲突 (min > max)
        for node in graph.by_category(ConstraintCategory.PHYSICAL):
            if node.constraint_type == "force_limit":
                min_f = node.params.get("min_force_n", 0.1)
                max_f = node.params.get("max_force_n", 10.0)
                if min_f >= max_f:
                    violations.append(
                        f"CONFLICT: {node.id}: min_force({min_f}) >= max_force({max_f})"
                    )

        if violations:
            graph.metadata["conflicts"] = violations


# ============================================================
# 便捷工厂
# ============================================================

def compile_constraints(
    instruction: str,
    behavior_tree: BehaviorTree,
    scene: Optional[SemanticSceneGraph] = None,
    memory_context: Optional[List[Dict[str, Any]]] = None,
    target: str = "",
) -> ConstraintGraph:
    """一键编译 (便捷函数)"""
    compiler = HybridConstraintCompiler()
    return compiler.compile(
        instruction=instruction,
        behavior_tree=behavior_tree,
        scene=scene,
        memory_context=memory_context,
        target=target,
    )
