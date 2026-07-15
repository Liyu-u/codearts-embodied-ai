"""
Skill Memory — 技能经验记忆

存储历史技能执行参数:
    - 针对不同物体的最佳抓取力
    - 成功的运动轨迹参数
    - 失败的经验教训

示例:
    {
        skill: "gentle_grasp",
        object: "medicine_bottle",
        force_n: 2.5,
        success: true,
    }
"""

from __future__ import annotations

from typing import List, Optional

from .base import MemoryInterface, MemoryItem, MemoryType, MemoryPriority


class SkillMemory(MemoryInterface):
    """
    技能经验记忆存储。

    检索策略: 按 skill_name + object 精确匹配优先,
              然后按成功率和最近访问排序
    """

    def __init__(self):
        super().__init__(name="skill_memory")

    # ============================================================
    # 实现抽象方法
    # ============================================================

    def add(self, item: MemoryItem) -> str:
        """添加技能经验记忆"""
        if item.memory_type != MemoryType.SKILL_EXPERIENCE:
            item.memory_type = MemoryType.SKILL_EXPERIENCE

        # 同 skill+object 去重 (保留最新)
        skill = item.metadata.get("skill", "")
        obj = item.metadata.get("object", "")
        if skill or obj:
            existing = self._find_by_skill_object(skill, obj)
            for old in existing:
                self.delete(old.id)

        self._store[item.id] = item
        return item.id

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """
        检索技能经验。

        策略:
            1. 优先精确匹配 skill_name + object
            2. 其次关键词模糊匹配
            3. 成功经验优先于失败经验
            4. 最近使用优先
        """
        query_lower = query.lower()
        scored: List[tuple[MemoryItem, int]] = []

        for item in self._store.values():
            score = 0
            skill = item.metadata.get("skill", "")
            obj = item.metadata.get("object", "")
            search_text = f"{skill} {obj} {item.key} {item.value}".lower()

            # 精确匹配
            if query_lower in search_text:
                score += 15

            # 技能名匹配 (高权重)
            if skill and skill.lower().replace("_", " ") in query_lower:
                score += 10

            # 物体名匹配
            if obj and obj.lower() in query_lower:
                score += 8

            # 关键词匹配
            tokens = query_lower.split()
            score += sum(
                3 for token in tokens if token in search_text
            )

            # 成功经验加权
            if item.metadata.get("success", True):
                score += 5

            # 最近访问加分
            score += min(item.access_count, 3)

            if score > 0:
                scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        result = [item for item, _ in scored[:top_k]]
        for item in result:
            item.touch()
        return result

    # ============================================================
    # 内部方法
    # ============================================================

    def _find_by_skill_object(
        self, skill: str, obj: str
    ) -> List[MemoryItem]:
        """按 skill+object 精确查找 (用于去重)"""
        matches = []
        for item in self._store.values():
            item_skill = item.metadata.get("skill", "")
            item_obj = item.metadata.get("object", "")
            if skill and item_skill == skill and obj and item_obj == obj:
                matches.append(item)
        return matches

    # ============================================================
    # 便捷工厂方法
    # ============================================================

    def add_skill_experience(
        self,
        skill: str,
        object_name: str,
        params: dict | None = None,
        success: bool = True,
        **metadata,
    ) -> str:
        """快捷添加技能经验"""
        item = MemoryItem(
            memory_type=MemoryType.SKILL_EXPERIENCE,
            key=f"skill:{skill}",
            value=params or {},
            priority=(
                MemoryPriority.HIGH if success else MemoryPriority.MEDIUM
            ),
            metadata={
                "skill": skill,
                "object": object_name,
                "success": success,
                **metadata,
            },
        )
        return self.add(item)

    def find_best_params(
        self, skill: str, object_name: str
    ) -> Optional[dict]:
        """
        查找指定技能+物体的最佳历史参数。

        Returns:
            最佳参数 dict, 无历史或未匹配到相关技能时返回 None
        """
        candidates = self.search(f"{skill} {object_name}", top_k=5)

        # 过滤: 必须 skill 或 object 与输入匹配
        relevant = [
            item for item in candidates
            if (skill and item.metadata.get("skill") == skill)
            or (object_name and item.metadata.get("object") == object_name)
        ]

        if not relevant:
            return None

        # 优先返回成功经验
        for item in relevant:
            if item.metadata.get("success", True):
                return item.value if isinstance(item.value, dict) else {}
        return relevant[0].value if isinstance(relevant[0].value, dict) else None
