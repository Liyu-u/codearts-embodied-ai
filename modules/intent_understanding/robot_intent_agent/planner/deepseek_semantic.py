"""
DeepSeek Semantic Parsing — two-stage, ProgPrompt-style, safety-bounded.

Architecture:
  Stage A: NL instruction + object catalog + action signatures + examples
           → DeepSeek (temp=0, json_mode) → SemanticDescriptor
  Stage B: If Stage A fails schema/mention/role validation
           → Repair prompt with errors → corrected SemanticDescriptor
  Post:    Schema validation → mention validation → role validation
           → EntityGrounder → Constraint compiler → FinalPlanValidator

DeepSeek NEVER decides: execution_allowed, final force, final velocity, plan_status.
All safety-critical values come from the deterministic RuleEngine pipeline.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field, ValidationError

from robot_intent_agent.config.settings import get_settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# SemanticDescriptor — the ONLY output DeepSeek is allowed to produce
# ══════════════════════════════════════════════════════════════

class SemanticRole(BaseModel):
    """One grounded or ungrounded role mention."""
    role: str = Field(..., description="theme | destination | support_surface | recipient | source | obstacle")
    mention: str = Field(default="", description="Text span from instruction")
    object_id: Optional[str] = Field(default=None, description="object_id from catalog, or null if uncertain")
    specific_class: Optional[str] = Field(default=None)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SemanticConstraint(BaseModel):
    """One numeric or qualitative constraint."""
    parameter: str = Field(..., description="force_n | velocity_ms | grip_style | height_m | ...")
    operator: str = Field(default="exact", description="exact | min | max | range | recommended")
    value: Optional[float] = Field(default=None)
    min_value: Optional[float] = Field(default=None)
    max_value: Optional[float] = Field(default=None)
    unit: str = Field(default="")
    text_span: str = Field(default="")


class SemanticCondition(BaseModel):
    """One conditional or sequential structure."""
    type: str = Field(..., description="IF_ELSE | UNLESS | BEFORE | AFTER | WAIT_UNTIL")
    condition_text: str = Field(default="")
    condition_predicate: Optional[str] = Field(default=None, description="GRIPPER_EMPTY | VISIBLE | OBJECT_MOVING | ...")
    condition_subject: Optional[str] = Field(default=None)
    then_action: str = Field(default="")
    else_action: str = Field(default="")
    raw_text: str = Field(default="")


class SemanticDescriptor(BaseModel):
    """The ONLY structured output DeepSeek is allowed to produce.

    This is NOT a BehaviorTree. It is a semantic parse that flows into
    the deterministic RuleEngine pipeline for grounding, constraint
    resolution, and validation.
    """

    action_candidates: List[str] = Field(
        default_factory=list,
        description="Ordered action kind candidates, e.g. ['FETCH', 'GRASP']")

    roles: Dict[str, SemanticRole] = Field(
        default_factory=dict,
        description="role_name → SemanticRole. Keys: theme, destination, "
                    "support_surface, recipient, source")

    avoid: List[SemanticRole] = Field(
        default_factory=list,
        description="Objects explicitly negated / to-be-avoided")

    conditions: List[SemanticCondition] = Field(default_factory=list)
    sequence: List[SemanticCondition] = Field(default_factory=list)

    constraints: List[SemanticConstraint] = Field(default_factory=list)
    manner: List[str] = Field(default_factory=list, description="gentle | fast | careful")
    uncertainties: List[str] = Field(default_factory=list,
        description="Things the model is uncertain about")

    parse_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ══════════════════════════════════════════════════════════════
# Action signatures — what each action requires
# ══════════════════════════════════════════════════════════════

ACTION_SIGNATURES: Dict[str, Dict[str, Any]] = {
    "GRASP": {
        "description": "抓取一个物体",
        "required_roles": ["theme"],
        "optional_roles": [],
        "example": "抓住杯子",
    },
    "FETCH": {
        "description": "拿取物体并送到指定区域",
        "required_roles": ["theme"],
        "optional_roles": ["delivery_zone"],
        "example": "把盒子拿过来",
    },
    "PLACE": {
        "description": "将物体放置到目标表面或容器",
        "required_roles": ["theme", "destination_or_support_surface"],
        "optional_roles": [],
        "example": "把杯子放到桌子上",
    },
    "HANDOVER": {
        "description": "将物体递交给接收者",
        "required_roles": ["theme", "recipient"],
        "optional_roles": ["handover_zone"],
        "example": "把药瓶递给用户",
    },
    "TRANSFER": {
        "description": "将物体从一处转移到另一处",
        "required_roles": ["theme", "source", "destination"],
        "optional_roles": [],
        "example": "把杯子从托盘移到桌子上",
    },
    "DYNAMIC_GRASP": {
        "description": "抓取正在移动的物体",
        "required_roles": ["theme"],
        "optional_roles": ["motion_condition"],
        "example": "抓住正在移动的红色小球",
    },
    "CUSTOM": {
        "description": "复合动作或特殊动作（翻转、倾倒等）",
        "required_roles": ["theme"],
        "optional_roles": ["sub_actions"],
        "example": "把杯子翻转过来",
    },
}


# ══════════════════════════════════════════════════════════════
# Object catalog builder — structured, no raw JSON dumps
# ══════════════════════════════════════════════════════════════

def build_object_catalog(
    scene: Any,
    perception_objects: Optional[List[Dict]] = None,
) -> List[Dict[str, Any]]:
    """Build a structured object catalog from scene + perception data.

    Each entry is a clean, LLM-friendly object descriptor.
    NO raw UUIDs, NO nested geometry — only what the LLM needs for semantic parsing.
    """
    catalog: List[Dict[str, Any]] = []

    # Build perception object_id → scene object mapping
    pid_to_scene: Dict[str, Any] = {}
    if scene:
        for obj in getattr(scene, "objects", []) or []:
            pid = (getattr(obj, "attributes", {}) or {}).get("_perception_object_id", "")
            if pid:
                pid_to_scene[pid] = obj

    # Use perception objects if available (they have clean object_ids)
    if perception_objects:
        for pobj in perception_objects:
            pid = pobj.get("object_id", "")
            cats = pobj.get("category_candidates", [])
            top_cat = cats[0].get("name", "unknown") if cats else "unknown"
            app = pobj.get("appearance", {}) or {}
            geom = pobj.get("geometry", {}).get("size", pobj.get("geometry", {})) or {}
            pos = pobj.get("pose", {}).get("position", {}) or {}

            entry = {
                "object_id": pid or "unknown",
                "category": top_cat,
                "color": app.get("color", "unknown"),
                "material": app.get("material", "unknown"),
                "size": _describe_size(geom),
                "motion_state": _get_motion_state(pobj),
                "affordances": pobj.get("affordances", []),
            }

            # Add spatial relations if scene is available
            scene_obj = pid_to_scene.get(pid)
            if scene_obj and scene:
                entry["position_relations"] = _get_position_relations(scene_obj, scene)
            else:
                entry["position_relations"] = []

            catalog.append(entry)

    elif scene:
        # Fallback: build from scene objects directly
        for obj in getattr(scene, "objects", []) or []:
            attrs = getattr(obj, "attributes", {}) or {}
            bbox = getattr(obj, "bbox", None)
            entry = {
                "object_id": getattr(obj, "id", "unknown"),
                "category": getattr(obj, "specific_class", "") or getattr(obj, "label", "") or getattr(obj, "name", "unknown"),
                "color": attrs.get("color", "unknown"),
                "material": attrs.get("material", "unknown"),
                "size": _describe_size_from_bbox(bbox),
                "motion_state": "stable",
                "affordances": [a.value if hasattr(a, 'value') else str(a) for a in getattr(obj, "affordances", [])],
                "position_relations": _get_position_relations(obj, scene),
            }
            catalog.append(entry)

    return catalog


def _describe_size(geom: Dict) -> str:
    """Describe size in human-readable terms."""
    if not geom:
        return "unknown"
    w = float(geom.get("width", 0.05))
    h = float(geom.get("height", 0.05))
    d = float(geom.get("depth", 0.05))
    vol = w * h * d
    if vol < 0.0001:
        return "tiny"
    elif vol < 0.0005:
        return "small"
    elif vol < 0.002:
        return "medium"
    else:
        return "large"


def _describe_size_from_bbox(bbox) -> str:
    if bbox is None:
        return "unknown"
    vol = getattr(bbox, "width", 0.05) * getattr(bbox, "height", 0.05) * getattr(bbox, "depth", 0.05)
    if vol < 0.0001:
        return "tiny"
    elif vol < 0.0005:
        return "small"
    elif vol < 0.002:
        return "medium"
    else:
        return "large"


def _get_motion_state(pobj: Dict) -> str:
    tracking = pobj.get("tracking", {}) or {}
    state = tracking.get("state", "stationary")
    if state == "moving":
        vel = tracking.get("velocity", {}) or {}
        speed = (vel.get("x", 0)**2 + vel.get("y", 0)**2 + vel.get("z", 0)**2)**0.5
        return f"moving({speed:.2f}m/s)"
    return "stable"


def _get_position_relations(obj: Any, scene: Any) -> List[str]:
    """Extract human-readable spatial relations for an object."""
    relations: List[str] = []
    if not scene:
        return relations
    obj_id = getattr(obj, "id", "")
    for rel in getattr(scene, "relations", []) or []:
        subj = getattr(rel, "subject", "")
        obj_name = getattr(rel, "object", "")
        pred = getattr(getattr(rel, "predicate", None), "value", None) or str(getattr(rel, "predicate", rel))
        if subj == obj_id:
            # Find the other object's name
            other = scene.find_object(obj_name) if hasattr(scene, 'find_object') else None
            other_name = getattr(other, "name", obj_name[:8]) if other else obj_name[:8]
            relations.append(f"{pred}:{other_name}")
    return relations


# ══════════════════════════════════════════════════════════════
# Example retriever — semantic category matching
# ══════════════════════════════════════════════════════════════

# Small frozen regression example bank (SemanticDescriptor only, no internal IDs)
_REGRESSION_EXAMPLES: Dict[str, List[Dict]] = {
    "multi_object_disambiguation": [
        {
            "instruction": "把大盒子拿过来",
            "descriptor": {
                "action_candidates": ["FETCH"],
                "roles": {"theme": {"role": "theme", "mention": "大盒子", "object_id": None, "specific_class": "box", "confidence": 0.85}},
                "avoid": [],
                "conditions": [],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": ["size_disambiguation: 需根据场景中所有盒子的大小选择最大的那个"],
                "parse_confidence": 0.85,
            },
        },
        {
            "instruction": "抓住中间那个杯子",
            "descriptor": {
                "action_candidates": ["GRASP"],
                "roles": {"theme": {"role": "theme", "mention": "中间那个杯子", "object_id": None, "specific_class": "cup", "confidence": 0.80}},
                "avoid": [],
                "conditions": [],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": ["spatial_disambiguation: 需根据场景中杯子的空间位置选择中间那个"],
                "parse_confidence": 0.80,
            },
        },
    ],
    "negation": [
        {
            "instruction": "不要碰那个红色的，把蓝色的拿过来",
            "descriptor": {
                "action_candidates": ["FETCH"],
                "roles": {"theme": {"role": "theme", "mention": "蓝色的", "object_id": None, "specific_class": None, "confidence": 0.85}},
                "avoid": [{"role": "obstacle", "mention": "那个红色的", "object_id": None, "specific_class": None, "confidence": 0.90}],
                "conditions": [],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": ["color_disambiguation: 蓝色vs红色，需根据场景对象颜色确定"],
                "parse_confidence": 0.90,
            },
        },
        {
            "instruction": "把盒子拿过来，别碰玻璃杯",
            "descriptor": {
                "action_candidates": ["FETCH"],
                "roles": {"theme": {"role": "theme", "mention": "盒子", "object_id": None, "specific_class": "box", "confidence": 0.90}},
                "avoid": [{"role": "obstacle", "mention": "玻璃杯", "object_id": None, "specific_class": "glass_cup", "confidence": 0.90}],
                "conditions": [],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": [],
                "parse_confidence": 0.90,
            },
        },
        {
            "instruction": "千万别碰玻璃杯，把盒子拿过来",
            "descriptor": {
                "action_candidates": ["FETCH"],
                "roles": {"theme": {"role": "theme", "mention": "盒子", "object_id": None, "specific_class": "box", "confidence": 0.90}},
                "avoid": [{"role": "obstacle", "mention": "玻璃杯", "object_id": None, "specific_class": "glass_cup", "confidence": 0.95}],
                "conditions": [],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": [],
                "parse_confidence": 0.92,
            },
        },
    ],
    "condition": [
        {
            "instruction": "除非夹爪是空的，否则不要抓取",
            "descriptor": {
                "action_candidates": ["GRASP"],
                "roles": {},
                "avoid": [],
                "conditions": [{"type": "UNLESS", "condition_text": "夹爪是空的", "condition_predicate": "GRIPPER_EMPTY", "then_action": "GRASP", "else_action": "禁止抓取"}],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": ["robot_state_dependent: 需要检查夹爪当前状态"],
                "parse_confidence": 0.85,
            },
        },
        {
            "instruction": "如果看到红色药瓶就先拿它，否则拿蓝色盒子",
            "descriptor": {
                "action_candidates": ["FETCH"],
                "roles": {
                    "theme": {"role": "theme", "mention": "红色药瓶或蓝色盒子", "object_id": None, "specific_class": None, "confidence": 0.70},
                },
                "avoid": [],
                "conditions": [{"type": "IF_ELSE", "condition_text": "看到红色药瓶", "condition_predicate": "VISIBLE", "condition_subject": "红色药瓶", "then_action": "FETCH 红色药瓶", "else_action": "FETCH 蓝色盒子"}],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": ["conditional_branching: 需要根据视觉输入选择分支"],
                "parse_confidence": 0.80,
            },
        },
    ],
    "roles": [
        {
            "instruction": "把杯子放到桌子左边",
            "descriptor": {
                "action_candidates": ["PLACE"],
                "roles": {
                    "theme": {"role": "theme", "mention": "杯子", "object_id": None, "specific_class": "cup", "confidence": 0.90},
                    "destination_or_support_surface": {"role": "support_surface", "mention": "桌子左边", "object_id": None, "specific_class": "table", "confidence": 0.85},
                },
                "avoid": [],
                "conditions": [],
                "sequence": [],
                "constraints": [],
                "manner": [],
                "uncertainties": ["spatial: 需要确定桌子左边的精确位置"],
                "parse_confidence": 0.88,
            },
        },
    ],
    "numeric": [
        {
            "instruction": "用5N抓住杯子",
            "descriptor": {
                "action_candidates": ["GRASP"],
                "roles": {"theme": {"role": "theme", "mention": "杯子", "object_id": None, "specific_class": "cup", "confidence": 0.90}},
                "avoid": [],
                "conditions": [],
                "sequence": [],
                "constraints": [{"parameter": "force_n", "operator": "exact", "value": 5.0, "unit": "N", "text_span": "5N"}],
                "manner": [],
                "uncertainties": [],
                "parse_confidence": 0.95,
            },
        },
    ],
}


def _detect_semantic_categories(instruction: str) -> List[str]:
    """Detect which semantic categories apply to an instruction."""
    cats = []
    normalized = unicodedata.normalize("NFKC", instruction)

    # Multi-object disambiguation cues
    if any(c in normalized for c in ["大", "小", "红", "蓝", "绿", "黄", "白", "黑", "左", "右", "中间", "前", "后", "那个", "这个"]):
        cats.append("multi_object_disambiguation")

    # Negation cues
    if any(c in normalized for c in ["别碰", "不要碰", "千万别碰", "避开", "绕过", "不想碰", "不能碰", "除了"]):
        cats.append("negation")

    # Condition cues
    if any(c in normalized for c in ["如果", "否则", "除非", "要不", "只有"]):
        cats.append("condition")

    # Role cues
    if any(c in normalized for c in ["放到", "递给", "交给", "搬到", "放到", "放在"]):
        cats.append("roles")

    # Numeric cues
    if re.search(r'\d+(?:\.\d+)?\s*(?:N|牛顿|m/s)', normalized):
        cats.append("numeric")

    if not cats:
        cats.append("multi_object_disambiguation")  # default fallback

    return cats


def retrieve_examples(instruction: str, max_examples: int = 5) -> List[Dict]:
    """Retrieve 3-5 examples matching the instruction's semantic categories."""
    cats = _detect_semantic_categories(instruction)
    selected: List[Dict] = []
    seen_instructions: Set[str] = set()

    for cat in cats:
        examples = _REGRESSION_EXAMPLES.get(cat, [])
        for ex in examples:
            instr = ex.get("instruction", "")
            if instr not in seen_instructions and len(selected) < max_examples:
                selected.append(ex)
                seen_instructions.add(instr)

    # Ensure at least 3 examples
    if len(selected) < 3:
        all_examples = []
        for cat_examples in _REGRESSION_EXAMPLES.values():
            all_examples.extend(cat_examples)
        for ex in all_examples:
            instr = ex.get("instruction", "")
            if instr not in seen_instructions and len(selected) < max_examples:
                selected.append(ex)
                seen_instructions.add(instr)

    return selected[:max_examples]


