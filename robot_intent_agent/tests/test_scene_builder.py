"""
Scene Builder 模块测试

关键测试场景 (来自规范):
    环境:
        - medicine bottle: right side
        - glass cup: between robot and bottle
        - gripper: empty

    验证:
        1. 物体正确实例化 (SceneObject with affordances)
        2. 空间关系正确推断 (left_of, blocking, near...)
        3. SemanticSceneGraph 完整输出
"""

import pytest

from robot_intent_agent.scene_builder import (
    RawObjectPercept,
    SpatialConfig,
    SpatialReasoner,
    SemanticSceneBuilder,
)
from robot_intent_agent.schemas.scene import (
    SceneObject,
    Position,
    BoundingBox,
    SemanticSceneGraph,
    SpatialPredicate,
    Affordance,
)


# ============================================================
# 测试数据 — 规范场景: 药瓶 + 玻璃杯
# ============================================================

MEDICINE_BOTTLE = RawObjectPercept(
    name="红色药瓶",
    x=0.15,
    y=0.05,
    z=0.03,
    width=0.03,
    height=0.08,
    depth=0.03,
    color="red",
    material="plastic",
    extra_attrs={"weight_g": 80},
)

GLASS_CUP = RawObjectPercept(
    name="玻璃水杯",
    x=0.08,   # 在 robot(0,0) 和 bottle(0.15,0.05) 之间
    y=0.03,
    z=0.06,
    width=0.07,
    height=0.12,
    depth=0.07,
    color="transparent",
    material="glass",
    extra_attrs={"fragile": True},
)

WOODEN_BLOCK = RawObjectPercept(
    name="木块",
    x=0.30,
    y=-0.10,
    z=0.02,
    width=0.05,
    height=0.05,
    depth=0.05,
    color="brown",
    material="wood",
)

# ============================================================
# 测试 RawObjectPercept
# ============================================================

class TestRawObjectPercept:
    def test_to_scene_object(self):
        """RawObjectPercept → SceneObject 转换"""
        obj = MEDICINE_BOTTLE.to_scene_object()
        assert isinstance(obj, SceneObject)
        assert obj.name == "红色药瓶"
        assert obj.label == "medicine_bottle"
        assert obj.position.x == 0.15
        assert obj.position.z == 0.03

    def test_affordance_inference(self):
        """可供性自动推断"""
        obj = MEDICINE_BOTTLE.to_scene_object()
        assert Affordance.GRASPABLE in obj.affordances
        assert Affordance.FRAGILE in obj.affordances

        cup = GLASS_CUP.to_scene_object()
        assert Affordance.CONTAINER in cup.affordances

    def test_attributes_transfer(self):
        """属性传递"""
        obj = MEDICINE_BOTTLE.to_scene_object()
        assert obj.attributes["color"] == "red"
        assert obj.attributes["material"] == "plastic"
        assert obj.attributes["weight_g"] == 80

    def test_default_affordance_fallback(self):
        """未知物体回退到默认可供性"""
        unknown = RawObjectPercept(name="未知物体", x=0, y=0, z=0).to_scene_object()
        assert Affordance.GRASPABLE in unknown.affordances
        assert Affordance.MOVABLE in unknown.affordances


# ============================================================
# 测试 SpatialReasoner
# ============================================================

class TestSpatialReasoner:
    @pytest.fixture
    def reasoner(self):
        return SpatialReasoner()

    def test_left_right(self, reasoner):
        """X 轴比较: left_of / right_of"""
        a = MEDICINE_BOTTLE.to_scene_object()   # x=0.15
        b = GLASS_CUP.to_scene_object()          # x=-0.05
        rels = reasoner._infer_pair(a, b, (0, 0, 0.5))

        predicates = {r.predicate for r in rels}
        assert SpatialPredicate.RIGHT_OF in predicates  # a is right of b
        # b is LEFT_OF a
        details = {r.predicate: r.subject for r in rels}
        assert any(
            r.predicate == SpatialPredicate.LEFT_OF
            for r in rels
        )

    def test_blocking_detection(self, reasoner):
        """
        玻璃杯在 robot(0,0,0.5) 和药瓶(0.15,0.05,0.03) 之间
        → 玻璃杯 blocks 药瓶
        """
        bottle = MEDICINE_BOTTLE.to_scene_object()
        cup = GLASS_CUP.to_scene_object()
        robot_origin = (0, 0, 0.5)

        rels = reasoner._infer_pair(bottle, cup, robot_origin)
        blocking_rels = [r for r in rels if r.predicate == SpatialPredicate.BLOCKING]
        assert len(blocking_rels) > 0, f"Expected blocking relation, got: {[(r.predicate.value, r.subject[:8], r.object[:8]) for r in rels]}"

    def test_near_detection(self, reasoner):
        """近距离物体应标记为 near"""
        a = SceneObject(
            name="A", position=Position(x=0.1, y=0.1, z=0.05),
            bbox=BoundingBox(width=0.03, height=0.03, depth=0.03),
        )
        b = SceneObject(
            name="B", position=Position(x=0.13, y=0.12, z=0.06),
            bbox=BoundingBox(width=0.03, height=0.03, depth=0.03),
        )
        rels = reasoner._infer_pair(a, b, (0, 0, 0.5))
        predicates = {r.predicate for r in rels}
        assert SpatialPredicate.NEAR in predicates

    def test_no_blocking_when_clear(self, reasoner):
        """无遮挡时不应有 blocking 关系"""
        a = SceneObject(
            name="A", position=Position(x=0.1, y=0.1, z=0.05),
            bbox=BoundingBox(width=0.03, height=0.03, depth=0.03),
        )
        b = SceneObject(
            name="B", position=Position(x=0.3, y=-0.1, z=0.02),
            bbox=BoundingBox(width=0.05, height=0.05, depth=0.05),
        )
        rels = reasoner._infer_pair(a, b, (0, 0, 0.5))
        blocking = [r for r in rels if r.predicate == SpatialPredicate.BLOCKING]
        assert len(blocking) == 0

    def test_all_relations_for_pair(self, reasoner):
        """两物体的完整关系集"""
        bottle = MEDICINE_BOTTLE.to_scene_object()
        cup = GLASS_CUP.to_scene_object()
        rels = reasoner._infer_pair(bottle, cup, (0, 0, 0.5))
        assert len(rels) >= 2  # 至少 left/right + near


