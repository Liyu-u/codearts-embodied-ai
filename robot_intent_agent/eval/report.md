# Intent Understanding — Evaluation Report (v2.0)

**Run ID**: `eval-adeaebea8578`
**Dataset**: blind_dataset.json
**Date**: 2026-07-21
**Total**: 110 | **Passed**: 90 | **Failed**: 20
**Severe Veto**: 14 cases failed by CRITICAL-only
**Pass Rate**: 81.8%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 18 |
| HIGH | 7 |
| MEDIUM | 1 |
| LOW | 0 |
| INFO | 0 |

## 13-Dimension Accuracy

| # | Dimension | Accuracy | Applicable | Critical | High | Medium |
|---|-----------|----------|------------|----------|------|--------|
| 1. Action Recognition | 98.0% | 98 | 0 | 2 | 0 |
| 2. Role Extraction | 90.9% | 44 | 0 | 3 | 1 |
| 3. Entity Grounding | 83.0% | 94 | 17 | 0 | 0 |
| 4. Multi-Object Disambiguation | 100.0% | 18 | 0 | 0 | 0 |
| 5. Negation Constraint Retention | 91.7% | 12 | 1 | 1 | 0 |
| 6. Conditional/Sequential Understanding | 98.2% | 55 | 0 | 1 | 0 |
| 7. Numeric/Operator/Unit Accuracy | 100.0% | 14 | 0 | 0 | 0 |
| 8. Perception Factual Fidelity | 100.0% | 105 | 0 | 0 | 0 |
| 9. Robot Capability Constraint | 100.0% | 21 | 0 | 0 | 0 |
| 10. BT/IR Cross-Field Consistency | 100.0% | 105 | 0 | 0 | 0 |
| 11. Schema Validity | 100.0% | 105 | 0 | 0 | 0 |
| 12. Dangerous Error Pass-Through | 100.0% | 45 | 0 | 0 | 0 |

## Latency

| Avg | P50 | P95 | P99 |
|-----|-----|-----|-----|
| 5.2ms | 4.5ms | 7.0ms | 27.6ms |

## By Category

| Category | Total | Passed | Critical | High |
|----------|-------|--------|----------|------|
| conflict | 10 | 8 | 3 | 1 |
| disambiguation | 10 | 8 | 2 | 0 |
| invalid_input | 10 | 10 | 0 | 0 |
| missing_target | 10 | 10 | 0 | 0 |
| mixed_colloquial | 10 | 8 | 1 | 2 |
| negation_condition | 10 | 6 | 5 | 1 |
| numeric_constraints | 10 | 10 | 0 | 0 |
| robot_state | 10 | 9 | 0 | 1 |
| roles | 10 | 5 | 4 | 1 |
| simple_action | 10 | 8 | 1 | 1 |
| spatial_descriptive | 10 | 8 | 2 | 0 |

## Legacy Metrics (backward compatible)

| Metric | Accuracy | Cases |
|--------|----------|-------|
| Action | 98.0% | 98 |
| Entity Grounding | 82.6% | 92 |
| Force Parsing | 100.0% | 8 |
| Role Detection | 93.2% | 44 |
| Schema Pass | 100.0% | 110 |
| Overall | 81.8% | 110 |

## Failed Cases

### ⚠️ B07 [simple_action]: 把蓝色积木放到托盘上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b07a, actual=obj-93d8e0
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface not identified (expected=obj-b07b, actual=None)

### ❌ B09 [simple_action]: 拿起那个东西
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b09, actual=None
- Execution: expected=None, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B19 [disambiguation]: 把那个小的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b19b, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B20 [disambiguation]: 抓住玻璃杯，别碰塑料杯
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b20a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B25 [spatial_descriptive]: 抓住高处那个瓶子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b25a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B29 [spatial_descriptive]: 抓住那个又大又红的方块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b29b, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B33 [roles]: 把盒子拿过来，别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b33a, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B35 [roles]: 把蓝色方块放到红色方块上面
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b35a, actual=obj-3cb249
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme grounded to wrong object: expected obj-b35a, got obj-b35b (expected=obj-b35a, actual=grounded to obj-b35b (scene=obj-3cb249))

### ❌ B37 [roles]: 把这个拿给用户
- Action: expected=HANDOVER, actual=HANDOVER
- Entity: expected=obj-b37, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ⚠️ B38 [roles]: 把杯子放到支撑面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b38a, actual=obj-e24450
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface not identified (expected=obj-b38b, actual=None)

### ❌ B39 [roles]: 抓住杯子，避开那个盒子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b39a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B41 [negation_condition]: 把盒子拿过来，千万别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b41a, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B44 [negation_condition]: 不要碰那个红色的，把蓝色的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b44b, actual=obj-b388a6
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=red, actual=blue)

### ❌ B45 [negation_condition]: 除非夹爪是空的，否则不要抓取
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b45, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B48 [negation_condition]: 如果看到红色药瓶就先拿它，否则拿蓝色盒子
- Action: expected=FETCH, actual=CUSTOM
- Entity: expected=obj-b48a, actual=obj-78e1bb
- Execution: expected=False, actual=False
- [HIGH] **action_recognition**: Action mismatch: expected FETCH, got CUSTOM (expected=FETCH, actual=CUSTOM)
- [MEDIUM] **role_extraction**: Expected missing role 'delivery_pose_or_fetch_zone' not detected (expected=missing_role:delivery_pose_or_fetch_zone, actual=[])
- [CRITICAL] **entity_grounding**: Theme grounded to wrong object: expected obj-b48a, got obj-b48b (expected=obj-b48a, actual=grounded to obj-b48b (scene=obj-78e1bb))
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=red, actual=blue)

### ⚠️ B70 [robot_state]: 把杯子夹住并翻转过来
- Action: expected=CUSTOM, actual=GRASP
- Entity: expected=obj-b70, actual=obj-dfea28
- Execution: expected=True, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected CUSTOM, got GRASP (expected=CUSTOM, actual=GRASP)

### ❌ B86 [conflict]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b86a, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B87 [conflict]: 把重物放到精密仪器旁边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b87a, actual=obj-008813
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme grounded to wrong object: expected obj-b87a, got obj-b87b (expected=obj-b87a, actual=grounded to obj-b87b (scene=obj-008813))
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b87b' not propagated to BT/CG (expected=obj-b87b, actual=['device'])
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Place'])

### ⚠️ B103 [mixed_colloquial]: grab那个红色的bottle然后放到table上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b103a, actual=obj-c47c08
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface not identified (expected=obj-b103b, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B104 [mixed_colloquial]: 把那玩意儿拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b104, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
