# Intent Understanding — Evaluation Report (v2.0)

**Dataset**: blind_dataset.json
**Date**: 2026-07-20
**Total**: 110 | **Passed**: 82 | **Failed**: 28
**Severe Veto**: 13 cases failed by CRITICAL-only
**Pass Rate**: 74.6%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 24 |
| HIGH | 15 |
| MEDIUM | 3 |
| LOW | 0 |
| INFO | 0 |

## 13-Dimension Accuracy

| # | Dimension | Accuracy | Applicable | Critical | High |
|---|-----------|----------|------------|----------|------|
| 1. Action Recognition | 0.0% | 2 | 0 | 2 |
| 2. Role Extraction | 50.0% | 4 | 1 | 1 |
| 3. Entity Grounding | 0.0% | 9 | 9 | 0 |
| 4. Multi-Object Disambiguation | 100.0% | 110 | 0 | 0 |
| 5. Negation Constraint Retention | 0.0% | 15 | 11 | 4 |
| 6. Conditional/Sequential Understanding | 11.1% | 9 | 0 | 8 |
| 7. Numeric/Operator/Unit Accuracy | 100.0% | 110 | 0 | 0 |
| 8. Perception Factual Fidelity | 100.0% | 110 | 0 | 0 |
| 9. Robot Capability Constraint | 100.0% | 110 | 0 | 0 |
| 10. BT/IR Cross-Field Consistency | 100.0% | 110 | 0 | 0 |
| 11. Schema Validity | 100.0% | 110 | 0 | 0 |
| 12. Dangerous Error Pass-Through | 0.0% | 3 | 3 | 0 |

## Latency

| Avg | P50 | P95 | P99 |
|-----|-----|-----|-----|
| 2.4ms | 2.0ms | 4.0ms | 4.5ms |

## By Category

| Category | Total | Passed | Critical | High |
|----------|-------|--------|----------|------|
| conflict | 10 | 7 | 4 | 2 |
| disambiguation | 10 | 7 | 3 | 2 |
| invalid_input | 10 | 7 | 0 | 3 |
| missing_target | 10 | 9 | 1 | 0 |
| mixed_colloquial | 10 | 5 | 5 | 1 |
| negation_condition | 10 | 4 | 7 | 3 |
| numeric_constraints | 10 | 9 | 0 | 1 |
| robot_state | 10 | 9 | 0 | 1 |
| roles | 10 | 6 | 3 | 2 |
| simple_action | 10 | 9 | 1 | 0 |
| spatial_descriptive | 10 | 10 | 0 | 0 |

## Legacy Metrics (backward compatible)

| Metric | Accuracy | Cases |
|--------|----------|-------|
| Action | 98.0% | 98 |
| Entity Grounding | 90.2% | 92 |
| Force Parsing | 100.0% | 8 |
| Role Detection | 97.8% | 92 |
| Schema Pass | 100.0% | 110 |
| Overall | 74.6% | 110 |

## Failed Cases

### ❌ B09 [simple_action]: 拿起那个东西
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b09, actual=None
- Execution: expected=None, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B16 [disambiguation]: 抓住前面的杯子，不要后面的
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b16a, actual=obj-82566f
- Execution: expected=True, actual=True
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b16b' not propagated to BT/CG (expected=obj-b16b, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Grasp'])

### ❌ B19 [disambiguation]: 把那个小的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b19b, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B20 [disambiguation]: 抓住玻璃杯，别碰塑料杯
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b20a, actual=obj-cd83f7
- Execution: expected=True, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b20b' not propagated to BT/CG (expected=obj-b20b, actual=['cup', 'obj-efae5f'])
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B33 [roles]: 把盒子拿过来，别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b33a, actual=obj-207108
- Execution: expected=False, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b33b' not propagated to BT/CG (expected=obj-b33b, actual=['cup', 'obj-94b785'])

