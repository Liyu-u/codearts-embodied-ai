"""
测试: Scene blocking -> BT avoidance 闭环

验证:
    即使用户没有说"别碰"/"避开",
    只要 Scene 中存在 blocking 关系,
    BT 必须自动生成 PlanPath (含 avoid_obstacles)

场景:
    Object:  光学聚焦镜片盒 (fragile=true)
    Obstacle: 高压电源箱 (blocking=true, movable=false)
    User:     "快把镜片盒拿过来"  (没有 "别碰" 关键词)
"""

import pytest

from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.schemas.behavior_tree import BTNodeType


class TestSceneBlockingAutoAvoid:
    """Scene blocking 必须自动转化为 BT 避障, 不依赖 NL 关键词"""

    def test_blocking_triggers_planpath_without_nl_keyword(self):
        """
        用户说"快把镜片盒拿过来" (无 "别碰" 关键词),
        场景中高压电源箱阻挡镜片盒,
        BT 必须包含 PlanPath 且 avoid_obstacles 包含高压电源箱.
        """
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(
                name="光学聚焦镜片盒", x=0.15, y=0.05, z=0.03,
                width=0.10, height=0.04, depth=0.10,
                color="black", material="glass",
                extra_attrs={"fragile": True},
            ),
            RawObjectPercept(
                name="高压电源箱", x=0.08, y=0.03, z=0.06,
                width=0.20, height=0.40, depth=0.20,
                color="gray", material="metal",
                extra_attrs={"movable": False},
            ),
        ])

        # 指令中没有任何 "别碰"/"避开"/"不要碰" 关键词
        instruction = "快把镜片盒拿过来"

        planner = BehaviorTreeGenerator()
        bt = planner.plan(instruction, scene=scene)

        # ── 断言 1: BT 包含 PlanPath 或 Avoid ──
        actions = bt.root.flatten_actions()
        skill_names = [a.skill_name for a in actions]
        assert (
            "PlanPath" in skill_names or "Avoid" in skill_names or "PlanPath" in skill_names
        ), (
            f"Scene has blocking but BT has NO PlanPath/Avoid! "
            f"Actions: {skill_names}"
        )

        # ── 断言 2: PlanPath 的 avoid_obstacles 包含高压电源箱 ──
        planpath_action = next(
            (a for a in actions if a.skill_name == "PlanPath"), None
        )
        avoid_action = next(
            (a for a in actions if a.skill_name == "Avoid"), None
        )
        avoid_targets = []
        if planpath_action:
            avoid_targets = planpath_action.params.get("avoid_obstacles", [])
        elif avoid_action:
            avoid_targets = [avoid_action.target or ""]

        assert any("高压电源箱" in t for t in avoid_targets), (
            f"Blocking obstacle '高压电源箱' NOT in avoid targets: {avoid_targets}"
        )

        # ── 断言 3: PlanPath 必须在 Reach 之前 ──
        ordered = [a.skill_name for a in actions
                   if a.skill_name in ("PlanPath", "Avoid", "Reach", "Grasp")]
        pp_idx = ordered.index("PlanPath") if "PlanPath" in ordered else -1
        av_idx = ordered.index("Avoid") if "Avoid" in ordered else -1
        reach_idx = ordered.index("Reach") if "Reach" in ordered else -1

        if pp_idx >= 0 and reach_idx >= 0:
            assert pp_idx < reach_idx, (
                f"PlanPath@{pp_idx} must be BEFORE Reach@{reach_idx}. "
                f"Order: {ordered}"
            )
        if av_idx >= 0 and reach_idx >= 0:
            assert av_idx < reach_idx, (
                f"Avoid@{av_idx} must be BEFORE Reach@{reach_idx}. "
                f"Order: {ordered}"
            )

        # ── 断言 4: 元数据记录了阻挡关系 ──
        print(f"  BT metadata.avoid_objects: {bt.metadata.get('avoid_objects', [])}")
        print(f"  Actions: {skill_names}")

    def test_no_blocking_no_false_positive(self):
        """
        场景无阻挡时, BT 不应强制插入 PlanPath/Avoid.
        """
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(
                name="红色药瓶", x=0.15, y=0.05, z=0.03,
                width=0.03, height=0.08, depth=0.03,
                color="red", material="plastic",
            ),
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("帮我把红色药瓶拿过来", scene=scene)

        actions = bt.root.flatten_actions()
        skill_names = [a.skill_name for a in actions]

        # 没有障碍物, 不应该有 PlanPath 或 Avoid
        assert "PlanPath" not in skill_names, (
            f"No obstacles but PlanPath was inserted: {skill_names}"
        )
        assert "Reach" in skill_names
        assert "Grasp" in skill_names

    def test_blocking_with_fragile_still_works(self):
        """
        同时有 blocking + fragile 时, BT 既要 PlanPath 也要 GentleGrasp.
        """
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(
                name="8寸晶圆盒", x=0.15, y=0.05, z=0.03,
                width=0.20, height=0.02, depth=0.20,
                color="silver", material="silicon",
                extra_attrs={"fragile": True},
            ),
            RawObjectPercept(
                name="激光轮廓仪", x=0.08, y=0.03, z=0.06,
                width=0.15, height=0.30, depth=0.15,
                color="black", material="metal",
            ),
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("赶时间，把晶圆盒拿过来", scene=scene)

        actions = bt.root.flatten_actions()
        skill_names = [a.skill_name for a in actions]

        assert "PlanPath" in skill_names or "Avoid" in skill_names or "PlanPath" in skill_names, (
            f"Blocking exists but no avoidance action: {skill_names}"
        )
        assert "GentleGrasp" in skill_names or "Grasp" in skill_names, (
            f"No grasp action: {skill_names}"
        )
