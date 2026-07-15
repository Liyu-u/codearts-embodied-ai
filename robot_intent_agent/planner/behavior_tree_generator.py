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

import re
from typing import Any, Dict, List, Optional, Tuple

from robot_intent_agent.schemas.scene import SemanticSceneGraph, SceneObject
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNode,
    BTNodeType,
    SkillAction,
    ConditionCheck,
    BTStatus,
)

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

# 动作关键词
ACTION_KEYWORDS: Dict[str, str] = {
    "push": "推|挪|移",
    "stack": "摞|叠|堆|放在.*上面",
    "pour": "倒|灌|倾",
    "pick_and_place": "拿|取|抓|递|给|放|搬|端",
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
    """基于规则的指令解析器 — 提取动作、目标、修饰语、规避对象"""

    @staticmethod
    def classify_action(text: str) -> str:
        """分类核心动作"""
        for action, pattern in ACTION_KEYWORDS.items():
            if re.search(pattern, text):
                return action
        return "pick_and_place"

    @staticmethod
    def extract_target(text: str) -> str:
        """提取目标物体名称"""
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
        # 1. 解析指令
        action = self.parser.classify_action(instruction)
        target = self.parser.extract_target(instruction)
        destination = self.parser.extract_destination(instruction)
        modifiers = self.parser.extract_modifiers(instruction)
        avoid_objects = self.parser.extract_avoid_objects(instruction)

        # 2. 选择技能管道
        pipeline = ACTION_PIPELINE.get(action, ACTION_PIPELINE["pick_and_place"])

        # 3. 注入 Memory 上下文参数
        memory_params = self._merge_memory(memory_context or [])
        modifiers = {**memory_params, **modifiers}

        # 4. 在场景中查找目标
        target_obj = scene.find_object(target) if scene else None

        # 5. 构建行为树子节点
        children: List[BTNode] = []

        # ── 前置条件 ──
        children.extend(self._build_preconditions(scene, target, avoid_objects))

        # ── Avoid 节点 (规避障碍物) ──
        if avoid_objects:
            # 场景中的阻挡物也加入规避列表
            if scene and target_obj:
                blockers = [
                    scene.find_object(bid).name
                    for bid in scene.blocking_objects(target_obj.id)
                    if scene.find_object(bid)
                ]
                avoid_objects = list(set(avoid_objects + blockers))

            for obstacle in avoid_objects:
                children.append(self._make_avoid_node(obstacle))

        # ── 主技能序列 ──
        for skill_name in pipeline:
            skill_def = self.catalog.get(skill_name)
            params = self._build_params(
                skill_def, target=target, destination=destination, modifiers=modifiers
            )
            children.append(
                BTNode(
                    type=BTNodeType.ACTION,
                    name=f"{skill_name}({target})",
                    skill=SkillAction(
                        skill_name=skill_name,
                        target=target,
                        params=params,
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
            annotation=f"Action={action} | Target={target}",
        )

        return BehaviorTree(
            task_id=f"task-{action}",
            description=instruction,
            root=root,
            metadata={
                "action": action,
                "target": target,
                "destination": destination,
                "modifiers": modifiers,
                "avoid_objects": avoid_objects,
                "planner": self.name,
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
    ) -> Dict[str, Any]:
        """为技能构建参数字典"""
        params: Dict[str, Any] = {}
        if target:
            params["target"] = target
        if destination:
            params["destination"] = destination

        # 根据技能类型注入修饰参数
        if skill_def.name in ("Grasp", "GentleGrasp"):
            if "force_n" in modifiers:
                params["force_n"] = modifiers["force_n"]
            if "grip_style" in modifiers:
                params["grip_style"] = modifiers["grip_style"]
        if skill_def.name in ("MoveTo", "Reach"):
            if "velocity_ms" in modifiers:
                params["velocity_ms"] = modifiers["velocity_ms"]

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