# ══════════════════════════════════════════════════════════════
# Prompt builders
# ══════════════════════════════════════════════════════════════

def _build_system_prompt() -> str:
    """Build the Stage A system prompt with action signatures and rules."""
    action_lines = []
    for name, sig in ACTION_SIGNATURES.items():
        req = ", ".join(sig["required_roles"])
        opt = ", ".join(sig["optional_roles"]) if sig["optional_roles"] else "无"
        action_lines.append(
            f"- **{name}**({req}{', [' + opt + ']' if opt != '无' else ''}): {sig['description']}"
        )

    return f"""你是一个具身智能机器人的语义解析器。你的唯一职责是：将用户自然语言指令解析为结构化的 SemanticDescriptor JSON。

## 可用动作及其角色要求

{chr(10).join(action_lines)}

## 输出格式（严格遵守）

你必须输出一个 JSON 对象，结构如下：

```json
{{
  "action_candidates": ["GRASP"],
  "roles": {{
    "theme": {{"role": "theme", "mention": "杯子", "object_id": null, "specific_class": "cup", "confidence": 0.90}}
  }},
  "avoid": [],
  "conditions": [],
  "sequence": [],
  "constraints": [],
  "manner": [],
  "uncertainties": [],
  "parse_confidence": 0.90
}}
```

## 字段说明

- **action_candidates**: 动作候选列表（按可能性排序），值必须是上述动作名之一
- **roles**: 角色 → 实体映射。key 为 role 名。每个实体必须包含 role, mention, object_id(可为null), specific_class(可为null), confidence
- **avoid**: 被否定的对象列表（"别碰X"、"不要碰Y"中的 X 和 Y）。结构同 roles
- **conditions**: 条件结构列表。每项含 type(IF_ELSE|UNLESS), condition_text, condition_predicate(GRIPPER_EMPTY|VISIBLE|OBJECT_MOVING|...), condition_subject, then_action, else_action
- **sequence**: 顺序结构列表。每项含 type(BEFORE|AFTER|SIMULTANEOUS), then_action, else_action
- **constraints**: 数值约束列表。每项含 parameter(force_n|velocity_ms), operator(exact|min|max|range), value, min_value, max_value, unit, text_span
- **manner**: 方式修饰词列表 ["gentle", "fast", "careful"]
- **uncertainties**: 不确定项列表（模型对什么不确定）
- **parse_confidence**: 0.0-1.0 解析信心

## 绝对禁止

1. 不要输出 Markdown 代码块标记（```json）
2. 不要输出任何解释性文字
3. 不要输出 behavior_tree
4. 不要决定 execution_allowed
5. 不要决定最终 force 值（只提取用户明确说的数值）
6. 不要创造 object_id（如果不确定，设为 null）
7. 只输出纯 JSON"""


