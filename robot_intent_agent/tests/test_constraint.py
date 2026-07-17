"""
Hybrid Constraint Compiler 模块测试

规范场景:
    "请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"

期望约束:
    1. Safety:   z >= 0.02m (红线)
    2. Safety:   joint limits +/-2.9 rad
    3. Safety:   gripper force <= 10N
    4. Safety:   workspace bounds
    5. Safety:   human proximity
    6. Physical: force(药瓶) <= 3.0N (来自 "轻一点")
    7. Physical: velocity <= 0.10 m/s (来自 "轻一点" → "小心" 模式)
    8. Spatial:  collision_avoid(水杯) (来自场景 blocking)
    9. Physical: force <= 3.0N (来自 fragile affordance)
"""

import pytest

from robot_intent_agent.constraint import (
    ConstraintNode,
    ConstraintGraph,
    ConstraintCategory,
    ConstraintPriority,
    ConstraintStatus,
    SpatialConstraint,
    PhysicalConstraint,
    SafetyConstraint,
    ConstraintRuleEngine,
    HybridConstraintCompiler,
    compile_constraints,
)
from robot_intent_agent.scene_builder import (
    SemanticSceneBuilder,
    RawObjectPercept,
)
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.memory import MemoryRetriever


CANONICAL_INSTRUCTION = "请把桌上的红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"


# ============================================================
# Fixtures
# ============================================================

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
def behavior_tree(scene):
    planner = BehaviorTreeGenerator()
    return planner.plan(
        CANONICAL_INSTRUCTION,
        scene=scene,
    )


@pytest.fixture
def memory_items():
    retriever = MemoryRetriever()
    retriever.add_user_preference("grip_style", "gentle", user="elderly")
    retriever.add_skill_experience(
        "gentle_grasp", "红色药瓶",
        params={"force_n": 2.5, "velocity_ms": 0.10}, success=True,
    )
    results = retriever.search("老人递药轻一点", top_k=5)
    return [item.to_dict() for item in results]


@pytest.fixture
def compiler():
    return HybridConstraintCompiler()


# ============================================================
# Test: SafetyConstraint
# ============================================================

class TestSafetyConstraint:
    def test_mandatory_set_has_five_constraints(self):
        """安全红线: 5 条强制约束"""
        constraints = SafetyConstraint.mandatory_set("test_target")
        assert len(constraints) == 5
        for c in constraints:
            assert c.priority == ConstraintPriority.HARD
            assert c.category == ConstraintCategory.SAFETY

    def test_z_axis_floor(self):
        c = SafetyConstraint.z_axis_floor()
        assert c.expression == "z >= 0.02 m"
        assert c.priority == ConstraintPriority.HARD

    def test_joint_limits(self):
        c = SafetyConstraint.joint_limits()
        assert "2.9" in c.expression
        assert "rad" in c.expression

    def test_max_gripper_force(self):
        c = SafetyConstraint.max_gripper_force()
        assert c.params["max_force_n"] == 10.0
        assert c.applies_to_skill == "Grasp"

    def test_workspace_bounds(self):
        c = SafetyConstraint.workspace_bounds()
        assert c.params["z_range"] == [0.02, 0.5]

    def test_human_proximity(self):
        c = SafetyConstraint.human_proximity()
        assert c.params["slow_zone_radius_m"] == 0.3
        assert c.params["max_velocity_in_zone_ms"] == 0.10


# ============================================================
# Test: PhysicalConstraint
# ============================================================

class TestPhysicalConstraint:
    def test_force_limit(self):
        c = PhysicalConstraint.force_limit("药瓶", max_force_n=3.0)
        assert c.constraint_type == "force_limit"
        assert c.params["max_force_n"] == 3.0
        assert c.params["min_force_n"] == 0.1

    def test_velocity_limit(self):
        c = PhysicalConstraint.velocity_limit(0.10)
        assert c.constraint_type == "velocity_limit"
        assert c.params["max_linear_ms"] == 0.10

    def test_height_limit(self):
        c = PhysicalConstraint.height_limit(min_z_m=0.02)
        assert c.constraint_type == "height_limit"
        assert "0.02" in c.expression

    def test_release_height(self):
        c = PhysicalConstraint.release_height("药瓶", max_height_m=0.10)
        assert c.constraint_type == "release_height"
        assert c.params["max_height_m"] == 0.10
        assert c.applies_to_skill == "Release"


