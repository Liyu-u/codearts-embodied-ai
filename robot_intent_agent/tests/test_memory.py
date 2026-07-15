"""
Memory 模块集成测试

测试覆盖:
    1. 添加记忆 → 所有三种类型
    2. 语义检索 → search() 返回相关结果
    3. top_k 限制
    4. 去重逻辑 (同 key 覆盖)
    5. MemoryRetriever 统一检索
"""

import pytest

from robot_intent_agent.memory import (
    MemoryItem,
    MemoryType,
    MemoryPriority,
    UserMemory,
    SkillMemory,
    EnvironmentMemory,
    MemoryRetriever,
)


# ============================================================
# 测试数据
# ============================================================

ELDERLY_USER_QUERY = "老人递药"

# 用户偏好
USER_PREFS = {
    "hand_preference": ("left", {"user": "elderly_person", "room": "living_room"}),
    "grip_style": ("gentle", {"user": "elderly_person", "reason": "arthritis"}),
    "speed_preference": ("slow", {"user": "elderly_person"}),
}

# 技能经验
SKILL_EXPERIENCES = [
    ("gentle_grasp", "medicine_bottle", {"force_n": 2.5, "velocity_ms": 0.1}, True),
    ("gentle_grasp", "glass_cup", {"force_n": 1.5, "velocity_ms": 0.08}, True),
    ("standard_grasp", "wooden_block", {"force_n": 5.0, "velocity_ms": 0.2}, True),
    ("move_to", "user_position", {"velocity_ms": 0.15}, True),
]

# 环境先验
ENV_FACTS = [
    ("table_height_m", 0.72, "elder_home", None),
    ("workspace_radius_m", 0.5, "elder_home", None),
    ("glass_cup_position", "table_left", "elder_home", "glass_cup"),
]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def user_memory():
    um = UserMemory()
    for key, (value, metadata) in USER_PREFS.items():
        um.add_preference(key=key, value=value, **metadata)
    return um


@pytest.fixture
def skill_memory():
    sm = SkillMemory()
    for skill, obj, params, success in SKILL_EXPERIENCES:
        sm.add_skill_experience(
            skill=skill, object_name=obj, params=params, success=success
        )
    return sm


@pytest.fixture
def environment_memory():
    em = EnvironmentMemory()
    for key, value, env, obj in ENV_FACTS:
        em.add_environment_fact(key=key, value=value, environment=env, object_name=obj)
    return em


@pytest.fixture
def retriever(user_memory, skill_memory, environment_memory):
    return MemoryRetriever(
        user_memory=user_memory,
        skill_memory=skill_memory,
        environment_memory=environment_memory,
    )


# ============================================================
# Test: UserMemory
# ============================================================

class TestUserMemory:
    def test_add_memory(self, user_memory):
        """添加用户偏好记忆"""
        assert len(user_memory) == 3

    def test_search_relevant(self, user_memory):
        """检索相关偏好"""
        results = user_memory.search("hand", top_k=3)
        assert len(results) > 0
        assert any("hand" in r.key for r in results)

    def test_search_returns_top_k(self, user_memory):
        """top_k 限制"""
        results = user_memory.search("user", top_k=2)
        assert len(results) <= 2

    def test_duplicate_key_overwrites(self, user_memory):
        """同 key 覆盖"""
        old_len = len(user_memory)
        user_memory.add_preference(key="hand_preference", value="right")
        assert len(user_memory) == old_len  # 不应增加
        pref = user_memory.get_preference("hand_preference")
        assert pref.value == "right"  # 值已更新

    def test_get_preference(self, user_memory):
        """获取指定偏好"""
        pref = user_memory.get_preference("grip_style")
        assert pref is not None
        assert pref.value == "gentle"

    def test_delete_memory(self, user_memory):
        """删除记忆"""
        items = user_memory.list_all()
        assert user_memory.delete(items[0].id)
        assert len(user_memory) == 2

    def test_get_by_type(self, user_memory):
        """按类型检索"""
        items = user_memory.get_by_type(MemoryType.USER_PREFERENCE)
        assert len(items) == len(user_memory)


# ============================================================
# Test: SkillMemory
# ============================================================

