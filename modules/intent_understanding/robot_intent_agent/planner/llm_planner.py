"""
LLM Planner — DeepSeek API 驱动的任务规划器

架构:
    用户指令 + 场景图 + 记忆上下文
        → DeepSeek API (JSON Mode)
        → 结构化 BehaviorTree JSON
        → Pydantic 校验
        → 合法 BehaviourTree 或 降级回规则引擎

依赖:
    pip install openai
"""

from __future__ import annotations

import json
import hashlib
import logging
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional

from robot_intent_agent.config.settings import get_settings
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNode,
    BTNodeType,
    SkillAction,
    ConditionCheck,
)
from robot_intent_agent.planner.base import TaskPlannerInterface
from robot_intent_agent.planner.skill_catalog import SkillCatalog
from robot_intent_agent.domain.action_schemas import ACTION_SCHEMAS, normalize_action
from robot_intent_agent.domain.relation_ontology import SUPPORTED_RELATIONS
from robot_intent_agent.schemas.semantic_task_graph import EvidenceSpan, SemanticCandidate, SemanticTaskGraph
from robot_intent_agent.semantic_parser.action_parser import parse_action_candidates

logger = logging.getLogger(__name__)


# ============================================================
# IntentFrame v1 标准化
# ============================================================

def normalize_intent_frame(frame: "IntentFrame") -> Dict[str, Any]:
    """将 IntentFrame v1 标准化为下游兼容的 parsed_task 格式。

    职责:
        1. 统一同义枚举
        2. 统一单位
        3. 统一 null/[]
        4. 禁止自动猜 object_id
        5. 禁止静默删除字段

    返回标准化的 parsed_task dict 供 load_parsed_task_from_bt() 使用。
    """
    from robot_intent_agent.schemas.intent_frame import (
        ActionKind, ProhibitionType, ConditionPredicate,
    )

    action_map = {
        ActionKind.GRASP: "GRASP",
        ActionKind.FETCH: "FETCH",
        ActionKind.PLACE: "PLACE",
        ActionKind.HANDOVER: "HANDOVER",
        ActionKind.TRANSFER: "TRANSFER",
        ActionKind.DYNAMIC_GRASP: "DYNAMIC_GRASP",
        ActionKind.CUSTOM: "CUSTOM",
    }

    def _normalize_entity(entity) -> Optional[Dict[str, Any]]:
        if entity is None:
            return None
        return {
            "mention": entity.mention,
            "specific_class": entity.category,
            "parent_class": None,
            "entity_id": None,  # NEVER set by LLM
            "role": None,
            "text_span": entity.source_text_span or entity.mention,
            "grounding_confidence": entity.confidence,
            "source": "nl",
            "ontology_path": [entity.category] if entity.category else [],
            "match_evidence": [],
            # Preserve descriptors for GroundingEngine
            "attributes": {
                "color": entity.descriptors.color,
                "material": entity.descriptors.material,
                "size": entity.descriptors.size,
                "shape": entity.descriptors.shape,
                "side": entity.descriptors.side,
                "height_relation": entity.descriptors.height_relation,
                "distance_relation": entity.descriptors.distance_relation,
                "motion_state": entity.descriptors.motion_state,
            },
        }

    def _normalize_prohibition(p) -> Dict[str, Any]:
        return {
            "prohibition_id": p.prohibition_id,
            "type": p.type.value,
            "target": _normalize_entity(p.target),
            "action": p.action.value if p.action else None,
            "parameter": p.parameter,
            "operator": p.operator.value if p.operator else None,
            "value": p.value,
            "unit": p.unit.value if p.unit else None,
            "condition": p.condition,
            "source_text_span": p.source_text_span,
            "confidence": p.confidence,
        }

    def _normalize_condition(c) -> Dict[str, Any]:
        return {
            "condition_id": c.condition_id,
            "predicate": c.predicate.value,
            "subject": _normalize_entity(c.subject),
            "operator": c.operator,
            "value": c.value,
            "unit": c.unit.value if c.unit else None,
            "required_before": [a.value for a in c.required_before],
            "on_true": c.on_true.value if c.on_true else None,
            "on_false": c.on_false.value if c.on_false else None,
            "on_true_subject": _normalize_entity(c.on_true_subject),
            "on_false_subject": _normalize_entity(c.on_false_subject),
            "hard": c.hard,
            "source_text_span": c.source_text_span,
        }

    def _normalize_constraint(c) -> Dict[str, Any]:
        return {
            "constraint_id": c.constraint_id,
            "parameter": c.parameter,
            # IntentFrame uses upper-case wire enums (MAX/MIN/...), while the
            # authoritative ParsedTask contract uses lower-case values.
            "operator": c.operator.value.lower(),
            "source": "user",
            "source_kind": f"USER_{c.operator.value}",
            "text_span": c.source_text_span,
            "unit": c.unit.value if isinstance(c.unit, object) and hasattr(c.unit, 'value') else str(c.unit) if c.unit else "",
            "value": c.value,
            "min_value": c.min_value,
            "max_value": c.max_value,
            "normalized_value": c.value if c.value is not None else (c.max_value if c.max_value is not None else c.min_value),
            "entity_id": None,
            "semantic_role": None,
            "confidence": 1.0,
            "is_hard": c.hard,
            "provenance": ["llm"],
        }

    normalized_destination = _normalize_entity(frame.destination)
    normalized_support_surface = None
    if frame.action == ActionKind.PLACE and normalized_destination is not None:
        # PLACE destination is also the support surface consumed downstream.
        normalized_support_surface = dict(normalized_destination)
        normalized_support_surface["role"] = "support_surface"

    normalized = {
        "instruction": "",  # Will be filled by caller
        "action": action_map.get(frame.action, "CUSTOM"),
        "theme": _normalize_entity(frame.theme),
        "source": _normalize_entity(frame.source),
        "destination": normalized_destination,
        "recipient": _normalize_entity(frame.recipient),
        "obstacle": [_normalize_entity(p.target) for p in frame.prohibitions
                     if p.type in (ProhibitionType.NO_CONTACT, ProhibitionType.AVOID_ENTITY,
                                   ProhibitionType.AVOID_REGION, ProhibitionType.FORBID_ACTION)],
        "support_surface": normalized_support_surface,
        "manner": frame.manner.value if frame.manner else None,
        "motion_state": {"state": "static", "speed_mps": None, "confidence": 0.0},
        "user_constraints": [_normalize_constraint(c) for c in frame.user_constraints],
        "raw_mentions": [],
        "unmet_roles": [],
        "parse_confidence": 0.90,
        "grounding_confidence": 0.0,
        "constraint_confidence": 0.95 if frame.user_constraints else 0.4,
        "notes": list(frame.explanatory_notes) if frame.explanatory_notes else [],
        "clarification": frame.clarification,
        # ── New v1 fields ──
        "prohibitions": [_normalize_prohibition(p) for p in frame.prohibitions],
        "conditions": [_normalize_condition(c) for c in frame.conditions],
        "sequence": [{"step_index": s.step_index, "action": s.action.value,
                       "description": s.description,
                       "entity": _normalize_entity(s.entity),
                       "condition_id": s.condition_id}
                     for s in frame.sequence],
        "steps": [{
            "step_index": s.step_index,
            "action": s.action.value,
            "description": s.description,
            "theme_mention": (s.entity.mention if s.entity else None),
            "destination_mention": None,
        } for s in frame.sequence],
        "urgency": frame.urgency.value,
        "clarification": frame.clarification,
        "intent_frame_version": frame.schema_version,
    }

    return normalized