# ============================================================
# Test: SpatialConstraint
# ============================================================

class TestSpatialConstraint:
    def test_collision_avoid(self):
        c = SpatialConstraint.collision_avoid("水杯", min_distance_m=0.05)
        assert c.constraint_type == "collision_avoid"
        assert c.params["obstacle"] == "水杯"
        assert c.params["min_distance_m"] == 0.05

    def test_trajectory_constraint(self):
        c = SpatialConstraint.trajectory_constraint([
            {"x": 0, "y": 0, "z": 0.15},
        ])
        assert c.constraint_type == "trajectory"

    def test_region_constraint(self):
        c = SpatialConstraint.region_constraint(
            (-0.3, 0.3), (-0.3, 0.3), (0.02, 0.4)
        )
        assert c.constraint_type == "region"


# ============================================================
# Test: ConstraintRuleEngine
# ============================================================

class TestConstraintRuleEngine:
    @pytest.fixture
    def engine(self):
        return ConstraintRuleEngine()

    def test_extract_force_from_qing(self, engine):
        """'轻一点' → force <= 3.0N"""
        constraints = engine.extract(
            "帮我把药瓶递给我，轻一点", target="红色药瓶"
        )
        force_constraints = [
            c for c in constraints if c.constraint_type == "force_limit"
        ]
        assert len(force_constraints) >= 1
        assert force_constraints[0].params["max_force_n"] == 3.0

    def test_extract_velocity_from_man(self, engine):
        """'慢一点' → velocity <= 0.10"""
        constraints = engine.extract(
            "帮我把药瓶递给我，慢一点", target="红色药瓶"
        )
        vel_constraints = [
            c for c in constraints if c.constraint_type == "velocity_limit"
        ]
        assert len(vel_constraints) >= 1
        assert vel_constraints[0].params["max_linear_ms"] == 0.10

    def test_extract_avoid_from_nl(self, engine, scene):
        """'别碰水杯' → collision_avoid(水杯) — 需场景实体接地"""
        constraints = engine.extract(
            "帮我把药瓶递给我，不要碰水杯", target="红色药瓶", scene=scene
        )
        avoid = [c for c in constraints if c.constraint_type == "collision_avoid"]
        assert len(avoid) >= 1
        assert any("水杯" in c.params.get("obstacle", "") for c in avoid)

    def test_extract_from_scene_blocking(self, engine, scene):
        """场景 blocking → collision_avoid"""
        constraints = engine.extract(
            "帮我把红色药瓶递给我", scene=scene, target="红色药瓶"
        )
        avoid = [c for c in constraints if c.constraint_type == "collision_avoid"]
        assert len(avoid) >= 1

    def test_extract_fragile_object(self, engine, scene):
        """fragile 物体 → force_limit"""
        constraints = engine.extract(
            "帮我把红色药瓶递给我", scene=scene, target="红色药瓶"
        )
        force = [c for c in constraints if c.constraint_type == "force_limit"]
        # 红色药瓶 is fragile → force_limit should be generated
        assert len(force) >= 1


# ============================================================
# Test: HybridConstraintCompiler (集成)
# ============================================================

