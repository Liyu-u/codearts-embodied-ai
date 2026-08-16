"""
Memory Retriever — 统一检索层

聚合 UserMemory / SkillMemory / EnvironmentMemory,
提供统一 search(query, top_k) 接口。

当前后端: in-memory (keyword-based scoring)
预留接口: FAISS vector search (embedding 字段已预留)
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .base import MemoryItem, MemoryType
from .user_memory import UserMemory
from .skill_memory import SkillMemory
from .environment_memory import EnvironmentMemory


class MemoryRetriever:
    """
    统一记忆检索器。

    架构:
        MemoryRetriever
            ├── UserMemory        (用户偏好)
            ├── SkillMemory       (技能经验)
            └── EnvironmentMemory (环境先验)

    用法:
        retriever = MemoryRetriever()
        retriever.add_user_preference("hand_preference", "left", user="elderly")
        retriever.add_skill_experience("gentle_grasp", "medicine_bottle", params={"force_n": 2.5})

        results = retriever.search("老人递药", top_k=5)
        # → 返回 user_preference + skill_experience + environment_prior
    """

    def __init__(
        self,
        user_memory: Optional[UserMemory] = None,
        skill_memory: Optional[SkillMemory] = None,
        environment_memory: Optional[EnvironmentMemory] = None,
    ):
        self.user_memory = user_memory or UserMemory()
        self.skill_memory = skill_memory or SkillMemory()
        self.environment_memory = environment_memory or EnvironmentMemory()

    # ============================================================
    # 统一检索
    # ============================================================

    def search(
        self,
        query: str,
        top_k: int = 5,
        memory_types: Optional[List[MemoryType]] = None,
    ) -> List[MemoryItem]:
        """
        跨 Memory 类型统一检索。

        Args:
            query:       自然语言查询
            top_k:       每类返回数
            memory_types:限定检索的记忆类型 (None=全部)

        Returns:
            MemoryItem 列表 (按相关性降序)
        """
        all_results: List[MemoryItem] = []

        sources = self._get_sources(memory_types)

        for source_name, source in sources:
            results = source.search(query, top_k=top_k)
            all_results.extend(results)

        # 全局排序: 高优先级 + 高匹配分 (来自各 source 的 search 已排序)
        all_results.sort(
            key=lambda item: (
                0 if item.priority.value == "high"
                else 1 if item.priority.value == "medium"
                else 2,
                -item.access_count,
            )
        )

        return all_results[:top_k]

    # ============================================================
    # 便捷添加方法
    # ============================================================

    def add_user_preference(
        self, key: str, value, priority=None, **metadata
    ) -> str:
        """添加快捷用户偏好"""
        from .base import MemoryPriority
        return self.user_memory.add_preference(
            key=key, value=value,
            priority=priority or MemoryPriority.MEDIUM,
            **metadata,
        )

    def add_skill_experience(
        self, skill: str, object_name: str, params=None, success=True, **metadata
    ) -> str:
        """添加快捷技能经验"""
        return self.skill_memory.add_skill_experience(
            skill=skill,
            object_name=object_name,
            params=params,
            success=success,
            **metadata,
        )

    def add_environment_fact(
        self, key: str, value, environment=None, object_name=None, **metadata
    ) -> str:
        """添加快捷环境先验"""
        return self.environment_memory.add_environment_fact(
            key=key, value=value,
            environment=environment,
            object_name=object_name,
            **metadata,
        )

    # ============================================================
    # 内部辅助
    # ============================================================

    def _get_sources(
        self, memory_types: Optional[List[MemoryType]] = None
    ) -> List[tuple[str, UserMemory | SkillMemory | EnvironmentMemory]]:
        """获取要检索的 memory source 列表"""
        all_sources = [
            ("user_memory", self.user_memory),
            ("skill_memory", self.skill_memory),
            ("environment_memory", self.environment_memory),
        ]

        if memory_types is None:
            return all_sources

        type_to_source = {
            MemoryType.USER_PREFERENCE: ("user_memory", self.user_memory),
            MemoryType.SKILL_EXPERIENCE: ("skill_memory", self.skill_memory),
            MemoryType.ENVIRONMENT_PRIOR: ("environment_memory", self.environment_memory),
        }

        return [
            type_to_source[mt]
            for mt in memory_types
            if mt in type_to_source
        ]

    # ============================================================
    # 管理方法
    # ============================================================

    def clear_all(self) -> None:
        """清空全部记忆"""
        self.user_memory.clear()
        self.skill_memory.clear()
        self.environment_memory.clear()

    def stats(self) -> Dict[str, int]:
        """各 memory 条目统计"""
        return {
            "user_memory": len(self.user_memory),
            "skill_memory": len(self.skill_memory),
            "environment_memory": len(self.environment_memory),
            "total": (
                len(self.user_memory)
                + len(self.skill_memory)
                + len(self.environment_memory)
            ),
        }

    # ============================================================
    # FAISS 接口预留
    # ============================================================

    def build_index(self) -> None:
        """
        [预留] 构建 FAISS 向量索引。

        当前实现: no-op
        未来实现:
            1. 对所有 MemoryItem 生成 embedding (via sentence-transformers)
            2. 构建 FAISS IVF 索引
            3. 替换 keyword search 为 vector search
        """
        # TODO: Integrate FAISS when ready
        pass

    def vector_search(
        self, query_embedding: List[float], top_k: int = 5
    ) -> List[MemoryItem]:
        """
        [预留] FAISS 向量语义检索。

        当前: fallback to keyword search
        """
        # TODO: FAISS index.search(query_embedding, top_k)
        raise NotImplementedError(
            "FAISS vector search not yet implemented. "
            "Use in-memory search() instead."
        )
