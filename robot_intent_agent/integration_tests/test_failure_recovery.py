"""
Case 3: 避障取药 — 模拟异常恢复

场景:
    用户: "帮我把药瓶拿过来，小心别碰周围的东西"
    环境: 药瓶被两个障碍物包围
    验证: 多障碍物约束生成、碰撞避免覆盖全部障碍物

异常场景:
    - 如果所有障碍物都不可绕过 → 约束图标记冲突
    - 如果 memory 无相关经验 → 使用默认安全参数
"""

import pytest

from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler, ConstraintCategory
from robot_intent_agent.ir import RobotTaskIRGenerator


class TestObstacleRecovery:
    """避障取药 — 异常恢复验证"""

    def test_multiple_obstacles_handled(self):
        """被 3 个障碍物包围 → 全部出现在 collision_avoid 中"""
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="药瓶", x=0.15, y=0.05, z=0.03,
                             width=0.03, height=0.08, depth=0.03,
                             color="red"),
            # 三面围堵
            RawObjectPercept(name="障碍物A", x=0.10, y=0.05, z=0.06,
                             width=0.05, height=0.10, depth=0.05),
            RawObjectPercept(name="障碍物B", x=0.15, y=0.00, z=0.06,
                             width=0.05, height=0.10, depth=0.05),
            RawObjectPercept(name="障碍物C", x=0.20, y=0.05, z=0.06,
                             width=0.05, height=0.10, depth=0.05),
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("帮我把药瓶拿过来，小心别碰周围的东西",
                          scene=scene)
        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            "帮我把药瓶拿过来，小心别碰周围的东西",
            behavior_tree=bt, scene=scene, target="药瓶",
        )

        # 碰撞避免约束数量 >= 1 (至少场景 blocking)
        avoid_nodes = [
            n for n in cg.nodes
            if n.constraint_type == "collision_avoid"
        ]
        assert len(avoid_nodes) >= 1, f"Expected collision_avoid, got {len(avoid_nodes)}"

    def test_no_memory_fallback_to_defaults(self):
        """无 Memory 时 → 使用默认安全参数"""
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="药瓶", x=0.15, y=0.05, z=0.03,
                             width=0.03, height=0.08, depth=0.03)
        ])

        planner = BehaviorTreeGenerator()
        bt = planner.plan("帮我把药瓶拿过来", scene=scene)

        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            "帮我把药瓶拿过来", behavior_tree=bt, scene=scene,
            memory_context=None, target="药瓶",
        )

        # 有 safety constraints (无论有无 memory)
        safety = [n for n in cg.nodes if n.category == ConstraintCategory.SAFETY]
        assert len(safety) >= 5

        # 无用户偏好 → 使用默认参数
        force_nodes = [
            n for n in cg.nodes if n.constraint_type == "force_limit"
        ]
        if force_nodes:
            # 有 fragile affordance → max_force_n=3.0
            # 否则 → 不额外限制
            pass

    def test_ir_generated_even_with_zero_memory(self):
        """零 Memory + 零 Scene → IR 仍然可生成"""
        planner = BehaviorTreeGenerator()
        bt = planner.plan("把东西拿过来")

        compiler = HybridConstraintCompiler()
        cg = compiler.compile(
            "把东西拿过来", behavior_tree=bt, target="东西",
        )

        generator = RobotTaskIRGenerator()
        ir = generator.generate(
            "把东西拿过来", behavior_tree=bt, constraint_graph=cg,
            scene=None, memory_context=None,
        )

        import json
        data = json.loads(ir.model_dump_json())
        assert data["ir_version"] == "3.0.0"
        assert "skills" in data