class TestHybridConstraintCompiler:
    def test_compile_returns_graph(self, compiler, behavior_tree, scene, memory_items):
        """编译返回 ConstraintGraph"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        assert isinstance(graph, ConstraintGraph)
        assert len(graph.nodes) > 0

    def test_safety_mandatory_present(self, compiler, behavior_tree, scene, memory_items):
        """安全红线全部存在"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        safety = graph.by_category(ConstraintCategory.SAFETY)
        assert len(safety) >= 5, f"Expected >=5 safety constraints, got {len(safety)}"

    def test_force_constraint_present(self, compiler, behavior_tree, scene, memory_items):
        """'轻一点' + fragile → force_limit 约束"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        force_limits = [
            n for n in graph.nodes
            if n.constraint_type == "force_limit"
        ]
        assert len(force_limits) >= 1
        # 取最严格的 force_limit
        max_forces = [n.params.get("max_force_n", 10.0) for n in force_limits]
        assert min(max_forces) <= 3.0, f"Expected max_force_n <= 3.0, got {min(max_forces)}"

    def test_collision_avoid_present(self, compiler, behavior_tree, scene, memory_items):
        """场景 blocking + '别碰水杯' → collision_avoid"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        avoid = [
            n for n in graph.nodes
            if n.constraint_type == "collision_avoid"
        ]
        assert len(avoid) >= 1
        obstacles = [n.params.get("obstacle", "") for n in avoid]
        assert any("水杯" in o or "玻璃" in o for o in obstacles)

    def test_graph_summary(self, compiler, behavior_tree, scene, memory_items):
        """摘要生成不抛异常"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        summary = graph.summary()
        assert len(summary) > 0
        assert "HARD" in summary or "hard" in summary

    def test_bind_to_skills(self, compiler, behavior_tree, scene, memory_items):
        """约束正确绑定到技能"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        bindings = graph.bind_to_skills()
        # Grasp 应该有 force_limit + max_gripper_force
        if "Grasp" in bindings:
            grasp_types = [n.constraint_type for n in bindings["Grasp"]]
            assert "force_limit" in grasp_types or "max_gripper_force" in grasp_types

    def test_hard_vs_soft(self, compiler, behavior_tree, scene, memory_items):
        """硬约束 vs 软约束分类"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        hard = graph.hard_constraints()
        soft = graph.soft_constraints()
        assert len(hard) >= 5  # safety red lines
        assert len(soft) >= 0
        total = len(hard) + len(soft)
        assert total == len(graph.nodes)

    def test_no_duplicates(self, compiler, behavior_tree, scene, memory_items):
        """去重后无重复"""
        graph = compiler.compile(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        # 检查同类型+同目标+同技能无重复
        keys = [
            f"{n.constraint_type}:{n.target}:{n.applies_to_skill}"
            for n in graph.nodes
        ]
        assert len(keys) == len(set(keys)), f"Duplicates found: {len(keys)} vs {len(set(keys))}"

    def test_convenience_function(self, behavior_tree, scene, memory_items):
        """便捷函数 compile_constraints 可用"""
        graph = compile_constraints(
            CANONICAL_INSTRUCTION,
            behavior_tree=behavior_tree,
            scene=scene,
            memory_context=memory_items,
            target="红色药瓶",
        )
        assert isinstance(graph, ConstraintGraph)
        assert len(graph.nodes) > 0


# ============================================================
# Test: ConstraintNode
# ============================================================

class TestConstraintNode:
    def test_to_dict(self):
        c = SpatialConstraint.collision_avoid("cup", 0.05)
        d = c.to_dict()
        assert d["constraint_type"] == "collision_avoid"
        assert d["category"] == "spatial"
        assert d["priority"] == "hard"

    def test_evaluate_with_fn(self):
        c = PhysicalConstraint.velocity_limit(0.10)
        c.check_fn = lambda state: state.get("velocity_ms", 0) <= 0.10
        assert c.evaluate({"velocity_ms": 0.05}) == ConstraintStatus.SATISFIED
        assert c.evaluate({"velocity_ms": 0.20}) == ConstraintStatus.VIOLATED

    def test_evaluate_without_fn(self):
        c = PhysicalConstraint.velocity_limit(0.10)
        assert c.evaluate({}) == ConstraintStatus.UNKNOWN