def _repair_intent_frame_wire(frame_data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize harmless LLM wire-shape variants before Pydantic validation.

    This adapter is intentionally structural only.  It may rename ``target``
    to the schema's ``entity`` and split ``{action,target}`` into
    ``on_true``/``on_true_subject``; it never invents IDs, actions, or scene
    objects and never changes semantic values.
    """
    data = deepcopy(frame_data)
    if isinstance(data.get("intent_frame"), dict) and not data.get("action"):
        data = deepcopy(data["intent_frame"])
    if data.get("action") is None and data.get("action_type") is not None:
        data["action"] = data.pop("action_type")
    if isinstance(data.get("action"), str):
        data["action"] = data["action"].upper()

    def _repair_entity(value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            return {"mention": value}
        if not isinstance(value, dict):
            return value
        if value.get("mention") is None:
            for alias in ("text", "name", "target", "entity"):
                if value.get(alias) is not None:
                    value["mention"] = str(value[alias])
                    break
        if value.get("category") is None and value.get("specific_class") is not None:
            value["category"] = value.get("specific_class")
        # EntityReference deliberately has no ID or execution-role fields.
        # Drop provider-specific copies at the semantic boundary.
        for forbidden in ("object_id", "entity_id", "id", "role", "specific_class"):
            value.pop(forbidden, None)
        relations = value.get("spatial_relations")
        if isinstance(relations, dict):
            value["spatial_relations"] = [f"{k}:{v}" for k, v in relations.items()]
        elif isinstance(relations, list):
            normalized_relations = []
            for relation in relations:
                if isinstance(relation, dict):
                    normalized_relations.append(",".join(f"{k}:{v}" for k, v in relation.items()))
                elif relation is not None:
                    normalized_relations.append(str(relation))
            value["spatial_relations"] = normalized_relations
        elif isinstance(relations, str):
            value["spatial_relations"] = [relations]
        if isinstance(value.get("required_affordances"), str):
            value["required_affordances"] = [value["required_affordances"]]
        if isinstance(value.get("descriptors"), str):
            value["descriptors"] = {"shape": value["descriptors"]}
        return value

    for role in ("theme", "source", "destination", "recipient"):
        if role in data:
            data[role] = _repair_entity(data[role])

    for index, prohibition in enumerate(data.get("prohibitions", []) or []):
        if isinstance(prohibition, dict):
            prohibition.setdefault("prohibition_id", f"proh-wire-{index}")
            if prohibition.get("target") is None:
                prohibition["target"] = prohibition.get("entity") or prohibition.get("object")
            prohibition["target"] = _repair_entity(prohibition.get("target"))
            prohibition.pop("entity", None)
            prohibition.pop("object", None)
            if isinstance(prohibition.get("type"), str):
                prohibition["type"] = prohibition["type"].upper()

    for index, condition in enumerate(data.get("conditions", []) or []):
        if not isinstance(condition, dict):
            continue
        condition.setdefault("condition_id", f"cond-wire-{index}")
        condition["subject"] = _repair_entity(condition.get("subject"))
        condition["on_true_subject"] = _repair_entity(condition.get("on_true_subject"))
        condition["on_false_subject"] = _repair_entity(condition.get("on_false_subject"))
        if condition.get("on_true") is None and condition.get("then_action") is not None:
            condition["on_true"] = condition.get("then_action")
        if condition.get("on_false") is None and condition.get("else_action") is not None:
            condition["on_false"] = condition.get("else_action")
        condition.pop("then_action", None)
        condition.pop("else_action", None)
        condition.pop("entity", None)
        condition.pop("object", None)
        if isinstance(condition.get("predicate"), str):
            predicate_aliases = {"OBJECT_EMPTY": "GRIPPER_EMPTY", "VISIBLE": "OBJECT_VISIBLE", "STABLE": "OBJECT_STABLE"}
            predicate = condition["predicate"].upper()
            condition["predicate"] = predicate_aliases.get(predicate, predicate)
        for branch_name in ("on_true", "on_false"):
            if isinstance(condition.get(branch_name), str):
                branch = condition[branch_name].upper()
                condition[branch_name] = branch if branch in {"GRASP", "FETCH", "PLACE", "HANDOVER", "TRANSFER", "DYNAMIC_GRASP", "PUSH", "POUR", "STACK", "WAIT", "CUSTOM"} else "CUSTOM"

    if isinstance(data.get("manner"), str) and data["manner"].lower() not in {"gentle", "fast", "careful", "firm", "slow"}:
        data.setdefault("explanatory_notes", []).append(f"unmapped_manner:{data['manner']}")
        data["manner"] = None
        for branch_name, subject_name in (("on_true", "on_true_subject"), ("on_false", "on_false_subject")):
            branch = condition.get(branch_name)
            if isinstance(branch, dict):
                if branch.get("action") is not None:
                    condition[branch_name] = branch.get("action")
                target = branch.get("target") or branch.get("entity")
                if target is not None and not condition.get(subject_name):
                    condition[subject_name] = target
        # Some models emit a branch list under branches instead of the two
        # typed fields.  Convert only when the branch carries explicit keys.
        for branch in condition.pop("branches", []) or []:
            if not isinstance(branch, dict):
                continue
            key = "on_true" if branch.get("when") in (True, "true", "TRUE") else "on_false"
            if condition.get(key) is None and branch.get("action") is not None:
                condition[key] = branch.get("action")
            target = branch.get("target") or branch.get("entity")
            subject_key = "on_true_subject" if key == "on_true" else "on_false_subject"
            if target is not None and not condition.get(subject_key):
                condition[subject_key] = target

    for constraint in data.get("user_constraints", []) or []:
        if isinstance(constraint, dict):
            if isinstance(constraint.get("operator"), str):
                constraint["operator"] = constraint["operator"].upper()
            if isinstance(constraint.get("parameter"), str):
                aliases = {"force": "force_n", "force_newton": "force_n", "velocity": "velocity_ms"}
                constraint["parameter"] = aliases.get(constraint["parameter"].lower(), constraint["parameter"])

    if not data.get("sequence"):
        candidate_steps = data.get("steps")
        if not candidate_steps and isinstance(data.get("action_plan"), dict):
            candidate_steps = data["action_plan"].get("steps")
        if isinstance(candidate_steps, list):
            data["sequence"] = candidate_steps

    allowed_actions = {"GRASP", "FETCH", "PLACE", "HANDOVER", "TRANSFER", "DYNAMIC_GRASP", "PUSH", "POUR", "STACK", "WAIT", "CUSTOM", None}
    for index, step in enumerate(data.get("sequence", []) or []):
        if not isinstance(step, dict):
            continue
        step.setdefault("step_index", index)
        if step.get("action") is None and step.get("skill_name") is not None:
            step["action"] = step.get("skill_name")
        if step.get("entity") is None and step.get("target_ref") is not None:
            step["entity"] = step.pop("target_ref")
        if step.get("destination") is None and step.get("destination_ref") is not None:
            step["destination"] = step.pop("destination_ref")
        if "entity" not in step and "target" in step:
            step["entity"] = step.pop("target")
        if "entity" not in step and "theme" in step:
            step["entity"] = step.pop("theme")
        if isinstance(step.get("entity"), str):
            step["entity"] = {"mention": step["entity"]}
        step["entity"] = _repair_entity(step.get("entity"))
        if step.get("destination") is not None:
            destination = step.pop("destination")
            destination_text = destination if isinstance(destination, str) else destination.get("mention", str(destination))
            step["description"] = f"{step.get('description', '')} destination={destination_text}".strip()
        step.pop("target", None)
        step.pop("target_ref", None)
        step.pop("destination_ref", None)
        if isinstance(step.get("action"), str):
            step["action"] = step["action"].upper()
        if step.get("action") not in allowed_actions:
            step["description"] = f"{step.get('description', '')} action={step['action']}".strip()
            step["action"] = "CUSTOM"
    return data


# ============================================================
# DeepSeek 系统提示词
# ============================================================

SYSTEM_PROMPT = """你是一个具身智能机器人的语义解析器。你必须将用户的自然语言指令转化为严格符合 IntentFrame v1 Schema 的结构化 JSON。

## 核心原则

你只负责语义理解：识别动作、角色、属性描述、禁止条件、条件和数值约束。
你**绝对不能**决定：
- 最终 object_id（由系统的 GroundingEngine 负责）
- 最终力/速度参数（由系统的 ConstraintCompiler 负责）
- execution_allowed / plan_status（由系统的 FinalPlanValidator 负责）
- 安全放行结论

## 动作枚举

| 动作 | 说明 | 典型触发词 |
|------|------|-----------|
| GRASP | 抓取物体 | 抓住、拿起、握住、grasp、grab |
| FETCH | 拿取并送达 | 拿过来、取过来、fetch、bring |
| PLACE | 放置物体 | 放到、放在、摆到、放入、place、put |
| HANDOVER | 递交给接收者 | 递给、交给、给我、handover、give |
| TRANSFER | 转移物体 | 转移、转运、transfer |
| DYNAMIC_GRASP | 抓取移动目标 | 抓住移动的、动态抓取 |
| CUSTOM | 复合/特殊动作 | 复杂多步骤任务 |

## Prohibition 禁止类型

| 类型 | 说明 | 示例 |
|------|------|------|
| NO_CONTACT | 禁止接触 | "别碰玻璃杯" |
| FORBID_ACTION | 禁止某动作 | "不要抓红色的" |
| AVOID_ENTITY | 避开实体 | "绕开桌子" |
| AVOID_REGION | 避开区域 | "不要靠近台面" |
| PARAMETER_MAX | 参数上限 | "不超过4N" |
| PARAMETER_MIN | 参数下限 | "不低于2N" |
| CONDITIONAL_PROHIBITION | 条件禁止 | "除非空手否则不要抓" |

## Condition 条件枚举

| 谓词 | 说明 |
|------|------|
| GRIPPER_EMPTY | 夹爪为空 |
| GRIPPER_HOLDING | 夹爪持物 |
| OBJECT_VISIBLE | 目标可见 |
| OBJECT_STABLE | 目标稳定 |
| OBJECT_MOVING | 目标移动中 |
| ROBOT_HOMED | 机器人已归位 |

## 输出格式 — IntentFrame v1

你必须输出以下严格 JSON。不要包含 Markdown 标记或解释文字。所有字段都必须存在。

{
  "intent_frame": {
    "schema_version": "1.0.0",
    "action": "GRASP",
    "theme": {
      "mention": "蓝色杯子",
      "category": "cup",
      "descriptors": {"color": "blue", "material": null, "size": null, "shape": null, "side": null, "height_relation": null, "distance_relation": null, "motion_state": null},
      "spatial_relations": [],
      "required_affordances": ["graspable"],
      "source_text_span": "蓝色杯子",
      "confidence": 0.95
    },
    "destination": null,
    "recipient": null,
    "source": null,
    "prohibitions": [
      {
        "prohibition_id": "proh-xxxxxxxxxxxx",
        "type": "NO_CONTACT",
        "target": {
          "mention": "红色方块",
          "category": "block",
          "descriptors": {"color": "red", "material": null, "size": null, "shape": null, "side": null, "height_relation": null, "distance_relation": null, "motion_state": null},
          "spatial_relations": [],
          "required_affordances": [],
          "source_text_span": "别碰红色方块",
          "confidence": 0.90
        },
        "action": null,
        "parameter": null,
        "operator": null,
        "value": null,
        "unit": null,
        "condition": null,
        "source_text_span": "别碰红色方块",
        "confidence": 0.90
      }
    ],
    "conditions": [
      {
        "condition_id": "cond-xxxxxxxxxxxx",
        "predicate": "OBJECT_STABLE",
        "subject": {"mention": "杯子", "category": "cup", "descriptors": {}, "spatial_relations": [], "required_affordances": [], "source_text_span": "杯子", "confidence": 0.90},
        "operator": null,
        "value": null,
        "unit": null,
        "required_before": ["GRASP"],
        "on_true": null,
        "on_false": null,
        "hard": true,
        "source_text_span": "杯子没停稳就先等它停下来再抓"
      }
    ],
    "sequence": [],
    "user_constraints": [
      {
        "constraint_id": "cstr-xxxxxxxxxxxx",
        "parameter": "force_n",
        "operator": "MAX",
        "value": null,
        "min_value": null,
        "max_value": 4.0,
        "unit": "N",
        "hard": true,
        "source_text_span": "不超过4N"
      }
    ],
    "manner": null,
    "urgency": "normal",
    "clarification": null,
    "explanatory_notes": []
  },
  "behavior_tree": {
    "type": "sequence", "name": "任务名",
    "children": [{"type": "action", "name": "Reach(目标)", "skill_name": "Reach", "target": "目标物体名", "params": {}}]
  }
}

## Few-shot 示例

### 示例 1: theme + destination
指令: "把蓝色方块放到红色方块上"
输出: action=PLACE, theme={mention:"蓝色方块", category:"block", descriptors:{color:"blue"}}, destination={mention:"红色方块", category:"block", descriptors:{color:"red"}, required_affordances:["support_surface"]}

### 示例 2: theme + prohibition
指令: "抓住玻璃杯，别碰塑料杯"
输出: action=GRASP, theme={mention:"玻璃杯", category:"cup", descriptors:{material:"glass"}}, prohibitions=[{type:NO_CONTACT, target:{mention:"塑料杯", category:"cup", descriptors:{material:"plastic"}}}]

### 示例 3: 条件执行
指令: "除非夹爪是空的，否则不要抓取"
输出: action=GRASP, conditions=[{predicate:GRIPPER_EMPTY, required_before:["GRASP"], hard:true}]

### 示例 4: 数值约束
指令: "抓力不要超过4N"
输出: user_constraints=[{parameter:"force_n", operator:MAX, max_value:4.0, unit:"N"}]

### 示例 5: 中英混合 + 多角色
指令: "grab那个红色的bottle然后放到table上"
输出: action=FETCH, theme={mention:"红色的bottle", category:"bottle", descriptors:{color:"red"}}, destination={mention:"table", category:"table", required_affordances:["support_surface"]}, sequence=[{step_index:0, action:GRASP}, {step_index:1, action:PLACE}]

## 重要规则

1. 永远不要编造 entity_id。你只提供语义描述（mention, category, descriptors）
2. 所有 prohibition 必须在 prohibitions 数组中，不得只放在 explanatory_notes
3. 所有 condition 必须在 conditions 数组中，hard 条件必须填写 required_before
4. 数值约束必须在 user_constraints 中，使用正确的 MAX/MIN/EXACT/RANGE 操作符
5. prohibition_id/condition_id/constraint_id 使用简短哈希前缀（如 "proh-" + 12位hex）
6. null 字段必须明确写 null，不得省略
7. 空数组必须写 []，不得省略
8. explanatory_notes 仅用于解释推理过程，不得作为编译器语义输入
9. 不要输出 execution_allowed 或 plan_status
10. 对“如果/否则/除非”必须同时填写 conditions 的 on_true/on_false，并在 sequence 中保留两个分支的动作与目标描述；不得把条件任务压缩成单一 GRASP
11. 对“拿给/递给/送到”必须同时填写 recipient；如果场景提供 handover/support 区域，填写 destination，但不得编造不存在的区域
12. 只输出 JSON，不输出 Markdown 或解释文本"""



# ============================================================
# 场景 → 紧凑 JSON 注入
# ============================================================

# Provider-facing canonical contract.  This is intentionally short and
# explicit so IF/ELSE and ordered tasks are emitted consistently.
SYSTEM_PROMPT += r'''

## 强制输出契约
始终返回一个对象，且必须包含 intent_frame、behavior_tree、target、modifiers、avoid_objects、task_description。
intent_frame 必须包含 schema_version、action、theme、source、destination、recipient、prohibitions、conditions、sequence、user_constraints、manner、urgency、clarification、explanatory_notes；无内容使用 null 或 []。
conditions 中每个分支必须是字符串动作字段，不得写成对象：
{"condition_id":"cond-1","predicate":"OBJECT_VISIBLE","subject":null,"required_before":[],"on_true":"FETCH","on_true_subject":null,"on_false":"FETCH","on_false_subject":null,"hard":true,"source_text_span":""}
sequence 中每项必须是：
{"step_index":0,"action":"GRASP","description":"","entity":null,"condition_id":null}
sequence 按 step_index 从0开始递增。不得使用 then_action、else_action、target、theme、destination 作为 sequence step 字段。
不得输出 confidence、plan_status、execution_allowed 或任何实体ID。所有否定、障碍物、数字约束和两个条件分支必须进入结构化数组，不能只写 explanatory_notes。
'''

# The semantic-candidate contract is the active provider boundary.  The
# legacy IntentFrame/BehaviorTree instructions above remain only for parsing
# old fixtures and are never treated as execution authority.
SYSTEM_PROMPT += r'''

## semantic-candidate-1.0（当前唯一语义输出）
仅返回 {"schema_version":"semantic-candidate-1.0","candidates":[...]}，最多三个候选。
候选只能包含 events/entities/relations/conditions/constraints/prohibitions/evidence_spans/confidence。
严禁 entity_id、object_id、plan_status、execution_allowed、机器人坐标、行为树、未在原文出现的对象或技能。
场景对象只能通过 candidate_key/category/attributes/affordances 描述；最终实体 ID 由确定性 GroundingEngine 绑定。
'''

# The legacy IntentFrame/BT prompt remains only as a compatibility parser for
# persisted fixtures.  Provider calls use this final, unambiguous contract.
SYSTEM_PROMPT = r'''你是领域受限机器人任务语义解析器。
只把用户原文转换为最多三个 semantic-candidate-1.0 候选，不生成执行计划。
输出格式必须是：{"schema_version":"semantic-candidate-1.0","candidates":[...]}。
候选只允许包含：events、entities、relations、conditions、constraints、prohibitions、
coreference_chains、ambiguities、evidence_spans、confidence。
每个语义字段都必须有原文证据（source_text、start、end、confidence、rule_id）；
没有证据就不要生成该字段。
动作和角色必须符合提供的领域 ActionSchema；技能只能引用 SkillCatalog 中的技能。
场景实体只能用 candidate_key、category、attributes、affordances 描述。
严禁输出 entity_id、object_id、任何机器人坐标、plan_status、execution_allowed、
行为树、执行参数裁决、未出现在原文或场景候选中的对象。
规则基线中的否定、障碍物、硬数值约束、条件和顺序不得删除；只能补充有原文证据的语义。
只输出 JSON，不输出解释文字。'''


# Keep the provider response deliberately small. The LLM is a semantic
# parser here, not an execution planner: one candidate and short source spans
# are enough for deterministic grounding and safety validation.
SYSTEM_PROMPT += r'''
Output discipline (mandatory): return exactly one candidate; omit empty arrays
and optional fields; never emit explanations, markdown, or long reasoning.
Every event must contain only event_id, action, role refs, sequence_index and a
short evidence_span. Every entity must contain local_ref, mention, category and
short evidence_spans. Use event_id/condition_id/constraint_id/prohibition_id;
never use a generic key named id. Never emit physical object IDs: scene objects
may only be referred to by candidate_key in entity descriptions. Do not copy
the full scene or repeat the instruction in evidence fields.'''

# The historical prompt above is retained for fixture compatibility, but the
# following prompt is the one actually sent to the provider.  Keeping the
# active contract in plain English avoids mojibake and removes the old
# IntentFrame/BehaviorTree instructions that conflicted with the semantic
# candidate boundary.
SYSTEM_PROMPT = r'''
You are a domain-restricted robot intent semantic compiler.

Your only job is to translate the user's natural-language instruction into
one semantic candidate. Do not plan execution and do not output final JSON.
The deterministic program will ground entities, check affordances and safety,
and create the executable result.

Return exactly:
{"schema_version":"semantic-candidate-1.0","candidates":[{...}]}

Allowed actions are exactly: GRASP, DYNAMIC_GRASP, PLACE, FETCH, TRANSFER,
HANDOVER, PUSH, POUR, STACK, WAIT, CUSTOM.

Action distinctions:
- GRASP: pick up, hold, secure, control, lift, or remove an object with no
  named destination.
- DYNAMIC_GRASP: grasp an object that is explicitly moving or in motion.
- PLACE: put or set an object at a named destination or surface.
- FETCH: bring or retrieve an object to a named recipient or receiving place.
- TRANSFER: move an object from one named location to another named location.
- HANDOVER: give an object to a person or operator.
- PUSH: move an object by pushing it.
- POUR: empty or pour contents from one container into another.
- STACK: place one stackable object on/above another object.
- WAIT: wait for an explicitly stated condition or duration. Do not invent a
  condition or duration from polite wording, motion adjectives, or scene data.
- CUSTOM: use only when the requested operation is outside the allowed set.

Important role rules:
- Do not turn GRASP into PLACE or FETCH unless the instruction explicitly
  states a destination, receiving place, or recipient.
- Do not create a destination just because the scene has a table, tray, or
  receive zone.
- Do not create a recipient unless the instruction names or clearly refers to
  a person/operator/user.
- Preserve explicit negation, constraints, conditions, sequence, and
  coreference. Do not add any of them from the scene or from assumptions.
- If an action is underspecified, keep the action and omit the missing role;
  the deterministic validator will request clarification.

Output only semantic fields: events, entities, relations, conditions,
constraints, prohibitions, coreference_chains, ambiguities, evidence_spans,
and confidence. Every event/entity/constraint/prohibition must have evidence
copied from the instruction. Event role references must point to an entity's
local_ref in this candidate. A scene candidate_key such as scene-object-1 may
appear only inside an entity description; never use it as an event reference.
Never output entity_id, object_id, target_entity_id, destination_entity_id,
plan_status, execution_allowed, behavior trees, coordinates, skills, or any
other execution result. Return JSON only.'''


def _scene_to_prompt_json(scene: Optional[SemanticSceneGraph]) -> Dict[str, Any]:
    """将场景图压缩为注入 Prompt 的紧凑 JSON"""
    if not scene or not scene.objects:
        return {"objects": [], "relations": [], "support_surfaces": [], "robot_state": "unknown"}

    objects = []
    for obj in scene.objects:
        objects.append({
            # Candidate keys are deliberately not physical IDs.  The LLM may
            # describe which candidate it means; GroundingEngine alone binds
            # the final scene entity_id.
            "candidate_key": f"scene-object-{len(objects) + 1}",
            "category": obj.specific_class or obj.label,
            "attributes": obj.attributes,
            "affordances": [a.value for a in obj.affordances],
        })

    candidate_keys = {getattr(obj, "id", ""): f"scene-object-{index + 1}"
                      for index, obj in enumerate(scene.objects)}
    relations = []
    for r in scene.relations:
        relations.append({
            "subject": candidate_keys.get(r.subject, "scene-object"),
            "predicate": r.predicate.value,
            "object": candidate_keys.get(r.object, "scene-object"),
            "confidence": round(r.confidence, 2),
        })

    support_surfaces = [
        {"candidate_key": f"scene-object-{index + 1}", "category": obj.specific_class or obj.label,
         "attributes": obj.attributes, "affordances": [a.value for a in obj.affordances]}
        for index, obj in enumerate(scene.objects)
        if any(a in {"fixed", "support_surface"} for a in [getattr(x, "value", str(x)) for x in (obj.affordances or [])])
    ]
    return {
        "objects": objects,
        "relations": relations,
        "support_surfaces": support_surfaces,
        "gripper": {
            "is_open": scene.robot_state.gripper.is_open,
            "has_object": scene.robot_state.gripper.has_object,
        },
    }


def _semantic_candidate_from_wire(raw: Dict[str, Any], instruction: str, source: str = "llm") -> SemanticCandidate:
    """Validate and sanitize one provider candidate at the semantic boundary."""
    candidate = dict(raw or {})
    repair_count = 0
    forbidden = {
        "entity_id", "object_id", "id", "target_entity_id", "destination_entity_id",
        "plan_status", "execution_allowed", "behavior_tree", "robot_coordinates",
    }

    # A few OpenAI-compatible providers still use a generic ``id`` for
    # non-execution semantic atoms. Canonicalize only those top-level atoms
    # before the execution-ID guard. Entity IDs remain forbidden; grounding
    # must still be performed from descriptions against the perception scene.
    atom_id_fields = {
        "events": "event_id",
        "conditions": "condition_id",
        "constraints": "constraint_id",
        "prohibitions": "prohibition_id",
    }
    for collection, canonical_key in atom_id_fields.items():
        atoms = candidate.get(collection)
        if not isinstance(atoms, list):
            continue
        normalized_atoms = []
        for atom in atoms:
            if isinstance(atom, dict):
                atom = dict(atom)
                if "id" in atom and canonical_key not in atom:
                    atom[canonical_key] = atom.pop("id")
            normalized_atoms.append(atom)
        candidate[collection] = normalized_atoms

    def find_forbidden(value: Any, path: str = "") -> List[str]:
        found: List[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if key in forbidden:
                    found.append(f"{path}.{key}" if path else key)
                found.extend(find_forbidden(item, f"{path}.{key}" if path else key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                found.extend(find_forbidden(item, f"{path}[{index}]"))
        return found

    forbidden_paths = find_forbidden(candidate)
    if forbidden_paths:
        raise ValueError(
            "semantic candidate contains execution-owned fields: "
            + ", ".join(forbidden_paths[:8])
        )

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key not in forbidden}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    candidate = scrub(candidate)
    if isinstance(candidate.get("graph"), dict) and not candidate.get("events"):
        candidate = {**candidate, **candidate["graph"]}
    graph_data = {
        "schema_version": "semantic-task-graph-1.0",
        "instruction": instruction,
        "entities": candidate.get("entities", []),
        "events": candidate.get("events", []),
        "relations": candidate.get("relations", []),
        "conditions": candidate.get("conditions", []),
        "constraints": candidate.get("constraints", []),
        "prohibitions": candidate.get("prohibitions", []),
        "coreference_chains": candidate.get("coreference_chains", []),
        "ambiguities": candidate.get("ambiguities", []),
    }

    def normalize_legacy_sequence_atoms() -> None:
        """Convert harmless legacy order output into graph-compatible form.

        Some provider responses still use ``conditions:[{type: sequence}]``
        and ``constraints:[{type: order, value: 'FETCH before PLACE'}]``.
        The latter is not a numeric constraint and must never be coerced into
        SemanticConstraint.value.  Rule-derived relations remain authoritative
        for ordering, so non-numeric order atoms can be safely discarded.
        """
        normalized_conditions = []
        for index, item in enumerate(graph_data["conditions"]):
            if not isinstance(item, dict):
                continue
            value = item.get("value") or item.get("order") or item.get("text")
            predicate = item.get("predicate") or item.get("condition_predicate")
            if not predicate and str(item.get("type", "")).lower() in {"sequence", "order", "temporal"}:
                predicate = "SEQUENCE"
            if predicate:
                item.setdefault("condition_id", f"llm-condition-{index + 1}")
                item["predicate"] = str(predicate).upper()
                item.setdefault("value", value or "ordered")
                span = item.get("evidence_span") or item.get("source_text")
                if isinstance(span, dict):
                    span = span.get("source_text") or span.get("value") or span.get("text")
                if isinstance(span, str) and span:
                    item["evidence_span"] = span
                normalized_conditions.append(item)
        graph_data["conditions"] = normalized_conditions

        numeric_constraints = []
        for item in graph_data["constraints"]:
            if not isinstance(item, dict):
                continue
            ctype = str(item.get("type", "")).lower()
            if ctype in {"order", "sequence", "temporal"}:
                # Ordering is represented by SemanticRelation/SEQUENCE and
                # already protected from deletion by deterministic parsing.
                continue
            if not item.get("parameter"):
                item["parameter"] = item.get("constraint_type") or "unknown"
            if not item.get("operator"):
                item["operator"] = "exact"
            numeric = any(item.get(key) is not None for key in ("value", "min_value", "max_value"))
            if numeric and all(not isinstance(item.get(key), str) for key in ("value", "min_value", "max_value")):
                numeric_constraints.append(item)
        graph_data["constraints"] = numeric_constraints

    normalize_legacy_sequence_atoms()

    def normalize_atom_wire(item: Dict[str, Any], collection: str) -> Dict[str, Any]:
        """Normalize provider shape without inventing semantic values."""
        value = dict(item)
        if isinstance(value.get("evidence_spans"), list):
            spans = []
            for entry in value["evidence_spans"]:
                if isinstance(entry, str):
                    spans.append(entry)
                elif isinstance(entry, dict):
                    text = entry.get("source_text") or entry.get("value") or entry.get("text")
                    if isinstance(text, str) and text:
                        spans.append(text)
            value["evidence_spans"] = spans
        if isinstance(value.get("evidence_span"), dict):
            value["evidence_span"] = (
                value["evidence_span"].get("source_text")
                or value["evidence_span"].get("value")
                or value["evidence_span"].get("text")
                or ""
            )
        if isinstance(value.get("evidence"), list):
            # Graph evidence is structured; tolerate string-only provider
            # output by converting it into the canonical EvidenceSpan shape.
            value["evidence"] = [
                entry if isinstance(entry, dict) else {"source_text": str(entry), "value": str(entry)}
                for entry in value["evidence"]
            ]
        if collection == "relations":
            value.setdefault("type", value.get("relation_type") or value.get("relation"))
        return value

    for collection_name in (
        "entities", "events", "relations", "conditions", "constraints",
        "prohibitions", "coreference_chains", "ambiguities",
    ):
        graph_data[collection_name] = [
            normalize_atom_wire(item, collection_name)
            if isinstance(item, dict) else item
            for item in graph_data[collection_name]
        ]

    # Stable local references are provider-owned labels, not physical IDs.
    # Normalize aliases once so event role references remain connected after
    # fusion even when the provider uses candidate_key instead of local_ref.
    entity_aliases: Dict[str, str] = {}
    for index, entity in enumerate(graph_data["entities"]):
        if not isinstance(entity, dict):
            continue
        local_ref = str(entity.get("local_ref") or entity.get("candidate_key") or f"llm-entity-{index + 1}")
        entity["local_ref"] = local_ref
        candidate_key = entity.get("candidate_key")
        if candidate_key and not re.fullmatch(r"scene-object-\d+", str(candidate_key)):
            raise ValueError(f"invalid semantic candidate_key: {candidate_key}")
        for alias in (entity.get("candidate_key"), entity.get("id"), entity.get("name")):
            if alias:
                entity_aliases[str(alias)] = local_ref
    def ref_value(value: Any) -> Any:
        if isinstance(value, dict):
            return (
                value.get("local_ref") or value.get("candidate_key")
                or value.get("name") or value.get("mention")
            )
        return value

    for event in graph_data["events"]:
        if not isinstance(event, dict):
            continue
        for field in ("theme_ref", "destination_ref", "source_ref", "recipient_ref"):
            value = ref_value(event.get(field))
            if value is not None and str(value) in entity_aliases:
                event[field] = entity_aliases[str(value)]
        event["obstacle_refs"] = [entity_aliases.get(str(ref_value(value)), ref_value(value))
                                  for value in (event.get("obstacle_refs") or [])]
        event["action"] = normalize_action(event.get("action"))
    for prohibition in graph_data["prohibitions"]:
        if isinstance(prohibition, dict):
            target_ref = ref_value(prohibition.get("target_ref") or prohibition.get("target"))
            if target_ref is not None:
                prohibition["target_ref"] = entity_aliases.get(str(target_ref), target_ref)
    # A missing relation type is an invalid relation atom, not grounds for
    # discarding otherwise valid actions and safety evidence.  Fusion keeps
    # the rule relation as the protected sequence source.
    graph_data["relations"] = [
        relation for relation in graph_data["relations"]
        if isinstance(relation, dict) and relation.get("type")
    ]
    for index, prohibition in enumerate(graph_data["prohibitions"]):
        if isinstance(prohibition, dict):
            prohibition.setdefault("prohibition_id", f"llm-prohibition-{index + 1}")
    for index, constraint in enumerate(graph_data["constraints"]):
        if isinstance(constraint, dict):
            constraint.setdefault("constraint_id", f"llm-constraint-{index + 1}")

    def evidence_text(item: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        for key in ("evidence_span", "evidence_spans", "evidence", "source_text_span"):
            value = item.get(key)
            if isinstance(value, str) and value:
                values.append(value)
            elif isinstance(value, list):
                for entry in value:
                    if isinstance(entry, str) and entry:
                        values.append(entry)
                    elif isinstance(entry, dict):
                        for nested_key in ("source_text", "value", "text"):
                            nested = entry.get(nested_key)
                            if isinstance(nested, str) and nested:
                                values.append(nested)
        return values

    def add_evidence(item: Dict[str, Any], text: str, rule_id: str) -> None:
        """Repair a missing span when the provider gave a source mention."""
        nonlocal repair_count
        if not text or text not in instruction:
            return
        start = instruction.find(text)
        if "evidence_spans" not in item:
            item["evidence_spans"] = [text]
            repair_count += 1
        if "evidence_span" not in item:
            item["evidence_span"] = text
            repair_count += 1
        if "evidence" not in item:
            item["evidence"] = [{
                "value": text, "source_text": text, "start": start,
                "end": start + len(text), "confidence": 0.85,
                "rule_id": rule_id,
            }]
            repair_count += 1

    # Accept common provider aliases but keep the canonical graph fields.
    for index, entity in enumerate(graph_data["entities"]):
        if isinstance(entity, dict):
            if "local_ref" not in entity:
                entity["local_ref"] = entity.get("candidate_key") or f"llm-entity-{index + 1}"
                repair_count += 1
            if "mention" not in entity:
                entity["mention"] = entity.get("name") or entity.get("text") or ""
                repair_count += 1
            add_evidence(entity, str(entity.get("mention") or ""), "llm.entity.mention")
            if "evidence_spans" not in entity:
                entity["evidence_spans"] = [entity.get("mention", "")] if entity.get("mention") else []
                repair_count += 1
            candidate_key = entity.get("candidate_key")
            evidence_values = [str(value) for value in entity.get("evidence_spans", [])
                               if value not in (None, "")]
            if (not candidate_key and entity.get("mention") not in (None, "")
                    and entity.get("mention") not in instruction
                    and not any(value in instruction for value in evidence_values)):
                entity["_reject_un_evidenced"] = True
    for index, event in enumerate(graph_data["events"]):
        if isinstance(event, dict):
            defaults = {
                "event_id": f"llm-event-{index + 1}", "action": "CUSTOM",
                "theme_ref": event.get("theme") or event.get("theme_id"),
                "destination_ref": event.get("destination") or event.get("destination_id"),
                "source_ref": event.get("source") or event.get("source_id"),
                "recipient_ref": event.get("recipient") or event.get("recipient_id"),
                "obstacle_refs": event.get("obstacles") or [],
                "evidence_span": event.get("evidence") or "",
                "sequence_index": index,
            }
            for key, value in defaults.items():
                if key not in event:
                    event[key] = value
                    repair_count += 1
            action = str(event.get("action") or "CUSTOM").upper()
            if action not in ACTION_SCHEMAS:
                event["_reject_unknown_action"] = True

            # Models commonly return the correct action but omit the explicit
            # span.  Recover it deterministically from the same action
            # candidate extractor used by the rule baseline.
            if not evidence_text(event):
                match = next((item for item in parse_action_candidates(instruction)
                              if item.value == normalize_action(action)), None)
                if match is not None:
                    add_evidence(event, match.evidence, "llm.event.action")

            # If the provider omitted the action or used CUSTOM while its
            # evidence clearly contains one supported domain verb, recover
            # that action from the deterministic lexical candidate.  This is
            # a local format repair, not an LLM semantic override; OOD text
            # still remains CUSTOM and is blocked by the domain gate.
            if normalize_action(action) == "CUSTOM":
                event_evidence = evidence_text(event)
                matching = [item for item in parse_action_candidates(instruction)
                            if not event_evidence or any(item.evidence == span or item.evidence in span
                                                         for span in event_evidence)]
                if len(matching) == 1:
                    event["action"] = matching[0].value
                    repair_count += 1
                    action = matching[0].value

    # Relation/condition event references are often emitted as numeric
    # indices or provider-specific aliases.  Convert numeric values to
    # strings before Pydantic validation; the compiler performs the final
    # order-preserving mapping against the rule graph.
    event_aliases: Dict[str, str] = {}
    for index, event in enumerate(graph_data["events"]):
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or f"llm-event-{index + 1}")
        action = str(event.get("action") or "").lower()
        for alias in (str(index), str(index + 1), f"e{index + 1}", f"ev{index + 1}",
                      f"event_{index + 1}", f"evt_{index + 1}",
                      f"event-{index}", f"event-{index + 1}",
                      f"evt-{index}", f"evt-{index + 1}", event_id,
                      f"event-{action}-{index + 1}" if action else "",
                      f"evt-{action}-{index + 1}" if action else ""):
            if alias:
                event_aliases[alias] = event_id
    for relation in graph_data["relations"]:
        if isinstance(relation, dict):
            for field in ("source_event", "target_event"):
                value = relation.get(field)
                if value is not None:
                    relation[field] = event_aliases.get(str(value), str(value))
    for condition in graph_data["conditions"]:
        if isinstance(condition, dict):
            for field in ("on_true_event_ids", "on_false_event_ids"):
                values = condition.get(field) or []
                condition[field] = [event_aliases.get(str(value), str(value)) for value in values]
    for prohibition in graph_data["prohibitions"]:
        if isinstance(prohibition, dict):
            values = prohibition.get("scope_event_ids") or []
            prohibition["scope_event_ids"] = [
                event_aliases.get(str(value), str(value)) for value in values
            ]

    known_refs = {
        str(item.get("local_ref")) for item in graph_data["entities"]
        if isinstance(item, dict) and item.get("local_ref")
    }
    # A provider occasionally emits a role reference (usually e1/e2) while
    # omitting the corresponding entity atom. Keep the semantic action and
    # represent the missing entity as explicitly unresolved; grounding will
    # either bind it from a compatible scene description or force clarification.
    # The placeholder has no evidence and can never become a physical ID by
    # itself.
    for event in graph_data["events"]:
        if not isinstance(event, dict):
            continue
        refs = [event.get("theme_ref"), event.get("destination_ref"),
                event.get("source_ref"), event.get("recipient_ref"),
                *(event.get("obstacle_refs") or [])]
        for ref in refs:
            if not ref or str(ref) in known_refs:
                continue
            placeholder = str(ref)
            graph_data["entities"].append({
                "local_ref": placeholder,
                "mention": "",
                "category": "object",
                "attributes": {"_grounding_unresolved": True, "_llm_placeholder": True},
                "evidence_spans": [],
                "evidence": [],
            })
            known_refs.add(placeholder)
    for index, event in enumerate(graph_data["events"]):
        if not isinstance(event, dict):
            continue
        refs = [event.get("theme_ref"), event.get("destination_ref"),
                event.get("source_ref"), event.get("recipient_ref"),
                *(event.get("obstacle_refs") or [])]
        unknown = [str(ref) for ref in refs if ref and str(ref) not in known_refs]
        if unknown:
            event["_reject_unknown_ref"] = unknown

    # Validate at atom level.  A malformed optional atom is dropped so a
    # useful action candidate can still reach fusion.  The main event must
    # retain evidence; otherwise accepting it would turn the LLM into an
    # unsupported guesser.  In particular, an incomplete prohibition or
    # constraint must not discard an otherwise valid action candidate.
    optional_requirements = {
        "conditions": ("condition_id", "predicate"),
        "constraints": ("constraint_id", "parameter", "operator"),
        "prohibitions": ("prohibition_id", "type"),
    }
    for collection_name, required_fields in optional_requirements.items():
        repaired_items = []
        for item in graph_data[collection_name]:
            if not isinstance(item, dict) or not all(item.get(field) not in (None, "")
                                                    for field in required_fields):
                repair_count += 1
                continue
            repaired_items.append(item)
        graph_data[collection_name] = repaired_items

    # Providers sometimes serialize evidence as a single string and emit
    # partially shaped coreference/ambiguity records.  Evidence already has
    # canonical spans elsewhere, so remove only the malformed optional value;
    # never discard the main action candidate for an optional formatting error.
    for collection_name in ("entities", "events", "conditions", "constraints", "prohibitions"):
        for item in graph_data[collection_name]:
            if isinstance(item, dict) and "evidence" in item and not isinstance(item.get("evidence"), list):
                item.pop("evidence", None)
                repair_count += 1

    valid_coreferences = []
    for item in graph_data["coreference_chains"]:
        if not isinstance(item, dict) or not item.get("chain_id"):
            repair_count += 1
            continue
        if not isinstance(item.get("evidence"), list):
            item["evidence"] = []
            repair_count += 1
        valid_coreferences.append(item)
    graph_data["coreference_chains"] = valid_coreferences

    valid_ambiguities = []
    for item in graph_data["ambiguities"]:
        if not isinstance(item, dict) or not item.get("ambiguity_id") or not item.get("type"):
            repair_count += 1
            continue
        if not isinstance(item.get("evidence"), list):
            item["evidence"] = []
            repair_count += 1
        valid_ambiguities.append(item)
    graph_data["ambiguities"] = valid_ambiguities
    for collection_name in ("entities", "events", "conditions", "constraints", "prohibitions"):
        valid_items = []
        for index, item in enumerate(graph_data[collection_name]):
            if not isinstance(item, dict):
                continue
            evidence = evidence_text(item)
            if evidence and any(value in instruction for value in evidence):
                valid_items.append(item)
            elif (collection_name == "entities" and (
                    bool(item.get("_llm_placeholder")) or
                    (isinstance(item.get("candidate_key"), str)
                     and re.fullmatch(r"scene-object-\d+", item["candidate_key"]))
            )):
                # A scene candidate key is grounded evidence supplied by the
                # read-only perception context, not a physical ID guessed by
                # the provider.  Retain the entity atom so the compiler can
                # bind it deterministically to the indexed scene object.
                valid_items.append(item)
            elif collection_name == "events":
                logger.warning("Rejecting LLM event without instruction evidence: %s", index)
        graph_data[collection_name] = valid_items
    if not graph_data["events"]:
        raise ValueError("semantic candidate has no evidenced event")

    graph_data["entities"] = [item for item in graph_data["entities"]
                               if not (isinstance(item, dict) and item.pop("_reject_un_evidenced", False))]
    graph_data["events"] = [item for item in graph_data["events"]
                             if not (isinstance(item, dict) and (
                                 item.pop("_reject_unknown_action", False) or
                                 item.pop("_reject_unknown_ref", False)))]
    if not graph_data["events"]:
        raise ValueError("semantic candidate events have no valid local references")
    graph = SemanticTaskGraph.model_validate(graph_data)
    graph.metadata["provider_repair_count"] = repair_count
    return SemanticCandidate.from_graph(graph, confidence=float(candidate.get("confidence", 0.0) or 0.0), source=source,
                                        )


def parse_semantic_candidates(raw: Dict[str, Any], instruction: str) -> List[SemanticCandidate]:
    """Parse at most three LLM semantic candidates; never accept execution output."""
    values = raw.get("candidates") if isinstance(raw, dict) else None
    if not isinstance(values, list):
        return []
    result = []
    for item in values[:3]:
        if not isinstance(item, dict):
            continue
        try:
            result.append(_semantic_candidate_from_wire(item, instruction))
        except Exception as exc:
            logger.warning("Rejecting malformed semantic candidate: %s", exc)
    return result


def _memory_to_prompt_json(memory_items: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """将记忆上下文压缩为注入 Prompt 的紧凑 JSON"""
    if not memory_items:
        return {"preferences": {}, "experiences": []}

    prefs = {}
    exps = []
    for item in memory_items:
        mtype = item.get("memory_type", "")
        if mtype == "user_preference":
            prefs[item.get("key", "")] = item.get("value")
        elif mtype == "skill_experience":
            exps.append({
                "skill": item.get("key", ""),
                "params": item.get("value", {}),
            })
    return {"preferences": prefs, "experiences": exps}


def _semantic_baseline_for_prompt(parsed_task: Any) -> Dict[str, Any]:
    """Create a read-only, ID-sanitized rule baseline for the LLM prompt."""
    raw = parsed_task.model_dump(mode="json") if hasattr(parsed_task, "model_dump") else dict(parsed_task)

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in {"entity_id", "object_id"}:
                    result[key] = None
                else:
                    result[key] = scrub(item)
            return result
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    data = scrub(raw)
    # Keep the prompt compact and focused on fields that can be improved by
    # semantic interpretation; execution status remains deterministic.
    return {
        "action": data.get("action"),
        "theme": data.get("theme"),
        "source": data.get("source"),
        "destination": data.get("destination"),
        "recipient": data.get("recipient"),
        "support_surface": data.get("support_surface"),
        "obstacle": data.get("obstacle", []),
        "user_constraints": data.get("user_constraints", []),
        "conditions": data.get("conditions", []),
        "prohibitions": data.get("prohibitions", []),
        "steps": data.get("steps", []),
        "manner": data.get("manner"),
        "unmet_roles": data.get("unmet_roles", []),
    }


# ============================================================
# LLMPlanner — DeepSeek API 实现
# ============================================================

class LLMPlanner(TaskPlannerInterface):
    """
    基于 DeepSeek API 的 LLM 任务规划器。

    用法:
        planner = LLMPlanner(api_key="sk-xxx")
        bt = planner.plan(
            instruction="请把红色药瓶递给我，轻一点，别碰水杯",
            scene=scene_graph,
            memory_context=memory_items,
        )
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        settings = get_settings()
        # None means "use configured key"; an explicit empty string means
        # "disable LLM" (required for deterministic fallback tests).
        self._api_key = settings.deepseek_api_key if api_key is None else api_key
        self._base_url = settings.deepseek_base_url
        self._model = model or settings.deepseek_model
        self._temperature = settings.deepseek_temperature
        self._max_tokens = settings.deepseek_max_tokens
        self._timeout = settings.deepseek_timeout_s
        self._max_retries = settings.deepseek_max_retries
        self._cache_enabled = settings.llm_cache_enabled
        self._candidate_cache: Dict[str, List[SemanticCandidate]] = {}
        self._last_call_metadata: Dict[str, Any] = {}
        self._client = None
        self._catalog = SkillCatalog()

    @property
    def name(self) -> str:
        return f"DeepSeek-{self._model}"

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    @property
    def last_call_metadata(self) -> Dict[str, Any]:
        """Transport/parse telemetry for the current semantic request."""
        return dict(self._last_call_metadata)

    @staticmethod
    def _candidate_cache_key(
        instruction: str,
        scene_json: Dict[str, Any],
        memory_json: Dict[str, Any],
    ) -> str:
        payload = json.dumps({
            "instruction": " ".join(str(instruction).split()),
            "scene": scene_json,
            "memory": memory_json,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    # ============================================================
    # 主接口
    # ============================================================

    def plan(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
        semantic_only: bool = False,
    ) -> BehaviorTree:
        """
        调用 DeepSeek API 生成 BehaviorTree。

        Args:
            instruction:    用户自然语言指令
            scene:          语义场景图
            memory_context: Memory 检索结果

        Returns:
            BehaviorTree

        Raises:
            LLMPlannerError: API 不可用或返回不合法时
        """
        if not self._api_key:
            raise LLMPlannerError(
                "DeepSeek API Key 未配置。请设置环境变量 RIA_DEEPSEEK_API_KEY "
                "或在代码中传入 api_key 参数。"
            )

        # 1. 构建 Prompt
        scene_json = _scene_to_prompt_json(scene)
        memory_json = _memory_to_prompt_json(memory_context)

        # Give the model a read-only deterministic baseline.  This is not a
        # final answer and contains no writable ID authority; it tells the LLM
        # exactly which semantics are already known and where it may add value.
        rule_baseline = None
        try:
            from robot_intent_agent.task_semantics import parse_task_semantics
            baseline = parse_task_semantics(instruction, scene=scene)
            rule_baseline = _semantic_baseline_for_prompt(baseline)
        except Exception as exc:
            logger.debug("Could not build rule baseline prompt: %s", exc)

        user_message = self._build_user_message(instruction, scene_json, memory_json, rule_baseline)

        # 2. 调用 API
        raw_json = self._call_api(user_message)

        # 3. 解析为 BehaviorTree
        bt = self._parse_response(raw_json, instruction, semantic_only=semantic_only,
                                  allow_legacy_compat=False)

        return bt

    def semantic_candidates(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> List[SemanticCandidate]:
        """Return provider semantics only; no executable artifact crosses this API."""
        if not self._api_key:
            raise LLMPlannerError("DeepSeek API Key 未配置")
        self._last_call_metadata = {
            "cache_hit": False,
            "network_call": False,
            "transport_succeeded": False,
            "json_parsed": False,
            "candidate_valid": False,
        }
        scene_json = _scene_to_prompt_json(scene)
        memory_json = _memory_to_prompt_json(memory_context)
        cache_key = self._candidate_cache_key(instruction, scene_json, memory_json)
        if self._cache_enabled and cache_key in self._candidate_cache:
            self._last_call_metadata.update({
                "cache_hit": True,
                "transport_succeeded": True,
                "json_parsed": True,
                "candidate_valid": True,
            })
            return deepcopy(self._candidate_cache[cache_key])
        raw = self._call_api(self._build_user_message(
            instruction, scene_json, memory_json,
        ))
        self._last_call_metadata["json_parsed"] = True
        candidates = parse_semantic_candidates(raw, instruction)
        if not candidates:
            raise LLMPlannerError("DeepSeek 未返回合法 semantic-candidate-1.0")
        self._last_call_metadata["candidate_valid"] = True
        if self._cache_enabled:
            # Cache only provider semantics.  Grounding and scene IDs are
            # intentionally added later by SemanticCompiler.
            self._candidate_cache[cache_key] = deepcopy(candidates)
        return deepcopy(candidates)

    # ============================================================
    # Prompt 构建
    # ============================================================

    def _build_user_message(
        self,
        instruction: str,
        scene_json: Dict[str, Any],
        memory_json: Dict[str, Any],
        rule_baseline: Optional[Dict[str, Any]] = None,
    ) -> str:
        """组装发送给 DeepSeek 的用户消息"""
        parts = [
            "## 用户指令",
            instruction,
            "",
            "## 当前场景状态",
            "```json",
            json.dumps(scene_json, ensure_ascii=False, indent=2),
            "```",
        ]

        if rule_baseline is not None:
            parts.extend([
                "",
                "## 规则引擎基线（只读证据，不是最终答案）",
                "规则基线已经识别出的否定、障碍物、数字约束和实体描述必须保留。你只能补充原文中有证据、但规则基线缺失的语义；不能删除、覆盖或改写安全字段，不能使用其中的entity_id作为最终绑定。",
                "```json",
                json.dumps(rule_baseline, ensure_ascii=False, indent=2),
                "```",
            ])

        # 如果有记忆，注入
        if memory_json.get("preferences") or memory_json.get("experiences"):
            parts.extend([
                "",
                "## 用户偏好与历史经验",
                "```json",
                json.dumps(memory_json, ensure_ascii=False, indent=2),
                "```",
            ])

        parts.extend([
            "Output budget: emit exactly one candidate. Omit empty arrays and optional fields. Use short exact evidence spans copied from the instruction. Never return explanations or the full scene. Never use a generic key named id; use event_id, condition_id, constraint_id, or prohibition_id.",
            "",
            "## 领域动作Schema",
            json.dumps({name: {
                "required_roles": list(schema.required_roles),
                "required_any_roles": [list(group) for group in schema.required_any_roles],
                "optional_roles": list(schema.optional_roles),
                "skill_template": list(schema.skill_template),
            } for name, schema in ACTION_SCHEMAS.items()}, ensure_ascii=False, separators=(",", ":")),
            "## 支持的关系",
            json.dumps(sorted(SUPPORTED_RELATIONS), ensure_ascii=False),
            "",
            "## 输出JSON Schema",
            json.dumps({
                "schema_version": "semantic-candidate-1.0",
                "candidates": [{
                    "events": [], "entities": [], "relations": [], "conditions": [],
                    "constraints": [], "prohibitions": [], "evidence_spans": [], "confidence": 0.0,
                }],
            }, ensure_ascii=False, separators=(",", ":")),
            "规则基线中的安全字段必须保留。最多输出三个语义候选；不得输出entity_id、object_id、plan_status、execution_allowed、机器人坐标、行为树或新实体。",
            "事件的theme_ref/destination_ref/source_ref/recipient_ref必须引用本候选entities中声明的local_ref（例如e1、e2），绝不能引用scene-object-1、scene-object-2作为事件引用。scene-object-*只能填写在entity.candidate_key。",
            "顺序只用relations（type=BEFORE，source_event/target_event）或conditions中的SEQUENCE表达；constraints只允许数字参数、operator和unit，不要把FETCH before STACK写进constraints.value。",
            "每个事件必须填写evidence_span，且必须逐字来自用户指令；每个实体必须填写mention和evidence_spans。只输出JSON。",
        ])

        return "\n".join(parts)

    # Active provider message.  This later definition intentionally overrides
    # the legacy prompt builder above while keeping old fixture code intact.
    def _build_user_message(
        self,
        instruction: str,
        scene_json: Dict[str, Any],
        memory_json: Dict[str, Any],
        rule_baseline: Optional[Dict[str, Any]] = None,
    ) -> str:
        action_contract = {
            name: {
                "required_roles": list(schema.required_roles),
                "required_any_roles": [list(group) for group in schema.required_any_roles],
                "optional_roles": list(schema.optional_roles),
            }
            for name, schema in ACTION_SCHEMAS.items()
        }
        parts = [
            "USER INSTRUCTION (the only language evidence):",
            instruction,
            "",
            "PERCEPTION SCENE (read-only grounding context; never copy physical IDs into the candidate):",
            json.dumps(scene_json, ensure_ascii=False, indent=2),
        ]
        if rule_baseline is not None:
            parts.extend([
                "",
                "RULE BASELINE (a fallible proposal, not execution authority):",
                json.dumps(rule_baseline, ensure_ascii=False, indent=2),
                "Correct it only when the instruction provides evidence. Do not invent roles or safety facts.",
            ])
        if memory_json.get("preferences") or memory_json.get("experiences"):
            parts.extend(["", "OPTIONAL MEMORY CONTEXT:", json.dumps(memory_json, ensure_ascii=False, indent=2)])
        parts.extend([
            "",
            "ACTION CONTRACT:",
            json.dumps(action_contract, ensure_ascii=False, separators=(",", ":")),
            "",
            "OUTPUT CONTRACT:",
            json.dumps({
                "schema_version": "semantic-candidate-1.0",
                "candidates": [{
                    "events": [], "entities": [], "relations": [], "conditions": [],
                    "constraints": [], "prohibitions": [], "coreference_chains": [],
                    "ambiguities": [], "evidence_spans": [], "confidence": 0.0,
                }],
            }, ensure_ascii=False, separators=(",", ":")),
            "Return exactly one candidate. Every event role reference must point to an entity declared in this same candidate. Declare the entity with its language description before referencing it; never invent an undeclared e1/e2 reference.",
            "Use scene-object-N only as entity.candidate_key and never as an event role reference. Never output physical object IDs or execution state.",
            "If a role is not described clearly in the instruction, omit that role so deterministic grounding can request clarification. Do not fill it with a guessed object.",
            "Return JSON only.",
        ])
        return "\n".join(parts)

    # ============================================================
    # API 调用
    # ============================================================

    # ============================================================
    # LLM JSON 安全解析 (防御性脱壳)
    # ============================================================

    @staticmethod
    def _safe_parse_llm_json(raw_text: str) -> Dict[str, Any]:
        """
        防御性解析 LLM 返回的 JSON。

        处理:
            1. Markdown 代码块剥离 (```json ... ```)
            2. 数组脱壳: 如果最外层是 list[{...}], 自动取第一项
            3. 类型校验: 确保最终返回 dict
        """
        cleaned = LLMPlanner._strip_markdown(raw_text)
        data = json.loads(cleaned)

        # 防御性脱壳: 无论 DeepSeek 包了多少层 list, 循环剥开取到最里面的 dict
        while isinstance(data, list) and len(data) > 0:
            logger.info(f"LLM returned a list of {len(data)} items; auto-unwrapping")
            data = data[0]

        # 类型严格校验
        if not isinstance(data, dict):
            raise TypeError(
                f"LLM 解析结果期望为 dict，实际得到 {type(data).__name__}: "
                f"{json.dumps(data, ensure_ascii=False)[:200]}"
            )

        return data

    # ============================================================
    # API 调用
    # ============================================================

    def _call_api(self, user_message: str) -> Dict[str, Any]:
        """调用 DeepSeek API，带重试和超时处理"""
        self._ensure_client()
        self._last_call_metadata["network_call"] = True

        for attempt in range(self._max_retries + 1):
            try:
                logger.info(
                    f"DeepSeek API call (attempt {attempt+1}/{self._max_retries+1}) "
                    f"model={self._model}"
                )
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    response_format={"type": "json_object"},
                    timeout=self._timeout,
                )

                raw_text = response.choices[0].message.content.strip()
                logger.info(f"DeepSeek response: {len(raw_text)} chars")
                self._last_call_metadata["transport_succeeded"] = True

                return self._safe_parse_llm_json(raw_text)

            except json.JSONDecodeError as e:
                logger.warning(f"DeepSeek returned invalid JSON (attempt {attempt+1}): {e}")
                raise LLMPlannerError(f"DeepSeek 返回了非法的 JSON: {e}")

            except (ValueError, TypeError) as e:
                logger.warning(f"DeepSeek JSON structure error (attempt {attempt+1}): {e}")
                raise LLMPlannerError(f"DeepSeek 返回的 JSON 结构异常: {e}")

            except Exception as e:
                logger.warning(f"DeepSeek API error (attempt {attempt+1}): {e}")
                if attempt >= self._max_retries or not self._is_retryable_api_error(e):
                    raise LLMPlannerError(f"DeepSeek API 调用失败: {e}")

        raise LLMPlannerError("DeepSeek API 调用失败：已达最大重试次数")

    @staticmethod
    def _is_retryable_api_error(error: Exception) -> bool:
        """Retry only transient transport/rate-limit/server failures."""
        status = getattr(error, "status_code", None)
        if status is None:
            response = getattr(error, "response", None)
            status = getattr(response, "status_code", None) if response is not None else None
        if status in {408, 409, 429} or (isinstance(status, int) and status >= 500):
            return True
        name = type(error).__name__.lower()
        return any(token in name for token in ("timeout", "connection", "network", "ratelimit"))

    def _ensure_client(self):
        """懒初始化 OpenAI 客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise LLMPlannerError(
                    "需要安装 openai 包。请执行: pip install openai"
                )
            self._client = OpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
            )

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """移除 DeepSeek 可能包裹的 Markdown 标记"""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    # ============================================================
    # 响应解析
    # ============================================================

    def _parse_response(
        self, raw: Dict[str, Any], instruction: str, semantic_only: bool = False,
        allow_legacy_compat: bool = True,
    ) -> BehaviorTree:
        """将 DeepSeek 返回的 JSON 解析为 BehaviorTree，同时验证 IntentFrame v1 并进行受控修复。"""
        semantic_candidates = parse_semantic_candidates(raw, instruction)
        if semantic_candidates and not raw.get("behavior_tree"):
            # Legacy transport adapter only.  Production code calls
            # semantic_candidates() and compiles through SemanticCompiler.
            from robot_intent_agent.planner.behavior_tree_generator import BehaviorTreeGenerator
            from robot_intent_agent.semantic_compiler import parsed_task_from_graph
            candidate = semantic_candidates[0]
            rule_bt = BehaviorTreeGenerator().generate_from_graph(
                candidate.graph, instruction=instruction
            )
            parsed = parsed_task_from_graph(candidate.graph, instruction)
            rule_bt.metadata.update({
                "semantic_candidates": [item.model_dump(mode="json") for item in semantic_candidates],
                "llm_semantic_candidate_count": len(semantic_candidates),
                "semantic_task_graph": candidate.graph.model_dump(mode="json"),
                "parsed_task": parsed.model_dump(mode="json"),
                "semantic_authority": "semantic_candidate_transport",
            })
            rule_bt.metadata.setdefault("engine_trace", {}).update({
                "llm_call_attempted": True,
                "llm_call_succeeded": True,
                "semantic_candidate_contract": "semantic-candidate-1.0",
                "final_execution_authority": "SemanticCompiler",
            })
            return rule_bt
        if not allow_legacy_compat and raw.get("behavior_tree"):
            raise LLMPlannerError(
                "Provider returned legacy behavior_tree; only semantic-candidate-1.0 "
                "responses are accepted on the production path"
            )
        bt_json = raw.get("behavior_tree")
        if not bt_json:
            raise LLMPlannerError("DeepSeek 返回的 JSON 缺少 behavior_tree 字段")

        # 防御: DeepSeek 可能把 behavior_tree 包成 [{...}]
        while isinstance(bt_json, list) and len(bt_json) > 0:
            bt_json = bt_json[0]
        if not isinstance(bt_json, dict):
            raise LLMPlannerError(f"behavior_tree 类型错误: 期望 dict, 实际 {type(bt_json).__name__}")

        # 递归构建 BTNode
        root = self._build_bt_node(bt_json)

        # 验证所有技能都在 SkillCatalog 中
        if not semantic_only:
            self._validate_skills(root)

        # ── 提取元数据 ──
        target = raw.get("target", "")
        modifiers = raw.get("modifiers", [])
        avoid_objects = raw.get("avoid_objects", [])
        description = raw.get("task_description", instruction)

        # ── IntentFrame v1 验证 ──
        raw_intent_frame = raw.get("intent_frame")
        parsed_task_data: Optional[Dict[str, Any]] = None
        intent_frame_valid = False

        if isinstance(raw_intent_frame, dict):
            try:
                from robot_intent_agent.schemas.intent_frame import IntentFrame
                repaired_frame = _repair_intent_frame_wire(raw_intent_frame)
                validated_frame = IntentFrame.model_validate(repaired_frame)
                # Normalize for downstream compatibility
                parsed_task_data = normalize_intent_frame(validated_frame)
                intent_frame_valid = True
                logger.info(f"IntentFrame v1 validated: action={validated_frame.action.value}")
            except Exception as e:
                logger.warning(f"IntentFrame v1 validation failed: {e}")
                # Fallback: try legacy parsed_task format
                raw_parsed_task = raw.get("parsed_task")
                if isinstance(raw_parsed_task, dict):
                    parsed_task_data = raw_parsed_task
                    logger.info("Falling back to legacy parsed_task format")
        else:
            # Try legacy parsed_task
            raw_parsed_task = raw.get("parsed_task")
            if isinstance(raw_parsed_task, dict):
                parsed_task_data = raw_parsed_task
                logger.info("No intent_frame found, using legacy parsed_task")

        # Also accept legacy format
        if parsed_task_data is None:
            raw_parsed_task = raw.get("parsed_task")
            if isinstance(raw_parsed_task, dict):
                parsed_task_data = raw_parsed_task

        semantic_frame_version = "1.0" if intent_frame_valid else "legacy"

        # The original instruction is authoritative pipeline input.  Never
        # accept an empty or rewritten instruction from the model.
        if isinstance(parsed_task_data, dict):
            parsed_task_data["instruction"] = instruction

        metadata: Dict[str, Any] = {
            "action": raw.get("action_type") or (validated_frame.action.value if intent_frame_valid else "custom"),
            "target": target,
            "modifiers": modifiers,
            "avoid_objects": avoid_objects,
            "planner": "LLMPlanner",
            "llm_model": self._model,
            "semantic_frame_version": semantic_frame_version,
            "engine_trace": {
                "requested_engine": "DeepSeek",
                "actual_engine": "LLMPlanner",
                "model_name": self._model,
                "llm_call_attempted": True,
                "llm_call_succeeded": True,
                "response_schema_valid": intent_frame_valid,
                "repair_attempted": False,
                "repair_succeeded": False,
                "fallback_used": False,
                "fallback_reason": None,
            },
        }

        # 传递完整 parsed_task
        if isinstance(parsed_task_data, dict):
            metadata["parsed_task"] = parsed_task_data
            logger.info(f"DeepSeek provided parsed_task with action={parsed_task_data.get('action')}")

        bt = BehaviorTree(
            task_id=f"task-llm-{hash(instruction) % 10000:04d}",
            description=description,
            root=root,
            metadata=metadata,
        )

        self._validate_semantic_contract(bt, parsed_task_data, semantic_only=semantic_only)

        return bt

    @staticmethod
    def _validate_semantic_contract(
        bt: BehaviorTree, parsed_task_data: Optional[Dict[str, Any]], semantic_only: bool = False
    ) -> None:
        """Reject structurally valid but semantically incomplete LLM plans.

        The LLM may understand the instruction correctly while emitting a BT
        that drops a required action or prohibition.  Such a result must not
        enter IR generation; raising LLMPlannerError activates the established
        deterministic rule fallback.
        """
        if not isinstance(parsed_task_data, dict):
            raise LLMPlannerError("LLM semantic contract missing parsed_task")

        action = str(parsed_task_data.get("action") or "CUSTOM").upper()
        if semantic_only:
            if parsed_task_data.get("theme") is None and action != "CUSTOM":
                raise LLMPlannerError(f"LLM semantic contract missing theme for {action}")
            return
        skills = {a.skill_name for a in bt.root.flatten_actions()}
        required_skills = {
            "GRASP": {"Grasp"},
            "FETCH": {"Fetch"},
            "PLACE": {"Place"},
            "HANDOVER": {"Handover"},
            "TRANSFER": {"Transfer"},
            "DYNAMIC_GRASP": {"DynamicGrasp", "WaitUntilStable"},
        }
        missing_skills = required_skills.get(action, set()) - skills
        if missing_skills:
            raise LLMPlannerError(
                f"LLM BT missing required skills for {action}: {sorted(missing_skills)}"
            )

        if parsed_task_data.get("theme") is None:
            raise LLMPlannerError(f"LLM semantic contract missing theme for {action}")

        if action == "PLACE" and parsed_task_data.get("support_surface") is None:
            raise LLMPlannerError("LLM semantic contract missing support_surface for PLACE")

        if action == "HANDOVER" and parsed_task_data.get("recipient") is None:
            raise LLMPlannerError("LLM semantic contract missing recipient for HANDOVER")
        if action in {"FETCH", "TRANSFER"} and parsed_task_data.get("destination") is None:
            raise LLMPlannerError(f"LLM semantic contract missing destination for {action}")

        obstacles = [o for o in (parsed_task_data.get("obstacle") or []) if o]
        if obstacles and not ({"Avoid", "PlanPath"} & skills):
            raise LLMPlannerError(
                "LLM BT dropped obstacle semantics: no Avoid/PlanPath enforcement"
            )
        if obstacles:
            obstacle_mentions = {
                str(o.get("mention") or o.get("specific_class") or "").strip()
                for o in obstacles if isinstance(o, dict)
            }
            obstacle_mentions.discard("")
            enforcement_found = False
            for action_node in bt.root.flatten_actions():
                if action_node.skill_name not in {"Avoid", "PlanPath"}:
                    continue
                params = action_node.params or {}
                raw_targets = (
                    params.get("avoid_obstacles") or params.get("avoid_objects")
                    or params.get("avoid") or params.get("obstacles") or []
                )
                if isinstance(raw_targets, str):
                    raw_targets = [raw_targets]
                enforced_targets = {str(x).strip() for x in raw_targets if str(x).strip()}
                action_target = str(getattr(action_node, "target", "") or "").strip()
                if action_target:
                    enforced_targets.add(action_target)
                if enforced_targets and (
                    not obstacle_mentions
                    or any(
                        expected in actual or actual in expected
                        for expected in obstacle_mentions for actual in enforced_targets
                    )
                ):
                    enforcement_found = True
                    break
            if not enforcement_found:
                raise LLMPlannerError(
                    "LLM BT contains an avoidance skill but does not pass the parsed obstacle to it"
                )

    def _build_bt_node(self, node_json) -> BTNode:
        """递归构建 BTNode (带类型防御)"""
        # 防御: 无论 DeepSeek 包了多少层 list, 循环剥开
        while isinstance(node_json, list) and len(node_json) > 0:
            node_json = node_json[0]
        if not isinstance(node_json, dict):
            raise LLMPlannerError(
                f"BT node 类型错误: 期望 dict, 实际 {type(node_json).__name__}: "
                f"{str(node_json)[:100]}"
            )
        node_type_str = node_json.get("type", "action")

        # 映射类型
        type_map = {
            "sequence": BTNodeType.SEQUENCE,
            "fallback": BTNodeType.FALLBACK,
            "parallel": BTNodeType.PARALLEL,
            "action": BTNodeType.ACTION,
            "condition": BTNodeType.CONDITION,
        }
        node_type = type_map.get(node_type_str, BTNodeType.ACTION)

        # 构建 SkillAction（仅 action 节点）
        skill = None
        if node_type == BTNodeType.ACTION:
            skill_name = node_json.get("skill_name", "Reach")
            target = node_json.get("target", "")
            params = node_json.get("params", {})
            # 防御: params 也可能是列表
            while isinstance(params, list) and len(params) > 0:
                params = params[0]
            if not isinstance(params, dict):
                params = {}

            # 参数清理：去掉可能的多余字段
            clean_params = {}
            for k, v in params.items():
                if isinstance(v, (str, int, float, bool)):
                    clean_params[k] = v
                elif isinstance(v, dict):
                    clean_params[k] = v

            skill = SkillAction(
                skill_name=skill_name,
                target=target,
                params=clean_params,
            )

        # 构建 ConditionCheck（仅 condition 节点）
        condition = None
        if node_type == BTNodeType.CONDITION:
            condition = ConditionCheck(
                condition=node_json.get("condition", "path_clear"),
                target=node_json.get("target", ""),
                expected=node_json.get("expected", True),
            )

        # 递归构建子节点
        children = []
        for child_json in node_json.get("children", []):
            children.append(self._build_bt_node(child_json))

        return BTNode(
            type=node_type,
            name=node_json.get("name", ""),
            children=children,
            skill=skill,
            condition=condition,
            annotation=node_json.get("annotation", ""),
        )

    def _validate_skills(self, root: BTNode) -> None:
        """验证所有 Action 的技能名都在 SkillCatalog 中。未知技能 → 拒绝 BT。"""
        invalid = []

        def _check(node: BTNode):
            if node.type == BTNodeType.ACTION and node.skill:
                skill_name = node.skill.skill_name
                try:
                    SkillCatalog.get(skill_name)
                except KeyError:
                    invalid.append(skill_name)
            for child in node.children:
                _check(child)

        _check(root)

        if invalid:
            raise LLMPlannerError(
                f"DeepSeek returned {len(invalid)} unknown skills: {invalid}. "
                f"BT rejected — unknown skills cannot enter executable IR."
            )


# ============================================================
# 混合路由：规则优先 + LLM 兜底
# ============================================================

class HybridRouter:
    """
    混合规划路由器。

    策略:
        1. 先用 RuleBasedPlanner 解析
        2. 如果置信度低（指令复杂/未覆盖）→ 切流给 LLM
        3. LLM 失败 → 降级回规则引擎结果
    """

    def __init__(self, llm_planner: Optional[LLMPlanner] = None):
        from robot_intent_agent.planner.behavior_tree_generator import BehaviorTreeGenerator
        self._rule_planner = BehaviorTreeGenerator()
        self._llm_planner = llm_planner
        self._settings = get_settings()

    @staticmethod
    def _record_engine_trace(bt: BehaviorTree, *, requested: str,
                             actual: str, fallback: bool = False,
                             reason: Optional[str] = None) -> BehaviorTree:
        """Attach a uniform, auditable engine decision to every BT result."""
        metadata = bt.metadata if isinstance(bt.metadata, dict) else {}
        metadata["planner"] = actual
        trace = metadata.get("engine_trace")
        if not isinstance(trace, dict):
            trace = {}
        trace.update({
            "requested_engine": requested,
            "actual_engine": actual,
            "llm_call_attempted": bool(trace.get("llm_call_attempted", False)),
            "llm_call_succeeded": bool(trace.get("llm_call_succeeded", False)),
            "fallback_used": fallback,
            "fallback_reason": reason,
        })
        metadata["engine_trace"] = trace
        bt.metadata = metadata
        return bt

    def _deterministicize_llm_tree(self, instruction: str, llm_bt: BehaviorTree,
                                   scene: Optional[SemanticSceneGraph],
                                   memory_context: Optional[List[Dict[str, Any]]]) -> BehaviorTree:
        """Keep LLM semantics but rebuild executable BT deterministically."""
        rule_bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
        llm_metadata = llm_bt.metadata if isinstance(llm_bt.metadata, dict) else {}
        rule_metadata = rule_bt.metadata if isinstance(rule_bt.metadata, dict) else {}
        llm_parsed = llm_metadata.get("parsed_task")
        rule_parsed = rule_metadata.get("parsed_task")
        if not isinstance(rule_parsed, dict):
            # BehaviorTreeGenerator does not need to carry semantic metadata
            # for normal rule execution. Build the same deterministic
            # baseline explicitly so the gain gate cannot be bypassed.
            try:
                from robot_intent_agent.task_semantics import parse_task_semantics
                rule_parsed = parse_task_semantics(instruction, scene=scene).model_dump(mode="json")
            except Exception as exc:
                logger.warning("Unable to build negative-gain baseline: %s", exc)
        negative_gain_reasons = self._negative_gain_reasons(rule_parsed, llm_parsed)
        if isinstance(llm_parsed, dict) and not negative_gain_reasons:
            rule_metadata["parsed_task"] = llm_parsed
        # New semantic-candidate path: fuse graph atoms for audit/diagnostics,
        # then keep the deterministic rule task as execution baseline.
        semantic_candidates = llm_metadata.get("semantic_candidates") or []
        if semantic_candidates:
            try:
                from robot_intent_agent.semantic_parser.rule_semantic_parser import RuleSemanticParser
                from robot_intent_agent.semantic_reasoner.semantic_fusion import SemanticFusion
                from robot_intent_agent.schemas.semantic_task_graph import SemanticCandidate
                rule_candidate = RuleSemanticParser().parse(instruction, scene=scene)
                llm_candidate = SemanticCandidate.model_validate(semantic_candidates[0])
                fused, audit = SemanticFusion().fuse(rule_candidate, llm_candidate, instruction)
                rule_metadata["semantic_task_graph"] = fused.graph.model_dump(mode="json")
                rule_metadata["fusion_trace"] = [record.model_dump() for record in audit]
                rule_metadata["semantic_candidate_count"] = len(semantic_candidates)
            except Exception as exc:
                logger.warning("Semantic candidate fusion failed; keeping rule baseline: %s", exc)
        if llm_metadata.get("semantic_frame_version"):
            rule_metadata["semantic_frame_version"] = llm_metadata["semantic_frame_version"]
        # Preserve the LLM provenance so downstream loaders execute the
        # rule-baseline + LLM-field fusion boundary instead of treating the
        # rebuilt tree as a pure rule result and silently bypassing fusion.
        llm_trace = dict(llm_metadata.get("engine_trace") or {})
        llm_trace.update({
            "requested_engine": "hybrid",
            "actual_engine": llm_metadata.get("planner", "LLMPlanner"),
            "llm_call_attempted": True,
            "llm_call_succeeded": True,
        })
        if negative_gain_reasons:
            llm_trace.update({
                "llm_fusion_rejected": True,
                "llm_rejection_reasons": negative_gain_reasons,
                "llm_rejection_policy": "negative_gain_protection",
                "final_semantics_source": "rule_baseline",
            })
            rule_metadata["planner"] = "RuleEngine"
            rule_metadata["semantic_frame_version"] = None
        else:
            llm_trace.update({
                "llm_fusion_rejected": False,
                "final_semantics_source": "rule_baseline+validated_llm_delta",
            })
        rule_metadata["engine_trace"] = llm_trace
        rule_metadata["llm_semantics_attached"] = not bool(negative_gain_reasons)
        rule_metadata["llm_bt_discarded"] = True
        rule_bt.metadata = rule_metadata
        return rule_bt

    @staticmethod
    def _negative_gain_reasons(rule_task: Any, llm_task: Any) -> List[str]:
        """Reject an LLM candidate when it regresses verified rule evidence."""
        if not isinstance(rule_task, dict) or not isinstance(llm_task, dict):
            return ["MISSING_SEMANTIC_BASELINE"] if isinstance(rule_task, dict) else []

        reasons: List[str] = []

        def nonempty(value: Any) -> bool:
            return value not in (None, "", [], {})

        # Safety and executable structure are protected fields.  The LLM may
        # enrich them, but it may never erase a rule-established record.
        for field in ("obstacle", "user_constraints", "prohibitions", "conditions", "steps"):
            baseline = rule_task.get(field) or []
            candidate = llm_task.get(field) or []
            if baseline and not candidate:
                reasons.append(f"DROPPED_RULE_{field.upper()}")

        def mentions(items: Any) -> set[str]:
            values = set()
            for item in items or []:
                if isinstance(item, dict):
                    value = item.get("mention") or item.get("target") or item.get("text_span")
                else:
                    value = getattr(item, "mention", None) or str(item)
                if value:
                    values.add(str(value).strip())
            return values

        rule_obstacles = mentions(rule_task.get("obstacle"))
        llm_obstacles = mentions(llm_task.get("obstacle"))
        if rule_obstacles and rule_obstacles != llm_obstacles:
            reasons.append("CHANGED_RULE_OBSTACLES")

        def constraint_signature(items: Any) -> set[tuple]:
            result = set()
            for item in items or []:
                if isinstance(item, dict):
                    result.add((item.get("parameter"), item.get("operator"),
                                item.get("value"), item.get("min_value"), item.get("max_value"),
                                item.get("unit")))
                else:
                    result.add((getattr(item, "parameter", None), getattr(item, "operator", None),
                                getattr(item, "value", None), getattr(item, "min_value", None),
                                getattr(item, "max_value", None), getattr(item, "unit", None)))
            return result

        rule_constraints = constraint_signature(rule_task.get("user_constraints"))
        llm_constraints = constraint_signature(llm_task.get("user_constraints"))
        if rule_constraints and not rule_constraints.issubset(llm_constraints):
            reasons.append("DROPPED_OR_CHANGED_RULE_CONSTRAINTS")

        rule_steps = rule_task.get("steps") or []
        llm_steps = llm_task.get("steps") or []
        if len(rule_steps) > len(llm_steps):
            reasons.append("DROPPED_RULE_SEQUENCE_STEPS")

        return list(dict.fromkeys(reasons))

    def plan(
        self,
        instruction: str,
        scene: Optional[SemanticSceneGraph] = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
    ) -> BehaviorTree:
        """
        混合规划。

        Returns:
            BehaviorTree（来自规则或 LLM）
        """
        engine = self._settings.planner_engine or "rule"
        if engine not in {"rule", "llm", "hybrid"}:
            engine = "rule"
        # Production authority: providers emit candidates, SemanticCompiler
        # fuses/grounds them and the graph compiler emits the BT.
        from robot_intent_agent.semantic_compiler import SemanticCompiler
        result = SemanticCompiler(self._llm_planner).compile(
            instruction,
            scene=scene,
            memory_context=memory_context,
            mode=engine,
        )
        bt = result.behavior_tree
        trace = result.engine_trace
        return self._record_engine_trace(
            bt,
            requested=engine,
            actual=str(trace.get("actual_engine", "RuleSemanticCompiler")),
            fallback=bool(trace.get("fallback_used")),
            reason=trace.get("fallback_reason"),
        )

        # ── 模式 1: 纯规则 ──
        if engine == "rule":
            bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
            return self._record_engine_trace(bt, requested="rule", actual="RuleEngine")

        # ── 模式 2: 纯 LLM ──
        if engine == "llm":
            if not self._llm_planner or not self._llm_planner.is_available:
                logger.warning("LLM engine selected but not available, falling back to rule")
                bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
                return self._record_engine_trace(bt, requested="llm", actual="RuleEngine",
                                                 fallback=True, reason="llm_unavailable")
            try:
                llm_bt = self._llm_planner.plan(instruction, scene=scene, memory_context=memory_context)
                bt = self._deterministicize_llm_tree(instruction, llm_bt, scene, memory_context)
                return self._record_engine_trace(bt, requested="llm", actual=bt.metadata.get("planner", "LLM"))
            except LLMPlannerError as e:
                logger.warning(f"LLM planner failed: {e}, falling back to rule")
                bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
                return self._record_engine_trace(bt, requested="llm", actual="RuleEngine",
                                                 fallback=True, reason=str(e))

        # ── 模式 3: Hybrid（规则优先 + LLM 兜底）──
        if engine == "hybrid":
            # 先用规则引擎
            rule_bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
            diagnostics = self._semantic_completeness(instruction, rule_bt, scene)
            force_llm = bool(diagnostics.get("force_llm"))

            if not force_llm:
                logger.info("Semantic completeness is sufficient; using deterministic rule result")
                return self._record_engine_trace(rule_bt, requested="hybrid", actual="RuleEngine",
                                                 reason="semantic_complete")

            # Incomplete semantic atoms trigger LLM candidate generation.
            logger.info("Semantic completeness requires LLM candidate review: %s", diagnostics)
            if self._llm_planner and self._llm_planner.is_available:
                try:
                    llm_bt = self._llm_planner.plan(
                        instruction, scene=scene, memory_context=memory_context,
                        semantic_only=True,
                    )
                    bt = self._deterministicize_llm_tree(instruction, llm_bt, scene, memory_context)
                    return self._record_engine_trace(bt, requested="hybrid", actual=bt.metadata.get("planner", "LLM"))
                except LLMPlannerError as e:
                    logger.warning(f"LLM fallback failed: {e}, using rule result")
                    return self._record_engine_trace(rule_bt, requested="hybrid", actual="RuleEngine",
                                                     fallback=True, reason=str(e))
            return self._record_engine_trace(rule_bt, requested="hybrid", actual="RuleEngine",
                                             fallback=True, reason="llm_unavailable")

        # 默认：规则
        bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
        return self._record_engine_trace(bt, requested=engine or "unknown", actual="RuleEngine",
                                         fallback=True, reason="unknown_engine")

    @staticmethod
    def _estimate_rule_confidence(instruction: str, bt: BehaviorTree) -> float:
        """
        估算规则引擎对当前指令的置信度。

        低置信度信号:
            - 目标物体为 "unknown"
            - 指令过长（>30 字）→ 可能是复杂长指令
            - 行为树只有默认动作（无修饰语匹配）
            - 无 Avoid 节点但指令含 "别"（规则漏匹配）
        """
        score = 0.8  # 基础分
        parsed = bt.metadata.get("parsed_task", {}) if isinstance(bt.metadata, dict) else {}
        # Semantic completeness gates: these cases require LLM review and
        # are still validated by deterministic fusion downstream.
        if any((
            bool(re.search(r"先|再|然后|之后|完成后|等待|直到|如果|除非|并且|同时", instruction)),
            bool(re.search(r"否则|不然|一旦|只有|除非", instruction)),
            len(re.findall(r"\d+(?:\.\d+)?", instruction)) >= 2,
            parsed.get("action") == "CUSTOM",
            bool(parsed.get("clarification")),
            bool(parsed.get("unmet_roles")),
            bool(parsed.get("conditions")),
            len(parsed.get("steps") or []) > 1,
            any(str(note).startswith("ambiguity:") for note in (parsed.get("notes") or [])),
            bool(re.search(r"给|递交|交给|上料|检测区|工位|料箱|传送带|夹具", instruction)),
        )):
            return 0.0

        # 目标未识别 → 降分
        target = bt.metadata.get("target", "")
        if not target or target == "unknown":
            score -= 0.4

        # 指令过长 → 可能是复杂指令
        if len(instruction) > 30:
            score -= 0.15

        # 有修饰语但没匹配到 → 降分
        modifier_keywords = ["轻", "慢", "快", "小心", "用力", "别碰", "不要碰", "避开", "千万"]
        for kw in modifier_keywords:
            if kw in instruction:
                matched = any(kw in str(bt.metadata.get("modifiers", [])) for _ in [1])
                if not matched:
                    score -= 0.05

        return max(0.0, min(1.0, score))

    @staticmethod
    def _semantic_completeness(instruction: str, bt: BehaviorTree, scene=None) -> Dict[str, Any]:
        """Route on semantic completeness, not an arbitrary confidence score."""
        from robot_intent_agent.semantic_parser.semantic_pipeline import SemanticPipeline
        candidate = SemanticPipeline().parse_rule(instruction, scene=scene)
        diagnostics = SemanticPipeline().diagnostics(candidate, scene=scene)
        text = instruction or ""
        explicit_force = any((
            candidate.graph.metadata.get("action_candidates") and any(item.get("value") == "CUSTOM" for item in candidate.graph.metadata.get("action_candidates", [])),
            bool(re.search(r"(?:如果|否则|不然|除非|直到|等待|先|再|然后)", text)),
            bool(re.search(r"(?:它|这个|那个|其)", text)),
            bool(re.search(r"(?:左边|右边|前面|后面|最左|最右)", text) and not candidate.graph.entities),
            len(candidate.graph.events) > 1,
            bool(candidate.graph.prohibitions and any(not item.target_ref for item in candidate.graph.prohibitions)),
            any(event.action == "CUSTOM" for event in candidate.graph.events),
        ))
        diagnostics["force_llm"] = bool(explicit_force or not diagnostics.get("action_complete")
                                         or not diagnostics.get("roles_complete")
                                         or diagnostics.get("semantic_conflicts"))
        return diagnostics


# ============================================================
# 异常类型
# ============================================================

class LLMPlannerError(Exception):
    """LLM 规划器异常"""
    pass
