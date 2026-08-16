# Scene Builder v1.1 Enhancement Plan

> 记录于 Step 4 完成后。当前 v1.0 已稳定（19 tests passed），
> 以下三项改进留待 v1.1 迭代，**不修改现有代码**。

---

## E1: 自适应空间阈值 (Geometry-Aware)

**现状**: `near_threshold_m = 0.10` (硬编码常数)

**问题**: 小桌面 10cm 很远，大房间 10cm 很近。

**目标**:
```python
# 自适应阈值 = max(object_radius) * factor
obj_radius_a = max(a.bbox.width, a.bbox.depth) / 2
obj_radius_b = max(b.bbox.width, b.bbox.depth) / 2
near_threshold = max(obj_radius_a, obj_radius_b) * 2.0

# 也可针对不同谓词使用不同 factor:
#   near: factor=2.0
#   blocking: perpendicular_dist < obj_radius * 1.5
```

**预计影响**: `SpatialConfig` 增加 `near_scale_factor`, `blocking_scale_factor`

---

## E2: SpatialRelation 置信度精细化

**现状**: `confidence = min(1.0, 1.0 - distance * 2)` (仅基于距离)

**问题**: 视觉推理存在不确定性 (遮挡、光照、VLM 幻觉)

**目标**:
```python
SpatialRelation(
    subject="glass_cup",
    predicate=SpatialPredicate.BLOCKING,
    object="medicine_bottle",
    confidence=0.92,       # 阻断置信度
    metadata={
        "distance_m": 0.034,
        "confidence_factors": {
            "geometric": 0.95,    # 几何判断可信
            "visual": 0.88,       # VLM 确认可信
            "combined": 0.92,     # 综合
        }
    }
)
```

**Confidence 综合公式** (建议):
```
confidence = w_geo * geometric + w_vis * visual
default: w_geo=0.6, w_vis=0.4
```

**用途**: Planner 可根据 confidence 决定:
- >0.9: 直接使用
- 0.5~0.9: 使用但标记 low-confidence
- <0.5: 触发主动感知 / 询问用户

**预计影响**: `SpatialRelation.confidence` 字段 (已有), 增加 `confidence_factors` metadata

---

## E3: SceneGraph 时间戳与失效机制

**现状**: `SemanticSceneGraph.timestamp` 字段已有 (# Optional[str])

**问题**: 机器人环境动态变化，旧场景不能继续使用。

**目标**:
```python
scene = SemanticSceneGraph(
    timestamp="2026-07-15T10:30:00Z",  # ISO 8601
    ttl_seconds=5.0,                      # 场景有效期
)

# 消费方检查:
if scene.is_stale():
    raise StaleSceneError("Scene expired, re-run scene builder")
```

**预计影响**: `SemanticSceneGraph` 增加 `ttl_seconds` + `is_stale()` 方法

---

## 实施计划

| 优先级 | 条目 | 复杂度 | 建议时机 |
|--------|------|--------|----------|
| P0 | E3 (timestamp+stale) | 低 | Step 5 前 (Planner 需要验证场景时效) |
| P1 | E2 (confidence 精细化) | 中 | 接入真实 VLM 时 |
| P2 | E1 (自适应阈值) | 低 | 多场景适配前 |
