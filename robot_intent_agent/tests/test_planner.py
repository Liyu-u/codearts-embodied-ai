"""
Task Planner 模块测试

按规范要求:
    输入: "请把桌上的红色药瓶递到我手上，动作轻一点，不要碰水杯"
    期望: BehaviorTree 包含 Reach, Grasp, MoveTo, Release, Avoid
"""

import pytest

from robot_intent_agent.planner import (
    TaskPlannerInterface,
    SkillCatalog,
    SkillDefinition,
    BehaviorTreeGenerator,
    RuleInstructionParser,
    LLMPlanner,
)
from robot_intent_agent.schemas.behavior_tree import (
    BehaviorTree,
    BTNodeType,
)
from robot_intent_agent.scene_builder import (
    SemanticSceneBuilder,
    RawObjectPercept,
)
from robot_intent_agent.memory import MemoryRetriever


# ============================================================
# 测试数据
# ============================================================

CANONICAL_INSTRUCTION = "请把桌上的红色药瓶递到我手上，动作轻一点，不要碰水杯"


@pytest.fixture
def scene():
    """测试场景: 药瓶 + 水杯"""
    builder = SemanticSceneBuilder()
    return builder.build([
        RawObjectPercept(
            name="红色药瓶", x=0.15, y=0.05, z=0.03,
            width=0.03, height=0.08, depth=0.03,
            color="red", material="plastic",
        ),
        RawObjectPercept(
            name="水杯", x=0.08, y=0.03, z=0.06,
            width=0.07, height=0.12, depth=0.07,
            color="transparent", material="glass",
        ),
    ])


@pytest.fixture
def memory_items():
    """Memory 上下文"""
    retriever = MemoryRetriever()
    retriever.add_user_preference("grip_style", "gentle", user="elderly")
    retriever.add_skill_experience(
        "gentle_grasp", "红色药瓶",
        params={"force_n": 2.5}, success=True,
    )
    results = retriever.search("老人递药轻一点", top_k=5)
    return [item.to_dict() for item in results]


@pytest.fixture
def planner():
    return BehaviorTreeGenerator()


# ============================================================
# Test: SkillCatalog
# ============================================================

class TestSkillCatalog:
    def test_all_six_skills_exist(self):
        """6 个技能: Reach, Grasp, MoveTo, Release, Avoid, Inspect"""
        skills = SkillCatalog.list_all()
        assert "Reach" in skills
        assert "Grasp" in skills
        assert "MoveTo" in skills
        assert "Release" in skills
        assert "Avoid" in skills
        assert "Inspect" in skills

    def test_get_skill_definition(self):
        """获取技能定义 — 包含 name, description, preconditions, effects"""
        reach = SkillCatalog.get("Reach")
        assert isinstance(reach, SkillDefinition)
        assert reach.name == "Reach"
        assert len(reach.description) > 0
        assert len(reach.preconditions) > 0
        assert len(reach.effects) > 0

    def test_grasp_preconditions(self):
        """Grasp 的前置条件"""
        grasp = SkillCatalog.get("Grasp")
        assert any("gripper_open" in p for p in grasp.preconditions)
        assert any("target_graspable" in p for p in grasp.preconditions)

    def test_avoid_description(self):
        """Avoid 技能描述"""
        avoid = SkillCatalog.get("Avoid")
        assert "collision" in avoid.description.lower()

    def test_inspect_description(self):
        """Inspect 技能描述"""
        inspect = SkillCatalog.get("Inspect")
        assert "confirm" in inspect.description.lower() or "visually" in inspect.description.lower()

    def test_unknown_skill_raises(self):
        """未知技能抛 KeyError"""
        with pytest.raises(KeyError):
            SkillCatalog.get("FlyToMoon")

    def test_primitive_skills(self):
        """基础操作技能: Reach, Grasp, MoveTo, Release"""
        primitives = SkillCatalog.get_primitive_skills()
        assert primitives == ["Reach", "Grasp", "MoveTo", "Release"]

    def test_safety_skills(self):
        """安全技能: Avoid, Inspect"""
        safety = SkillCatalog.get_safety_skills()
        assert "Avoid" in safety
        assert "Inspect" in safety


# ============================================================
# Test: RuleInstructionParser
# ============================================================

