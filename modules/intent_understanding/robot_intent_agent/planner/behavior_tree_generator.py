"""
Behavior Tree Generator — 规则规划器 (Rule-based Planner)

不调用 LLM。纯规则匹配:

    中文关键词          →  技能序列
    ─────────────────────────────────────
    拿/取/递/给/抓      →  Reach → Grasp → MoveTo → Release
    推/挪               →  Reach → Push → Release
    摞/叠/堆            →  Reach → Grasp → MoveTo → Stack → Release
    不要碰/避开/别碰    →  插入 Avoid 节点
    轻一点/小心          →  Grasp → GentleGrasp (memory 注入)
    慢一点              →  velocity_ms 参数下调

遵循 TaskPlannerInterface，输出 BehaviorTree。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from robot_intent_agent.schemas.scene import SemanticSceneGraph, SceneObject
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNode,
    BTNodeType,
    SkillAction,
    ConditionCheck,
    BTStatus,
)
from robot_intent_agent.task_semantics import (
    ParsedTask,
    PlanStatus,
    TaskActionKind,
    build_grounded_task,
    parse_task_semantics,
)
from robot_intent_agent.domain.action_schemas import get_action_schema

from .base import TaskPlannerInterface
from .skill_catalog import SkillCatalog, SkillDefinition


# ============================================================
# 指令关键词 → 技能管道映射
# ============================================================

ACTION_PIPELINE: Dict[str, List[str]] = {
    # 拿/取/递/给/抓 → 标准 pick-and-place
    "pick_and_place": ["Reach", "Grasp", "MoveTo", "Release"],
    # 推/挪
    "push": ["Reach", "Push", "Release"],
    # 摞/叠/堆
    "stack": ["Reach", "Grasp", "MoveTo", "Stack", "Release"],
    # 倒/灌
    "pour": ["Reach", "Grasp", "MoveTo", "Pour", "Release"],
}

SEMANTIC_PIPELINES: Dict[TaskActionKind, List[str]] = {
    TaskActionKind.GRASP: ["Reach", "Grasp"],
    TaskActionKind.FETCH: ["Reach", "Grasp", "Fetch"],
    TaskActionKind.PLACE: ["Reach", "Grasp", "Transport", "Place"],
    TaskActionKind.HANDOVER: ["Reach", "Grasp", "MoveToHandoverZone", "Handover"],
    TaskActionKind.TRANSFER: ["Reach", "Grasp", "Transport", "Place"],
    TaskActionKind.DYNAMIC_GRASP: ["WaitUntilStable", "Reach", "DynamicGrasp"],
    TaskActionKind.PUSH: ["Reach", "Push", "Release"],
    TaskActionKind.POUR: ["Reach", "Grasp", "MoveTo", "Pour", "Release"],
    TaskActionKind.STACK: ["Reach", "Grasp", "MoveTo", "Stack", "Release"],
    TaskActionKind.WAIT: ["WaitUntil"],
    TaskActionKind.CUSTOM: ["Reach", "Grasp", "MoveTo", "Release"],
}

# ── @deprecated: Legacy keyword tables — replaced by task_semantics.py modules ──
# ACTION_KEYWORDS → task_semantics._ACTION_PATTERNS + SEMANTIC_PIPELINES
# AVOID_KEYWORDS → task_semantics._extract_obstacles() + constraint/rule_engine.AVOID_KEYWORDS
# CONSTRAINT_PARAMS → constraint/rule_engine.MODIFIER_TO_CONSTRAINT
# ─────────────────────────────────────────────────────────────────────────────

# 动作关键词
ACTION_KEYWORDS: Dict[str, str] = {
    "push": "推|挪",
    "stack": "摞|叠|堆|放在.*上面|放到.*上",
    "pour": "倒|灌|倾",
    "pick_and_place": "拿|取|抓|递|给|放[进到入在]|搬|端|移[动开]",
}

# 规避关键词
AVOID_KEYWORDS = re.compile(r"别碰|不要碰|千万别碰|避开|绕过|躲开")

# 约束短语 → 参数调整
CONSTRAINT_PARAMS: Dict[str, Dict[str, Any]] = {
    "轻一点": {"force_n": 3.0, "grip_style": "gentle"},
    "轻": {"force_n": 3.0, "grip_style": "gentle"},
    "小心": {"force_n": 3.0, "velocity_ms": 0.10},
    "慢一点": {"velocity_ms": 0.10},
    "慢": {"velocity_ms": 0.10},
    "用力": {"force_n": 7.0},
}


# ============================================================
# 指令解析器 (纯规则)
# ============================================================

class RuleInstructionParser:
    """@deprecated: Legacy regex-based instruction parser.

    Replaced by parse_task_semantics() in task_semantics.py.
    Only parse_structured_task() (which delegates to parse_task_semantics)
    is used in production. The static methods below are fallbacks kept
    for backward compatibility in BehaviorTreeGenerator.plan().
    """

    @staticmethod
    def classify_action(text: str) -> str:
        """@deprecated: Use parse_task_semantics() → parsed_task.action instead."""
        """保留旧接口：返回 legacy 分类"""
        if re.search(r"推|挪", text):
            return "push"
        if re.search(r"摞|叠|堆|放在.*上面|放到.*上", text):
            return "stack"
        if re.search(r"倒|灌|倾", text):
            return "pour"
        return "pick_and_place"

    @staticmethod
    def extract_target(text: str) -> str:
        """@deprecated: Use parse_task_semantics() → parsed_task.theme.mention instead."""
        patterns = [
            r"把(\S{2,8}?)(?:拿|取|抓|递|推|放|给|搬|端)",
            r"(\S{2,8})递给我",
            r"(\S{2,8})给我",
            r"把(\S{2,8})",
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                target = m.group(1).strip()
                target = re.sub(r"[的了呢吗啊]$", "", target)
                if target:
                    return target
        # Fallback: 提取描述性名词
        color_mat = r"(?:红|蓝|绿|黄|白|黑|透明|玻璃|塑料|木|铁)"
        obj_name = r"(?:药瓶|水杯|方块|圆柱|杯子|瓶子|盒子|积木|块)"
        match = re.search(rf"({color_mat}?{obj_name})", text)
        if match:
            return match.group(1)
        return "unknown"

    @staticmethod
    def extract_destination(text: str) -> Optional[str]:
        """提取目标位置"""
        pats = [
            r"放到?(\S{2,6})(?:旁边|上面|下面|左边|右边|中间)?",
            r"递给(\S{2,4})",
            r"放到?(\S{2,6})",
        ]
        for pat in pats:
            m = re.search(pat, text)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def extract_avoid_objects(text: str) -> List[str]:
        """提取需要规避的物体"""
        objects = []
        for match in AVOID_KEYWORDS.finditer(text):
            # 提取关键词后面的物体名 (最多 6 个中文字符)
            start = match.end()
            obj_match = re.match(r"([一-鿿\w]{1,4})", text[start:])
            if obj_match:
                obj = obj_match.group(1).strip()
                obj = re.sub(r"[的了呢吗啊]$", "", obj)
                if obj and len(obj) >= 1:
                    objects.append(obj)
        return objects

    @staticmethod
    def extract_modifiers(text: str) -> Dict[str, Any]:
        """提取约束/修饰语 → 参数字典"""
        params: Dict[str, Any] = {}
        for keyword, param_map in CONSTRAINT_PARAMS.items():
            if keyword in text:
                params.update(param_map)
        return params

    @staticmethod
    def parse_structured_task(text: str, scene: Optional[SemanticSceneGraph] = None) -> ParsedTask:
        return parse_task_semantics(text, scene=scene)


# ============================================================
# Behavior Tree Generator (核心)
# ============================================================

class BehaviorTreeGenerator(TaskPlannerInterface):
    """
    规则行为树生成器。

    实现 TaskPlannerInterface:
        plan(instruction, scene, memory_context) → BehaviorTree

    规则:
        1. 解析中文指令 → 动作类型 + 目标 + 修饰语 + 规避对象
        2. 从 SkillCatalog 选择技能管道
        3. 插入 Avoid 节点 (如果有规避对象)
        4. 根据修饰语调整技能参数
        5. 添加场景相关前置条件 (blocking objects)
        6. 注入 Memory 上下文参数
    """

    def __init__(self):
        self.catalog = SkillCatalog()
        self.parser = RuleInstructionParser()

    @property
    def name(self) -> str:
        return "RuleBasedPlanner"

    def generate_from_graph(self, graph, scene: Optional[SemanticSceneGraph] = None,
                            instruction: str = "") -> BehaviorTree:
        """Compile a SemanticTaskGraph without consulting natural language.

        ``plan`` remains the legacy adapter.  New callers can use this method
        as the canonical deterministic compiler and receive the exact domain
        skill templates (including Transport, WaitUntil and handover-zone
        actions).
        """
        from robot_intent_agent.schemas.semantic_task_graph import SemanticTaskGraph
        from robot_intent_agent.domain.action_schemas import get_action_schema
        if not isinstance(graph, SemanticTaskGraph):
            graph = SemanticTaskGraph.model_validate(graph)

        def entity_target(local_ref: Optional[str]) -> str:
            entity = graph.entity(local_ref) if local_ref else None
            return (entity.entity_id or entity.mention) if entity else ""

        def entity_display(local_ref: Optional[str]) -> str:
            """Return a human-readable scene name for path annotations."""
            entity = graph.entity(local_ref) if local_ref else None
            if entity is None:
                return ""
            if scene and entity.entity_id:
                obj = scene.find_object(entity.entity_id)
                if obj:
                    return obj.name
            return entity.mention

        def action_node(skill_name: str, target: str, params: Optional[Dict[str, Any]] = None,
                        role: Optional[str] = None) -> BTNode:
            definition = self.catalog.get(skill_name)
            action_params = dict(params or {})
            scene_entity_ids = {entity.entity_id for entity in graph.entities if entity.entity_id}
            if target and target in scene_entity_ids:
                action_params.setdefault("target_entity_id", target)
            if action_params.get("destination") in scene_entity_ids:
                action_params.setdefault("destination_entity_id", action_params["destination"])
            return BTNode(type=BTNodeType.ACTION, name=f"{skill_name}({target})",
                          skill=SkillAction(skill_name=skill_name, target=target,
                                            params=action_params,
                                            preconditions=list(definition.preconditions),
                                            success_conditions=list(definition.success_conditions),
                                            failure_conditions=list(definition.failure_conditions),
                                            timeout_s=definition.timeout_s,
                                            retry_policy=dict(definition.retry_policy),
                                            fallback=definition.fallback,
                                            runtime_safety_guards=list(definition.runtime_safety_guards),
                                            semantic_role=role))

        def event_sequence(event) -> BTNode:
            schema = get_action_schema(event.action)
            target = entity_target(event.theme_ref)
            destination = entity_target(event.destination_ref)
            recipient = entity_target(event.recipient_ref)
            params = {"destination": destination} if destination else {}
            if recipient:
                params["recipient"] = recipient
            graph_manner = graph.metadata.get("manner") if isinstance(graph.metadata, dict) else None
            if graph_manner:
                params["manner"] = graph_manner
                if graph_manner == "gentle":
                    params.setdefault("grip_style", "gentle")
                    params.setdefault("force_n", 3.0)
            for constraint in graph.constraints:
                if constraint.parameter in {"force_n", "velocity_ms"}:
                    value = constraint.value
                    if value is None:
                        value = constraint.max_value if constraint.max_value is not None else constraint.min_value
                    if value is not None:
                        params.setdefault(constraint.parameter, value)
            # Keep compatibility-visible parameter keys on the canonical
            # graph path; all values still originate from graph evidence or
            # deterministic memory/scene resolution.
            if graph_manner == "gentle":
                params.setdefault("force_n", 3.0)
            skills = []
            for skill in schema.skill_template:
                if skill == "WaitUntil":
                    skills.append(action_node("WaitUntil", "condition",
                                              {"condition": event.parameters.get("condition", "")}, "condition"))
                elif skill == "Transport":
                    skills.append(action_node("Transport", target, params, "transport"))
                elif skill == "Place":
                    skills.append(action_node("Place", target, params, "destination"))
                elif skill == "MoveToHandoverZone":
                    skills.append(action_node(skill, target, {"recipient": recipient}, "recipient"))
                elif skill in self.catalog.list_all():
                    skill_params = dict(params)
                    if skill not in {"Grasp", "GentleGrasp", "DynamicGrasp"}:
                        skill_params.pop("force_n", None)
                    if skill not in {"Reach", "MoveTo", "Transport", "Push"}:
                        skill_params.pop("velocity_ms", None)
                    if event.parameters:
                        skill_params.update(event.parameters)
                    skills.append(action_node(skill, target, skill_params,
                                              "theme" if skill in {"Grasp", "Reach"} else None))
            prohibition_refs = [prohibition.target_ref for prohibition in graph.prohibitions
                                if prohibition.target_ref and
                                (not prohibition.scope_event_ids or event.event_id in prohibition.scope_event_ids)]
            for obstacle_ref in list(dict.fromkeys([*event.obstacle_refs, *prohibition_refs])):
                obstacle = entity_target(obstacle_ref)
                obstacle_display = entity_display(obstacle_ref)
                obstacle_params = {
                    "avoid_obstacles": [obstacle_display or obstacle],
                    "avoid_obstacle_entity_ids": [obstacle] if obstacle else [],
                    "collision_check": True,
                }
                skills.insert(0, action_node("PlanPath", target,
                                             obstacle_params, "obstacle"))
            if not skills:
                skills.append(action_node("Reach", target))
            if scene and target:
                target_obj = scene.find_object(target)
                if target_obj:
                    blocker_objects = [scene.find_object(item) for item in scene.blocking_objects(target_obj.id)
                                       if scene.find_object(item)]
                    if blocker_objects:
                        blockers = [item.name for item in blocker_objects]
                        blocker_ids = [item.id for item in blocker_objects]
                        skills.insert(0, action_node("PlanPath", target,
                                                      {"avoid_obstacles": blockers,
                                                       "avoid_obstacle_entity_ids": blocker_ids,
                                                       "collision_check": True}, "obstacle"))
            return BTNode(type=BTNodeType.SEQUENCE, name=f"Event:{event.event_id}", children=skills)

        event_nodes = [event_sequence(event) for event in sorted(graph.events, key=lambda item: item.sequence_index)]
        if not event_nodes:
            event_nodes = [action_node("Reach", "")]
        # Keep deterministic runtime guards required by the existing robot
        # contract.  They are compiler-generated nodes, not language-derived
        # plan content.
        guard_nodes = [BTNode(type=BTNodeType.CONDITION, name="CheckGripperEmpty",
                              condition=ConditionCheck(condition="is_gripper_empty", expected=True))]
        # Preserve a visible obstacle precondition for downstream monitors.
        obstacle_entities = [graph.entity(ref) for event in graph.events
                              for ref in event.obstacle_refs if graph.entity(ref)]
        guard_nodes.extend(
            BTNode(type=BTNodeType.CONDITION,
                   name=f"ClearPath({entity.mention})",
                   condition=ConditionCheck(condition="path_clear", target=entity.entity_id or entity.mention,
                                            expected=True))
            for entity in obstacle_entities
        )
        root_children = [*guard_nodes, *event_nodes]
        # A single-event graph can be flattened without losing semantics. It
        # keeps each deterministic skill visible to legacy monitors while
        # multi-event/conditional graphs retain explicit event subtrees.
        if not graph.conditions and len(event_nodes) == 1:
            root_children = [*guard_nodes, *event_nodes[0].children]
        if graph.conditions:
            condition = graph.conditions[0]
            check = BTNode(type=BTNodeType.CONDITION, name=f"Condition:{condition.condition_id}",
                           condition=ConditionCheck(condition=condition.predicate,
                                                    target=condition.value if isinstance(condition.value, str) else "",
                                                    expected=True))
            true_ids = {relation.target_event for relation in graph.relations
                        if relation.type == "IF_TRUE" and relation.target_event}
            false_ids = {relation.target_event for relation in graph.relations
                         if relation.type == "IF_FALSE" and relation.target_event}
            event_by_id = {event.event_id: node for event, node in
                           zip(sorted(graph.events, key=lambda item: item.sequence_index), event_nodes)}
            true_nodes = [event_by_id[event_id] for event_id in true_ids if event_id in event_by_id]
            false_nodes = [event_by_id[event_id] for event_id in false_ids if event_id in event_by_id]
            if not true_nodes:
                true_nodes = event_nodes
            if not false_nodes:
                false_nodes = [action_node("WaitUntil", "condition", {"condition": "false_branch"})]
            true_branch = BTNode(type=BTNodeType.SEQUENCE, name="condition_true",
                                 children=[check, *true_nodes])
            false_branch = BTNode(type=BTNodeType.SEQUENCE, name="condition_false",
                                  children=false_nodes)
            root_children = [*guard_nodes, BTNode(type=BTNodeType.FALLBACK, name="ConditionalTask",
                                                  children=[true_branch, false_branch])]
        root = BTNode(type=BTNodeType.SEQUENCE, name="SemanticTaskGraph", children=root_children)
        primary_event = sorted(graph.events, key=lambda item: item.sequence_index)[0] if graph.events else None
        avoid_objects = []
        if primary_event:
            avoid_objects.extend(entity_display(ref) for ref in primary_event.obstacle_refs)
        avoid_objects.extend(
            entity_display(item.target_ref) for item in graph.prohibitions if item.target_ref
        )
        avoid_objects = list(dict.fromkeys(item for item in avoid_objects if item))
        return BehaviorTree(task_id="task-semantic-graph", description=instruction or graph.instruction,
                            root=root,
                            metadata={"planner": "RuleBasedPlanner",
                                      "semantic_planner": "SemanticGraphCompiler",
                                      "semantic_authority": "SemanticCompiler",
                                      "compiler": "SemanticCompiler",
                                      "semantic_task_graph": graph.model_dump(mode="json"),
                                      "action": primary_event.action if primary_event else "CUSTOM",
                                      "target": entity_display(primary_event.theme_ref) if primary_event else "",
                                      "avoid_objects": avoid_objects,
                                      "standard_skill_templates": [list(get_action_schema(event.action).skill_template)
                                                                    for event in graph.events]})

    # ============================================================
    # 主接口
    # ============================================================

    def plan(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> BehaviorTree:
        """
        完整规划流程。

        Args:
            instruction:    用户自然语言指令
            scene:          语义场景图
            memory_context: MemoryRetriever.search() 输出的记忆列表

        Returns:
            BehaviorTree
        """
        # 1. 解析结构化任务语义
        from robot_intent_agent.semantic_compiler import SemanticCompiler
        return SemanticCompiler().compile(
            instruction, scene=scene, memory_context=memory_context, mode="rule"
        ).behavior_tree

        parsed_task = self.parser.parse_structured_task(instruction, scene=scene)
        grounded_task = build_grounded_task(parsed_task, scene=scene)
        semantic_graph = parsed_task.semantic_task_graph or {}
        graph_events = semantic_graph.get("events", []) if isinstance(semantic_graph, dict) else []

        action_kind = parsed_task.action
        target = parsed_task.theme.mention if parsed_task.theme else self.parser.extract_target(instruction)
        destination = parsed_task.destination.mention if parsed_task.destination else self.parser.extract_destination(instruction)
        avoid_objects = [obj.mention for obj in parsed_task.obstacle]

        # 2. 选择技能管道 from the semantic graph.  The names below are the
        # existing SkillCatalog compatibility vocabulary; the canonical
        # domain template is retained in metadata and is consumed by the
        # downstream compiler/validator.
        legacy_action = self.parser.classify_action(instruction)
        if action_kind == TaskActionKind.CUSTOM and legacy_action in ACTION_PIPELINE:
            pipeline = ACTION_PIPELINE[legacy_action]
        else:
            pipeline = SEMANTIC_PIPELINES.get(action_kind, SEMANTIC_PIPELINES[TaskActionKind.CUSTOM])
        # Composite AST steps are authoritative for ordering.  Expand each
        # semantic step through the deterministic skill catalog so an LLM or
        # summary action cannot collapse "grasp then place" into one action.
        if len(graph_events) > 1:
            expanded = []
            graph_map = {
                "GRASP": ["Reach", "Grasp"],
                "PLACE": ["Reach", "Grasp", "Transport", "Place"],
                "FETCH": ["Fetch"],
                "HANDOVER": ["Handover"],
                "TRANSFER": ["Transport", "Place"],
                "DYNAMIC_GRASP": ["WaitUntilStable", "Reach", "DynamicGrasp"],
                "PUSH": ["Push", "Release"],
                "POUR": ["MoveTo", "Pour", "Release"],
                "STACK": ["MoveTo", "Stack", "Release"],
                "WAIT": ["WaitUntil"],
            }
            for event in sorted(graph_events, key=lambda item: item.get("sequence_index", 0)):
                expanded.extend(graph_map.get(str(event.get("action", "CUSTOM")).upper(), []))
            if expanded:
                pipeline = expanded
        elif parsed_task.steps:
            expanded: List[str] = []
            step_map = {
                "GRASP": ["Reach", "Grasp"],
                "PLACE": ["Reach", "Grasp", "Transport", "Place"],
                "FETCH": ["Fetch"],
                "HANDOVER": ["Handover"],
                "TRANSFER": ["Transport", "Place"],
                "DYNAMIC_GRASP": ["WaitUntilStable", "Reach", "DynamicGrasp"],
                "PUSH": ["Push", "Release"],
                "POUR": ["MoveTo", "Pour", "Release"],
                "STACK": ["MoveTo", "Stack", "Release"],
                "WAIT": ["WaitUntil"],
            }
            for step in sorted(parsed_task.steps, key=lambda item: item.get("step_index", 0)):
                step_action = str(step.get("action", "CUSTOM"))
                # In Chinese, “选择…并拿起来” may be represented by the
                # AST as FETCH because of the conjunction, but the task
                # summary is GRASP and has no delivery intent. Preserve the
                # physical action implied by the authoritative summary.
                if step_action == "FETCH" and action_kind == TaskActionKind.GRASP:
                    step_action = "GRASP"
                expanded.extend(step_map.get(step_action, []))
            if expanded:
                pipeline = list(dict.fromkeys(expanded))
        # PLACE normally assumes an object is already held.  Composite natural
        # language instructions explicitly asking to pick and then place need
        # both manipulation stages in the executable tree.
        if action_kind == TaskActionKind.PLACE and re.search(
            r"(?:拿起|抓取|抓|拿到|取起|取).*(?:放到|放在|放入|放进|放回|摆放|置于)",
            instruction,
        ):
            pipeline = ["Reach", "Grasp", "Place"]

        # 3. 注入 Memory 上下文参数
        memory_params = self._merge_memory(memory_context or [])
        modifiers = {**memory_params}
        if parsed_task.manner == "gentle":
            modifiers.setdefault("grip_style", "gentle")
            modifiers.setdefault("force_n", 3.0)
        if parsed_task.manner == "fast":
            modifiers.setdefault("velocity_ms", 0.20)
        for constraint in parsed_task.user_constraints:
            if constraint.parameter == "force_n" and constraint.value is not None:
                modifiers["force_n"] = constraint.value
            if constraint.parameter == "velocity_ms" and constraint.value is not None:
                modifiers["velocity_ms"] = constraint.value
        if parsed_task.motion_state.state == "moving" and parsed_task.motion_state.speed_mps is not None:
            modifiers["target_speed_mps"] = parsed_task.motion_state.speed_mps

        # 4. 在场景中查找目标 — 实体接地 (双向匹配)
        target_obj = scene.find_object(target) if scene else None
        if scene and not target_obj:
            for obj in scene.objects:
                # 双向匹配: scene_obj.name 包含 target, 或 target 包含在 scene_obj.name 中
                if target and (target in obj.name or obj.name in target or obj.name in instruction):
                    target = obj.name
                    target_obj = obj
                    break

        # Canonical compiler path.  The compatibility path below remains as
        # a fallback for legacy fixtures whose graph cannot be validated; all
        # supported domain instructions are compiled from the graph here.
        if graph_events and semantic_graph.get("entities"):
            try:
                from robot_intent_agent.schemas.semantic_task_graph import SemanticTaskGraph
                graph = SemanticTaskGraph.model_validate(semantic_graph)
                canonical_bt = self.generate_from_graph(graph, scene=scene, instruction=instruction)
                canonical_bt.task_id = f"task-{action_kind.value.lower()}"
                canonical_bt.metadata.update({
                    "action": action_kind.value,
                    "legacy_action": self.parser.classify_action(instruction),
                    "task_action_kind": action_kind.value,
                    "target": target,
                    "destination": destination,
                    "modifiers": {},
                    "avoid_objects": avoid_objects,
                    "planner": self.name,
                    "parsed_task": parsed_task.model_dump(),
                    "grounded_task": grounded_task.model_dump(),
                    "standard_action_schema": get_action_schema(action_kind.value).model_dump(),
                    "plan_status": PlanStatus.NEEDS_CLARIFICATION.value if grounded_task.required_clarifications else PlanStatus.READY.value,
                })
                return canonical_bt
            except Exception as exc:
                logger.warning("Semantic graph compiler fallback to compatibility planner: %s", exc)

        # 5. 构建行为树子节点
        children: List[BTNode] = []

        # ── 前置条件 ──
        children.extend(self._build_preconditions(scene, target, avoid_objects, parsed_task))

        # ── Avoid / PlanCollisionFreePath (规避障碍物) ──
        # 收集所有需要规避的物体: NL 显式提及 + 场景几何阻挡 (不依赖 NL 关键词!)
        all_avoid = list(avoid_objects)  # from NL parsing (may be empty)
        scene_blockers = []
        if scene and target_obj:
            scene_blockers = [
                scene.find_object(bid).name
                for bid in scene.blocking_objects(target_obj.id)
                if scene.find_object(bid)
            ]
            all_avoid = list(set(all_avoid + scene_blockers))

        # 调试日志: BT 生成详情
        logger.info(f"[BT_GENERATOR] Target: {target}")
        logger.info(f"[BT_GENERATOR] NL avoid_objects: {avoid_objects}")
        logger.info(f"[BT_GENERATOR] Scene blockers: {scene_blockers}")
        logger.info(f"[BT_GENERATOR] ALL avoid: {all_avoid}")

        if all_avoid:
            # PlanPath: 全局无碰撞路径规划 (包含所有避障约束)
            children.insert(0, BTNode(
                type=BTNodeType.ACTION,
                name=f"PlanCollisionFreePath(avoid={','.join(all_avoid)})",
                skill=SkillAction(
                    skill_name="PlanPath",
                    target=target,
                    params={
                        "avoid_obstacles": all_avoid,
                        "collision_check": True,
                        "min_clearance_m": 0.05,
                    },
                    preconditions=["obstacle_positions_known"],
                    success_conditions=["collision_free_path_planned"],
                    failure_conditions=["path_blocked", "timeout_exceeded"],
                    timeout_s=self.catalog.get("PlanPath").timeout_s,
                    retry_policy=self.catalog.get("PlanPath").retry_policy,
                    fallback=self.catalog.get("PlanPath").fallback,
                    runtime_safety_guards=self.catalog.get("PlanPath").runtime_safety_guards,
                ),
                annotation=f"Plan collision-free path avoiding: {', '.join(all_avoid)}",
            ))
            logger.info(f"[BT_GENERATOR] Injected PlanPath with avoid={all_avoid}")

        logger.info(f"[BT_GENERATOR] Final action order: "
                     f"{[c.name for c in children if c.type == BTNodeType.ACTION]}")

        # ── 主技能序列 ──
        for skill_name in pipeline:
            skill_def = self.catalog.get(skill_name)
            params = self._build_params(
                skill_def,
                target=target,
                destination=destination,
                modifiers=modifiers,
                parsed_task=parsed_task,
            )
            children.append(
                BTNode(
                    type=BTNodeType.ACTION,
                    name=f"{skill_name}({target})",
                    skill=SkillAction(
                        skill_name=skill_name,
                        target=target,
                        params=params,
                        preconditions=list(skill_def.preconditions),
                        success_conditions=list(skill_def.success_conditions),
                        failure_conditions=list(skill_def.failure_conditions),
                        timeout_s=skill_def.timeout_s,
                        retry_policy=dict(skill_def.retry_policy),
                        fallback=skill_def.fallback,
                        runtime_safety_guards=list(skill_def.runtime_safety_guards),
                        semantic_role=self._infer_semantic_role(skill_name),
                    ),
                    annotation=skill_def.description.format(
                    target=target, destination=destination or "user"
                ),
                )
            )

        # 6. 组装根节点
        root = BTNode(
            type=BTNodeType.SEQUENCE,
            name=f"Task: {instruction[:50]}",
            children=children,
            annotation=f"Action={action_kind.value} | Target={target}",
        )

        return BehaviorTree(
            task_id=f"task-{action_kind.value.lower()}",
            description=instruction,
            root=root,
            metadata={
                "action": action_kind.value,
                "legacy_action": self.parser.classify_action(instruction),
                "task_action_kind": action_kind.value,
                "target": target,
                "destination": destination,
                "modifiers": modifiers,
                "avoid_objects": avoid_objects,
                "planner": self.name,
                "parsed_task": parsed_task.model_dump(),
                "grounded_task": grounded_task.model_dump(),
                "semantic_task_graph": semantic_graph,
                "standard_action_schema": get_action_schema(action_kind.value).model_dump(),
                "standard_skill_template": list(get_action_schema(action_kind.value).skill_template),
                "semantic_events": graph_events,
                "plan_status": PlanStatus.NEEDS_CLARIFICATION.value if grounded_task.required_clarifications else PlanStatus.READY.value,
            },
        )

    # ============================================================
    # 内部辅助方法
    # ============================================================

    def _build_preconditions(
        self,
        scene: Optional[SemanticSceneGraph],
        target: str,
        avoid_objects: List[str],
        parsed_task: Optional[ParsedTask] = None,
    ) -> List[BTNode]:
        """构建前置条件检查节点"""
        conditions: List[BTNode] = []

        # 夹爪为空
        conditions.append(
            BTNode(
                type=BTNodeType.CONDITION,
                name="CheckGripperEmpty",
                condition=ConditionCheck(
                    condition="is_gripper_empty",
                    expected=True,
                ),
            )
        )

        # 目标在视野中
        if scene and target:
            target_obj = scene.find_object(target)
            if target_obj:
                conditions.append(
                    BTNode(
                        type=BTNodeType.CONDITION,
                        name=f"CheckVisible({target})",
                        condition=ConditionCheck(
                            condition="target_in_view",
                            target=target,
                            expected=True,
                        ),
                    )
                )

            if parsed_task and parsed_task.action in (TaskActionKind.FETCH, TaskActionKind.HANDOVER, TaskActionKind.TRANSFER):
                recipient = parsed_task.recipient
                if recipient is None or recipient.entity_id is None:
                    conditions.append(
                        BTNode(
                            type=BTNodeType.CONDITION,
                            name="CheckRecipientAvailable",
                            condition=ConditionCheck(
                                condition="recipient_known",
                                target=recipient.mention if recipient else "recipient",
                                expected=True,
                            ),
                        )
                    )
            if parsed_task and parsed_task.action == TaskActionKind.PLACE:
                support_surface = parsed_task.support_surface
                if support_surface is None or support_surface.entity_id is None:
                    conditions.append(
                        BTNode(
                            type=BTNodeType.CONDITION,
                            name="CheckSupportSurfaceAvailable",
                            condition=ConditionCheck(
                                condition="support_surface_known",
                                target="support_surface",
                                expected=True,
                            ),
                        )
                    )

        # 规避对象路径检查
        for obj in avoid_objects:
            conditions.append(
                BTNode(
                    type=BTNodeType.CONDITION,
                    name=f"CheckClear({obj})",
                    condition=ConditionCheck(
                        condition="path_clear",
                        target=obj,
                        expected=True,
                    ),
                )
            )

        return conditions

    @staticmethod
    def _infer_semantic_role(skill_name: str) -> Optional[str]:
        mapping = {
            "Fetch": "task",
            "Place": "task",
            "Handover": "task",
            "Transfer": "task",
            "DynamicGrasp": "task",
            "Grasp": "theme",
            "MoveTo": "transport",
            "Reach": "approach",
            "Release": "release",
        }
        return mapping.get(skill_name)

    def _make_avoid_node(self, obstacle: str) -> BTNode:
        """创建 Avoid 动作节点"""
        skill_def = self.catalog.get("Avoid")
        return BTNode(
            type=BTNodeType.ACTION,
            name=f"Avoid({obstacle})",
            skill=SkillAction(
                skill_name="Avoid",
                target=obstacle,
                params={
                    "min_distance_m": 0.05,
                    "avoid_strategy": "go_around",
                },
            ),
            annotation=f"Avoid collision with {obstacle}",
        )

    def _build_params(
        self,
        skill_def: SkillDefinition,
        target: str,
        destination: Optional[str],
        modifiers: Dict[str, Any],
        parsed_task: Optional[ParsedTask] = None,
    ) -> Dict[str, Any]:
        """为技能构建参数字典"""
        params: Dict[str, Any] = {}
        if target:
            params["target"] = target
        # v3.0: always include grounded entity_id alongside display name
        if parsed_task and parsed_task.theme and parsed_task.theme.entity_id:
            params["target_entity_id"] = parsed_task.theme.entity_id
        if destination:
            params["destination"] = destination

        # 根据技能类型注入修饰参数
        if skill_def.name in ("Grasp", "GentleGrasp"):
            if "force_n" in modifiers:
                params["force_n"] = modifiers["force_n"]
            if "grip_style" in modifiers:
                params["grip_style"] = modifiers["grip_style"]
        if skill_def.name == "Place":
            if destination:
                params["support_surface"] = destination
            if parsed_task and parsed_task.support_surface:
                params["support_surface"] = parsed_task.support_surface.mention
        if skill_def.name in ("Fetch", "Handover", "Transfer"):
            if parsed_task and parsed_task.recipient:
                params["recipient"] = parsed_task.recipient.mention
        if skill_def.name in ("Handover", "MoveToHandoverZone") and parsed_task and parsed_task.recipient:
            params["recipient"] = parsed_task.recipient.mention
        if skill_def.name in ("MoveTo", "Transport", "Reach"):
            if "velocity_ms" in modifiers:
                params["velocity_ms"] = modifiers["velocity_ms"]
            if "target_speed_mps" in modifiers:
                params["target_speed_mps"] = modifiers["target_speed_mps"]
        if skill_def.name == "DynamicGrasp":
            if "target_speed_mps" in modifiers:
                params["target_speed_mps"] = modifiers["target_speed_mps"]
            if parsed_task and parsed_task.motion_state.speed_mps is not None:
                params["target_speed_mps"] = parsed_task.motion_state.speed_mps
            params.setdefault("max_wait_s", 6.0)

        return params

    @staticmethod
    def _merge_memory(memory_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """将 MemoryRetriever 搜索结果合并为参数字典"""
        merged: Dict[str, Any] = {}
        for item in memory_items:
            key = item.get("key", "")
            value = item.get("value", "")
            mtype = item.get("memory_type", "")

            if mtype == "user_preference":
                key_map = {
                    "hand_preference": "hand",
                    "grip_style": "grip_style",
                    "speed_preference": "speed_label",
                }
                mapped_key = key_map.get(key, key)
                merged[mapped_key] = value

            elif mtype == "skill_experience":
                if isinstance(value, dict):
                    merged.update(value)

        return merged
