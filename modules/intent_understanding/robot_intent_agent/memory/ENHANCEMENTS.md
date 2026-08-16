# Memory v1.1 Enhancement Plan

> 记录于 Step 3 完成后。当前 v1.0 已稳定（30 tests passed），
> 以下三项改进留待 v1.1 迭代，**不修改现有代码**。

---

## E1: MemoryItem 增加时间衰减字段

**现状**: `created_at` + `last_accessed` + `access_count` 已有

**待加**: `updated_at`

**动机**:
- 用户偏好 (如 hand_preference) 长期稳定 → 时间衰减慢
- 技能经验 (如 grasp force) 换硬件后快速失效 → 时间衰减快
- 检索时可对 `last_accessed` 或 `updated_at` 做指数衰减:
  `score *= exp(-lambda * days_since_last_access)`

**预计影响**: `base.py` MemoryItem + 各 search() 排序逻辑

---

## E2: SkillMemory 增加适用条件 (conditions)

**现状**:
```python
{
    skill: "gentle_grasp",
    object: "medicine_bottle",
    force: 2.5
}
```

**目标**:
```python
{
    skill: "gentle_grasp",
    object: "medicine_bottle",
    conditions: {
        material: "plastic",
        weight_range: [50, 100],     # grams
        surface: "smooth",
        size_range: [0.02, 0.05],    # meters
    },
    force: 2.5
}
```

**动机**:
- 同一物体名可对应不同材质/尺寸 (玻璃瓶 vs 塑料瓶)
- 检索时 conditions 作为精确匹配过滤器
- 无匹配 conditions 时降级为仅 object 匹配

**预计影响**: `skill_memory.py` add_skill_experience() + search() 过滤逻辑

---

## E3: Retriever 输出增加来源元数据

**现状**: `MemoryRetriever.search()` 返回 `List[MemoryItem]`

**目标**: 每个结果包装为带来源信息:
```python
{
    "value": "left_hand",
    "source": "UserMemory",
    "source_type": "user_preference",
    "confidence": 0.92,
    "item": MemoryItem(...)       # 原始条目
}
```

**动机**:
- LLM (下游 Planner/CodeArts) 需要知道信息可信度
- 不同来源有不同权重: UserMemory > SkillMemory > EnvironmentMemory
- confidence 可由 access_count、priority、recency 自动计算

**预计影响**: 新增 `SearchResult` dataclass, `retriever.py` search() 返回值变更

---

## 实施计划

| 优先级 | 条目 | 复杂度 | 建议时机 |
|--------|------|--------|----------|
| P0 | E3 (来源+置信度) | 低 | Step 5 前 (Planner 需要) |
| P1 | E2 (conditions) | 中 | Step 6 前 (Constraint 需要精确参数) |
| P2 | E1 (updated_at) | 低 | Sprint 2 (性能优化) |