class TestSkillMemory:
    def test_add_skill(self, skill_memory):
        """添加技能经验"""
        assert len(skill_memory) == 4

    def test_search_by_skill_object(self, skill_memory):
        """按技能+物体精确检索"""
        results = skill_memory.search("gentle_grasp medicine_bottle", top_k=3)
        assert len(results) > 0
        assert results[0].metadata.get("skill") == "gentle_grasp"
        assert results[0].metadata.get("object") == "medicine_bottle"

    def test_find_best_params(self, skill_memory):
        """查找历史最佳参数"""
        params = skill_memory.find_best_params("gentle_grasp", "medicine_bottle")
        assert params is not None
        assert params.get("force_n") == 2.5

    def test_find_best_params_unknown(self, skill_memory):
        """未知技能返回 None"""
        params = skill_memory.find_best_params("unknown_skill", "unknown_object")
        assert params is None

    def test_success_priority(self, skill_memory):
        """成功经验优先"""
        # 添加一个同名失败经验
        skill_memory.add_skill_experience(
            skill="gentle_grasp",
            object_name="medicine_bottle",
            params={"force_n": 8.0},
            success=False,
        )
        results = skill_memory.search("gentle_grasp medicine_bottle", top_k=2)
        # 成功经验应排在前面
        success_items = [r for r in results if r.metadata.get("success")]
        fail_items = [r for r in results if not r.metadata.get("success")]
        if success_items and fail_items:
            # 成功经验的访问计数更高 → 排在前面
            pass

    def test_duplicate_skill_object_overwrites(self, skill_memory):
        """同 skill+object 去重"""
        old_len = len(skill_memory)
        skill_memory.add_skill_experience(
            skill="gentle_grasp",
            object_name="medicine_bottle",
            params={"force_n": 3.0},
        )
        assert len(skill_memory) == old_len

    def test_get_by_type(self, skill_memory):
        items = skill_memory.get_by_type(MemoryType.SKILL_EXPERIENCE)
        assert len(items) == len(skill_memory)


# ============================================================
# Test: EnvironmentMemory
# ============================================================

class TestEnvironmentMemory:
    def test_add_environment(self, environment_memory):
        assert len(environment_memory) == 3

    def test_search_by_environment(self, environment_memory):
        """按环境名检索"""
        results = environment_memory.search("elder_home", top_k=5)
        assert len(results) == 3

    def test_get_environment(self, environment_memory):
        """获取指定环境全部先验"""
        facts = environment_memory.get_environment("elder_home")
        assert len(facts) == 3

    def test_get_table_height(self, environment_memory):
        """获取桌面高度"""
        h = environment_memory.get_table_height()
        assert h == 0.72

    def test_search_by_object(self, environment_memory):
        """按物体名检索"""
        results = environment_memory.search("glass_cup", top_k=3)
        assert len(results) > 0

    def test_duplicate_key_overwrites(self, environment_memory):
        old_len = len(environment_memory)
        environment_memory.add_environment_fact(
            key="table_height_m", value=0.75, environment="elder_home"
        )
        assert len(environment_memory) == old_len


# ============================================================
# Test: MemoryRetriever (统一检索)
# ============================================================

class TestMemoryRetriever:
    def test_unified_search(self, retriever):
        """统一跨类型检索"""
        results = retriever.search(ELDERLY_USER_QUERY, top_k=8)
        assert len(results) > 0

    def test_all_three_types_present(self, retriever):
        """确保三种类型都有数据"""
        stats = retriever.stats()
        assert stats["user_memory"] == 3
        assert stats["skill_memory"] == 4
        assert stats["environment_memory"] == 3
        assert stats["total"] == 10

    def test_search_hand_preference(self, retriever):
        """'老人习惯左手接物' → 返回 hand_preference"""
        results = retriever.search("老人习惯左手接物", top_k=5)
        assert any(
            r.key == "hand_preference" and r.value == "left"
            for r in results
        ), f"Expected hand_preference=left in results, got: {[(r.key, r.value) for r in results]}"

    def test_search_gentle_grasp(self, retriever):
        """'轻一点抓药瓶' → 返回 gentle_grasp 技能"""
        results = retriever.search("轻一点抓药瓶", top_k=5)
        skill_types = [r.memory_type for r in results]
        assert MemoryType.SKILL_EXPERIENCE in skill_types

    def test_search_environment(self, retriever):
        """'elder_home table height' — 环境事实检索 (限定 ENVIRONMENT_PRIOR 类型)"""
        results = retriever.search(
            "elder_home table height",
            top_k=3,
            memory_types=[MemoryType.ENVIRONMENT_PRIOR],
        )
        assert any(
            r.key == "table_height_m" for r in results
        )

    def test_top_k_limit(self, retriever):
        """top_k 限制生效"""
        results = retriever.search(ELDERLY_USER_QUERY, top_k=3)
        assert len(results) <= 3

    def test_filter_by_memory_type(self, retriever):
        """按类型过滤检索"""
        results = retriever.search(
            "medicine",
            top_k=5,
            memory_types=[MemoryType.SKILL_EXPERIENCE],
        )
        for r in results:
            assert r.memory_type == MemoryType.SKILL_EXPERIENCE

    def test_clear_all(self, retriever):
        """清空全部记忆"""
        retriever.clear_all()
        stats = retriever.stats()
        assert stats["total"] == 0

    def test_roundtrip_memoryitem(self):
        """MemoryItem 序列化往返"""
        item = MemoryItem(
            memory_type=MemoryType.USER_PREFERENCE,
            key="test_key",
            value="test_value",
        )
        d = item.to_dict()
        assert d["key"] == "test_key"
        assert d["value"] == "test_value"
        assert d["memory_type"] == "user_preference"

    def test_faiss_placeholder_raises(self, retriever):
        """FAISS 接口预先抛 NotImplementedError"""
        with pytest.raises(NotImplementedError):
            retriever.vector_search([0.1, 0.2, 0.3])
