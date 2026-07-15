"""
Robot Task IR Generator — 统一中间表示编译器

输入:   BehaviorTree + ConstraintGraph + SceneGraph + Memory
输出:   RobotTaskIR (Pydantic 模型, 可序列化为 JSON)

供下游 Isaac Sim / ROS2 / 真机控制接口解析。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from robot_intent_agent.schemas.robot_task_ir import (
    RobotTaskIR,
    TaskMetadata,
    PreconditionSet,
    OptimizationSpace,
)
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNodeType,
)
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.constraint import ConstraintSet
from robot_intent_agent.constraint.base import ConstraintGraph, ConstraintNode


class RobotTaskIRGenerator:
    """
    Robot Task IR 生成器 — 全链路最终编译器。

    输入:  Step 3 (Memory) + Step 4 (Scene) + Step 5 (BT) + Step 6 (Constraints)
    输出:  RobotTaskIR (CodeArts / TraceCoder 可直接解析的 JSON)

    用法:
        generator = RobotTaskIRGenerator()
        ir = generator.generate(
            instruction="请把红色药瓶递给我，轻一点，别碰水杯",
            behavior_tree=bt,
            constraint_graph=cg,
            scene=scene,
            memory_context=mem_items,
        )
        # 序列化为 JSON
        ir.model_dump_json(indent=2)
        # 输出友好摘要
        ir.summary()
    """

    def generate(
        self,
        instruction: str,
        behavior_tree: BehaviorTree,
        constraint_graph: ConstraintGraph,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> RobotTaskIR:
        """
        全链路编译 → RobotTaskIR。

        Args:
            instruction:      用户原始自然语言指令
            behavior_tree:    Step 5 输出 (行为树)
            constraint_graph: Step 6 输出 (约束图)
            scene:            Step 4 输出 (场景图)
            memory_context:   Step 3 输出 (记忆检索结果)
        """
        task_id = behavior_tree.task_id or f"task-{uuid4().hex[:8]}"

        # ── 1. 元数据 ──
        metadata = TaskMetadata(
            task_id=task_id,
            raw_instruction=instruction,
            language="zh",
            created_at=datetime.now(timezone.utc).isoformat(),
            user_context=self._extract_memory(memory_context),
        )

        # ── 2. 前置条件 ──
        preconditions = self._extract_preconditions(behavior_tree)

        # ── 3. Skills 映射 (含约束 + 物体信息) ──
        skills = self._build_skills(behavior_tree, constraint_graph, scene)

        # ── 4. 约束集 (hard/soft 从 ConstraintGraph 转换) ──
        compiled_constraints = ConstraintSet(task_id=task_id)

        # ── 5. 优化空间 ──
        optimization = self._build_optimization(constraint_graph)

        # ── 6. Memory 上下文 ──
        mem_ctx = self._build_memory_context(memory_context, constraint_graph)

        # ── 7. 组装 ──
        ir = RobotTaskIR(
            ir_version="1.0.0",
            task_metadata=metadata,
            precondition_assertions=preconditions,
            scene=scene,
            behavior_tree=behavior_tree,
            skills=skills,
            compiled_constraints=compiled_constraints,
            optimization_space=optimization,
            memory_context=mem_ctx,
        )

        return ir

    # ============================================================
    # Skills — 核心: BT Action + Constraint 绑定
    # ============================================================

    def _build_skills(
        self,
        bt: BehaviorTree,
        cg: ConstraintGraph,
        scene: Optional[SemanticSceneGraph],
    ) -> Dict[str, Dict[str, Any]]:
        """
        构建技能映射表。

        输出结构:
        {
          "Grasp": {
            "target": "红色药瓶",
            "params": {"force_n": 3.0},
            "constraints": {
              "force": {"max_force_n": 3.0},
              "fragile": true
            },
            "object": { "position": {...}, "bbox": {...}, "affordances": [...] }
          },
          "MoveTo": { ... },
          ...
        }
        """
        skills: Dict[str, Dict[str, Any]] = {}
        bindings = cg.bind_to_skills()

        for action in bt.root.flatten_actions():
            skill_name = action.skill_name
            target = action.target or ""

            # 该技能的约束
            skill_constraints = bindings.get(skill_name, [])
            global_constraints = bindings.get("_global", [])

            # 编译约束为可读结构
            compiled = self._compile_skill_constraints(
                skill_constraints + global_constraints
            )

            # 物体信息
            object_info = {}
            if scene and target:
                obj = scene.find_object(target)
                if obj:
                    object_info = {
                        "label": obj.label,
                        "position": {
                            "x": obj.position.x,
                            "y": obj.position.y,
                            "z": obj.position.z,
                        },
                        "bbox": {
                            "width": obj.bbox.width,
                            "height": obj.bbox.height,
                            "depth": obj.bbox.depth,
                        },
                        "affordances": [a.value for a in obj.affordances],
                        "attributes": obj.attributes,
                    }

            skills[skill_name] = {
                "target": target,
                "params": action.params,
                "constraints": compiled,
                "object": object_info,
            }

        return skills

    def _compile_skill_constraints(
        self, nodes: List[ConstraintNode]
    ) -> Dict[str, Any]:
        """将 ConstraintNode 列表编译为简洁的约束字典"""
        result: Dict[str, Any] = {
            "avoid": [],
            "force": {},
            "velocity": {},
            "safety": [],
        }

        for node in nodes:
            if node.constraint_type == "force_limit":
                result["force"] = {
                    "max_force_n": node.params.get("max_force_n", 10.0),
                    "min_force_n": node.params.get("min_force_n", 0.1),
                }
                result["fragile"] = node.params.get("max_force_n", 10.0) <= 3.0
            elif node.constraint_type == "velocity_limit":
                result["velocity"] = {
                    "max_linear_ms": node.params.get("max_linear_ms", 0.3),
                }
            elif node.constraint_type == "collision_avoid":
                obstacle = node.params.get("obstacle", "")
                if obstacle and obstacle not in result["avoid"]:
                    result["avoid"].append(obstacle)
            elif node.constraint_type == "max_gripper_force":
                result["force"]["max_gripper_force"] = node.params.get("max_force_n", 10.0)
            elif node.constraint_type == "release_height":
                result["release_height_m"] = node.params.get("max_height_m")
            elif node.category.value == "safety":
                result["safety"].append(node.expression)

        return result

    # ============================================================
    # 前置条件
    # ============================================================

    def _extract_preconditions(self, bt: BehaviorTree) -> PreconditionSet:
        """从 BT Condition 节点提取前置条件"""
        pre = PreconditionSet()
        for child in bt.root.children:
            if child.type == BTNodeType.CONDITION and child.condition:
                pre.add(
                    assertion=child.condition.condition,
                    description=child.name,
                )
        pre.add(assertion="z >= 0.02", description="Z-axis safety floor")
        pre.add(assertion="gripper_force <= 10.0", description="Max gripper force")
        return pre

    # ============================================================
    # 优化空间
    # ============================================================

    def _build_optimization(
        self, cg: ConstraintGraph
    ) -> OptimizationSpace:
        """从约束图中提取优化边界"""
        force_nodes = [n for n in cg.nodes if n.constraint_type == "force_limit"]
        vel_nodes = [n for n in cg.nodes if n.constraint_type == "velocity_limit"]

        # 取最严格的边界
        max_force = min(
            (n.params.get("max_force_n", 10.0) for n in force_nodes),
            default=10.0,
        )
        min_force = max(
            (n.params.get("min_force_n", 0.1) for n in force_nodes),
            default=0.1,
        )
        max_vel = min(
            (n.params.get("max_linear_ms", 0.3) for n in vel_nodes),
            default=0.3,
        )

        return OptimizationSpace(
            force_range_n=(min_force, max_force),
            velocity_range_ms=(0.05, max_vel),
            z_safe_margin_m=(0.02, 0.10),
            collision_margin_m=(0.03, 0.15),
            targets=["max_safety", "min_time"],
            free_params={},
        )

    # ============================================================
    # Memory 上下文
    # ============================================================

    def _extract_memory(
        self, memory_items: Optional[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """提取 Memory 数据为 user_context"""
        if not memory_items:
            return {}
        context: Dict[str, Any] = {}
        for item in memory_items:
            if item.get("memory_type") == "user_preference":
                context[item.get("key", "")] = item.get("value")
        return context

    def _build_memory_context(
        self,
        memory_items: Optional[List[Dict[str, Any]]],
        cg: ConstraintGraph,
    ) -> Dict[str, Any]:
        """构建完整的 memory_context (含约束摘要)"""
        ctx: Dict[str, Any] = {
            "user_preferences": {},
            "skill_experiences": [],
            "constraint_summary": {
                "total": len(cg.nodes),
                "hard": len(cg.hard_constraints()),
                "soft": len(cg.soft_constraints()),
                "by_type": {},
            },
        }

        # 按类型统计
        for node in cg.nodes:
            ctype = node.constraint_type
            ctx["constraint_summary"]["by_type"][ctype] = (
                ctx["constraint_summary"]["by_type"].get(ctype, 0) + 1
            )

        # Memory 数据
        if memory_items:
            for item in memory_items:
                mtype = item.get("memory_type", "")
                if mtype == "user_preference":
                    ctx["user_preferences"][item.get("key", "")] = item.get("value")
                elif mtype == "skill_experience":
                    ctx["skill_experiences"].append({
                        "skill": item.get("key", ""),
                        "params": item.get("value", {}),
                    })

        return ctx


# ============================================================
# 便捷函数
# ============================================================

def generate_robot_task_ir(
    instruction: str,
    behavior_tree,
    constraint_graph,
    scene=None,
    memory_context=None,
):
    """一键生成 RobotTaskIR"""
    return RobotTaskIRGenerator().generate(
        instruction=instruction,
        behavior_tree=behavior_tree,
        constraint_graph=constraint_graph,
        scene=scene,
        memory_context=memory_context,
    )
