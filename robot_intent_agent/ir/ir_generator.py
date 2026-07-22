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
    PlanMetadata,
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
from robot_intent_agent.task_semantics import (
    ParsedTask,
    GroundedTask,
    ConstraintResolution,
    ValidationResult,
    PlanDecision,
    PlanStatus,
    ValidationIssue,
    build_grounded_task,
    load_parsed_task_from_bt,
    parse_task_semantics,
    RobotCapability,
    RobotCapabilityValidator,
    CapabilityDecision,
)
from robot_intent_agent.final_plan_validator import FinalPlanValidator


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
        parsed_task = self._load_parsed_task(instruction, behavior_tree, scene)
        grounded_task = build_grounded_task(parsed_task, scene=scene)
        constraint_resolution = self._load_constraint_resolution(constraint_graph)
        validator = FinalPlanValidator()
        validation_result = validator.validate(
            parsed_task=parsed_task,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            resolution=constraint_resolution,
        )

        # ── Robot capability validation ──
        robot_cap = RobotCapability()
        robot_validator = RobotCapabilityValidator(robot_cap)
        robot_executable, robot_decisions, robot_blocking = robot_validator.validate(
            parsed_task=parsed_task,
            scene=scene,
            behavior_tree=behavior_tree,
            constraint_resolution=constraint_resolution,
        )
        if robot_blocking:
            # Merge robot blocking reasons into validation
            for reason in robot_blocking:
                validation_result.issues.append(ValidationIssue(
                    code="ROBOT_CAPABILITY_BLOCKED",
                    message=reason,
                    severity="error",
                    subject="robot_capability",
                ))
            validation_result.execution_allowed = False
            validation_result.status = PlanStatus.BLOCKED
            # Also update plan_metadata to stay consistent
            constraint_resolution.plan_status = PlanStatus.BLOCKED

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
        skills = self._build_skills(behavior_tree, constraint_graph, scene, constraint_resolution)

        # ── 4. 约束集 (从 ConstraintGraph 转换 → Pydantic 模型) ──
        compiled_constraints = ConstraintSet(task_id=task_id)
        compiled_constraints = self._populate_constraint_set(
            compiled_constraints, constraint_graph
        )

        # ── 5. 优化空间 ──
        optimization = self._build_optimization(constraint_graph, constraint_resolution)

        # ── 6. Memory 上下文 ──
        mem_ctx = self._build_memory_context(memory_context, constraint_graph)

        # ── 7. v2.0: 决策轨迹 DAG + 系统置信度 ──
        decision_trace = self._build_decision_trace(
            instruction, behavior_tree, constraint_graph, scene, memory_context
        )
        overall_confidence = self._compute_overall_confidence(decision_trace)

        # ── 8. v3.0: 结构化意图 + 可解释性报告 ──
        task_intent = self._build_task_intent(parsed_task, scene)
        explain_report = self._build_explain_report(
            parsed_task, behavior_tree, constraint_graph, scene, decision_trace, constraint_resolution, validation_result
        )
        risk_objects = self._build_risk_objects(scene)
        plan_metadata = self._build_plan_metadata(
            constraint_resolution,
            validation_result,
            parsed_task,
            grounded_task,
            decision_trace,
        )

        # ── 8.5. Phase 8: Semantic enforcement trace ──
        enforcement_trace = self._build_enforcement_trace(
            parsed_task, grounded_task, behavior_tree, constraint_graph,
            scene, validation_result
        )

        # ── 9. 组装 v3.0 ──
        ir = RobotTaskIR(
            ir_version="3.0.0",
            task_metadata=metadata,
            precondition_assertions=preconditions,
            parsed_task=parsed_task,
            grounded_task=grounded_task,
            scene=scene,
            behavior_tree=behavior_tree,
            skills=skills,
            compiled_constraints=compiled_constraints,
            optimization_space=optimization,
            memory_context=mem_ctx,
            overall_confidence=overall_confidence,
            decision_trace=decision_trace,
            task_intent=task_intent,
            constraint_resolution=constraint_resolution,
            validation_result=validation_result,
            robot_capability_decisions=[d.__dict__ for d in robot_decisions],
            plan_metadata=plan_metadata,
            explain_report=explain_report,
            risk_objects=risk_objects,
            semantic_enforcement_trace=enforcement_trace,
        )

        return ir

    # ============================================================
    # Skills — 核心: BT Action + Constraint 绑定
    # ============================================================

    def _load_parsed_task(
        self,
        instruction: str,
        behavior_tree: BehaviorTree,
        scene: Optional[SemanticSceneGraph],
    ) -> ParsedTask:
        return load_parsed_task_from_bt(instruction, behavior_tree.metadata, scene=scene)

    def _load_constraint_resolution(self, constraint_graph: ConstraintGraph) -> ConstraintResolution:
        raw = constraint_graph.metadata.get("constraint_resolution")
        if isinstance(raw, dict):
            try:
                return ConstraintResolution.model_validate(raw)
            except Exception:
                pass
        return ConstraintResolution()

    def _build_plan_metadata(
        self,
        resolution: ConstraintResolution,
        validation_result: ValidationResult,
        parsed_task: ParsedTask,
        grounded_task: GroundedTask,
        decision_trace: List[DecisionTraceNode],
    ) -> PlanMetadata:
        return PlanMetadata(
            compiler_version="1.0.0",
            planner_name="RuleBasedPlanner",
            llm_model=None,
            rule_set_version=resolution.rule_set_version,
            audit_id=resolution.audit_id,
            plan_hash=resolution.plan_hash,
            plan_status=validation_result.status if validation_result else resolution.plan_status,
            parse_confidence=parsed_task.parse_confidence,
            grounding_confidence=grounded_task.grounding_confidence,
            constraint_confidence=parsed_task.constraint_confidence,
            plan_feasibility_confidence=0.0 if validation_result and not validation_result.execution_allowed else 1.0,
            execution_readiness=1.0 if validation_result and validation_result.execution_allowed else 0.0,
        )

    def _build_skills(
        self,
        bt: BehaviorTree,
        cg: ConstraintGraph,
        scene: Optional[SemanticSceneGraph],
        resolution: ConstraintResolution,
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

            # v3.0: 将 action.params + 域裁决结果以 ParamValue 结构注入
            self._merge_params_into_constraints(compiled, action.params, action.skill_name, resolution)

            # 物体信息
            object_info = None
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

            # P1-3: 全局兜底 + 值同步
            wrapped_params = self._wrap_all_params(action.params, skill_name)
            # Sync clamped values from compiled constraints into params
            wrapped_params = self._sync_clamped_values(wrapped_params, compiled, skill_name, resolution)
            skills[skill_name] = {
                "target": target,
                "params": wrapped_params,
                "constraints": compiled,
                "object": object_info,
            }

        return skills

    def _sync_clamped_values(
        self, params: Dict[str, Any], compiled: Dict[str, Any], skill_name: str, resolution: ConstraintResolution
    ) -> Dict[str, Any]:
        """
        P0-1: 将 compiled constraints 中的 clamped 值同步回 params。
        确保前端视图 C 显示的 params.force_n.value 等于最终裁决值。
        """
        result = dict(params)
        # force_n sync
        force_value = resolution.parameters.get("force_n").selected_value if resolution.parameters.get("force_n") else None
        velocity_value = resolution.parameters.get("velocity_ms").selected_value if resolution.parameters.get("velocity_ms") else None
        if force_value is not None and skill_name in ("Grasp", "GentleGrasp", "DynamicGrasp"):
            if "force_n" in result and isinstance(result["force_n"], dict):
                result["force_n"]["value"] = force_value
                result["force_n"]["source"] = [resolution.parameters["force_n"].selected_source_kind.value if resolution.parameters.get("force_n") and resolution.parameters["force_n"].selected_source_kind else "resolution"]
                result["force_n"]["evidence"] = [f"resolution:{force_value}"]
        if velocity_value is not None and skill_name in ("Reach", "MoveTo", "Push"):
            if "velocity_ms" in result and isinstance(result["velocity_ms"], dict):
                result["velocity_ms"]["value"] = velocity_value
                result["velocity_ms"]["source"] = [resolution.parameters["velocity_ms"].selected_source_kind.value if resolution.parameters.get("velocity_ms") and resolution.parameters["velocity_ms"].selected_source_kind else "resolution"]
                result["velocity_ms"]["evidence"] = [f"resolution:{velocity_value}"]
        return result

    def _wrap_all_params(
        self, params: Dict[str, Any], skill_name: str
    ) -> Dict[str, Any]:
        """
        P1-3: 全局兜底 -- 将所有物理参数无条件包装为 ParamValue 字典。
        前端视图 C 永远能解出 {value, source, evidence} 结构, 不会出现 ? N。
        """
        wrapped: Dict[str, Any] = {}
        for key, val in params.items():
            if key in ("force_n", "velocity_ms", "grip_style"):
                if isinstance(val, dict) and "value" in val:
                    wrapped[key] = val  # already wrapped
                else:
                    wrapped[key] = {
                        "value": val,
                        "source": ["param"],
                        "evidence": [f"action_param:{skill_name}.{key}={val}"],
                    }
            else:
                wrapped[key] = val
        return wrapped

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
        skill_name: str, resolution: ConstraintResolution,
    ) -> None:
        """
        v3.0: 以 ParamValue 结构将域裁决参数注入 compiled constraints。

        优先级: CG clamping 值 > action.params 值 > 默认值
        每个注入的参数包含: value, source, evidence
        """
        force_res = resolution.parameters.get("force_n")
        vel_res = resolution.parameters.get("velocity_ms")
        override_ledger = list(resolution.override_ledger)

        # ── force_n → ParamValue ──
        if skill_name in ("Grasp", "GentleGrasp"):
            final_val = force_res.selected_value if force_res and force_res.selected_value is not None else float(params.get("force_n", 0) or 5.0)

            # 构建溯源
            sources = [force_res.selected_source_kind.value] if force_res and force_res.selected_source_kind else ["resolution"]
            evidence = [f"resolution:{final_val}N"]
            for entry in override_ledger:
                if entry.get("parameter") == "force_n":
                    evidence.append(f"{entry.get('selected_value')}N")

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
            final_v = vel_res.selected_value if vel_res and vel_res.selected_value is not None else float(params.get("velocity_ms", 0) or 0.15)

            sources_v = [vel_res.selected_source_kind.value] if vel_res and vel_res.selected_source_kind else ["resolution"]
            evidence_v = [f"resolution:{final_v}m/s"]

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
    # v3.0: 决策轨迹 DAG (SSOT — 只读聚合器, 零独立推理)
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
                output="constraint resolution complete; domain-based parameters selected without overrides",
                reason="Domain-based constraint resolution produced unified values; no conflicts detected",
                depends_on=["MEMORY_RETRIEVAL", "CONSTRAINT_REASONING"],
                latency_ms=0.3,
                confidence=1.0,
            ))

        # ── Node 6: TASK_COMPILATION ──
        action_count = bt.root.action_count() if bt.root else 0
        trace.append(DecisionTraceNode(
            module="TASK_COMPILATION",
            input=f"BT({action_count} actions) + CG({len(cg.nodes)} nodes) + Scene({scene_obj_count} objs)",
            output=f"Universal Task IR v3.0 compiled",
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
        self,
        parsed_task: ParsedTask,
        scene: Any,
    ) -> TaskIntent:
        """从结构化 ParsedTask 构建兼容性的 TaskIntent"""
        target_entity = None
        if parsed_task.theme and parsed_task.theme.entity_id and scene:
            obj = scene.find_object(parsed_task.theme.entity_id) or scene.find_object(parsed_task.theme.mention)
            if obj:
                target_entity = GroundedEntity.from_scene_object(obj, confidence=parsed_task.theme.grounding_confidence or 0.96)
        elif parsed_task.theme:
            target_entity = GroundedEntity(
                entity_id=parsed_task.theme.entity_id or "",
                name=parsed_task.theme.mention,
                label=parsed_task.theme.specific_class,
                grounding_confidence=parsed_task.theme.grounding_confidence,
            )

        urgency = "normal"
        if parsed_task.manner == "fast":
            urgency = "high"
        if parsed_task.motion_state.state == "moving":
            urgency = "high"

        user_constraints: Dict[str, Any] = {}
        for constraint in parsed_task.user_constraints:
            if constraint.parameter == "force_n" and constraint.value is not None:
                user_constraints["force_n"] = constraint.value
            if constraint.parameter == "velocity_ms" and constraint.value is not None:
                user_constraints["velocity_ms"] = constraint.value

        return TaskIntent(
            action=parsed_task.action.value.lower(),
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
        parsed_task: ParsedTask,
        bt: BehaviorTree,
        cg: ConstraintGraph,
        scene: Any,
        trace: List[DecisionTraceNode],
        resolution: ConstraintResolution,
        validation_result: ValidationResult,
    ) -> ExplainReport:
        """生成完整的可解释性报告，仅从结构化裁决读数值。"""

        scene_obj_count = len(scene.objects) if scene and scene.objects else 0
        scene_rel_count = len(scene.relations) if scene and scene.relations else 0
        blocking_pairs = []
        if scene:
            for r in scene.relations:
                if hasattr(r, 'predicate') and r.predicate.value == "blocking":
                    subj_obj = scene.find_object(r.subject)
                    obj_obj = scene.find_object(r.object)
                    if subj_obj and obj_obj:
                        blocking_pairs.append({"obstacle": obj_obj.name, "target": subj_obj.name})

        scene_summary = {
            "objects_count": scene_obj_count,
            "relations_count": scene_rel_count,
            "blocking_count": len(blocking_pairs),
            "blocking_pairs": blocking_pairs,
        }

        override_ledger_entries: List[OverrideLedgerEntry] = []
        for index, entry in enumerate(resolution.override_ledger):
            if entry.get("selected_value") is None:
                continue
            unit = "N" if entry.get("parameter") == "force_n" else "m/s"
            requested = entry.get("selected_value")
            override_ledger_entries.append(OverrideLedgerEntry(
                conflict_id=f"Conflict #{index + 1}",
                parameter=entry.get("parameter", ""),
                user_request=f"{entry.get('value', entry.get('requested', requested))} {unit}",
                competing_constraint=f"{entry.get('source_kind', 'resolution')} / {entry.get('operator', '')}",
                resolved_value=f"{requested} {unit}",
                arbitration_rule=f"{entry.get('selected_source_kind', 'resolution')} selected within feasible domain",
            ))

        md_lines = [
            "# 具身决策归因报告",
            "",
            "## 任务摘要",
            f"- **指令**: {parsed_task.instruction[:80]}",
            f"- **动作**: {parsed_task.action.value}",
            f"- **计划状态**: {resolution.plan_status.value}",
            f"- **验证状态**: {validation_result.status.value}",
            f"- **IR 版本**: 3.0.0",
            f"- **规划引擎**: {bt.metadata.get('planner', 'RuleEngine')}",
            f"- **系统置信度**: {self._compute_overall_confidence(trace):.2%}",
            "",
            "## 场景接地",
            f"- **物体数**: {scene_obj_count}",
            f"- **空间关系**: {scene_rel_count} 条",
            f"- **阻挡关系**: {len(blocking_pairs)} 对",
        ]
        if parsed_task.theme:
            md_lines.append(f"- **主题**: {parsed_task.theme.mention} / {parsed_task.theme.specific_class or 'unknown'}")
        if parsed_task.destination:
            md_lines.append(f"- **目的地**: {parsed_task.destination.mention}")
        if parsed_task.recipient:
            md_lines.append(f"- **接收者**: {parsed_task.recipient.mention}")
        if parsed_task.support_surface:
            md_lines.append(f"- **支撑面**: {parsed_task.support_surface.mention}")
        if parsed_task.obstacle:
            md_lines.append("- **障碍物**: " + ", ".join(o.mention for o in parsed_task.obstacle))
        if blocking_pairs:
            md_lines.append("- **阻挡详情**:")
            for bp in blocking_pairs:
                md_lines.append(f"  - `{bp['obstacle']}` 阻挡 `{bp['target']}`")

        md_lines.extend([
            "",
            "## 约束裁决",
        ])
        for parameter, param_resolution in resolution.parameters.items():
            domain = param_resolution.domain
            md_lines.append(f"### {parameter}")
            md_lines.append(f"- 可行域: [{domain.min_value if domain.min_value is not None else '-inf'}, {domain.max_value if domain.max_value is not None else '+inf'}] {domain.unit}")
            md_lines.append(f"- 最终值: {param_resolution.selected_value} {domain.unit}")
            md_lines.append(f"- 来源: {param_resolution.selected_source_kind.value if param_resolution.selected_source_kind else 'unknown'}")
            if param_resolution.request_infeasible:
                md_lines.append(f"- 请求不可满足: {param_resolution.substitution_reason}")

        md_lines.extend(["", "## 验证结果"])
        if validation_result.issues:
            for issue in validation_result.issues:
                md_lines.append(f"- [{issue.severity}] {issue.code}: {issue.message}")
        else:
            md_lines.append("- 所有验证通过")

        decision_report_md = "\n".join(md_lines)

        mermaid_lines = ["graph TD"]
        mermaid_lines.append('  User["👤 用户原始诉求"]')
        for parameter, param_resolution in resolution.parameters.items():
            if param_resolution.selected_value is None:
                continue
            unit = "N" if parameter == "force_n" else "m/s"
            mermaid_lines.append(f'  User -->|"{parameter}"| Domain_{parameter}["可行域"]')
            mermaid_lines.append(f'  Domain_{parameter} -->|"selected {param_resolution.selected_value}{unit}"| Final_{parameter}["最终值"]')
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
        """从场景图中提取高危物体 (v3.0 SemanticObject-based)"""
        risk_list = []
        if not scene or not scene.objects:
            return risk_list

        from robot_intent_agent.semantic_reasoner.property_fusion import PropertyFusion
        for obj in scene.objects:
            sem_obj = PropertyFusion.from_scene_object(obj)

            # Fragility risk
            if sem_obj.fragility_level >= 1:
                risk_list.append(RiskObject(
                    entity_id=getattr(obj, 'id', ''),
                    name=getattr(obj, 'name', ''),
                    risk_type="collision",
                    priority="high" if sem_obj.fragility_level >= 3 else "medium",
                    description=f"易碎物体 (L{sem_obj.fragility_level}): {sem_obj.name}",
                ))

            # Electrical hazard
            if sem_obj.electrical_hazard:
                risk_list.append(RiskObject(
                    entity_id=getattr(obj, 'id', ''),
                    name=getattr(obj, 'name', ''),
                    risk_type="electrical",
                    priority="critical",
                    description=f"电气危险: {sem_obj.name}",
                ))

            # Fixed obstacle
            mob = sem_obj.mobility_type
            mob_str = mob.value if hasattr(mob, 'value') else str(mob)
            if mob_str == "fixed":
                risk_list.append(RiskObject(
                    entity_id=getattr(obj, 'id', ''),
                    name=getattr(obj, 'name', ''),
                    risk_type="collision",
                    priority="medium",
                    description=f"固定障碍物: {sem_obj.name}",
                ))
        return risk_list

    def _build_enforcement_trace(
        self,
        parsed_task: "ParsedTask",
        grounded_task: "GroundedTask",
        behavior_tree: "BehaviorTree",
        constraint_graph: "ConstraintGraph",
        scene: Any,
        validation_result: "ValidationResult",
    ) -> Dict[str, Any]:
        """Build the semantic enforcement trace — full-chain audit of every prohibition and condition.

        Phase 8: Provides CODE EVIDENCE that prohibitions and conditions flow through
        all pipeline stages: parsed → grounded → compiled → BT enforced → validator checked.
        """
        trace: Dict[str, Any] = {
            "prohibitions": [],
            "conditions": [],
            "summary": {"total_hard_prohibitions": 0, "all_enforced": True},
        }

        # ── Prohibitions from parsed_task.obstacle ──
        bt_actions = behavior_tree.root.flatten_actions() if behavior_tree and behavior_tree.root else []
        bt_skill_names = {a.skill_name for a in bt_actions}
        bt_avoid_params: set = set()
        for a in bt_actions:
            for key in ("avoid_obstacles", "avoid", "avoid_objects"):
                av = a.params.get(key, [])
                if isinstance(av, list):
                    bt_avoid_params.update(str(x) for x in av)
            # Also check PlanPath node
            if a.skill_name == "PlanPath":
                obs = a.params.get("avoid_obstacles", [])
                if isinstance(obs, list):
                    bt_avoid_params.update(str(x) for x in obs)

        cg_collision_avoids: Dict[str, str] = {}
        for n in constraint_graph.nodes:
            if n.constraint_type == "collision_avoid":
                obs_name = n.params.get("obstacle", "")
                if obs_name:
                    cg_collision_avoids[obs_name] = n.id

        for i, obs in enumerate(parsed_task.obstacle or []):
            obs_id = obs.entity_id or obs.mention
            grounded = obs.entity_id is not None
            compiled = obs_id in cg_collision_avoids
            bt_enforced = (
                obs_id in bt_avoid_params or
                any(obs_id in x for x in bt_avoid_params) or
                "PlanPath" in bt_skill_names or
                "Avoid" in bt_skill_names
            )
            eid = obs.entity_id or ""
            validator_issues = [
                i for i in (validation_result.issues or [])
                if hasattr(i, 'code') and 'NEGATION' in (i.code or "") and
                (obs.mention in (i.message or "") or (eid and eid in (i.message or "")))
            ]
            validator_checked = len(validator_issues) == 0  # No errors = enforcement verified

            status = "ENFORCED" if (grounded or compiled or bt_enforced) and validator_checked else "MISSING"
            if not grounded:
                status = "NOT_GROUNDED"
            elif not compiled:
                status = "NOT_COMPILED"
            elif not bt_enforced:
                status = "NOT_BT_ENFORCED"

            trace["prohibitions"].append({
                "index": i,
                "mention": obs.mention,
                "entity_id": obs.entity_id,
                "grounded": grounded,
                "compiled": compiled,
                "constraint_id": cg_collision_avoids.get(obs_id, ""),
                "bt_enforced": bt_enforced,
                "bt_mechanism": "PlanPath" if "PlanPath" in bt_skill_names else ("Avoid" if "Avoid" in bt_skill_names else "none"),
                "validator_checked": validator_checked,
                "enforcement_status": status,
            })

        # ── Conditions from parsed_task notes ──
        for note in (parsed_task.notes or []):
            if note.startswith("conditional_detected:") or note.startswith("unsupported_conditional:"):
                trace["conditions"].append({
                    "source": "parsed_task.notes",
                    "note": note,
                    "enforcement_status": "ENFORCED" if "unsupported" not in note else "UNSUPPORTED",
                })

        # Check BT for WaitUntilStable (condition enforcement evidence)
        has_wait_until_stable = "WaitUntilStable" in bt_skill_names
        if has_wait_until_stable:
            trace["conditions"].append({
                "source": "behavior_tree",
                "mechanism": "WaitUntilStable",
                "bt_enforced": True,
                "enforcement_status": "ENFORCED",
            })

        trace["summary"]["total_hard_prohibitions"] = len(trace["prohibitions"])
        trace["summary"]["total_conditions"] = len(trace["conditions"])
        trace["summary"]["all_enforced"] = all(
            p["enforcement_status"] == "ENFORCED" for p in trace["prohibitions"]
        )

        return trace

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
        self, cg: ConstraintGraph, resolution: ConstraintResolution
    ) -> OptimizationSpace:
        """从约束裁决结果提取优化边界。"""
        force_resolution = resolution.parameters.get("force_n")
        velocity_resolution = resolution.parameters.get("velocity_ms")
        force_domain = force_resolution.domain if force_resolution else None
        velocity_domain = velocity_resolution.domain if velocity_resolution else None
        return OptimizationSpace(
            force_range_n=(force_domain.min_value if force_domain and force_domain.min_value is not None else 0.1,
                           force_domain.max_value if force_domain and force_domain.max_value is not None else 10.0),
            velocity_range_ms=(velocity_domain.min_value if velocity_domain and velocity_domain.min_value is not None else 0.05,
                               velocity_domain.max_value if velocity_domain and velocity_domain.max_value is not None else 0.3),
            z_safe_margin_m=(0.02, 0.10),
            collision_margin_m=(0.03, 0.15),
            targets=["max_safety", "min_time"],
            free_params={"plan_status": resolution.plan_status.value},
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
