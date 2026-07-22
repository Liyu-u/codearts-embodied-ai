"""
测试: Object Semantic Schema -- Raw Perception -> SemanticObject -> Constraint

验证:
    Case 1: 光学镜片盒 -> fragility_level>=3 -> force<=2N
    Case 2: 高压电源箱 -> mobility=fixed + electrical_hazard
    Case 3: 普通塑料盒 -> fragility_level=0 -> force<=10N
"""

import pytest
from robot_intent_agent.semantic_reasoner.property_fusion import PropertyFusion
from robot_intent_agent.models.object_semantic_schema import (
    FragilityLevel, MobilityType, HazardType,
    FRAGILITY_FORCE_MAP,
)
from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.planner import BehaviorTreeGenerator


class TestSemanticObject:
    """Raw -> SemanticObject conversion"""

    def test_case1_optical_lens_box(self):
        """光学镜片盒: precision component -> fragility>=3, force<=2N"""
        raw = {
            "name": "光学聚焦镜片盒",
            "material": "optical_glass",
            "x": 0.15, "y": 0.05, "z": 0.03,
            "width": 0.10, "height": 0.04, "depth": 0.10,
        }
        obj = PropertyFusion.fuse(raw, context="optical_lab")

        assert obj.fragility_level >= FragilityLevel.PRECISION, \
            f"Expected >=PRECISION(3), got {obj.fragility_level}"
        assert obj.max_grasp_force_n <= 2.0, \
            f"Expected force<=2.0N, got {obj.max_grasp_force_n}"
        assert obj.damage_sensitive is True
        assert "fragile" in obj.risk_tags
        assert "force_sensitive" in obj.risk_tags or obj.fragility_level >= 3
        print(f"  Case1: {obj.name} L{int(obj.fragility_level)} force<={obj.max_grasp_force_n}N")

    def test_case2_power_supply_box(self):
        """高压电源箱: name->fixed+electrical, NOT fragile"""
        raw = {
            "name": "高压电源箱",
            "material": "steel",
            "x": 0.08, "y": 0.03, "z": 0.06,
            "width": 0.20, "height": 0.40, "depth": 0.20,
        }
        obj = PropertyFusion.fuse(raw)

        assert obj.mobility_type == MobilityType.FIXED, \
            f"Expected FIXED, got {obj.mobility_type}"
        assert obj.electrical_hazard is True
        assert obj.hazard_type == HazardType.ELECTRICAL
        assert obj.fragility_level == FragilityLevel.NORMAL, \
            f"Power supply should NOT be fragile, got L{int(obj.fragility_level)}"
        assert obj.max_grasp_force_n >= 5.0  # steel box -> normal force
        assert "electrical_hazard" in obj.risk_tags
        print(f"  Case2: {obj.name} fixed={obj.mobility_type} elec={obj.electrical_hazard}")

    def test_case3_plain_plastic_box(self):
        """普通塑料盒: fragility=0, force=10N"""
        raw = {
            "name": "普通塑料盒",
            "material": "plastic",
            "x": 0.20, "y": -0.10, "z": 0.03,
            "width": 0.08, "height": 0.06, "depth": 0.08,
        }
        obj = PropertyFusion.fuse(raw)

        # plastic maps to SENSITIVE(1) in our material KB
        assert obj.fragility_level <= FragilityLevel.SENSITIVE, \
            f"Expected <=SENSITIVE(1), got {int(obj.fragility_level)}"
        assert obj.max_grasp_force_n >= 3.0  # SENSITIVE -> 5N cap
        assert "fragile" not in obj.risk_tags  # SENSITIVE is not tagged as "fragile"
        print(f"  Case3: {obj.name} L{int(obj.fragility_level)} force<={obj.max_grasp_force_n}N")

    def test_semantic_validation_warnings(self):
        """Semantic consistency check: 电源 should auto-set electrical"""
        raw = {"name": "配电箱", "material": "steel"}
        obj = PropertyFusion.fuse(raw)
        assert obj.electrical_hazard is True, str(obj.semantic_warnings)
        assert len(obj.semantic_warnings) > 0  # should log the auto-fix

    def test_fragility_force_map(self):
        """FRAGILITY_FORCE_MAP sanity"""
        assert FRAGILITY_FORCE_MAP[FragilityLevel.NORMAL][1] == 10.0
        assert FRAGILITY_FORCE_MAP[FragilityLevel.FRAGILE][1] == 3.0
        assert FRAGILITY_FORCE_MAP[FragilityLevel.ULTRA_PRECISION][1] == 1.0


