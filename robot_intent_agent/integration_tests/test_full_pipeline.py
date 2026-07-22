"""
Case 1: 药瓶递送 — 完整端到端管线测试

验证:
    Instruction → Memory → Scene → BT → Constraints → IR → 可序列化 JSON

场景:
    用户: "请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"
    环境: 红色药瓶 (x=0.15) + 玻璃水杯 (x=0.08, 阻挡)
    Memory: 老人偏好左手 + gentle_grasp (force_n=2.5)
"""

import json
import pytest

from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR
from robot_intent_agent.schemas.behavior_tree import BTNodeType


CANONICAL = "请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"


class TestFullPipeline:
    """端到端管线: 一条指令走完 5 个模块"""

    def test_full_pipeline_output(self):
        """全链路: Memory → Scene → BT → Constraints → IR"""
        # ===== Step 3: Memory =====
        retriever = MemoryRetriever()
        retriever.add_user_preference(
            "grip_style", "gentle", user="elderly_person"
        )
        retriever.add_skill_experience(
            "gentle_grasp", "红色药瓶",
            params={"force_n": 2.5}, success=True,
        )
        memory_results = retriever.search("老人递药轻一点", top_k=5)
        memory_items = [item.to_dict() for item in memory_results]

        assert len(memory_items) > 0, "Memory retrieval returned empty"

        # ===== Step 4: Scene =====
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(
                name="红色药瓶", x=0.15, y=0.05, z=0.03,
                width=0.03, height=0.08, depth=0.03,
                color="red", material="plastic",
            ),
            RawObjectPercept(
                name="玻璃水杯", x=0.08, y=0.03, z=0.06,
                width=0.07, height=0.12, depth=0.07,
                color="transparent", material="glass",
            ),
        ])

        assert len(scene.objects) == 2
        assert scene.find_object("红色药瓶") is not None
        assert scene.find_object("玻璃水杯") is not None

        # ===== Step 5: BT =====
        planner = BehaviorTreeGenerator()
        bt = planner.plan(CANONICAL, scene=scene, memory_context=memory_items)

        assert bt.root.type == BTNodeType.SEQUENCE
        skills = [a.skill_name for a in bt.root.flatten_actions()]
        assert "Reach" in skills
        assert "Grasp" in skills
        # v3.0: HANDOVER semantic replaces MoveTo+Release
        assert ("Handover" in skills or "Fetch" in skills or "MoveTo" in skills)
        assert "Avoid" in skills or "PlanPath" in skills

        # ===== Step 6: Constraints =====
        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            CANONICAL, behavior_tree=bt, scene=scene,
            memory_context=memory_items, target="红色药瓶",
        )

        assert len(cg.nodes) > 5
        hard = cg.hard_constraints()
        assert len(hard) >= 5, f"Expected >=5 hard constraints, got {len(hard)}"

        # ===== Step 7: IR =====
        generator = RobotTaskIRGenerator()
        ir = generator.generate(
            CANONICAL, behavior_tree=bt, constraint_graph=cg,
            scene=scene, memory_context=memory_items,
        )

        assert isinstance(ir, RobotTaskIR)
        assert ir.ir_version == "3.0.0"

        # ===== 验证: IR 可序列化 =====
        json_str = ir.model_dump_json(indent=2)
        data = json.loads(json_str)

        # 关键字段完整性
        assert data["ir_version"] == "3.0.0"
        assert data["task_metadata"]["raw_instruction"] == CANONICAL
        assert "skills" in data
        assert "behavior_tree" in data
        assert "optimization_space" in data

        # Grasp 约束
        grasp = data["skills"]["Grasp"]
        assert grasp["constraints"]["fragile"] is True
        force_val = grasp["constraints"]["force"]["max_force_n"]
        if isinstance(force_val, dict):
            force_val = force_val.get("value", 10.0)
        assert float(force_val) <= 3.0

        # Avoid 障碍物
        all_avoids = []
        for skill_data in data["skills"].values():
            all_avoids.extend(skill_data["constraints"].get("avoid", []))
        assert any("水杯" in a or "玻璃" in a for a in all_avoids), \
            f"No water cup in avoid list: {all_avoids}"

        # ===== 打印管线摘要 =====
        print("\n" + "=" * 60)
        print("  Full Pipeline: Medicine Delivery")
        print("=" * 60)
        print(f"  Instruction: {CANONICAL}")
        print(f"  Memory: {len(memory_items)} items retrieved")
        print(f"  Scene: {len(scene.objects)} objects, {len(scene.relations)} relations")
        print(f"  BT: {bt.root.name}")
        print(f"  BT Actions: {' → '.join(skills)}")
        print(f"  Constraints: {len(cg.nodes)} total ({len(hard)} hard)")
        print(f"  IR: {ir.task_metadata.task_id}")
        print(f"  Optimization: force={ir.optimization_space.force_range_n}")
        print("=" * 60)

    def test_ir_summary_readable(self):
        """IR 摘要可读"""
        retriever = MemoryRetriever()
        retriever.add_user_preference("grip_style", "gentle")
        mem = [i.to_dict() for i in retriever.search("grip", top_k=3)]

        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="红色药瓶", x=0.15, y=0.05, z=0.03,
                             width=0.03, height=0.08, depth=0.03)
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("请把红色药瓶递给我", scene=scene)

        compiler = HybridConstraintCompiler()
        cg = compiler.compile("请把红色药瓶递给我",
                              behavior_tree=bt, scene=scene,
                              target="红色药瓶")

        generator = RobotTaskIRGenerator()
        ir = generator.generate("请把红色药瓶递给我",
                                behavior_tree=bt, constraint_graph=cg,
                                scene=scene)

        summary = ir.summary()
        assert "Task IR" in summary
        assert "Task IR" in summary
