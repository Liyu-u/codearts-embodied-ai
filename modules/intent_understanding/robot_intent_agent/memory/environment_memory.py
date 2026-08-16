"""
Environment Memory — 环境先验记忆

存储对物理环境的先验知识:
    - 场景布局 (桌面高度、工作空间范围)
    - 物体固定属性 (材质、重量)
    - 历史场景快照

示例:
    {
        environment: "elder_home",
        table_height_m: 0.72,
        workspace_radius_m: 0.5,
    }
"""

from __future__ import annotations

from typing import List, Optional

from .base import MemoryInterface, MemoryItem, MemoryType, MemoryPriority


class EnvironmentMemory(MemoryInterface):
    """
    环境先验记忆存储。

    检索策略: 按 environment 场景名 + 物体属性匹配
    """

    def __init__(self):
        super().__init__(name="environment_memory")

    # ============================================================
    # 实现抽象方法
    # ============================================================

    def add(self, item: MemoryItem) -> str:
        """添加环境先验记忆"""
        if item.memory_type != MemoryType.ENVIRONMENT_PRIOR:
            item.memory_type = MemoryType.ENVIRONMENT_PRIOR

        # 同 key 覆盖
        existing = self.get_by_key(item.key)
        for old in existing:
            self.delete(old.id)

        self._store[item.id] = item
        return item.id

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """
        检索环境先验。

        策略:
            1. 环境场景名匹配 (高权重)
            2. 物体属性关键词匹配
            3. 最近更新优先
        """
        query_lower = query.lower()
        scored: List[tuple[MemoryItem, int]] = []

        for item in self._store.values():
            score = 0
            env = item.metadata.get("environment", "")
            obj = item.metadata.get("object", "")
            search_text = (
                f"{env} {obj} {item.key} {item.value}".lower()
            )

            # 场景名精确匹配
            if env and env.lower().replace("_", " ") in query_lower:
                score += 12

            # 物体匹配
            if obj and obj.lower() in query_lower:
                score += 8

            # 关键词匹配
            if query_lower in search_text:
                score += 6

            tokens = query_lower.split()
            score += sum(
                2 for token in tokens if token in search_text
            )

            # 高优先级记忆额外加权
            priority_weights = {
                MemoryPriority.HIGH: 3,
                MemoryPriority.MEDIUM: 1,
                MemoryPriority.LOW: 0,
            }
            score += priority_weights.get(item.priority, 0)

            if score > 0:
                scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        result = [item for item, _ in scored[:top_k]]
        for item in result:
            item.touch()
        return result

    # ============================================================
    # 便捷工厂方法
    # ============================================================

    def add_environment_fact(
        self,
        key: str,
        value: Any,
        environment: Optional[str] = None,
        object_name: Optional[str] = None,
        priority: MemoryPriority = MemoryPriority.MEDIUM,
        **metadata,
    ) -> str:
        """快捷添加环境先验"""
        full_metadata = {**metadata}
        if environment:
            full_metadata["environment"] = environment
        if object_name:
            full_metadata["object"] = object_name

        item = MemoryItem(
            memory_type=MemoryType.ENVIRONMENT_PRIOR,
            key=key,
            value=value,
            priority=priority,
            metadata=full_metadata,
        )
        return self.add(item)

    def get_environment(self, env_name: str) -> List[MemoryItem]:
        """获取指定环境的所有先验记忆"""
        return [
            item for item in self._store.values()
            if item.metadata.get("environment") == env_name
        ]

    def get_table_height(self) -> Optional[float]:
        """获取桌面高度 (常用查询)"""
        items = self.get_by_key("table_height_m")
        if items:
            val = items[0].value
            return float(val) if val is not None else None
        return None
