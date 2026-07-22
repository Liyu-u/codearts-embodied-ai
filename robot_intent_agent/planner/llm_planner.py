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
import logging
import re
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
            "hard": c.hard,
            "source_text_span": c.source_text_span,
        }

    def _normalize_constraint(c) -> Dict[str, Any]:
        return {
            "constraint_id": c.constraint_id,
            "parameter": c.parameter,
            "operator": c.operator.value,
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

    normalized = {
        "instruction": "",  # Will be filled by caller
        "action": action_map.get(frame.action, "CUSTOM"),
        "theme": _normalize_entity(frame.theme),
        "source": _normalize_entity(frame.source),
        "destination": _normalize_entity(frame.destination),
        "recipient": _normalize_entity(frame.recipient),
        "obstacle": [_normalize_entity(p.target) for p in frame.prohibitions
                     if p.type in (ProhibitionType.NO_CONTACT, ProhibitionType.AVOID_ENTITY,
                                   ProhibitionType.AVOID_REGION, ProhibitionType.FORBID_ACTION)],
        "support_surface": None,  # Derived from destination for PLACE
        "manner": frame.manner.value if frame.manner else None,
        "motion_state": {"state": "static", "speed_mps": None, "confidence": 0.0},
        "user_constraints": [_normalize_constraint(c) for c in frame.user_constraints],
        "raw_mentions": [],
        "unmet_roles": [],
        "parse_confidence": 0.90,
        "grounding_confidence": 0.0,
        "constraint_confidence": 0.95 if frame.user_constraints else 0.4,
        "notes": list(frame.explanatory_notes) if frame.explanatory_notes else [],
        # ── New v1 fields ──
        "prohibitions": [_normalize_prohibition(p) for p in frame.prohibitions],
        "conditions": [_normalize_condition(c) for c in frame.conditions],
        "sequence": [{"step_index": s.step_index, "action": s.action.value,
                       "description": s.description,
                       "entity": _normalize_entity(s.entity),
                       "condition_id": s.condition_id}
                     for s in frame.sequence],
        "urgency": frame.urgency.value,
        "clarification": frame.clarification,
        "intent_frame_version": frame.schema_version,
    }

    return normalized


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
10. 只输出 JSON，不输出 Markdown 或解释文本"""



# ============================================================
# 场景 → 紧凑 JSON 注入
# ============================================================

def _scene_to_prompt_json(scene: Optional[SemanticSceneGraph]) -> Dict[str, Any]:
    """将场景图压缩为注入 Prompt 的紧凑 JSON"""
    if not scene or not scene.objects:
        return {"objects": [], "relations": [], "robot_state": "unknown"}

    objects = []
    for obj in scene.objects:
        objects.append({
            "name": obj.name,
            "label": obj.label,
            "position": {"x": round(obj.position.x, 3), "y": round(obj.position.y, 3), "z": round(obj.position.z, 3)},
            "bbox": {"w": round(obj.bbox.width, 3), "h": round(obj.bbox.height, 3), "d": round(obj.bbox.depth, 3)},
            "affordances": [a.value for a in obj.affordances],
            "attributes": obj.attributes,
        })

    relations = []
    for r in scene.relations:
        relations.append({
            "subject": r.subject[:12],
            "predicate": r.predicate.value,
            "object": r.object[:12],
            "confidence": round(r.confidence, 2),
        })

    return {
        "objects": objects,
        "relations": relations,
        "gripper": {
            "is_open": scene.robot_state.gripper.is_open,
            "has_object": scene.robot_state.gripper.has_object,
        },
    }


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
        self._api_key = api_key or settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url
        self._model = model or settings.deepseek_model
        self._temperature = settings.deepseek_temperature
        self._max_tokens = settings.deepseek_max_tokens
        self._timeout = settings.deepseek_timeout_s
        self._max_retries = settings.deepseek_max_retries
        self._client = None
        self._catalog = SkillCatalog()

    @property
    def name(self) -> str:
        return f"DeepSeek-{self._model}"

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

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

        user_message = self._build_user_message(instruction, scene_json, memory_json)

        # 2. 调用 API
        raw_json = self._call_api(user_message)

        # 3. 解析为 BehaviorTree
        bt = self._parse_response(raw_json, instruction)

        return bt

    # ============================================================
    # Prompt 构建
    # ============================================================

    def _build_user_message(
        self,
        instruction: str,
        scene_json: Dict[str, Any],
        memory_json: Dict[str, Any],
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
            "",
            "请根据以上信息，输出行为树 JSON。记住：只输出 JSON，不要加任何解释。",
            f"可用技能: {', '.join(SkillCatalog.list_all())}",
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

                return self._safe_parse_llm_json(raw_text)

            except json.JSONDecodeError as e:
                logger.warning(f"DeepSeek returned invalid JSON (attempt {attempt+1}): {e}")
                if attempt >= self._max_retries:
                    raise LLMPlannerError(f"DeepSeek 返回了非法的 JSON: {e}")

            except (ValueError, TypeError) as e:
                logger.warning(f"DeepSeek JSON structure error (attempt {attempt+1}): {e}")
                if attempt >= self._max_retries:
                    raise LLMPlannerError(f"DeepSeek 返回的 JSON 结构异常: {e}")

            except Exception as e:
                logger.warning(f"DeepSeek API error (attempt {attempt+1}): {e}")
                if attempt >= self._max_retries:
                    raise LLMPlannerError(f"DeepSeek API 调用失败: {e}")

        raise LLMPlannerError("DeepSeek API 调用失败：已达最大重试次数")

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
        self, raw: Dict[str, Any], instruction: str
    ) -> BehaviorTree:
        """将 DeepSeek 返回的 JSON 解析为 BehaviorTree，同时验证 IntentFrame v1 并进行受控修复。"""
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
                validated_frame = IntentFrame.model_validate(raw_intent_frame)
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

        metadata: Dict[str, Any] = {
            "action": raw.get("action_type") or (validated_frame.action.value if intent_frame_valid else "custom"),
            "target": target,
            "modifiers": modifiers,
            "avoid_objects": avoid_objects,
            "planner": self.name,
            "llm_model": self._model,
            "semantic_frame_version": semantic_frame_version,
            "engine_trace": {
                "requested_engine": "DeepSeek",
                "actual_engine": self.name,
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

        return bt

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
        engine = self._settings.planner_engine

        # ── 模式 1: 纯规则 ──
        if engine == "rule":
            return self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)

        # ── 模式 2: 纯 LLM ──
        if engine == "llm":
            if not self._llm_planner or not self._llm_planner.is_available:
                logger.warning("LLM engine selected but not available, falling back to rule")
                return self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
            try:
                return self._llm_planner.plan(instruction, scene=scene, memory_context=memory_context)
            except LLMPlannerError as e:
                logger.warning(f"LLM planner failed: {e}, falling back to rule")
                return self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)

        # ── 模式 3: Hybrid（规则优先 + LLM 兜底）──
        if engine == "hybrid":
            # 先用规则引擎
            rule_bt = self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)
            confidence = self._estimate_rule_confidence(instruction, rule_bt)

            if confidence >= self._settings.rule_confidence_threshold:
                logger.info(f"Rule planner confidence={confidence:.2f} >= threshold, using rule")
                return rule_bt

            # 低置信度，尝试 LLM
            logger.info(f"Rule planner confidence={confidence:.2f} < threshold, falling back to LLM")
            if self._llm_planner and self._llm_planner.is_available:
                try:
                    return self._llm_planner.plan(instruction, scene=scene, memory_context=memory_context)
                except LLMPlannerError as e:
                    logger.warning(f"LLM fallback failed: {e}, using rule result")
            return rule_bt

        # 默认：规则
        return self._rule_planner.plan(instruction, scene=scene, memory_context=memory_context)

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


# ============================================================
# 异常类型
# ============================================================

class LLMPlannerError(Exception):
    """LLM 规划器异常"""
    pass