def _build_stage_a_user_message(
    instruction: str,
    object_catalog: List[Dict],
    robot_state: Optional[Dict] = None,
    examples: Optional[List[Dict]] = None,
) -> str:
    """Build the Stage A user message."""
    parts = [
        "## 用户指令",
        instruction,
        "",
        "## 场景对象目录",
        "以下对象可供参考。object_id 只能从下面列表中选择，不可创造新 ID。",
        "如果 mention 无法对应到具体对象，将 object_id 设为 null。",
        "```json",
        json.dumps(object_catalog, ensure_ascii=False, indent=2),
        "```",
    ]

    if robot_state:
        parts.extend([
            "",
            "## 机器人状态",
            "```json",
            json.dumps(robot_state, ensure_ascii=False),
            "```",
        ])

    if examples:
        parts.extend([
            "",
            "## 参考示例（仅展示 SemanticDescriptor 结构）",
        ])
        for i, ex in enumerate(examples):
            parts.extend([
                f"### 示例 {i+1}: {ex.get('instruction', '')}",
                "```json",
                json.dumps(ex.get("descriptor", {}), ensure_ascii=False, indent=2),
                "```",
            ])

    parts.extend([
        "",
        "请输出 SemanticDescriptor JSON（纯 JSON，不要 Markdown 包裹）：",
    ])

    return "\n".join(parts)


