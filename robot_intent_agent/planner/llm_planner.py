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
# DeepSeek 系统提示词
# ============================================================

SYSTEM_PROMPT = """你是一个具身智能机器人的"前端大脑"。你的职责是：将用户的自然语言指令，转化为结构化的机器人行为树（Behavior Tree）JSON。

## 你可以调用的原子技能（Skill Catalog）

| 技能名 | 说明 | 参数 |
|--------|------|------|
| Reach | 移动到目标物体上方安全高度 | target(物体名), safe_z_offset(默认0.10m) |
| Grasp | 标准抓取物体 | target, force_n(默认5.0N,范围0.1-10.0) |
| GentleGrasp | 轻柔抓取（用于易碎物体） | target, force_n(默认3.0N) |
| MoveTo | 将手中物体移动到目标位置 | target, velocity_ms(默认0.15,范围0.05-0.30) |
| Release | 释放手中物体 | target |
| Push | 沿直线推动物体 | target, direction, distance_m |
| Stack | 将物体堆叠到另一个物体上 | target(被堆叠物), destination(底座物体) |
| Avoid | 绕开障碍物 | target(障碍物名), min_distance_m(默认0.05) |
| Inspect | 视觉确认物体状态 | target, check_type(position|grasp|clearance) |

## 行为树节点类型

- "sequence": 顺序执行所有子节点
- "action": 执行一个原子技能

## 输出格式要求

你必须严格输出以下 JSON 格式，不要包含任何 Markdown 标记或解释文字：

{
  "task_description": "一句话描述任务",
  "target": "主目标物体名",
  "modifiers": ["gentle_grasp", "slow_velocity"],  // 从指令中提取的修饰语
  "avoid_objects": ["障碍物名1", "障碍物名2"],     // 需要避开的物体
  "behavior_tree": {
    "type": "sequence",
    "name": "任务名",
    "children": [
      {
        "type": "action",
        "name": "Reach(目标)",
        "skill_name": "Reach",
        "target": "目标物体名",
        "params": {}
      }
    ]
  }
}

## 重要规则

1. 每个 "action" 节点必须包含 skill_name, target, params 三个字段
2. skill_name 必须是上述技能表中的技能名
3. 如果用户说"轻一点"→ Grasp 改为 GentleGrasp，force_n 设为 3.0
4. 如果用户说"慢一点"→ MoveTo 的 velocity_ms 设为 0.10
5. 如果用户说"别碰X"/"避开X"→ 增加 Avoid(X) 节点，并在 avoid_objects 中列出
6. params 中的数值必须带单位（如 force_n: 3.0, velocity_ms: 0.10）
7. 不要生成 Python 代码，只生成 JSON"""


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

                # 清理可能的 Markdown 包裹
                raw_text = self._strip_markdown(raw_text)

                return json.loads(raw_text)

            except json.JSONDecodeError as e:
                logger.warning(f"DeepSeek returned invalid JSON (attempt {attempt+1}): {e}")
                if attempt >= self._max_retries:
                    raise LLMPlannerError(f"DeepSeek 返回了非法的 JSON: {e}")

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
        """将 DeepSeek 返回的 JSON 解析为 BehaviorTree"""
        bt_json = raw.get("behavior_tree")
        if not bt_json:
            raise LLMPlannerError("DeepSeek 返回的 JSON 缺少 behavior_tree 字段")

        # 递归构建 BTNode
        root = self._build_bt_node(bt_json)

        # 验证所有技能都在 SkillCatalog 中
        self._validate_skills(root)

        # 提取元数据
        target = raw.get("target", "")
        modifiers = raw.get("modifiers", [])
        avoid_objects = raw.get("avoid_objects", [])
        description = raw.get("task_description", instruction)

        bt = BehaviorTree(
            task_id=f"task-llm-{hash(instruction) % 10000:04d}",
            description=description,
            root=root,
            metadata={
                "action": raw.get("action_type", "custom"),
                "target": target,
                "modifiers": modifiers,
                "avoid_objects": avoid_objects,
                "planner": self.name,
                "llm_model": self._model,
            },
        )

        return bt

    def _build_bt_node(self, node_json: Dict[str, Any]) -> BTNode:
        """递归构建 BTNode"""
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
        """验证所有 Action 的技能名都在 SkillCatalog 中"""
        invalid = []

        def _check(node: BTNode):
            if node.type == BTNodeType.ACTION and node.skill:
                try:
                    SkillCatalog.get(node.skill.skill_name)
                except KeyError:
                    invalid.append(node.skill.skill_name)
            for child in node.children:
                _check(child)

        _check(root)

        if invalid:
            logger.warning(
                f"LLM returned {len(invalid)} unknown skills: {invalid}. "
                f"These will be kept but may cause downstream errors."
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