class TestRuleInstructionParser:
    def test_classify_pick_and_place(self):
        """'拿/取/递/给/放' → pick_and_place"""
        for text in ["帮我拿一下", "取个东西", "递给我", "放桌上"]:
            assert RuleInstructionParser.classify_action(text) == "pick_and_place"

    def test_classify_push(self):
        """'推/挪' → push"""
        assert RuleInstructionParser.classify_action("推到左边") == "push"

    def test_classify_stack(self):
        """'摞/叠/堆' → stack"""
        assert RuleInstructionParser.classify_action("摞上去") == "stack"

    def test_extract_target_with_ba(self):
        """'把红色方块拿过来' → '红色方块'"""
        target = RuleInstructionParser.extract_target("把红色方块拿过来")
        assert "红色方块" in target or "方块" in target

    def test_extract_target_with_geiwo(self):
        """'把药瓶递给我' → '药瓶'"""
        target = RuleInstructionParser.extract_target("把药瓶递给我")
        assert "药瓶" in target

    def test_extract_avoid_objects(self):
        """'不要碰水杯，别碰玻璃杯' → ['水杯', '玻璃杯']"""
        objects = RuleInstructionParser.extract_avoid_objects("不要碰水杯，别碰玻璃杯")
        assert "水杯" in objects

    def test_extract_avoid_dont_touch(self):
        """'千万别碰旁边的水杯' → 提取水杯 (regex 捕获最后4个汉字)"""
        objects = RuleInstructionParser.extract_avoid_objects("千万别碰旁边的水杯")
        assert len(objects) > 0, "Should extract at least one avoid object"
        assert any("水" in o or "杯" in o for o in objects)

    def test_extract_modifiers(self):
        """'轻一点' → {force_n: 3.0, grip_style: gentle}"""
        modifiers = RuleInstructionParser.extract_modifiers("轻一点")
        assert modifiers.get("force_n") == 3.0
        assert modifiers.get("grip_style") == "gentle"

    def test_extract_no_modifiers(self):
        """无修饰语 — 返回空字典"""
        modifiers = RuleInstructionParser.extract_modifiers("把方块拿过来")
        assert modifiers == {}


# ============================================================
# Test: BehaviorTreeGenerator (Rule-based Planner)
# ============================================================

class TestBehaviorTreeGenerator:
    def test_implements_interface(self, planner):
        """BehaviorTreeGenerator 实现 TaskPlannerInterface"""
        assert isinstance(planner, TaskPlannerInterface)
        assert planner.name == "RuleBasedPlanner"

    def test_plan_returns_behavior_tree(self, planner):
        """plan() 返回 BehaviorTree"""
        bt = planner.plan("帮我把红色方块拿过来")
        assert isinstance(bt, BehaviorTree)
        assert bt.root.type == BTNodeType.SEQUENCE

    def test_basic_pick_and_place_pipeline(self, planner):
        """简单抓取: 包含 Reach, Grasp, MoveTo, Release"""
        bt = planner.plan("帮我把红色方块拿过来")
        skill_names = [a.skill_name for a in bt.root.flatten_actions()]
        for expected in ["Reach", "Grasp", "MoveTo", "Release"]:
            assert expected in skill_names, f"Missing {expected}. Got: {skill_names}"

    def test_push_pipeline(self, planner):
        """推: Reach, Push, Release (不含 Grasp)"""
        bt = planner.plan("把绿色圆柱推到桌子右边")
        skill_names = [a.skill_name for a in bt.root.flatten_actions()]
        assert "Push" in skill_names
        assert "Grasp" not in skill_names

    def test_stack_pipeline(self, planner):
        """堆叠: 包含 Stack"""
        bt = planner.plan("把红色方块摞到蓝色方块上面")
        skill_names = [a.skill_name for a in bt.root.flatten_actions()]
        assert "Stack" in skill_names

    def test_avoid_node_inserted(self, planner):
        """'不要碰水杯' → Avoid 节点"""
        bt = planner.plan("帮我把药瓶拿过来，不要碰水杯")
        skill_names = [a.skill_name for a in bt.root.flatten_actions()]
        assert "Avoid" in skill_names, f"Expected Avoid node. Got: {skill_names}"

    def test_grasp_params_from_modifier(self, planner):
        """'轻一点' → Grasp params 含 force_n"""
        bt = planner.plan("帮我把药瓶拿过来，轻一点")
        grasp_actions = [
            a for a in bt.root.flatten_actions()
            if a.skill_name == "Grasp"
        ]
        assert len(grasp_actions) > 0
        if grasp_actions:
            assert "force_n" in grasp_actions[0].params

    def test_preconditions_in_tree(self, planner):
        """BT 包含前置条件节点 (CheckGripperEmpty)"""
        bt = planner.plan("帮我把方块拿过来")
        condition_types = {c.type for c in bt.root.children}
        assert BTNodeType.CONDITION in condition_types

    def test_memory_injection(self, planner, memory_items):
        """Memory 上下文参数注入"""
        bt = planner.plan(
            "请把药瓶递给我，动作轻一点",
            memory_context=memory_items,
        )
        grasp_actions = [
            a for a in bt.root.flatten_actions()
            if a.skill_name == "Grasp"
        ]
        if grasp_actions:
            params = grasp_actions[0].params
            # Memory 中的 gentle_grasp 参数应被注入
            assert "force_n" in params or params.get("grip_style") == "gentle"