### ❌ B37 [roles]: 把这个拿给用户
- Action: expected=HANDOVER, actual=HANDOVER
- Entity: expected=obj-b37, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ⚠️ B38 [roles]: 把杯子放到支撑面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b38a, actual=obj-236cf2
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface not identified (expected=obj-b38b, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B39 [roles]: 抓住杯子，避开那个盒子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b39a, actual=obj-7db414
- Execution: expected=True, actual=True
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b39b' not propagated to BT/CG (expected=obj-b39b, actual=['box', 'obj-1dc02e'])

### ❌ B41 [negation_condition]: 把盒子拿过来，千万别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b41a, actual=obj-2c3c02
- Execution: expected=False, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b41b' not propagated to BT/CG (expected=obj-b41b, actual=['cup', 'obj-57872f'])

### ❌ B44 [negation_condition]: 不要碰那个红色的，把蓝色的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b44b, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b44a' not propagated to BT/CG (expected=obj-b44a, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Grasp', 'Fetch'])

### ❌ B45 [negation_condition]: 除非夹爪是空的，否则不要抓取
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b45, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ⚠️ B46 [negation_condition]: 抓住玻璃杯，但别用力
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b46, actual=obj-77d943
- Execution: expected=True, actual=True
- [MEDIUM] **conditional_sequential_understanding**: Manner mismatch (expected=gentle, actual=None)

### ❌ B48 [negation_condition]: 如果看到红色药瓶就先拿它，否则拿蓝色盒子
- Action: expected=FETCH, actual=CUSTOM
- Entity: expected=obj-b48a, actual=obj-ed1707
- Execution: expected=False, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected FETCH, got CUSTOM (expected=FETCH, actual=CUSTOM)
- [MEDIUM] **role_extraction**: Expected missing role 'delivery_pose_or_fetch_zone' not detected (expected=missing_role:delivery_pose_or_fetch_zone, actual=[])
- [CRITICAL] **dangerous_error_pass_through**: DANGEROUS: execution allowed when it should be blocked (expected=blocked, actual=allowed)

### ❌ B50 [negation_condition]: 不要碰任何东西，把最右边的杯子拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b50c, actual=obj-dc961e
- Execution: expected=False, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b50a' not propagated to BT/CG (expected=obj-b50a, actual=['cup', 'obj-7eb017', 'obj-9b2f06'])
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b50b' not propagated to BT/CG (expected=obj-b50b, actual=['cup', 'obj-7eb017', 'obj-9b2f06'])

### ⚠️ B54 [numeric_constraints]: 用3到5N的力量抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b54, actual=obj-386278
- Execution: expected=True, actual=False
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=BLOCKED)

### ⚠️ B70 [robot_state]: 把杯子夹住并翻转过来
- Action: expected=CUSTOM, actual=GRASP
- Entity: expected=obj-b70, actual=obj-1deff6
- Execution: expected=True, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected CUSTOM, got GRASP (expected=CUSTOM, actual=GRASP)

### ❌ B76 [missing_target]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b76, actual=obj-53216c
- Execution: expected=False, actual=False
- [CRITICAL] **role_extraction**: Support surface fabricated when not in scene (expected=none, actual=桌)

### ❌ B86 [conflict]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b86a, actual=obj-82dc63
- Execution: expected=False, actual=True
- [MEDIUM] **role_extraction**: Expected missing role 'support_surface' not detected (expected=missing_role:support_surface, actual=[])
- [CRITICAL] **dangerous_error_pass_through**: DANGEROUS: execution allowed when it should be blocked (expected=blocked, actual=allowed)

### ❌ B87 [conflict]: 把重物放到精密仪器旁边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b87a, actual=obj-872510
- Execution: expected=True, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b87b' not propagated to BT/CG (expected=obj-b87b, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Place'])
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B88 [conflict]: 抓住杯子，但我不想让你碰桌子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b88a, actual=obj-1bbc9a
- Execution: expected=True, actual=True
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b88b' not propagated to BT/CG (expected=obj-b88b, actual=['obj-ceb82c', 'table'])
- [CRITICAL] **dangerous_error_pass_through**: Execution allowed with missing critical roles: ['recipient'] (expected=blocked (missing roles), actual=allowed)

### ⚠️ B93 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=None
- Execution: expected=True, actual=False
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ⚠️ B95 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=None
- Execution: expected=True, actual=False
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ⚠️ B96 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=None
- Execution: expected=True, actual=False
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B104 [mixed_colloquial]: 把那玩意儿拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b104, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B105 [mixed_colloquial]: 把beizi拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b105, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B106 [mixed_colloquial]: 帮我把那个玻璃bei拿过来行不
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b106, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ❌ B108 [mixed_colloquial]: grasp the cup, but don't touch anything else!
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b108a, actual=obj-8b0d91
- Execution: expected=True, actual=True
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b108b' not propagated to BT/CG (expected=obj-b108b, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Grasp'])

### ❌ B110 [mixed_colloquial]: s'il vous plait, 把那个bouteille拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b110, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
