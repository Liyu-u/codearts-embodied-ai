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

from typing import Any, Dict, List, Optional, Tuple, Iterable
from dataclasses import dataclass, field

from robot_intent_agent.config.settings import get_settings
from robot_intent_agent.task_semantics import (
    ConstraintDomain,
    ConstraintOperator,
    ConstraintResolution,
    ConstraintSourceKind,
    ParsedConstraint,
    ParsedTask,
    ParameterResolution,
    PlanDecision,
    PlanStatus,
    TaskActionKind,
    build_grounded_task,
    load_parsed_task_from_bt,
    make_plan_hash,
    parse_task_semantics,
)

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


@dataclass
class FeasibleDomainResult:
    """Result of interval intersection (never an average of constraints)."""
    parameter: str
    min_value: Optional[float]
    max_value: Optional[float]
    selected_value: Optional[float]
    status: PlanStatus
    sources: List[str] = field(default_factory=list)
    substituted_from: Optional[float] = None
    reason: str = ""

    def model_dump(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def compile_feasible_domain(
    parameter: str,
    constraints: Iterable[Dict[str, Any]],
    default_domain: Tuple[Optional[float], Optional[float]] = (None, None),
    allow_safe_substitution: bool = True,
) -> FeasibleDomainResult:
    """Intersect hard intervals and choose a safe value.

    Each constraint may contain ``min``/``max`` or ``min_value``/``max_value``
    plus an optional ``value`` and ``operator``.  A contradictory interval is
    ``BLOCKED``; no midpoint can make an empty domain executable.
    """
    lower, upper = default_domain
    sources: List[str] = []
    requested: Optional[float] = None
    for item in constraints or []:
        operator = str(item.get("operator", "")).lower()
        value = item.get("value")
        item_min = item.get("min_value", item.get("min"))
        item_max = item.get("max_value", item.get("max"))
        source = str(item.get("source") or item.get("source_kind") or "constraint")
        sources.append(source)
        if operator in {"exact", "eq", "="} and value is not None:
            requested = float(value); item_min = value; item_max = value
        elif operator in {"min", ">=", "at_least"}:
            item_min = value if item_min is None else item_min
            if value is not None: requested = requested if requested is not None else float(value)
        elif operator in {"max", "<=", "at_most"}:
            item_max = value if item_max is None else item_max
            if value is not None: requested = requested if requested is not None else float(value)
        elif operator == "range":
            pass
        if item_min is not None:
            lower = float(item_min) if lower is None else max(lower, float(item_min))
        if item_max is not None:
            upper = float(item_max) if upper is None else min(upper, float(item_max))
    if lower is not None and upper is not None and lower > upper:
        return FeasibleDomainResult(parameter, lower, upper, None, PlanStatus.BLOCKED,
                                    sources, requested, "empty_intersection")
    if requested is None:
        selected = lower if lower is not None else upper
        status = PlanStatus.READY
        return FeasibleDomainResult(parameter, lower, upper, selected, status, sources)
    if (lower is None or requested >= lower) and (upper is None or requested <= upper):
        return FeasibleDomainResult(parameter, lower, upper, requested, PlanStatus.READY, sources)
    if not allow_safe_substitution:
        return FeasibleDomainResult(parameter, lower, upper, None, PlanStatus.BLOCKED,
                                    sources, requested, "requested_value_outside_domain")
    selected = requested
    if lower is not None: selected = max(selected, lower)
    if upper is not None: selected = min(selected, upper)
    return FeasibleDomainResult(parameter, lower, upper, selected,
                                PlanStatus.READY_WITH_SAFE_SUBSTITUTION, sources,
                                requested, "safe_clamp_to_intersection")


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
        parsed_task = self._load_parsed_task(instruction, behavior_tree, scene)
        grounded_task = build_grounded_task(parsed_task, scene=scene)

        graph = ConstraintGraph(
            task_id=behavior_tree.task_id,
            metadata={
                "instruction": instruction,
                "planner": behavior_tree.metadata.get("planner", "unknown"),
                "parsed_task": parsed_task.model_dump(),
                "grounded_task": grounded_task.model_dump(),
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
        # The semantic graph is the source for user constraints and obstacles.
        # The legacy rule extractor remains only as a compatibility supplement
        # for old BTs without compiler metadata.
        if behavior_tree.metadata.get("semantic_authority") == "SemanticCompiler":
            rule_constraints = self._graph_constraints(
                parsed_task, scene=scene, target=target, memory_context=memory_context or []
            )
        else:
            rule_constraints = self.engine.extract(
                instruction=instruction,
                scene=scene,
                target=target,
                memory_context=memory_context or [],
            )
        graph.add_all(rule_constraints)

        # ══════════════════════════════════════════
        # 第 1.5 层: 提取 user_request 注入约束图
        # 确保用户原始期望值参与 Layer 4 的三向对冲
        # ══════════════════════════════════════════
        self._inject_user_requests(graph, parsed_task)

        # ══════════════════════════════════════════
        # 第 1.6 层: 注入 obstacle/avoid 约束
        # 确保 parsed_task.obstacle 传播到 CG collision_avoid
        # ══════════════════════════════════════════
        self._inject_obstacle_constraints(graph, parsed_task, scene)

        # ══════════════════════════════════════════
        # 第 2 层: 与 BehaviorTree 对齐
        # ══════════════════════════════════════════
        self._align_with_bt(graph, behavior_tree)

        # ══════════════════════════════════════════
        # 第 3 层: 去重 + 冲突检测
        # ══════════════════════════════════════════
        self._deduplicate(graph)
        self._resolve_conflicts(graph, parsed_task, grounded_task)

        # ══════════════════════════════════════════
        # 第 4 层: 可行域裁决
        # ══════════════════════════════════════════
        resolution = self._resolve_constraints(graph, parsed_task, grounded_task, scene)

        # Write resolved numeric values back to BT so consistency checks pass
        self._apply_resolution_to_bt(behavior_tree, resolution)

        graph.metadata["constraint_resolution"] = resolution.model_dump()
        graph.metadata["plan_status"] = resolution.plan_status.value
        graph.metadata["final_parameters"] = resolution.final_values()
        graph.metadata["audit_id"] = resolution.audit_id
        graph.metadata["plan_hash"] = resolution.plan_hash
        graph.metadata["rule_set_version"] = resolution.rule_set_version

        plan_decision = PlanDecision(
            parsed_task=parsed_task,
            grounded_task=grounded_task,
            constraint_resolution=resolution,
            validation_result=self._placeholder_validation(resolution.plan_status),
            plan_status=resolution.plan_status,
            final_parameters=resolution.final_values(),
            ready_for_execution=resolution.plan_status in (PlanStatus.READY, PlanStatus.READY_WITH_SAFE_SUBSTITUTION),
            plan_hash=resolution.plan_hash,
            audit_id=resolution.audit_id,
            rule_set_version=resolution.rule_set_version,
            parse_confidence=parsed_task.parse_confidence,
            grounding_confidence=grounded_task.grounding_confidence,
            constraint_confidence=parsed_task.constraint_confidence,
            plan_feasibility_confidence=1.0 if resolution.plan_status != PlanStatus.BLOCKED else 0.0,
            execution_readiness=1.0 if resolution.plan_status in (PlanStatus.READY, PlanStatus.READY_WITH_SAFE_SUBSTITUTION) else 0.0,
        )
        graph.metadata["plan_decision"] = plan_decision.model_dump()

        return graph

    def _graph_constraints(self, parsed_task: ParsedTask, scene=None, target: str = "",
                           memory_context: Optional[List[Dict[str, Any]]] = None) -> List[ConstraintNode]:
        constraints: List[ConstraintNode] = []
        # User numeric atoms are injected by _inject_user_requests below.
        # Do not materialize them a second time here: an EXACT request would
        # otherwise become both a parsed exact constraint and a hard interval,
        # turning a safely substitutable request (5N on a 3N object) into an
        # artificial empty domain.
        for obstacle in parsed_task.obstacle:
            constraints.append(SpatialConstraint.collision_avoid(
                obstacle=obstacle.entity_id or obstacle.mention,
                min_distance_m=0.05,
                applies_to_skill="",
                priority=ConstraintPriority.HARD,
            ))
        # Scene-derived physical limits are part of the grounded execution
        # contract, not a second language parser.  Use the already grounded
        # entity and the domain property mapper to preserve fragility and
        # precision-object limits.
        target_obj = self._scene_object_for(scene, parsed_task.theme) if scene is not None else None
        target_name = getattr(target_obj, "name", None) or (parsed_task.theme.mention if parsed_task.theme else target)
        if scene is not None and target_name:
            constraints.extend(self.engine._extract_object_constraints(scene, target_name))
            constraints.extend(self.engine._extract_scene_constraints(scene, target_name))
        if memory_context:
            constraints.extend(self.engine._extract_memory_constraints(memory_context, target_name))
        return constraints

    @staticmethod
    def _scene_object_for(scene: Optional[SemanticSceneGraph], ref: Any):
        if scene is None or ref is None:
            return None
        entity_id = getattr(ref, "entity_id", None)
        mention = getattr(ref, "mention", None) or str(ref)
        if entity_id:
            found = scene.find_object(entity_id)
            if found:
                return found
        found = scene.find_object(mention)
        if found:
            return found
        return next((obj for obj in scene.objects
                     if mention and (mention in obj.name or obj.name in mention)), None)

    # ============================================================
    # 第 1.5 层: 注入 user_request 约束
    # ============================================================

    def _load_parsed_task(
        self,
        instruction: str,
        bt: BehaviorTree,
        scene: Optional[SemanticSceneGraph] = None,
    ) -> ParsedTask:
        return load_parsed_task_from_bt(instruction, bt.metadata, scene=scene)

    def _inject_user_requests(
        self, graph: ConstraintGraph, parsed_task: ParsedTask
    ) -> None:
        """
        从 ParsedTask 的 user_constraints 中提取用户原始期望值。
        只记录显式用户约束，不把系统默认值或安全红线伪装成用户请求。
        """
        for constraint in parsed_task.user_constraints:
            if constraint.parameter == "force_n":
                node = PhysicalConstraint.force_limit(
                    target=parsed_task.theme.mention if parsed_task.theme else "",
                    max_force_n=constraint.max_value if constraint.operator == ConstraintOperator.MAX and constraint.max_value is not None else (constraint.value if constraint.value is not None else 10.0),
                    min_force_n=constraint.min_value if constraint.min_value is not None else 0.1,
                    applies_to_skill="Grasp",
                    priority=ConstraintPriority.SOFT if constraint.operator != ConstraintOperator.EXACT else ConstraintPriority.HARD,
                )
                node.description = f"user_request:{constraint.operator.value}:{constraint.text_span}"
                node.params["_source_label"] = "user_request"
                node.params["_source_kind"] = constraint.source_kind.value
                node.params["_constraint_id"] = constraint.constraint_id
                node.params["_operator"] = constraint.operator.value
                node.params["_text_span"] = constraint.text_span
                node.params["_unit"] = constraint.unit
                if constraint.value is not None:
                    node.params["_requested_value"] = constraint.value
                if constraint.min_value is not None:
                    node.params["_requested_min"] = constraint.min_value
                if constraint.max_value is not None:
                    node.params["_requested_max"] = constraint.max_value
                graph.add(node)

            if constraint.parameter == "velocity_ms":
                node = PhysicalConstraint.velocity_limit(
                    max_linear_ms=constraint.max_value if constraint.operator == ConstraintOperator.MAX and constraint.max_value is not None else (constraint.value if constraint.value is not None else 0.3),
                    applies_to_skill="Reach",
                    priority=ConstraintPriority.SOFT if constraint.operator != ConstraintOperator.EXACT else ConstraintPriority.HARD,
                )
                node.description = f"user_request:{constraint.operator.value}:{constraint.text_span}"
                node.params["_source_label"] = "user_request"
                node.params["_source_kind"] = constraint.source_kind.value
                node.params["_constraint_id"] = constraint.constraint_id
                node.params["_operator"] = constraint.operator.value
                node.params["_text_span"] = constraint.text_span
                node.params["_unit"] = constraint.unit
                if constraint.value is not None:
                    node.params["_requested_value"] = constraint.value
                if constraint.min_value is not None:
                    node.params["_requested_min"] = constraint.min_value
                if constraint.max_value is not None:
                    node.params["_requested_max"] = constraint.max_value
                graph.add(node)

    def _inject_obstacle_constraints(
        self, graph: ConstraintGraph, parsed_task: ParsedTask,
        scene: Optional[SemanticSceneGraph] = None,
    ) -> None:
        """Create collision_avoid nodes for each obstacle in parsed_task.obstacle."""
        if not parsed_task.obstacle:
            return
        for obs in parsed_task.obstacle:
            obstacle_id = obs.entity_id or obs.mention
            if not obstacle_id:
                continue
            node = SpatialConstraint.collision_avoid(
                obstacle=obstacle_id,
                min_distance_m=0.05,
                applies_to_skill="",  # global — applies to all motion
                priority=ConstraintPriority.HARD,
            )
            node.description = f"obstacle:{obs.mention}"
            node.params["obstacle"] = obstacle_id
            node.params["semantic_role"] = "obstacle"
            graph.add(node)

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
            "velocity_limit":   ["MoveTo", "Transport", "Reach", "Push"],
            "collision_avoid":  [],   # 空 = 全局
            "z_axis_floor":     [],   # 空 = 全局
            "joint_limits":     [],
            "max_gripper_force": ["Grasp", "GentleGrasp"],
            "workspace_bounds": [],
            "human_proximity":  ["MoveTo", "Transport"],
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
    # BT 回写 — 将裁决结果写入行为树
    # ============================================================

    @staticmethod
    def _apply_resolution_to_bt(bt: BehaviorTree, resolution: ConstraintResolution) -> None:
        """Write resolved parameter values back to BT action nodes."""
        final_force = resolution.parameters.get("force_n")
        final_velocity = resolution.parameters.get("velocity_ms")
        for action in bt.root.flatten_actions():
            if action.skill_name in ("Grasp", "GentleGrasp", "DynamicGrasp") and final_force and final_force.selected_value is not None:
                action.params["force_n"] = final_force.selected_value
            if action.skill_name in ("Reach", "MoveTo", "Transport", "Push") and final_velocity and final_velocity.selected_value is not None:
                action.params["velocity_ms"] = final_velocity.selected_value

    # ============================================================
    # 冲突检测
    # ============================================================

    def _resolve_conflicts(self, graph: ConstraintGraph, parsed_task: ParsedTask, grounded_task) -> None:
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
                if min_f > max_f:
                    violations.append(
                        f"CONFLICT: {node.id}: min_force({min_f}) >= max_force({max_f})"
                    )

        if violations:
            graph.metadata["conflicts"] = violations

    def _resolve_constraints(
        self,
        graph: ConstraintGraph,
        parsed_task: ParsedTask,
        grounded_task,
        scene: Optional[SemanticSceneGraph],
    ) -> ConstraintResolution:
        settings = get_settings()
        resolution = ConstraintResolution(rule_set_version="1.0.0")
        plan_status = PlanStatus.READY

        # Only escalate to NEEDS_CLARIFICATION for truly blocking clarifications
        # (theme, recipient identity). Operational gaps like
        # "delivery_pose_or_fetch_zone" are expected and handled downstream.
        _BLOCKING_CLARIFICATION_KEYWORDS = ("缺少接收者", "缺少目标", "未识别")
        blocking_clarifications = [
            c for c in grounded_task.required_clarifications
            if any(kw in c for kw in _BLOCKING_CLARIFICATION_KEYWORDS)
        ]
        if blocking_clarifications:
            plan_status = PlanStatus.NEEDS_CLARIFICATION

        force_resolution = self._resolve_numeric_parameter(
            parameter="force_n",
            nodes=graph.nodes,
            parsed_constraints=[c for c in parsed_task.user_constraints if c.parameter == "force_n"],
            hard_default=(0.1, settings.default_max_force_n),
            scene=scene,
            parsed_task=parsed_task,
        )
        velocity_resolution = self._resolve_numeric_parameter(
            parameter="velocity_ms",
            nodes=graph.nodes,
            parsed_constraints=[c for c in parsed_task.user_constraints if c.parameter == "velocity_ms"],
            hard_default=(0.05, settings.default_max_velocity_ms),
            scene=scene,
            parsed_task=parsed_task,
        )

        resolution.parameters["force_n"] = force_resolution
        resolution.parameters["velocity_ms"] = velocity_resolution

        if force_resolution.request_infeasible or velocity_resolution.request_infeasible:
            if plan_status != PlanStatus.NEEDS_CLARIFICATION:
                # Finite numeric requests outside the robot/object domain are
                # clamped by _resolve_numeric_parameter.  Dispatch the safe
                # value and expose the substitution in the audit ledger.
                # Clarification is reserved for missing/ambiguous semantics;
                # treating 50N→2N as a clarification unnecessarily blocked a
                # recoverable operation.
                plan_status = PlanStatus.READY_WITH_SAFE_SUBSTITUTION

        if force_resolution.domain.is_empty() or velocity_resolution.domain.is_empty():
            plan_status = PlanStatus.BLOCKED

        # Only set NEEDS_CLARIFICATION for truly blocking missing roles.
        # Operational gaps like "delivery_pose_or_fetch_zone" are expected
        # and should not block execution readiness.
        _BLOCKING_ROLES = {"theme", "recipient", "recipient_pose_or_handover_zone"}
        if plan_status == PlanStatus.READY and parsed_task.action in (TaskActionKind.FETCH, TaskActionKind.HANDOVER, TaskActionKind.TRANSFER):
            blocking_missing = [r for r in grounded_task.missing_roles if r in _BLOCKING_ROLES]
            if blocking_missing:
                plan_status = PlanStatus.NEEDS_CLARIFICATION

        if parsed_task.action == TaskActionKind.WAIT and "condition" in grounded_task.missing_roles:
            plan_status = PlanStatus.NEEDS_CLARIFICATION

        if parsed_task.action == TaskActionKind.PLACE and "support_surface" in grounded_task.missing_roles:
            plan_status = PlanStatus.NEEDS_CLARIFICATION

        # Distinguish a recoverable request outside robot/object limits from
        # an internally contradictory user instruction.  The former may be
        # clamped; the latter must remain blocked (e.g. EXACT 8N plus MAX 2N).
        by_parameter = {}
        for constraint in parsed_task.user_constraints:
            by_parameter.setdefault(constraint.parameter, []).append(constraint)
        for parameter, constraints in by_parameter.items():
            exact_values = [c.value for c in constraints if c.operator.value == "exact" and c.value is not None]
            if len(set(exact_values)) > 1:
                plan_status = PlanStatus.BLOCKED
            min_values = [c.value if c.value is not None else c.min_value for c in constraints if c.operator.value == "min"]
            max_values = [c.value if c.value is not None else c.max_value for c in constraints if c.operator.value == "max"]
            if any(v is not None and any(m is not None and v < m for m in min_values) for v in exact_values):
                plan_status = PlanStatus.BLOCKED
            if any(v is not None and any(m is not None and v > m for m in max_values) for v in exact_values):
                plan_status = PlanStatus.BLOCKED
            if min_values and max_values and max(min_values) > min(max_values):
                plan_status = PlanStatus.BLOCKED

        resolution.plan_status = plan_status
        # Only actual overrides (not all audit trail entries) go into override_ledger
        if force_resolution.request_infeasible:
            resolution.override_ledger.extend([e for e in force_resolution.audit_trail if e.get("code") == "REQUEST_INFEASIBLE"])
        if velocity_resolution.request_infeasible:
            resolution.override_ledger.extend([e for e in velocity_resolution.audit_trail if e.get("code") == "REQUEST_INFEASIBLE"])
        resolution.plan_hash = make_plan_hash({
            "instruction": graph.metadata.get("instruction", ""),
            "plan_status": resolution.plan_status.value,
            "force": force_resolution.selected_value,
            "velocity": velocity_resolution.selected_value,
            "task": parsed_task.action.value,
        })
        return resolution

    @staticmethod
    @staticmethod
    def _placeholder_validation(plan_status: PlanStatus):
        """@deprecated: Use FinalPlanValidator.validate() instead.

        This produces a weak ValidationResult that only checks plan_status,
        NOT the 8 real validation dimensions. The authoritative validation
        happens in RobotTaskIRGenerator.generate() via FinalPlanValidator.
        """
        from robot_intent_agent.task_semantics import ValidationResult

        return ValidationResult(
            status=plan_status,
            execution_allowed=plan_status in (PlanStatus.READY, PlanStatus.READY_WITH_SAFE_SUBSTITUTION),
            issues=[],
        )

    def _resolve_numeric_parameter(
        self,
        parameter: str,
        nodes: List[ConstraintNode],
        parsed_constraints: List[ParsedConstraint],
        hard_default: Tuple[float, float],
        scene: Optional[SemanticSceneGraph],
        parsed_task: ParsedTask,
    ) -> ParameterResolution:
        domain = ConstraintDomain(min_value=hard_default[0], max_value=hard_default[1], unit="N" if parameter == "force_n" else "m/s")
        candidates: List[ParsedConstraint] = list(parsed_constraints)
        audit: List[Dict[str, Any]] = []

        target_obj = None
        if scene and parsed_task.theme:
            target_obj = self._scene_object_for(scene, parsed_task.theme)

        if target_obj is not None:
            try:
                # Use PropertyMapper for authoritative object limits (same as web UI)
                from robot_intent_agent.property_inference.property_mapper import PropertyMapper

                mapper = PropertyMapper()
                obj_attrs = getattr(target_obj, "attributes", {}) or {}
                obs_input = {
                    "name": getattr(target_obj, "name", "unknown"),
                    "category": getattr(target_obj, "specific_class", None) or getattr(target_obj, "label", None) or getattr(target_obj, "name", "unknown"),
                    "geometry": {
                        "width": getattr(getattr(target_obj, "bbox", None), "width", 0.05),
                        "height": getattr(getattr(target_obj, "bbox", None), "height", 0.08),
                        "depth": getattr(getattr(target_obj, "bbox", None), "depth", 0.05),
                    },
                    "position": [
                        getattr(getattr(target_obj, "position", None), "x", 0.0),
                        getattr(getattr(target_obj, "position", None), "y", 0.0),
                        getattr(getattr(target_obj, "position", None), "z", 0.03),
                    ],
                    "material": obj_attrs.get("material", ""),
                }
                sem_obj = mapper.infer(obs_input)
                if parameter == "force_n":
                    domain.max_value = min(domain.max_value or sem_obj.max_force_N.value, sem_obj.max_force_N.value)
                else:
                    domain.max_value = min(domain.max_value or sem_obj.max_velocity_ms.value, sem_obj.max_velocity_ms.value)
                audit.append({"source": "OBJECT_HARD_LIMIT", "entity_id": getattr(target_obj, "id", None), "max": domain.max_value})
            except Exception:
                pass

        for node in nodes:
            # Parsed user atoms are the authoritative source for exact/min/max
            # semantics.  The compatibility node created by
            # _inject_user_requests is an annotation for binding/traceability,
            # not a second hard interval; counting it here makes 5N on a 2N
            # object look like an internal empty domain before substitution.
            if node.params.get("_source_label") == "user_request":
                continue
            if parameter == "force_n" and node.constraint_type in ("force_limit", "max_gripper_force"):
                max_force = node.params.get("max_force_n")
                min_force = node.params.get("min_force_n")
                source_kind = node.params.get("_source_kind") or self._infer_source_kind(node)
                constraint_id = node.id
                if max_force is not None:
                    candidates.append(self._node_to_candidate(node, parameter, ConstraintOperator.MAX, float(max_force), None, float(max_force), source_kind))
                    domain.max_value = float(max_force) if domain.max_value is None else min(domain.max_value, float(max_force))
                    if constraint_id not in domain.upper_sources:
                        domain.upper_sources.append(constraint_id)
                if min_force is not None:
                    candidates.append(self._node_to_candidate(node, parameter, ConstraintOperator.MIN, None, float(min_force), None, source_kind))
                    domain.min_value = float(min_force) if domain.min_value is None else max(domain.min_value, float(min_force))
                    if constraint_id not in domain.lower_sources:
                        domain.lower_sources.append(constraint_id)
            if parameter == "velocity_ms" and node.constraint_type == "velocity_limit":
                max_v = node.params.get("max_linear_ms")
                source_kind = node.params.get("_source_kind") or self._infer_source_kind(node)
                if max_v is not None:
                    candidates.append(self._node_to_candidate(node, parameter, ConstraintOperator.MAX, float(max_v), None, float(max_v), source_kind))
                    domain.max_value = float(max_v) if domain.max_value is None else min(domain.max_value, float(max_v))
                    if node.id not in domain.upper_sources:
                        domain.upper_sources.append(node.id)

        # 只有显式用户精确约束才可触发不可满足请求
        exact_user = next((c for c in parsed_constraints if c.operator == ConstraintOperator.EXACT and c.value is not None), None)
        max_user = next((c for c in parsed_constraints if c.operator == ConstraintOperator.MAX and c.max_value is not None), None)
        min_user = next((c for c in parsed_constraints if c.operator == ConstraintOperator.MIN and c.min_value is not None), None)
        range_user = next((c for c in parsed_constraints if c.operator == ConstraintOperator.RANGE and c.min_value is not None and c.max_value is not None), None)

        if domain.min_value is not None and domain.max_value is not None and domain.min_value > domain.max_value:
            return ParameterResolution(
                parameter=parameter,
                domain=domain,
                candidates=candidates,
                request_infeasible=True,
                override_required=False,
                substitution_reason="hard_constraints_empty",
                audit_trail=[{"code": "HARD_CONSTRAINT_EMPTY", "parameter": parameter, "domain": domain.model_dump()}],
            )

        selected = None
        selected_source = None
        selected_constraint_id = None
        substituted_from = None
        substitution_reason = None
        request_infeasible = False
        override_required = False

        if exact_user is not None:
            if exact_user.value is not None and (domain.min_value is None or exact_user.value >= domain.min_value) and (domain.max_value is None or exact_user.value <= domain.max_value):
                selected = exact_user.value
                selected_source = exact_user.source_kind
                selected_constraint_id = exact_user.constraint_id
            else:
                request_infeasible = True
                override_required = True
                substituted_from = exact_user.value
                selected = self._safe_substitute(domain, exact_user.value if exact_user.value is not None else domain.midpoint() or 0.0)
                substitution_reason = "USER_EXACT_EXCEEDS_OBJECT_HARD_LIMIT"
                audit.append({"code": "REQUEST_INFEASIBLE", "parameter": parameter, "requested": exact_user.value, "selected": selected, "reason": substitution_reason})
        elif range_user is not None:
            lower = range_user.min_value
            upper = range_user.max_value
            feasible_lower = max(domain.min_value if domain.min_value is not None else lower, lower)
            feasible_upper = min(domain.max_value if domain.max_value is not None else upper, upper)
            if feasible_lower > feasible_upper:
                request_infeasible = True
                override_required = True
                selected = self._safe_substitute(domain, range_user.max_value)
                substituted_from = range_user.max_value
                substitution_reason = "USER_RANGE_INFEASIBLE"
                audit.append({"code": "REQUEST_INFEASIBLE", "parameter": parameter, "requested": [lower, upper], "selected": selected, "reason": substitution_reason})
            else:
                midpoint = (feasible_lower + feasible_upper) / 2.0
                selected = self._prefer_within_domain(midpoint, domain)
                selected_source = range_user.source_kind
                selected_constraint_id = range_user.constraint_id
        elif max_user is not None:
            feasible_upper = min(domain.max_value if domain.max_value is not None else max_user.max_value or domain.max_value or 0.0, max_user.max_value or domain.max_value or 0.0)
            if domain.min_value is not None and feasible_upper < domain.min_value:
                request_infeasible = True
                override_required = True
                selected = self._safe_substitute(domain, feasible_upper)
                substituted_from = max_user.max_value
                substitution_reason = "USER_MAX_INFEASIBLE"
                audit.append({"code": "REQUEST_INFEASIBLE", "parameter": parameter, "requested": max_user.max_value, "selected": selected, "reason": substitution_reason})
            else:
                preferred = max_user.max_value if max_user.max_value is not None else domain.max_value
                selected = self._prefer_within_domain(preferred if preferred is not None else domain.midpoint() or 0.0, domain)
                selected_source = max_user.source_kind
                selected_constraint_id = max_user.constraint_id
        elif min_user is not None:
            feasible_lower = max(domain.min_value if domain.min_value is not None else min_user.min_value or 0.0, min_user.min_value or 0.0)
            if domain.max_value is not None and feasible_lower > domain.max_value:
                request_infeasible = True
                override_required = True
                selected = self._safe_substitute(domain, feasible_lower)
                substituted_from = min_user.min_value
                substitution_reason = "USER_MIN_INFEASIBLE"
                audit.append({"code": "REQUEST_INFEASIBLE", "parameter": parameter, "requested": min_user.min_value, "selected": selected, "reason": substitution_reason})
            else:
                preferred = min_user.min_value if min_user.min_value is not None else domain.midpoint()
                selected = self._prefer_within_domain(preferred if preferred is not None else domain.midpoint() or 0.0, domain)
                selected_source = min_user.source_kind
                selected_constraint_id = min_user.constraint_id
        else:
            # no explicit user value: prefer RECOMMENDED_VALUE over DEFAULT_VALUE over midpoint
            recommended_candidates = [c for c in candidates if c.source_kind == ConstraintSourceKind.RECOMMENDED_VALUE]
            default_candidates = [c for c in candidates if c.source_kind == ConstraintSourceKind.DEFAULT_VALUE]
            memory_candidates = [c for c in candidates if c.source_kind == ConstraintSourceKind.MEMORY_PREFERENCE]
            soft_candidates = recommended_candidates + memory_candidates + default_candidates
            if soft_candidates:
                preferred_candidate = soft_candidates[0]
                preferred_value = preferred_candidate.normalized_value or preferred_candidate.value or preferred_candidate.max_value or preferred_candidate.min_value
                if preferred_value is not None:
                    selected = self._prefer_within_domain(preferred_value, domain)
                    selected_source = preferred_candidate.source_kind
                    selected_constraint_id = preferred_candidate.constraint_id
            if selected is None:
                # Fall back to midpoint, not the domain max (differentiates from hard limit)
                mid = domain.midpoint()
                if mid is not None:
                    selected = mid
                else:
                    selected = domain.max_value or domain.min_value or 0.0

        if selected is None:
            selected = domain.midpoint()

        if selected is not None and domain.min_value is not None and selected < domain.min_value:
            selected = domain.min_value
        if selected is not None and domain.max_value is not None and selected > domain.max_value:
            selected = domain.max_value

        if selected_source is None:
            selected_source = ConstraintSourceKind.RECOMMENDED_VALUE if not request_infeasible else ConstraintSourceKind.SAFETY_SUBSTITUTION

        if request_infeasible:
            plan_status = PlanStatus.READY_WITH_SAFE_SUBSTITUTION
        else:
            plan_status = PlanStatus.READY

        audit.extend(self._audit_sources(parameter, candidates, domain, selected, selected_constraint_id, selected_source))
        return ParameterResolution(
            parameter=parameter,
            domain=domain,
            candidates=candidates,
            selected_value=selected,
            selected_source_kind=selected_source,
            selected_constraint_id=selected_constraint_id,
            substituted_from=substituted_from,
            substitution_reason=substitution_reason,
            request_infeasible=request_infeasible,
            override_required=override_required,
            audit_trail=audit,
        )

    @staticmethod
    def _safe_substitute(domain: ConstraintDomain, requested: float) -> float:
        if domain.min_value is not None and requested < domain.min_value:
            return domain.min_value
        if domain.max_value is not None and requested > domain.max_value:
            return domain.max_value
        return requested

    @staticmethod
    def _prefer_within_domain(value: float, domain: ConstraintDomain) -> float:
        return domain.clamp(value)

    def _node_to_candidate(
        self,
        node: ConstraintNode,
        parameter: str,
        operator: ConstraintOperator,
        value: Optional[float],
        min_value: Optional[float],
        max_value: Optional[float],
        source_kind: Any,
    ) -> ParsedConstraint:
        kind = source_kind if isinstance(source_kind, ConstraintSourceKind) else ConstraintSourceKind[source_kind] if isinstance(source_kind, str) and source_kind in ConstraintSourceKind.__members__ else self._infer_source_kind(node)
        return ParsedConstraint(
            parameter=parameter,
            operator=operator,
            source=node.description or node.constraint_type,
            source_kind=kind,
            text_span=node.expression,
            unit="N" if parameter == "force_n" else "m/s",
            value=value,
            min_value=min_value,
            max_value=max_value,
            normalized_value=value if value is not None else (max_value if max_value is not None else min_value),
            entity_id=node.params.get("entity_id") or node.target or None,
            semantic_role=node.params.get("semantic_role") or node.applies_to_skill or None,
            confidence=1.0,
            is_hard=node.priority == ConstraintPriority.HARD,
            provenance=[node.constraint_type],
        )

    def _infer_source_kind(self, node: ConstraintNode) -> ConstraintSourceKind:
        label = str(node.params.get("_source_label", "") or node.description or node.constraint_type).lower()
        if "user_request" in label:
            return ConstraintSourceKind.USER_EXACT
        if "memory" in label:
            return ConstraintSourceKind.MEMORY_PREFERENCE
        if "safety" in label or node.constraint_type in ("max_gripper_force", "workspace_bounds", "human_proximity", "z_axis_floor", "joint_limits"):
            return ConstraintSourceKind.GLOBAL_HARD_LIMIT
        if "object" in label or node.constraint_type in ("force_limit", "velocity_limit"):
            # Constraints derived from object material/geometry are OBJECT_HARD_LIMIT
            return ConstraintSourceKind.OBJECT_HARD_LIMIT
        if "fragile" in label:
            return ConstraintSourceKind.OBJECT_HARD_LIMIT
        if "rule" in label or "modifier" in label:
            return ConstraintSourceKind.RECOMMENDED_VALUE
        return ConstraintSourceKind.DEFAULT_VALUE

    def _audit_sources(
        self,
        parameter: str,
        candidates: List[ParsedConstraint],
        domain: ConstraintDomain,
        selected: Optional[float],
        selected_constraint_id: Optional[str],
        selected_source: ConstraintSourceKind,
    ) -> List[Dict[str, Any]]:
        ledger: List[Dict[str, Any]] = []
        for candidate in candidates:
            ledger.append({
                "parameter": parameter,
                "constraint_id": candidate.constraint_id,
                "source_kind": candidate.source_kind.value,
                "operator": candidate.operator.value,
                "value": candidate.value,
                "min_value": candidate.min_value,
                "max_value": candidate.max_value,
                "unit": candidate.unit,
                "text_span": candidate.text_span,
            })
        ledger.append({
            "parameter": parameter,
            "selected_value": selected,
            "selected_constraint_id": selected_constraint_id,
            "selected_source_kind": selected_source.value,
            "feasible_domain": domain.model_dump(),
        })
        return ledger


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