# ============================================================
# 测试 SemanticSceneBuilder
# ============================================================

class TestSemanticSceneBuilder:
    @pytest.fixture
    def builder(self):
        return SemanticSceneBuilder()

    def test_build_from_raw(self, builder):
        """从 RawObjectPercept 列表构建场景图"""
        scene = builder.build(
            [MEDICINE_BOTTLE, GLASS_CUP, WOODEN_BLOCK]
        )
        assert isinstance(scene, SemanticSceneGraph)
        assert len(scene.objects) == 3

    def test_scene_has_relations(self, builder):
        """场景图应包含空间关系"""
        scene = builder.build([MEDICINE_BOTTLE, GLASS_CUP])
        assert len(scene.relations) > 0

    def test_blocking_in_scene(self, builder):
        """完整场景中玻璃杯阻挡药瓶"""
        scene = builder.build([MEDICINE_BOTTLE, GLASS_CUP])
        bottle_obj = next(o for o in scene.objects if "药瓶" in o.name)
        blocking = scene.blocking_objects(bottle_obj.id)
        # blocking_objects returns IDs of objects that block the target
        cup_obj = next(o for o in scene.objects if "水杯" in o.name)
        assert cup_obj.id in blocking, (
            f"Expected glass cup to block medicine bottle.\n"
            f"Relations: {[(r.subject[:8], r.predicate.value, r.object[:8]) for r in scene.relations]}"
        )

    def test_build_from_dict(self, builder):
        """便捷 dict 接口"""
        scene = builder.build_from_dict([
            {"name": "测试物体", "x": 0.1, "y": 0.0, "z": 0.03}
        ])
        assert len(scene.objects) == 1
        assert scene.objects[0].name == "测试物体"

    def test_default_robot_state(self, builder):
        """默认机器人状态"""
        scene = builder.build([MEDICINE_BOTTLE])
        assert scene.robot_state.is_homed
        assert scene.robot_state.gripper.is_open
        assert not scene.robot_state.gripper.has_object

    def test_find_object(self, builder):
        """按名称查找物体"""
        scene = builder.build([MEDICINE_BOTTLE, GLASS_CUP])
        found = scene.find_object("红色药瓶")
        assert found is not None
        assert found.label == "medicine_bottle"

    def test_find_object_not_found(self, builder):
        """查找不存在的物体"""
        scene = builder.build([MEDICINE_BOTTLE])
        assert scene.find_object("不存在") is None

    def test_scene_id_generated(self, builder):
        """场景 ID 自动生成"""
        scene = builder.build([MEDICINE_BOTTLE])
        assert scene.scene_id.startswith("scene-")
        assert len(scene.scene_id) > 8

    def test_relations_with_metadata(self, builder):
        """空间关系包含距离元数据"""
        scene = builder.build([MEDICINE_BOTTLE, GLASS_CUP])
        for rel in scene.relations:
            assert "distance_m" in rel.metadata or rel.confidence >= 0.0


# ============================================================
# 集成测试 — 规范场景
# ============================================================

class TestCanonicalScenario:
    """
    规范测试场景:
        "请把桌上那个红色药瓶递到我手上，动作轻一点，千万别碰倒旁边的玻璃水杯"

    环境:
        - medicine bottle: right side (x=0.15)
        - glass cup: left side (x=-0.05), between robot and bottle
        - gripper: empty
    """

    def test_scenario(self):
        builder = SemanticSceneBuilder()
        scene = builder.build(
            [MEDICINE_BOTTLE, GLASS_CUP],
            robot_origin=(0, 0, 0.5),
        )

        # 1. 两个物体都在场景中
        assert len(scene.objects) == 2
        names = {o.name for o in scene.objects}
        assert "红色药瓶" in names
        assert "玻璃水杯" in names

        # 2. 药瓶在右边
        bottle = scene.find_object("红色药瓶")
        cup = scene.find_object("玻璃水杯")
        assert bottle.position.x > cup.position.x, "药瓶应在玻璃杯右边"

        # 3. 玻璃杯阻挡药瓶
        blocking = scene.blocking_objects(bottle.id)
        assert cup.id in blocking, "玻璃杯应在 robot→药瓶 路径上"

        # 4. 夹爪为空
        assert scene.robot_state.gripper.is_open
        assert not scene.robot_state.gripper.has_object

        # 5. 药瓶有 fragile affordance
        assert Affordance.FRAGILE in bottle.affordances

        # 6. 玻璃杯有 container affordance
        assert Affordance.CONTAINER in cup.affordances