def _build_stage_b_user_message(
    original_instruction: str,
    stage_a_output: Dict,
    errors: List[str],
    object_catalog: List[Dict],
) -> str:
    """Build the Stage B repair prompt."""
    return "\n".join([
        "## 原始指令",
        original_instruction,
        "",
        "## 你上一轮的输出",
        "```json",
        json.dumps(stage_a_output, ensure_ascii=False, indent=2),
        "```",
        "",
        "## 验证错误（必须修正）",
        *[f"- {e}" for e in errors],
        "",
        "## 可用对象列表（object_id 只能从下面选）",
        "```json",
        json.dumps([{"object_id": o["object_id"], "category": o["category"],
                      "color": o.get("color", ""), "affordances": o.get("affordances", [])}
                     for o in object_catalog], ensure_ascii=False, indent=2),
        "```",
        "",
        "请修正以上错误，重新输出完整的 SemanticDescriptor JSON（纯 JSON）：",
    ])


# ══════════════════════════════════════════════════════════════
# Schema validation — deterministic post-processing gate
# ══════════════════════════════════════════════════════════════

@dataclass
class ValidationReport:
    """Result of validating a SemanticDescriptor against schema + scene."""
    valid: bool = True
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def validate_descriptor(
    descriptor: Dict[str, Any],
    object_catalog: List[Dict],
) -> ValidationReport:
    """Deterministic validation of a SemanticDescriptor.

    Checks:
      1. Schema: required fields present, types correct
      2. Mention: every role.mention is non-empty
      3. Object IDs: every object_id exists in catalog (or is None)
      4. Roles: action_candidates are valid, required roles present
      5. Conditions: condition_predicate is a known value or null
      6. Constraints: numeric values are finite
    """
    report = ValidationReport()
    valid_ids = {o["object_id"] for o in object_catalog} | {None, "null", ""}

    # ── 1. Schema checks ──
    if not isinstance(descriptor, dict):
        report.errors.append("Descriptor is not a dict")
        report.valid = False
        return report

    required_fields = ["action_candidates", "roles", "avoid", "conditions",
                       "sequence", "constraints", "manner", "uncertainties"]
    for field in required_fields:
        if field not in descriptor:
            report.errors.append(f"Missing required field: {field}")

    # action_candidates must be non-empty list of strings
    ac = descriptor.get("action_candidates", [])
    if not isinstance(ac, list) or len(ac) == 0:
        report.errors.append("action_candidates must be a non-empty list")
    else:
        valid_actions = set(ACTION_SIGNATURES.keys())
        for act in ac:
            if act not in valid_actions:
                report.errors.append(f"Invalid action: {act}. Valid: {sorted(valid_actions)}")

    # roles must be a dict
    roles = descriptor.get("roles", {})
    if not isinstance(roles, dict):
        report.errors.append("roles must be a dict")

    # ── 2. Mention checks ──
    for role_name, role_data in roles.items():
        if isinstance(role_data, dict):
            mention = role_data.get("mention", "")
            if not mention or not mention.strip():
                report.warnings.append(f"Role '{role_name}' has empty mention")

    # ── 3. Object ID checks ──
    all_role_data = list(roles.values()) + descriptor.get("avoid", [])
    for role_data in all_role_data:
        if isinstance(role_data, dict):
            oid = role_data.get("object_id")
            if oid is not None and oid not in valid_ids:
                report.errors.append(
                    f"object_id '{oid}' (mention='{role_data.get('mention','')}') "
                    f"not found in object catalog. Valid IDs: {sorted(valid_ids - {None, 'null', ''})}"
                )

    # ── 4. Role validation ──
    if ac and isinstance(ac, list) and len(ac) > 0:
        top_action = ac[0]
        sig = ACTION_SIGNATURES.get(top_action, {})
        required = sig.get("required_roles", [])
        role_keys = set(roles.keys())
        for req_role in required:
            # "destination_or_support_surface" means either is OK
            if req_role == "destination_or_support_surface":
                if "destination" not in role_keys and "support_surface" not in role_keys:
                    report.errors.append(
                        f"Action {top_action} requires destination or support_surface, "
                        f"but neither role is present. Roles: {sorted(role_keys)}"
                    )
            elif req_role not in role_keys:
                report.errors.append(
                    f"Action {top_action} requires role '{req_role}', "
                    f"but it's not in roles. Got: {sorted(role_keys)}"
                )

    # ── 5. Condition checks ──
    known_predicates = {"GRIPPER_EMPTY", "GRIPPER_HAS_OBJECT", "GRIPPER_OPEN",
                        "IS_HOMED", "VISIBLE", "OBJECT_MOVING", "OBJECT_STABLE",
                        "CAPABILITY_AVAILABLE"}
    for cond in descriptor.get("conditions", []):
        if isinstance(cond, dict):
            pred = cond.get("condition_predicate")
            if pred is not None and pred not in known_predicates:
                report.warnings.append(f"Unknown condition_predicate: {pred}")

    # ── 6. Numeric checks ──
    for c in descriptor.get("constraints", []):
        if isinstance(c, dict):
            for key in ("value", "min_value", "max_value"):
                v = c.get(key)
                if v is not None:
                    try:
                        fv = float(v)
                        if fv != fv:  # NaN check
                            report.errors.append(f"NaN value in constraint {c.get('parameter','?')}.{key}")
                    except (TypeError, ValueError):
                        report.errors.append(f"Non-numeric value in constraint {c.get('parameter','?')}.{key}: {v}")

    report.valid = len(report.errors) == 0
    return report


