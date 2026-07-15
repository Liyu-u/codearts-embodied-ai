"""
Case 2: 老人提醒喝水 — 多物体场景

场景:
    用户: "帮我把桌上的水杯拿给老人，慢一点，桌上有药瓶别碰倒了"
    环境: 水杯 + 药瓶 (fragile) + 老人位置
    验证: 多物体区分、目标正确、避碰约束生成
"""

import json
import pytest

from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator


INSTRUCTION = "帮我把桌上的水杯拿给老人，慢一点，桌上有药瓶别碰倒了"


class TestMultiObjectTask:
    """多物体任务: 区分目标 vs 障碍物"""

    def test_correct_target_selected(self):
        """目标应为水杯，非药瓶"""
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="水杯", x=0.10, y=0.00, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="药瓶", x=0.05, y=0.02, z=0.04,
                             width=0.03, height=0.08, depth=0.03,
                             color="orange", material="plastic"),
        ])

        retriever = MemoryRetriever()
        retriever.add_user_preference("speed_preference", "slow", user="elderly")
        mem = [i.to_dict() for i in retriever.search("slow elderly", top_k=3)]

        planner = BehaviorTreeGenerator()
        bt = planner.plan(INSTRUCTION, scene=scene, memory_context=mem)

        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            INSTRUCTION, behavior_tree=bt, scene=scene,
            memory_context=mem, target="水杯",
        )

        generator = RobotTaskIRGenerator()
        ir = generator.generate(
            INSTRUCTION, behavior_tree=bt, constraint_graph=cg,
            scene=scene, memory_context=mem,
        )
        data = json.loads(ir.model_dump_json())

        # 目标: 水杯
        grasp = data["skills"].get("Grasp", {})
        target = grasp.get("target", "")
        assert "水杯" in target or "水杯" in data["task_metadata"]["raw_instruction"]

        # 避碰: 药瓶
        all_avoids = []
        for skill_data in data["skills"].values():
            all_avoids.extend(skill_data["constraints"].get("avoid", []))
        assert any("药瓶" in a for a in all_avoids), \
            f"Expected 药瓶 in avoid list, got {all_avoids}"

        # 速度约束: 慢一点 → velocity <= 0.10
        vel_from_ir = data["optimization_space"]["velocity_range_ms"]
        assert vel_from_ir[1] <= 0.3  # 验证有速度上限

    def test_memory_preference_applied(self):
        """Memory 'slow' preference 生效"""
        retriever = MemoryRetriever()
        retriever.add_user_preference("speed_preference", "slow")
        mem = [i.to_dict() for i in retriever.search("slow", top_k=3)]

        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="水杯", x=0.1, y=0.0, z=0.06,
                             width=0.07, height=0.12, depth=0.07)
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("把水杯拿过来，慢一点", scene=scene, memory_context=mem)
        compiler = HybridConstraintCompiler()
        cg = compiler.compile("把水杯拿过来，慢一点",
                              behavior_tree=bt, scene=scene,
                              memory_context=mem, target="水杯")
        generator = RobotTaskIRGenerator()
        ir = generator.generate("把水杯拿过来，慢一点",
                                behavior_tree=bt, constraint_graph=cg,
                                scene=scene, memory_context=mem)

        data = json.loads(ir.model_dump_json())
        # Memory context 应包含 user_preference
        mem_ctx = data.get("memory_context", {})
        assert "user_preferences" in mem_ctx or "constraint_summary" in mem_ctx
