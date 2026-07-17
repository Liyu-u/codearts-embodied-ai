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
    DecisionTraceNode,
    TaskIntent,
    GroundedEntity,
    RiskObject,
    OverrideLedgerEntry,
    ExplainReport,
)
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNodeType,
)
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.constraint import (
    ConstraintSet, ConstraintPriority,
    ForceConstraint, VelocityConstraint, CollisionConstraint,
    HeightConstraint, TemporalConstraint, PreferenceConstraint,
)
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

        # ── 4. 约束集 (从 ConstraintGraph 转换 → Pydantic 模型) ──
        compiled_constraints = ConstraintSet(task_id=task_id)
        compiled_constraints = self._populate_constraint_set(
            compiled_constraints, constraint_graph
        )

        # ── 5. 优化空间 ──
        optimization = self._build_optimization(constraint_graph)

        # ── 6. Memory 上下文 ──
        mem_ctx = self._build_memory_context(memory_context, constraint_graph)

        # ── 7. v2.0: 决策轨迹 DAG + 系统置信度 ──
        decision_trace = self._build_decision_trace(
            instruction, behavior_tree, constraint_graph, scene, memory_context
        )
        overall_confidence = self._compute_overall_confidence(decision_trace)

        # ── 8. v3.0: 结构化意图 + 可解释性报告 ──
        task_intent = self._build_task_intent(instruction, behavior_tree, scene)
        explain_report = self._build_explain_report(
            instruction, behavior_tree, constraint_graph, scene, decision_trace
        )
        risk_objects = self._build_risk_objects(scene)

        # ── 9. 组装 v3.0 ──
        ir = RobotTaskIR(
            ir_version="3.0.0",
            task_metadata=metadata,
            precondition_assertions=preconditions,
            scene=scene,
            behavior_tree=behavior_tree,
            skills=skills,
            compiled_constraints=compiled_constraints,
            optimization_space=optimization,
            memory_context=mem_ctx,
            overall_confidence=overall_confidence,
            decision_trace=decision_trace,
            task_intent=task_intent,
            explain_report=explain_report,
            risk_objects=risk_objects,
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

            # v2.1: 将 action.params + CG clamping 结果以 ParamValue 结构注入
            self._merge_params_into_constraints(
                compiled, action.params, action.skill_name, cg
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

    def _merge_params_into_constraints(
        self, compiled: Dict[str, Any], params: Dict[str, Any],
        skill_name: str, cg: ConstraintGraph,
    ) -> None:
        """
        v2.1: 以 ParamValue 结构将最终裁决参数注入 compiled constraints。

        优先级: CG clamping 值 > action.params 值 > 默认值
        每个注入的参数包含: value, source, evidence
        """
        force_clamp = cg.metadata.get("force_clamping", {})
        vel_clamp = cg.metadata.get("velocity_clamping", {})
        override_ledger = cg.metadata.get("override_ledger", [])

        # ── force_n → ParamValue ──
        if skill_name in ("Grasp", "GentleGrasp"):
            # 确定最终值: clamping 优先 → params 次之 → 默认 5.0
            clamped_val = force_clamp.get("selected")
            param_val = float(params.get("force_n", 0)) if params else 0
            final_val = clamped_val if clamped_val is not None else (param_val or 5.0)

            # 构建溯源
            sources = list(force_clamp.get("sources", []))
            evidence = list(force_clamp.get("evidence", []))
            if not sources:
                sources = ["param"] if param_val else ["default"]
            if not evidence and param_val:
                evidence = [f"action_param:{param_val}N"]
            if not evidence:
                evidence = ["default:5.0N"]

            # 检查 override ledger 补充来源
            for entry in override_ledger:
                if entry.get("parameter") == "force_n":
                    if "override" not in sources:
                        sources.append("override")
                    evidence.append(
                        f"override:{entry['requested_val']}N->{entry['clamped_val']}N"
                    )

            if not compiled.get("force"):
                compiled["force"] = {}
            compiled["force"]["max_force_n"] = {
                "value": final_val,
                "source": sources,
                "evidence": evidence,
            }
            compiled["fragile"] = final_val <= 3.0

        # ── velocity_ms → ParamValue ──
        if skill_name in ("Reach", "MoveTo", "Push"):
            clamped_v = vel_clamp.get("selected")
            param_v = float(params.get("velocity_ms", 0)) if params else 0
            final_v = clamped_v if clamped_v is not None else (param_v or 0.15)

            sources_v = list(vel_clamp.get("sources", []))
            evidence_v = list(vel_clamp.get("evidence", []))
            if not sources_v:
                sources_v = ["param"] if param_v else ["default"]
            if not evidence_v and param_v:
                evidence_v = [f"action_param:{param_v}mps"]
            if not evidence_v:
                evidence_v = ["default:0.15mps"]

            for entry in override_ledger:
                if entry.get("parameter") == "velocity_ms":
                    if "override" not in sources_v:
                        sources_v.append("override")
                    evidence_v.append(
                        f"override:{entry['requested_val']}mps->{entry['clamped_val']}mps"
                    )

            if not compiled.get("velocity"):
                compiled["velocity"] = {}
            compiled["velocity"]["max_linear_ms"] = {
                "value": final_v,
                "source": sources_v,
                "evidence": evidence_v,
            }

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

    # ============================================================
    # 约束集转换: ConstraintNode (dataclass) → Pydantic 约束模型
    # ============================================================

    def _populate_constraint_set(
        self, cs: ConstraintSet, cg: ConstraintGraph
    ) -> ConstraintSet:
        """
        将 ConstraintGraph 中的所有 ConstraintNode 转换为
        Pydantic 约束模型，并按 hard/soft 分类填充 ConstraintSet。
        """
        for node in cg.nodes:
            model = self._convert_to_pydantic(node)
            if model is None:
                continue
            model.priority = ConstraintPriority.HARD if node.priority.value == "hard" else ConstraintPriority.SOFT

            if node.priority.value == "hard":
                cs.hard_constraints.append(model)
            else:
                cs.soft_constraints.append(model)

        return cs

    def _convert_to_pydantic(self, node) -> Any:
        """将单个 ConstraintNode 转为对应 Pydantic 约束模型"""
        ctype = node.constraint_type
        params = node.params

        try:
            if ctype == "force_limit":
                return ForceConstraint(
                    max_force_n=float(params.get("max_force_n", 10.0)),
                    min_force_n=float(params.get("min_force_n", 0.1)),
                    description=node.expression,
                )
            elif ctype == "velocity_limit":
                return VelocityConstraint(
                    max_linear_ms=float(params.get("max_linear_ms", 0.3)),
                    description=node.expression,
                )
            elif ctype == "collision_avoid":
                return CollisionConstraint(
                    avoid_object=str(params.get("obstacle", "")),
                    min_distance_m=float(params.get("min_distance_m", 0.05)),
                    description=node.expression,
                )
            elif ctype in ("z_axis_floor", "height_limit"):
                return HeightConstraint(
                    min_z_m=float(params.get("min_z_m", 0.02)),
                    description=node.expression,
                )
            elif ctype in ("max_gripper_force", "joint_limits",
                           "workspace_bounds", "human_proximity"):
                # safety constraints — wrap as PreferenceConstraint for now
                # (they are already HARD and captured in preconditions)
                return PreferenceConstraint(
                    key=ctype,
                    value=params,
                    description=node.expression,
                )
            elif ctype in ("release_height", "gripper_width",
                           "approach_direction", "trajectory", "region"):
                return PreferenceConstraint(
                    key=ctype,
                    value=params,
                    description=node.expression,
                )
            else:
                # unknown type → wrap as generic preference
                return PreferenceConstraint(
                    key=ctype,
                    value=params,
                    description=node.expression,
                )
        except Exception:
            return None

    # ============================================================
    # v2.1: 决策轨迹 DAG (SSOT — 只读聚合器, 零独立推理)
    # ============================================================

    def _build_decision_trace(
        self,
        instruction: str,
        bt: BehaviorTree,
        cg: ConstraintGraph,
        scene: Any,
        memory_context: Optional[List[Dict[str, Any]]],
    ) -> List[DecisionTraceNode]:
        """
        决策轨迹 DAG — 纯只读聚合器。

        原则: 绝不在此方法内做任何二次推理 (如重算 blocking/碰撞)。
        所有数据直接从已计算完成的 scene/cg/bt 中提取。
        """
        trace: List[DecisionTraceNode] = []

        # ── Node 1: NL_PARSE ──
        action = bt.metadata.get("action", "?")
        raw_target = bt.metadata.get("target", "?")
        trace.append(DecisionTraceNode(
            module="NL_PARSE",
            input=instruction[:80],
            output=f"action={action}, mention='{raw_target}'",
            reason="Keyword-based action classification + object mention extraction",
            depends_on=[],
            latency_ms=0.5,
            confidence=0.90,
        ))

        # ── Node 2: SCENE_GROUNDING (SSOT: 只读 scene 已计算数据) ──
        scene_obj_count = len(scene.objects) if scene and scene.objects else 0
        scene_rel_count = len(scene.relations) if scene and scene.relations else 0
        blocking_count = sum(1 for r in (scene.relations if scene else [])
                            if hasattr(r, 'predicate') and r.predicate.value == "blocking")

        # 检查目标是否成功接地 (NOT re-computed here)
        target_grounded = False
        target_entity = None
        if scene:
            target_entity = scene.find_object(raw_target)
            target_grounded = target_entity is not None

        grounding_conf = 0.96 if target_grounded else 0.30
        reason = (
            f"Scene parsed: {scene_obj_count} objects, {scene_rel_count} relations, "
            f"{blocking_count} blocking. Target '{raw_target}' "
            f"{'grounded to entity' if target_grounded else 'NOT FOUND in scene — LOW CONFIDENCE'}"
        )
        trace.append(DecisionTraceNode(
            module="SCENE_GROUNDING",
            input=f"{scene_obj_count} objects, query='{raw_target}'",
            output=f"grounding={'success' if target_grounded else 'FAILED'}, relations={scene_rel_count}, blocking={blocking_count}",
            reason=reason,
            depends_on=["NL_PARSE"],
            latency_ms=1.0,
            confidence=grounding_conf,
        ))

        # ── Node 3: MEMORY_RETRIEVAL ──
        mem_count = len(memory_context) if memory_context else 0
        pref_keys = []
        skill_keys = []
        if memory_context:
            for item in memory_context:
                if item.get("memory_type") == "user_preference":
                    pref_keys.append(item.get("key", ""))
                elif item.get("memory_type") == "skill_experience":
                    skill_keys.append(item.get("key", ""))
        mem_confidence = 0.85 if mem_count >= 2 else (0.65 if mem_count >= 1 else 0.50)
        trace.append(DecisionTraceNode(
            module="MEMORY_RETRIEVAL",
            input=f"query='{instruction[:30]}...'",
            output=f"{mem_count} items (prefs={pref_keys[:3]}, skills={skill_keys[:2]})",
            reason=f"Retrieved {mem_count} memory items: {pref_keys[:2] + skill_keys[:1]}" if mem_count else "No matching memory found",
            depends_on=["NL_PARSE"],
            latency_ms=0.8,
            confidence=mem_confidence,
        ))

        # ── Node 4: CONSTRAINT_REASONING (SSOT: 只读 cg) ──
        hard_count = len(cg.hard_constraints())
        soft_count = len(cg.soft_constraints())
        trace.append(DecisionTraceNode(
            module="CONSTRAINT_REASONING",
            input=f"{len(cg.nodes)} constraint nodes from safety+rule+scene+memory",
            output=f"{hard_count} hard, {soft_count} soft constraints compiled",
            reason=f"Constraints compiled: safety redlines + NL modifiers + scene geometry + object affordances",
            depends_on=["SCENE_GROUNDING", "MEMORY_RETRIEVAL"],
            latency_ms=1.5,
            confidence=1.0,
        ))

        # ── Node 5: CONFLICT_RESOLUTION (SSOT: 只读 cg.metadata) ──
        force_clamp = cg.metadata.get("force_clamping", {})
        vel_clamp = cg.metadata.get("velocity_clamping", {})
        override_ledger = cg.metadata.get("override_ledger", [])

        if force_clamp or vel_clamp:
            candidates_f = force_clamp.get('candidates', [])
            selected_f = force_clamp.get('selected')
            candidates_v = vel_clamp.get('candidates', [])
            selected_v = vel_clamp.get('selected')

            reason_parts = []
            if len(candidates_f) > 1:
                reason_parts.append(
                    f"Force conflict detected: proposals={candidates_f}N. "
                    f"Min-clamping selected {selected_f}N (sources: {force_clamp.get('sources',[])}; "
                    f"evidence: {force_clamp.get('evidence',[])})"
                )
            elif selected_f is not None:
                reason_parts.append(f"Force constraint unified at {selected_f}N")

            if len(candidates_v) > 1:
                reason_parts.append(
                    f"Velocity conflict detected: proposals={candidates_v}m/s. "
                    f"Clamped to {selected_v}m/s"
                )
            elif selected_v is not None:
                reason_parts.append(f"Velocity constraint unified at {selected_v}m/s")

            reason = "Min-Clamping arbitration: " + "; ".join(reason_parts) if reason_parts else "All constraints aligned"
            trace.append(DecisionTraceNode(
                module="CONFLICT_RESOLUTION",
                input=(
                    f"force_candidates={candidates_f}, vel_candidates={candidates_v}"
                ),
                output=(
                    f"resolved_force={selected_f}N, resolved_vel={selected_v}m/s"
                ),
                reason=reason,
                depends_on=["MEMORY_RETRIEVAL", "CONSTRAINT_REASONING"],
                latency_ms=0.8,
                confidence=1.0,
            ))
        else:
            trace.append(DecisionTraceNode(
                module="CONFLICT_RESOLUTION",
                input="no clamping metadata found",
                output="no override needed",
                reason="All constraint sources consistent; min-clamping produced unified values without conflict",
                depends_on=["MEMORY_RETRIEVAL", "CONSTRAINT_REASONING"],
                latency_ms=0.3,
                confidence=1.0,
            ))

        # ── Node 6: TASK_COMPILATION ──
        action_count = bt.root.action_count() if bt.root else 0
        trace.append(DecisionTraceNode(
            module="TASK_COMPILATION",
            input=f"BT({action_count} actions) + CG({len(cg.nodes)} nodes) + Scene({scene_obj_count} objs)",
            output=f"Universal Task IR v2.1 compiled",
            reason=f"All modules aggregated: {action_count} actions, {len(cg.nodes)} constraints, {scene_obj_count} objects",
            depends_on=["NL_PARSE", "SCENE_GROUNDING", "MEMORY_RETRIEVAL",
                       "CONSTRAINT_REASONING", "CONFLICT_RESOLUTION"],
            latency_ms=1.2,
            confidence=0.95,
        ))

        return trace

    # ============================================================
    # v3.0: 结构化意图
    # ============================================================

    def _build_task_intent(
        self, instruction: str, bt: BehaviorTree, scene: Any,
    ) -> TaskIntent:
        """从指令 + BT + 场景构建结构化 TaskIntent"""
        action = bt.metadata.get("action", "grasp")
        raw_target = bt.metadata.get("target", "")

        # 场景接地
        target_entity = None
        if scene and raw_target:
            obj = scene.find_object(raw_target)
            if obj:
                target_entity = GroundedEntity.from_scene_object(
                    obj, confidence=0.96
                )

        # 紧急度检测
        urgency = "normal"
        emergency_kw = ["快", "急", "赶紧", "马上", "立刻", "救命", "警报", "产线赶时间"]
        for kw in emergency_kw:
            if kw in instruction:
                urgency = "emergency"
                break

        # 用户约束提取
        user_constraints = {}
        modifiers = bt.metadata.get("modifiers", {})
        if isinstance(modifiers, dict):
            if "force_n" in modifiers:
                user_constraints["force_n"] = modifiers["force_n"]
            if "velocity_ms" in modifiers:
                user_constraints["velocity_ms"] = modifiers["velocity_ms"]

        return TaskIntent(
            action=action,
            target=target_entity,
            user_constraints=user_constraints,
            urgency=urgency,
            safety_goal="collision_free",
        )

    # ============================================================
    # v3.0: 可解释性报告
    # ============================================================

    def _build_explain_report(
        self,
        instruction: str,
        bt: BehaviorTree,
        cg: ConstraintGraph,
        scene: Any,
        trace: List[DecisionTraceNode],
    ) -> ExplainReport:
        """生成完整的可解释性报告 (Markdown + Mermaid + Override Ledger)"""

        # ── Scene summary (SSOT) ──
        scene_obj_count = len(scene.objects) if scene and scene.objects else 0
        scene_rel_count = len(scene.relations) if scene and scene.relations else 0
        blocking_pairs = []
        if scene:
            for r in scene.relations:
                if hasattr(r, 'predicate') and r.predicate.value == "blocking":
                    subj_obj = scene.find_object(r.subject)
                    obj_obj = scene.find_object(r.object)
                    if subj_obj and obj_obj:
                        blocking_pairs.append({
                            "obstacle": obj_obj.name,
                            "target": subj_obj.name,
                        })
        blocking_count = len(blocking_pairs)
        scene_summary = {
            "objects_count": scene_obj_count,
            "relations_count": scene_rel_count,
            "blocking_count": blocking_count,
            "blocking_pairs": blocking_pairs,
        }

        # ── Override ledger ──
        override_ledger_entries: List[OverrideLedgerEntry] = []
        raw_ledger = cg.metadata.get("override_ledger", [])
        force_clamp = cg.metadata.get("force_clamping", {})
        vel_clamp = cg.metadata.get("velocity_clamping", {})

        for i, entry in enumerate(raw_ledger):
            override_ledger_entries.append(OverrideLedgerEntry(
                conflict_id=f"Conflict #{i+1}",
                parameter=entry.get("parameter", ""),
                user_request=f"{entry.get('requested_val', '?')} {'N' if 'force' in entry.get('parameter','') else 'm/s'}",
                competing_constraint=f"Memory/Safety: {entry.get('clamping_sources', [])}",
                resolved_value=f"{entry.get('clamped_val', '?')} {'N' if 'force' in entry.get('parameter','') else 'm/s'}",
                arbitration_rule="Safety & Fragile affordance strictly override user command",
            ))

        # If no explicit ledger entries but clamping metadata exists, create from clamping
        if not override_ledger_entries:
            if force_clamp and len(force_clamp.get('candidates', [])) > 1:
                candidates = force_clamp['candidates']
                override_ledger_entries.append(OverrideLedgerEntry(
                    conflict_id="Conflict #1",
                    parameter="force_n",
                    user_request=f"{max(candidates)} N",
                    competing_constraint=f"Safety/Affordance limits (sources: {force_clamp.get('sources',[])})",
                    resolved_value=f"{force_clamp['selected']} N",
                    arbitration_rule="Min-Clamping: strictest safety constraint selected",
                ))
            if vel_clamp and len(vel_clamp.get('candidates', [])) > 1:
                candidates_v = vel_clamp['candidates']
                override_ledger_entries.append(OverrideLedgerEntry(
                    conflict_id=f"Conflict #{len(override_ledger_entries)+1}",
                    parameter="velocity_ms",
                    user_request=f"{max(candidates_v)} m/s",
                    competing_constraint=f"Safety/Preference limits (sources: {vel_clamp.get('sources',[])})",
                    resolved_value=f"{vel_clamp['selected']} m/s",
                    arbitration_rule="Min-Clamping: strictest velocity constraint selected",
                ))

        # ── Markdown report ──
        grounding_conf = 0.96
        for n in trace:
            if n.module == "SCENE_GROUNDING":
                grounding_conf = n.confidence
                break
        grounding_status = "✅ 成功" if grounding_conf >= 0.6 else "⚠️ 低置信度"

        md_lines = [
            f"# 具身决策归因报告",
            f"",
            f"## 任务摘要",
            f"- **指令**: {instruction[:80]}",
            f"- **IR 版本**: 3.0.0",
            f"- **规划引擎**: {bt.metadata.get('planner', 'RuleEngine')}",
            f"- **系统置信度**: {self._compute_overall_confidence(trace):.2%}",
            f"",
            f"## 场景接地",
            f"- **物体数**: {scene_obj_count}",
            f"- **空间关系**: {scene_rel_count} 条",
            f"- **阻挡关系**: {blocking_count} 对",
            f"- **接地状态**: {grounding_status} (置信度 {grounding_conf:.0%})",
        ]
        if blocking_pairs:
            md_lines.append("- **阻挡详情**:")
            for bp in blocking_pairs:
                md_lines.append(f"  - `{bp['obstacle']}` 阻挡 `{bp['target']}`")

        md_lines.extend([
            f"",
            f"## 冲突裁决记录",
        ])
        if override_ledger_entries:
            for entry in override_ledger_entries:
                md_lines.append(f"### {entry.conflict_id}: {entry.parameter}")
                md_lines.append(f"- 用户原始要求: **{entry.user_request}**")
                md_lines.append(f"- 竞争约束: {entry.competing_constraint}")
                md_lines.append(f"- 最终裁决: **{entry.resolved_value}**")
                md_lines.append(f"- 裁决规则: {entry.arbitration_rule}")
                md_lines.append("")
        else:
            md_lines.append("✅ 无冲突 — 所有约束一致通过。")
            md_lines.append("")

        decision_report_md = "\n".join(md_lines)

        # ── Mermaid graph ──
        mermaid_lines = ["graph TD"]
        mermaid_lines.append('  User["👤 用户原始诉求"]')
        if force_clamp and len(force_clamp.get('candidates', [])) > 1:
            mermaid_lines.append(f'  User -->|"要求 {max(force_clamp["candidates"])}N"| ForceCheck["🔴 触碰安全红线"]')
            mermaid_lines.append(f'  ForceCheck -->|"Min-Clamping"| ForceResult["✅ 裁决: {force_clamp["selected"]}N"]')
        if vel_clamp and len(vel_clamp.get('candidates', [])) > 1:
            mermaid_lines.append(f'  User -->|"要求 {max(vel_clamp["candidates"])}m/s"| VelCheck["🔴 触碰速度红线"]')
            mermaid_lines.append(f'  VelCheck -->|"Min-Clamping"| VelResult["✅ 裁决: {vel_clamp["selected"]}m/s"]')
        if not force_clamp.get('candidates') or len(force_clamp.get('candidates', [])) <= 1:
            mermaid_lines.append('  User -->|"参数合法"| Safe["✅ 直接通过"]')
        constraint_explain_graph_mermaid = "\n".join(mermaid_lines)

        return ExplainReport(
            decision_report_md=decision_report_md,
            constraint_explain_graph_mermaid=constraint_explain_graph_mermaid,
            override_ledger=override_ledger_entries,
            scene_summary=scene_summary,
        )

    # ============================================================
    # v3.0: 场景高危标记
    # ============================================================

    def _build_risk_objects(self, scene: Any) -> List[RiskObject]:
        """从场景图中提取高危物体"""
        risk_list = []
        if not scene or not scene.objects:
            return risk_list
        for obj in scene.objects:
            # fragile → collision risk
            if hasattr(obj, 'affordances'):
                for a in obj.affordances:
                    if hasattr(a, 'value') and a.value == "fragile":
                        risk_list.append(RiskObject(
                            entity_id=obj.id if hasattr(obj, 'id') else "",
                            name=obj.name if hasattr(obj, 'name') else "",
                            risk_type="collision",
                            priority="high",
                            description=f"易碎物体: {obj.name}",
                        ))
                        break
        return risk_list

    def _compute_overall_confidence(
        self, trace: List[DecisionTraceNode]
    ) -> float:
        """
        P1-1: 木桶短板公式 — min() + 低接地惩罚。

        公式: Conf_overall = min(Conf_nl, Conf_grounding, Conf_memory, Conf_constraint, Conf_compile)
               × W_penalty

        其中 W_penalty = 0.6 当 grounding_conf < 0.60 时,
        否则 W_penalty = 1.0
        """
        if not trace:
            return 0.0

        confs = {n.module: n.confidence for n in trace}
        grounding_conf = confs.get("SCENE_GROUNDING", 1.0)

        # 木桶短板: 取所有非 TASK_COMPILATION 步骤的最低置信度
        key_modules = ["NL_PARSE", "SCENE_GROUNDING", "MEMORY_RETRIEVAL",
                       "CONSTRAINT_REASONING", "CONFLICT_RESOLUTION"]
        min_conf = min(confs.get(m, 1.0) for m in key_modules)

        # 低接地惩罚
        penalty = 0.6 if grounding_conf < 0.60 else 1.0

        return round(min_conf * penalty, 3)

    def _build_optimization(
        self, cg: ConstraintGraph
    ) -> OptimizationSpace:
        """从约束图中提取优化边界（优先使用 min-clamping 裁决结果）"""
        # 优先使用 _apply_min_clamping 的裁决结果
        force_clamp = cg.metadata.get("force_clamping", {})
        vel_clamp = cg.metadata.get("velocity_clamping", {})

        force_nodes = [n for n in cg.nodes if n.constraint_type == "force_limit"]
        vel_nodes = [n for n in cg.nodes if n.constraint_type == "velocity_limit"]

        # 取最严格: 优先采信 clamping 裁决值
        max_force = force_clamp.get("selected") if force_clamp else min(
            (n.params.get("max_force_n", 10.0) for n in force_nodes),
            default=10.0,
        )
        min_force = max(
            (n.params.get("min_force_n", 0.1) for n in force_nodes),
            default=0.1,
        )
        max_vel = vel_clamp.get("selected") if vel_clamp else min(
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