# ══════════════════════════════════════════════════════════════
# Two-stage DeepSeek caller
# ══════════════════════════════════════════════════════════════

@dataclass
class DeepSeekCallResult:
    """Full record of a DeepSeek semantic parsing attempt."""
    model: str = ""
    stage_a_success: bool = False
    stage_b_attempted: bool = False
    stage_b_success: bool = False
    fallback_to_rule: bool = False
    descriptor: Optional[SemanticDescriptor] = None
    raw_stage_a: str = ""
    raw_stage_b: str = ""
    validation_errors: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    api_error: str = ""


class DeepSeekSemanticParser:
    """Two-stage DeepSeek semantic parser with deterministic post-processing.

    Stage A: NL → SemanticDescriptor
    Stage B: Repair (if Stage A fails validation)
    Fallback: RuleEngine if both stages fail

    DeepSeek NEVER decides safety-critical values.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ):
        settings = get_settings()
        # None means "use configured key"; an explicit empty string means
        # "disable LLM" so offline/fallback paths never call the real API.
        self._api_key = settings.deepseek_api_key if api_key is None else api_key
        self._base_url = settings.deepseek_base_url
        self._model = model or settings.deepseek_model
        self._temperature = max(0.0, min(temperature, 2.0))
        self._max_tokens = settings.deepseek_max_tokens
        self._timeout = settings.deepseek_timeout_s
        self._client = None
        self.last_result: Optional[DeepSeekCallResult] = None

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    # ── Main entry ───────────────────────────────────────────

    def parse(
        self,
        instruction: str,
        scene: Any = None,
        perception_objects: Optional[List[Dict]] = None,
        robot_state: Optional[Dict] = None,
    ) -> DeepSeekCallResult:
        """Run the two-stage semantic parsing pipeline.

        Returns DeepSeekCallResult with descriptor and diagnostics.
        The caller MUST still run: EntityGrounder → Constraint compiler → FinalPlanValidator.
        """
        t0 = time.time()
        result = DeepSeekCallResult(model=self._model)

        if not self._api_key:
            result.fallback_to_rule = True
            result.api_error = "No API key configured"
            result.elapsed_ms = (time.time() - t0) * 1000
            self.last_result = result
            return result

        # Build object catalog
        object_catalog = build_object_catalog(scene, perception_objects)

        # Retrieve examples
        examples = retrieve_examples(instruction)

        # ── Stage A ──
        try:
            stage_a_raw = self._call_stage_a(instruction, object_catalog, robot_state, examples)
            result.raw_stage_a = stage_a_raw
            stage_a_json = self._safe_parse_json(stage_a_raw)
        except Exception as e:
            result.api_error = f"Stage A failed: {e}"
            result.fallback_to_rule = True
            result.elapsed_ms = (time.time() - t0) * 1000
            self.last_result = result
            return result

        # Validate Stage A
        report = validate_descriptor(stage_a_json, object_catalog)

        if report.valid:
            result.stage_a_success = True
            result.descriptor = self._dict_to_descriptor(stage_a_json)
            result.elapsed_ms = (time.time() - t0) * 1000
            self.last_result = result
            return result

        # ── Stage B: Repair ──
        result.validation_errors = report.errors
        result.stage_b_attempted = True

        try:
            stage_b_raw = self._call_stage_b(
                instruction, stage_a_json, report.errors, object_catalog)
            result.raw_stage_b = stage_b_raw
            stage_b_json = self._safe_parse_json(stage_b_raw)
        except Exception as e:
            result.api_error = f"Stage B failed: {e}"
            result.fallback_to_rule = True
            result.elapsed_ms = (time.time() - t0) * 1000
            self.last_result = result
            return result

        # Validate Stage B
        report_b = validate_descriptor(stage_b_json, object_catalog)
        if report_b.valid:
            result.stage_b_success = True
            result.descriptor = self._dict_to_descriptor(stage_b_json)
        else:
            result.validation_errors.extend(report_b.errors)
            result.fallback_to_rule = True

        result.elapsed_ms = (time.time() - t0) * 1000
        self.last_result = result
        return result

    # ── API calls ────────────────────────────────────────────

    def _call_stage_a(
        self,
        instruction: str,
        object_catalog: List[Dict],
        robot_state: Optional[Dict],
        examples: Optional[List[Dict]],
    ) -> str:
        user_msg = _build_stage_a_user_message(instruction, object_catalog, robot_state, examples)
        return self._call_api(_build_system_prompt(), user_msg)

    def _call_stage_b(
        self,
        instruction: str,
        stage_a_output: Dict,
        errors: List[str],
        object_catalog: List[Dict],
    ) -> str:
        system = _build_system_prompt() + "\n\n你正在修正上一轮输出的错误。请仔细阅读验证错误并修正。"
        user_msg = _build_stage_b_user_message(instruction, stage_a_output, errors, object_catalog)
        return self._call_api(system, user_msg)

    def _call_api(self, system_prompt: str, user_message: str) -> str:
        """Call DeepSeek API and return raw text. Raises on failure."""
        self._ensure_client()

        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            response_format={"type": "json_object"},
            timeout=self._timeout,
        )

        raw = response.choices[0].message.content.strip()
        logger.info(f"DeepSeek ({self._model}) response: {len(raw)} chars, temp={self._temperature}")
        return raw

    def _ensure_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError:
                raise RuntimeError("需要安装 openai 包: pip install openai")
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url)

    # ── JSON parsing ─────────────────────────────────────────

    @staticmethod
    def _safe_parse_json(raw: str) -> Dict[str, Any]:
        """Defensive JSON parsing with markdown stripping."""
        cleaned = raw.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object boundaries
            m = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
            else:
                raise

        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")

        return data

    @staticmethod
    def _dict_to_descriptor(d: Dict) -> SemanticDescriptor:
        """Convert a validated dict to SemanticDescriptor, dropping unknown fields."""
        try:
            return SemanticDescriptor.model_validate(d)
        except ValidationError:
            # Fallback: construct manually with known fields
            return SemanticDescriptor(
                action_candidates=d.get("action_candidates", []),
                roles={k: SemanticRole(**v) if isinstance(v, dict) else SemanticRole(role=k, mention=str(v))
                       for k, v in d.get("roles", {}).items()},
                avoid=[SemanticRole(**a) if isinstance(a, dict) else SemanticRole(role="obstacle", mention=str(a))
                       for a in d.get("avoid", [])],
                conditions=[SemanticCondition(**c) for c in d.get("conditions", []) if isinstance(c, dict)],
                sequence=[SemanticCondition(**s) for s in d.get("sequence", []) if isinstance(s, dict)],
                constraints=[SemanticConstraint(**c) for c in d.get("constraints", []) if isinstance(c, dict)],
                manner=d.get("manner", []),
                uncertainties=d.get("uncertainties", []),
                parse_confidence=float(d.get("parse_confidence", 0.0)),
            )


# ══════════════════════════════════════════════════════════════
# Deterministic post-processing — SemanticDescriptor → ParsedTask
# ══════════════════════════════════════════════════════════════

def merge_descriptor_into_task(
    descriptor: SemanticDescriptor,
    instruction: str,
) -> Dict[str, Any]:
    """Merge a DeepSeek SemanticDescriptor into task-level hints.

    This does NOT bypass EntityGrounder or any safety gate.
    It provides hints that the RuleEngine pipeline uses, but the
    pipeline makes all final decisions.

    Returns a dict of hints that parse_task_semantics() can use.
    """
    hints: Dict[str, Any] = {
        "action_hint": descriptor.action_candidates[0] if descriptor.action_candidates else None,
        "role_hints": {},
        "avoid_hints": [],
        "condition_hints": [],
        "constraint_hints": [],
        "manner_hints": list(descriptor.manner),
        "uncertainties": list(descriptor.uncertainties),
        "deepseek_confidence": descriptor.parse_confidence,
    }

    for role_name, role_data in descriptor.roles.items():
        hints["role_hints"][role_name] = {
            "mention": role_data.mention,
            "object_id": role_data.object_id,
            "specific_class": role_data.specific_class,
        }

    for avoid in descriptor.avoid:
        hints["avoid_hints"].append({
            "mention": avoid.mention,
            "object_id": avoid.object_id,
            "specific_class": avoid.specific_class,
        })

    for cond in descriptor.conditions:
        hints["condition_hints"].append(cond.model_dump() if hasattr(cond, 'model_dump') else cond)

    for c in descriptor.constraints:
        hints["constraint_hints"].append(c.model_dump() if hasattr(c, 'model_dump') else c)

    return hints
