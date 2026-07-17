"""
Robot Task IR Generator 测试

全链路:  Memory → Scene → BT → Constraint → IR
规范场景: "请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"
"""

import json
import pytest

from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR
from robot_intent_agent.ir import RobotTaskIRGenerator, generate_robot_task_ir
from robot_intent_agent.memory import MemoryRetriever
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler


CANONICAL = "请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"


# ============================================================
# Fixture: 全链路产物
# ============================================================

@pytest.fixture
def memory_items():
    retriever = MemoryRetriever()
    retriever.add_user_preference("grip_style", "gentle", user="elderly")
    retriever.add_skill_experience(
        "gentle_grasp", "红色药瓶",
        params={"force_n": 2.5}, success=True,
    )
    results = retriever.search("老人递药轻一点", top_k=5)
    return [item.to_dict() for item in results]


@pytest.fixture
def scene():
    builder = SemanticSceneBuilder()
    return builder.build([
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


@pytest.fixture
def behavior_tree(scene, memory_items):
    planner = BehaviorTreeGenerator()
    return planner.plan(CANONICAL, scene=scene, memory_context=memory_items)


@pytest.fixture
def constraint_graph(behavior_tree, scene, memory_items):
    compiler = HybridConstraintCompiler()
    return compiler.compile(
        CANONICAL,
        behavior_tree=behavior_tree,
        scene=scene,
        memory_context=memory_items,
        target="红色药瓶",
    )


@pytest.fixture
def generator():
    return RobotTaskIRGenerator()


# ============================================================
# Test: IR Generator
# ============================================================

class TestRobotTaskIRGenerator:
    def test_generate_returns_ir(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """生成 RobotTaskIR"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        assert isinstance(ir, RobotTaskIR)

    def test_ir_has_metadata(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """IR 包含任务元数据"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        assert ir.task_metadata.raw_instruction == CANONICAL
        assert len(ir.task_metadata.task_id) > 0
        assert ir.task_metadata.language == "zh"

    def test_ir_has_preconditions(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """IR 包含前置条件断言"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        assertions = [
            a.assertion for a in ir.precondition_assertions.assertions
        ]
        assert "z >= 0.02" in assertions
        assert "gripper_force <= 10.0" in assertions

    def test_ir_has_skills(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """IR 的 skills 映射包含所有 BT 技能"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        assert "Reach" in ir.skills
        assert "Grasp" in ir.skills
        assert "MoveTo" in ir.skills
        assert "Release" in ir.skills
        assert "Avoid" in ir.skills

    def test_grasp_has_force_constraint(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """Grasp 技能的 constraints 包含 force 和 fragile"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        grasp = ir.skills.get("Grasp", {})
        constraints = grasp.get("constraints", {})
        assert "force" in constraints or "force_limit" in constraints
        assert constraints.get("fragile") is True

    def test_avoid_has_obstacles(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """Avoid 技能约束包含障碍物信息"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        # 在所有技能约束汇总中查找 avoid 列表
        all_avoids = []
        for skill_name, skill_data in ir.skills.items():
            constraints = skill_data.get("constraints", {})
            all_avoids.extend(constraints.get("avoid", []))
        assert len(all_avoids) > 0

    def test_skills_have_object_info(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """skills 包含目标物体的 3D 信息"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        grasp = ir.skills.get("Grasp", {})
        obj_info = grasp.get("object", {})
        if obj_info:
            assert "position" in obj_info
            assert "affordances" in obj_info

    def test_ir_has_optimization_space(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """IR 包含优化空间"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        assert ir.optimization_space.force_range_n[1] <= 3.0
        assert "max_safety" in ir.optimization_space.targets

    def test_ir_serializable_to_json(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """IR 可序列化为 JSON"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        json_str = ir.model_dump_json(indent=2)
        data = json.loads(json_str)
        assert data["ir_version"] == "3.0.0"
        assert data["task_metadata"]["raw_instruction"] == CANONICAL
        assert "skills" in data
        assert "behavior_tree" in data

    def test_ir_summary(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """IR summary() 不抛异常"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        s = ir.summary()
        assert len(s) > 0
        assert "Task IR" in s

    def test_memory_context_in_ir(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        """Memory 上下文注入到 IR"""
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        ctx = ir.memory_context
        assert "user_preferences" in ctx or "constraint_summary" in ctx

    def test_convenience_function(
        self, behavior_tree, constraint_graph, scene, memory_items
    ):
        """便捷函数可调用"""
        ir = generate_robot_task_ir(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )
        assert isinstance(ir, RobotTaskIR)


# ============================================================
# Test: Full Pipeline IR Content
# ============================================================

class TestCanonicalIR:
    """验证规范场景的 IR 内容完整性"""

    def test_full_ir_output(
        self, generator, behavior_tree, constraint_graph, scene, memory_items
    ):
        ir = generator.generate(
            CANONICAL,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
            memory_context=memory_items,
        )

        ir_dict = json.loads(ir.model_dump_json(indent=2))

        # 顶层结构
        assert "ir_version" in ir_dict
        assert "task_metadata" in ir_dict
        assert "precondition_assertions" in ir_dict
        assert "behavior_tree" in ir_dict
        assert "skills" in ir_dict
        assert "optimization_space" in ir_dict
        assert "memory_context" in ir_dict

        # Skills 结构
        for skill in ["Reach", "Grasp", "MoveTo", "Release", "Avoid"]:
            assert skill in ir_dict["skills"], f"Missing skill: {skill}"
            skill_data = ir_dict["skills"][skill]
            assert "target" in skill_data
            assert "params" in skill_data
            assert "constraints" in skill_data

        # 约束在 IR 中的体现
        grasp = ir_dict["skills"]["Grasp"]
        assert grasp["constraints"].get("fragile") is True

        # 优化边界
        opt = ir_dict["optimization_space"]
        assert opt["force_range_n"][1] <= 3.0