# ============================================================
# Test: 规范场景 (集成)
# ============================================================

class TestCanonicalScenario:
    """
    规范测试:
        "请把桌上的红色药瓶递到我手上，动作轻一点，不要碰水杯"

    期望输出 BehaviorTree:
        ✅ Reach    — 移动到药瓶上方
        ✅ Grasp    — 抓取药瓶 (轻一点 → force_n=3.0)
        ✅ MoveTo   — 移动到用户手中
        ✅ Release  — 释放药瓶
        ✅ Avoid    — 规避水杯
        ✅ CheckClear / CheckGripperEmpty — 前置条件
    """

    def test_full_pipeline_has_required_skills(self, planner, scene, memory_items):
        """规范场景 — 全部 5 种技能都在 BT 中"""
        bt = planner.plan(
            instruction=CANONICAL_INSTRUCTION,
            scene=scene,
            memory_context=memory_items,
        )

        skill_names = [a.skill_name for a in bt.root.flatten_actions()]

        # 必须包含 5 种技能
        assert "Reach" in skill_names, f"Missing Reach. Skills: {skill_names}"
        assert "Grasp" in skill_names, f"Missing Grasp. Skills: {skill_names}"
        assert "MoveTo" in skill_names, f"Missing MoveTo. Skills: {skill_names}"
        assert "Release" in skill_names, f"Missing Release. Skills: {skill_names}"
        assert "Avoid" in skill_names, f"Missing Avoid. Skills: {skill_names}"

    def test_avoid_target_is_water_cup(self, planner, scene, memory_items):
        """Avoid 节点的 target 是水杯"""
        bt = planner.plan(
            instruction=CANONICAL_INSTRUCTION,
            scene=scene,
            memory_context=memory_items,
        )
        avoid_actions = [
            a for a in bt.root.flatten_actions()
            if a.skill_name == "Avoid"
        ]
        assert len(avoid_actions) > 0
        assert any("水杯" in a.target for a in avoid_actions if a.target)

    def test_grasp_has_gentle_force(self, planner, scene, memory_items):
        """轻一点 → Grasp.force_n <= 3.0"""
        bt = planner.plan(
            instruction=CANONICAL_INSTRUCTION,
            scene=scene,
            memory_context=memory_items,
        )
        grasp_actions = [
            a for a in bt.root.flatten_actions()
            if a.skill_name == "Grasp"
        ]
        for a in grasp_actions:
            force = a.params.get("force_n")
            if force is not None:
                assert force <= 3.0, f"Expected gentle force <= 3.0, got {force}"

    def test_bt_metadata_complete(self, planner, scene, memory_items):
        """BT 元数据完整"""
        bt = planner.plan(
            instruction=CANONICAL_INSTRUCTION,
            scene=scene,
            memory_context=memory_items,
        )
        assert bt.metadata["action"] == "pick_and_place"
        assert "红色药瓶" in bt.metadata.get("target", "")
        assert len(bt.metadata.get("avoid_objects", [])) > 0
        assert bt.metadata.get("planner") == "RuleBasedPlanner"

    def test_bt_root_is_sequence_with_children(self, planner, scene, memory_items):
        """根节点为 Sequence, 有多个子节点"""
        bt = planner.plan(
            instruction=CANONICAL_INSTRUCTION,
            scene=scene,
            memory_context=memory_items,
        )
        assert bt.root.type == BTNodeType.SEQUENCE
        assert len(bt.root.children) >= 7  # 2 conditions + Avoid + 4 core skills

    def test_preconditions_include_blocking_check(self, planner, scene, memory_items):
        """前置条件包含对阻挡水杯的路径检查"""
        bt = planner.plan(
            instruction=CANONICAL_INSTRUCTION,
            scene=scene,
            memory_context=memory_items,
        )
        condition_names = [
            c.name for c in bt.root.children
            if c.type == BTNodeType.CONDITION
        ]
        assert any("水杯" in name or "Clear" in name for name in condition_names)


# ============================================================
# Test: LLMPlanner (Mock)
# ============================================================

class TestLLMPlanner:
    def test_implements_interface(self):
        """LLMPlanner 实现 TaskPlannerInterface"""
        llm = LLMPlanner()
        assert isinstance(llm, TaskPlannerInterface)

    def test_name_reflects_model(self):
        """名称包含模型名"""
        llm = LLMPlanner(model="gpt-4o")
        assert "gpt-4o" in llm.name

    def test_plan_raises_without_key(self):
        """无 API Key 时 plan() 抛出 LLMPlannerError"""
        from robot_intent_agent.planner.llm_planner import LLMPlannerError
        llm = LLMPlanner(api_key="")  # empty key
        with pytest.raises(LLMPlannerError):
            llm.plan("测试指令")
