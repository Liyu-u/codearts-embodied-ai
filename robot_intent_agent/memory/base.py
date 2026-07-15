"""
Memory Framework — 抽象基础层

三层 Memory 架构:
    ┌─────────────────┐
    │  Memory Manager │
    └────────┬────────┘
    ┌────────┼────────┐
    │        │        │
    User   Skill   Environment
    Memory Memory  Memory
    │        │        │
    └────────┼────────┘
    ┌────────┴────────┐
    │ Vector Retriever│ (FAISS 接口预留)
    └─────────────────┘
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


# ============================================================
# 枚举
# ============================================================

class MemoryType(str, Enum):
    """记忆类型"""
    USER_PREFERENCE = "user_preference"         # 用户偏好
    SKILL_EXPERIENCE = "skill_experience"        # 技能经验
    ENVIRONMENT_PRIOR = "environment_prior"      # 环境先验
    EPISODIC = "episodic"                        # 情节记忆 (预留)


class MemoryPriority(str, Enum):
    """记忆优先级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ============================================================
# 核心数据结构
# ============================================================

@dataclass
class MemoryItem:
    """
    单条记忆条目。

    示例:
        MemoryItem(
            memory_type=MemoryType.USER_PREFERENCE,
            key="hand_preference",
            value="left",
            metadata={"user": "elderly_person", "confidence": 0.9},
        )
    """
    memory_type: MemoryType
    key: str
    value: Any
    id: str = field(default_factory=lambda: f"mem-{uuid4().hex[:8]}")
    priority: MemoryPriority = MemoryPriority.MEDIUM
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_accessed: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    access_count: int = 0
    embedding: Optional[List[float]] = field(default=None)  # FAISS 预留

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典 (便于 JSON 导出)"""
        return {
            "id": self.id,
            "memory_type": self.memory_type.value,
            "key": self.key,
            "value": self.value,
            "priority": self.priority.value,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
        }

    def touch(self) -> None:
        """更新最后访问时间与计数"""
        self.last_accessed = datetime.now(timezone.utc).isoformat()
        self.access_count += 1


# ============================================================
# 抽象接口
# ============================================================

class MemoryInterface(ABC):
    """
    Memory 抽象接口。

    所有 Memory 子类必须实现:
        - add(item)       : 添加记忆条目
        - search(query, k): 语义/关键词检索
        - delete(item_id) : 删除记忆条目
        - clear()         : 清空全部记忆
    """

    def __init__(self, name: str):
        self.name = name
        self._store: Dict[str, MemoryItem] = {}

    @abstractmethod
    def add(self, item: MemoryItem) -> str:
        """添加记忆 — 返回记忆 ID"""
        ...

    @abstractmethod
    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """检索记忆 — 返回 top_k 条最相关记忆"""
        ...

    def delete(self, item_id: str) -> bool:
        """删除指定记忆 — 返回是否成功"""
        if item_id in self._store:
            del self._store[item_id]
            return True
        return False

    def clear(self) -> None:
        """清空全部记忆"""
        self._store.clear()

    def get_by_id(self, item_id: str) -> Optional[MemoryItem]:
        """按 ID 获取"""
        return self._store.get(item_id)

    def get_by_key(self, key: str) -> List[MemoryItem]:
        """按键检索"""
        return [item for item in self._store.values() if item.key == key]

    def get_by_type(self, memory_type: MemoryType) -> List[MemoryItem]:
        """按类型检索"""
        return [
            item for item in self._store.values()
            if item.memory_type == memory_type
        ]

    def list_all(self) -> List[MemoryItem]:
        """列出全部记忆"""
        return list(self._store.values())

    def __len__(self) -> int:
        return len(self._store)

    def __contains__(self, item_id: str) -> bool:
        return item_id in self._store
