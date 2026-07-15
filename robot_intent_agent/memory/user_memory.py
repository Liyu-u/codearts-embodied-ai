"""
User Memory — 用户偏好记忆

存储用户个性化偏好:
    - 接物手势偏好 (左手/右手)
    - 速度偏好 (快/慢/标准)
    - 抓取风格偏好 (轻柔/标准/强力)
    - 交互距离偏好

示例:
    Input:  "老人习惯左手接物"
    Store:  {type:"user_preference", key:"hand_preference", value:"left"}

    Input:  "动作轻一点"
    Store:  {type:"user_preference", key:"grip_style", value:"gentle"}
"""

from __future__ import annotations

from typing import List

from .base import MemoryInterface, MemoryItem, MemoryType, MemoryPriority


class UserMemory(MemoryInterface):
    """
    用户偏好记忆存储。

    检索策略: 关键词匹配 + 类型精确匹配
    """

    def __init__(self):
        super().__init__(name="user_memory")

    # ============================================================
    # 实现抽象方法
    # ============================================================

    def add(self, item: MemoryItem) -> str:
        """添加用户偏好记忆"""
        if item.memory_type != MemoryType.USER_PREFERENCE:
            item.memory_type = MemoryType.USER_PREFERENCE

        # 去重: 同 key 覆盖
        existing = self.get_by_key(item.key)
        for old in existing:
            self.delete(old.id)

        self._store[item.id] = item
        return item.id

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """
        关键词检索用户偏好。

        策略: 在 key, value, metadata 中进行子串匹配,
              按 priority 降序 + access_count 降序排序
        """
        query_lower = query.lower()
        scored: List[tuple[MemoryItem, int]] = []

        for item in self._store.values():
            score = 0
            search_text = (
                item.key.lower()
                + " "
                + str(item.value).lower()
                + " "
                + " ".join(str(v).lower() for v in item.metadata.values())
            )

            # 精确匹配
            if query_lower in search_text:
                score += 10

            # 关键词拆解匹配
            tokens = query_lower.split()
            for token in tokens:
                if token in search_text:
                    score += 3

            # 优先级加权
            priority_weights = {
                MemoryPriority.HIGH: 5,
                MemoryPriority.MEDIUM: 2,
                MemoryPriority.LOW: 0,
            }
            score += priority_weights.get(item.priority, 0)

            # 访问频率加权
            score += min(item.access_count, 5)

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

    def add_preference(
        self,
        key: str,
        value: Any,
        priority: MemoryPriority = MemoryPriority.MEDIUM,
        **metadata,
    ) -> str:
        """快捷添加用户偏好"""
        item = MemoryItem(
            memory_type=MemoryType.USER_PREFERENCE,
            key=key,
            value=value,
            priority=priority,
            metadata=metadata,
        )
        return self.add(item)

    def get_preference(self, key: str) -> MemoryItem | None:
        """获取指定偏好"""
        items = self.get_by_key(key)
        if items:
            items[0].touch()
            return items[0]
        return None