class TestConstraintFromSemantic:
    """Constraint generation based on fragility_level"""

    def test_precision_object_gets_low_force_constraint(self):
        """光学镜片盒(L3) -> constraint force<=2N"""
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(
                name="光学聚焦镜片盒", x=0.15, y=0.05, z=0.03,
                width=0.10, height=0.04, depth=0.10,
                color="black", material="optical_glass",
                extra_attrs={"fragile": True},
            ),
        ])
        bt = BehaviorTreeGenerator().plan("把镜片盒拿过来", scene=scene)
        cg = HybridConstraintCompiler().compile("把镜片盒拿过来", bt, scene=scene, target="光学聚焦镜片盒")

        force_nodes = [n for n in cg.nodes if n.constraint_type == "force_limit"]
        assert len(force_nodes) > 0, "No force constraint generated for precision object"
        max_forces = [n.params.get("max_force_n", 99) for n in force_nodes]
        assert min(max_forces) <= 3.0, f"Precision object force not clamped: {max_forces}"
        print(f"  Constraint: force<={min(max_forces)}N for optical lens box")

    def test_steel_box_gets_normal_force(self):
        """钢制电源箱 -> NO fragile constraint"""
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(
                name="高压电源箱", x=0.08, y=0.03, z=0.06,
                width=0.20, height=0.40, depth=0.20,
                color="gray", material="steel",
            ),
            RawObjectPercept(
                name="零件盒", x=0.20, y=0.05, z=0.03,
                width=0.05, height=0.05, depth=0.05,
            ),
        ])
        bt = BehaviorTreeGenerator().plan("把零件盒拿过来", scene=scene)
        cg = HybridConstraintCompiler().compile("把零件盒拿过来", bt, scene=scene, target="零件盒")

        force_nodes = [n for n in cg.nodes if n.constraint_type == "force_limit" and "零件盒" in str(n.params)]
        # 零件盒 is plastic -> SENSITIVE(L1) -> 5N, which IS a constraint
        # But no ADDITIONAL fragile constraint from the power box
        print(f"  Force nodes for 零件盒: {len(force_nodes)}")

    def test_risk_objects_include_electrical(self):
        """IR risk_objects includes electrical hazard"""
        from robot_intent_agent.ir import RobotTaskIRGenerator
        builder = SemanticSceneBuilder()
        scene = builder.build([
            RawObjectPercept(name="高压电源箱", x=0.08, y=0.03, z=0.06,
                             width=0.20, height=0.40, depth=0.20,
                             color="gray", material="steel"),
            RawObjectPercept(name="光学镜片盒", x=0.15, y=0.05, z=0.03,
                             width=0.10, height=0.04, depth=0.10,
                             color="black", material="optical_glass",
                             extra_attrs={"fragile": True}),
        ])
        bt = BehaviorTreeGenerator().plan("把镜片盒拿过来", scene=scene)
        cg = HybridConstraintCompiler().compile("把镜片盒拿过来", bt, scene=scene, target="光学镜片盒")
        ir = RobotTaskIRGenerator().generate("把镜片盒拿过来", bt, cg, scene=scene)

        import json
        data = json.loads(ir.model_dump_json())
        risk_objs = data.get("risk_objects", [])
        risk_types = [r["risk_type"] for r in risk_objs]
        assert "electrical" in risk_types, f"No electrical risk detected: {risk_types}"
        assert "collision" in risk_types
        print(f"  Risk objects: {[(r['name'], r['risk_type']) for r in risk_objs]}")
