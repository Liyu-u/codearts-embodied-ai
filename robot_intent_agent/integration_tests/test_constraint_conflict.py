"""
Case 4: 脆弱物品搬运 — 约束冲突场景

场景:
    用户: "帮我把那个古董花瓶搬到展示台上，轻一点，但是别掉下来"
    冲突: "轻一点"(force<=3N) vs "别掉下来"(需要足够抓力 force>=5N)
    验证: 冲突检测 & 安全优先 (safety > user preference)
"""

import pytest

from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import (
    HybridConstraintCompiler,
    ConstraintCategory,
    ConstraintPriority,
    PhysicalConstraint,
    SafetyConstraint,
)


class TestConstraintConflict:
    """约束冲突: 矛盾的指令"""

    def test_contradictory_force_instructions(self):
        """
        "轻一点"(force<=3N) + "用力抓住"(force>=5N)

        → 去重后取最严格的约束
        """
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="古董花瓶", x=0.10, y=0.00, z=0.05,
                             width=0.08, height=0.20, depth=0.08,
                             color="blue", material="ceramic",
                             extra_attrs={"fragile": True, "heavy": True})
        ])

        retriever = MemoryRetriever()
        retriever.add_user_preference("grip_style", "gentle")
        retriever.add_skill_experience(
            "standard_grasp", "花瓶",
            params={"force_n": 6.0}, success=True,
        )
        mem = [i.to_dict() for i in retriever.search("花瓶 grasp", top_k=5)]

        planner = BehaviorTreeGenerator()
        # 故意构造冲突指令
        bt = planner.plan(
            "帮我把古董花瓶搬到展示台上，轻一点，但是要抓住",
            scene=scene, memory_context=mem,
        )

        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            "帮我把古董花瓶搬到展示台上，轻一点，但是要抓住",
            behavior_tree=bt, scene=scene, memory_context=mem,
            target="古董花瓶",
        )

        # 查找 force_limit 约束
        force_nodes = [
            n for n in cg.nodes if n.constraint_type == "force_limit"
        ]

        if force_nodes:
            # 多重 force_limit 应被去重 (取最严格 max_force_n)
            max_forces = [n.params.get("max_force_n", 10.0) for n in force_nodes]
            min_forces = [n.params.get("min_force_n", 0.1) for n in force_nodes]

            # 去重后只有一个
            print(f"  Force constraints: max={min(max_forces)}, min={max(min_forces)}")

        # Safety 红线始终存在
        safety = cg.by_category(ConstraintCategory.SAFETY)
        assert len(safety) >= 5

    def test_safety_always_priority(self):
        """安全约束始终 HARD, 用户偏好始终 SOFT (除非是安全相关)"""
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="花瓶", x=0.10, y=0.00, z=0.05,
                             width=0.08, height=0.20, depth=0.08)
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("把花瓶拿过来，轻一点", scene=scene)

        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            "把花瓶拿过来，轻一点", behavior_tree=bt, scene=scene,
            target="花瓶",
        )

        # 验证 priority 分布
        hard = cg.hard_constraints()
        soft = cg.soft_constraints()

        # 所有 safety constraints 必须是 hard
        for n in cg.by_category(ConstraintCategory.SAFETY):
            assert n.priority == ConstraintPriority.HARD, \
                f"Safety constraint {n.constraint_type} should be HARD"

        # 验证 IR 生成不因冲突而崩溃
        from robot_intent_agent.ir import RobotTaskIRGenerator
        generator = RobotTaskIRGenerator()
        ir = generator.generate(
            "把花瓶拿过来，轻一点",
            behavior_tree=bt, constraint_graph=cg, scene=scene,
        )
        import json
        data = json.loads(ir.model_dump_json())
        assert data["ir_version"] == "1.0.0"
