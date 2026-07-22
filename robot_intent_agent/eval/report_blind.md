# Intent Understanding — Evaluation Report (v2.0)

**Dataset**: blind_dataset.json
**Date**: 2026-07-20
**Total**: 110 | **Passed**: 60 | **Failed**: 50
**Severe Veto**: 15 cases failed by CRITICAL-only
**Pass Rate**: 54.5%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 40 |
| HIGH | 45 |
| MEDIUM | 5 |
| LOW | 0 |
| INFO | 0 |

## 13-Dimension Accuracy

| # | Dimension | Accuracy | Applicable | Critical | High |
|---|-----------|----------|------------|----------|------|
| 1. Action Recognition | 0.0% | 7 | 0 | 7 |
| 2. Role Extraction | 30.8% | 13 | 1 | 8 |
| 3. Entity Grounding | 0.0% | 22 | 22 | 0 |
| 4. Multi-Object Disambiguation | 100.0% | 110 | 0 | 0 |
| 5. Negation Constraint Retention | 0.0% | 16 | 11 | 5 |
| 6. Conditional/Sequential Understanding | 4.2% | 24 | 0 | 23 |
| 7. Numeric/Operator/Unit Accuracy | 0.0% | 2 | 0 | 2 |
| 8. Perception Factual Fidelity | 100.0% | 110 | 0 | 0 |
| 9. Robot Capability Constraint | 100.0% | 110 | 0 | 0 |
| 10. BT/IR Cross-Field Consistency | 100.0% | 110 | 0 | 0 |
| 11. Schema Validity | 100.0% | 110 | 0 | 0 |
| 12. Dangerous Error Pass-Through | 0.0% | 6 | 6 | 0 |

## Latency

| Avg | P50 | P95 | P99 |
|-----|-----|-----|-----|
| 3.4ms | 3.5ms | 5.2ms | 6.1ms |

## By Category

| Category | Total | Passed | Critical | High |
|----------|-------|--------|----------|------|
| conflict | 10 | 6 | 7 | 3 |
| disambiguation | 10 | 6 | 4 | 2 |
| invalid_input | 10 | 7 | 0 | 3 |
| missing_target | 10 | 8 | 3 | 0 |
| mixed_colloquial | 10 | 3 | 5 | 4 |
| negation_condition | 10 | 2 | 7 | 8 |
| numeric_constraints | 10 | 8 | 0 | 3 |
| robot_state | 10 | 5 | 3 | 6 |
| roles | 10 | 5 | 4 | 4 |
| simple_action | 10 | 5 | 3 | 7 |
| spatial_descriptive | 10 | 5 | 4 | 5 |

## Legacy Metrics (backward compatible)

| Metric | Accuracy | Cases |
|--------|----------|-------|
| Action | 92.9% | 98 |
| Entity Grounding | 78.3% | 92 |
| Force Parsing | 100.0% | 8 |
| Role Detection | 90.2% | 92 |
| Schema Pass | 100.0% | 110 |
| Overall | 54.5% | 110 |

## Failed Cases

### ⚠️ B03 [simple_action]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b03a, actual=obj-cb9b09
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-b03b, actual=桌)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B05 [simple_action]: 抓住红色方块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b05, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B07 [simple_action]: 把蓝色积木放到托盘上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b07a, actual=obj-520c38
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '托盘' (expected=obj-b07b, actual=托盘)
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=blue, actual=gray)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B09 [simple_action]: 拿起那个东西
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b09, actual=None
- Execution: expected=None, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ⚠️ B10 [simple_action]: 把杯子放到桌面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b10a, actual=obj-621410
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-b10b, actual=桌)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B12 [disambiguation]: 抓住蓝色杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b12b, actual=obj-c340a9
- Execution: expected=True, actual=True
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=blue, actual=red)

### ❌ B16 [disambiguation]: 抓住前面的杯子，不要后面的
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b16a, actual=obj-af372c
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
- Entity: expected=obj-b20a, actual=obj-e13de5
- Execution: expected=True, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b20b' not propagated to BT/CG (expected=obj-b20b, actual=['玻璃杯'])
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B21 [spatial_descriptive]: 抓住正在移动的红色小球
- Action: expected=DYNAMIC_GRASP, actual=DYNAMIC_GRASP
- Entity: expected=obj-b21, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ⚠️ B23 [spatial_descriptive]: 把杯子放到桌子左边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b23a, actual=obj-664007
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-b23b, actual=桌)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B26 [spatial_descriptive]: 抓住桌子上那个杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b26a, actual=obj-91d3fc
- Execution: expected=True, actual=True
- [CRITICAL] **dangerous_error_pass_through**: Execution allowed with missing critical roles: ['support_surface'] (expected=blocked (missing roles), actual=allowed)

### ❌ B27 [spatial_descriptive]: 把远处的盒子推过来
- Action: expected=FETCH, actual=CUSTOM
- Entity: expected=obj-b27a, actual=obj-8d2bf4
- Execution: expected=False, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected FETCH, got CUSTOM (expected=FETCH, actual=CUSTOM)
- [MEDIUM] **role_extraction**: Expected missing role 'delivery_pose_or_fetch_zone' not detected (expected=missing_role:delivery_pose_or_fetch_zone, actual=[])
- [CRITICAL] **dangerous_error_pass_through**: DANGEROUS: execution allowed when it should be blocked (expected=blocked, actual=allowed)

### ❌ B29 [spatial_descriptive]: 抓住那个又大又红的方块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b29b, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B33 [roles]: 把盒子拿过来，别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b33a, actual=obj-8de99b
- Execution: expected=False, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b33b' not propagated to BT/CG (expected=obj-b33b, actual=['cup', 'obj-3b5bf7'])

### ❌ B35 [roles]: 把蓝色方块放到红色方块上面
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b35a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B37 [roles]: 把这个拿给用户
- Action: expected=HANDOVER, actual=CUSTOM
- Entity: expected=obj-b37, actual=None
- Execution: expected=False, actual=False
- [HIGH] **action_recognition**: Action mismatch: expected HANDOVER, got CUSTOM (expected=HANDOVER, actual=CUSTOM)
- [MEDIUM] **role_extraction**: Recipient not identified (expected=recipient identified, actual=none)
- [MEDIUM] **role_extraction**: Expected missing role 'recipient_pose_or_handover_zone' not detected (expected=missing_role:recipient_pose_or_handover_zone, actual=['theme'])
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)

### ⚠️ B38 [roles]: 把杯子放到支撑面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b38a, actual=obj-3fdda9
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface not identified (expected=obj-b38b, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B39 [roles]: 抓住杯子，避开那个盒子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b39a, actual=obj-2c1c6d
- Execution: expected=True, actual=True
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b39b' not propagated to BT/CG (expected=obj-b39b, actual=['box', 'obj-57e17a'])

### ❌ B41 [negation_condition]: 把盒子拿过来，千万别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b41a, actual=obj-70df29
- Execution: expected=False, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b41b' not propagated to BT/CG (expected=obj-b41b, actual=['cup', 'obj-45ab0d'])

### ⚠️ B43 [negation_condition]: 先抓住杯子，再放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b43a, actual=obj-744460
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-b43b, actual=桌)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

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
- Entity: expected=obj-b46, actual=obj-e2505a
- Execution: expected=True, actual=True
- [MEDIUM] **conditional_sequential_understanding**: Manner mismatch (expected=gentle, actual=None)

### ⚠️ B47 [negation_condition]: 把杯子放到桌上，但不要放在边缘
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b47a, actual=obj-ff9c8d
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-b47b, actual=桌)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B48 [negation_condition]: 如果看到红色药瓶就先拿它，否则拿蓝色盒子
- Action: expected=FETCH, actual=CUSTOM
- Entity: expected=obj-b48a, actual=obj-49b9c5
- Execution: expected=False, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected FETCH, got CUSTOM (expected=FETCH, actual=CUSTOM)
- [MEDIUM] **role_extraction**: Expected missing role 'delivery_pose_or_fetch_zone' not detected (expected=missing_role:delivery_pose_or_fetch_zone, actual=[])
- [CRITICAL] **dangerous_error_pass_through**: DANGEROUS: execution allowed when it should be blocked (expected=blocked, actual=allowed)

### ❌ B50 [negation_condition]: 不要碰任何东西，把最右边的杯子拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b50c, actual=obj-3021c9
- Execution: expected=False, actual=False
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b50a' not propagated to BT/CG (expected=obj-b50a, actual=none)
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b50b' not propagated to BT/CG (expected=obj-b50b, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Grasp', 'Fetch'])

### ⚠️ B53 [numeric_constraints]: 至少2N抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b53, actual=obj-6e496b
- Execution: expected=True, actual=True
- [HIGH] **numeric_operator_unit**: Force min value mismatch (expected=2.0, actual=None)
- [HIGH] **numeric_operator_unit**: Resolved force below requested minimum (expected=>= 2.0, actual=1.05)

### ⚠️ B54 [numeric_constraints]: 用3到5N的力量抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b54, actual=obj-7f592e
- Execution: expected=True, actual=False
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=BLOCKED)

### ⚠️ B63 [robot_state]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b63a, actual=obj-a8668a
- Execution: expected=True, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-b63b, actual=桌)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B64 [robot_state]: 抓住很重的铁块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b64, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B66 [robot_state]: 抓住那根针
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b66, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B67 [robot_state]: 抓住带USB线的设备
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b67, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ⚠️ B70 [robot_state]: 把杯子夹住并翻转过来
- Action: expected=CUSTOM, actual=GRASP
- Entity: expected=obj-b70, actual=obj-4c1551
- Execution: expected=True, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected CUSTOM, got GRASP (expected=CUSTOM, actual=GRASP)

### ❌ B72 [missing_target]: 抓住红色杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=None, actual=obj-009659
- Execution: expected=False, actual=True
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=red, actual=blue)
- [CRITICAL] **dangerous_error_pass_through**: DANGEROUS: execution allowed when it should be blocked (expected=blocked, actual=allowed)

### ❌ B76 [missing_target]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b76, actual=obj-53497b
- Execution: expected=False, actual=False
- [CRITICAL] **role_extraction**: Support surface fabricated when not in scene (expected=none, actual=桌)

### ❌ B81 [conflict]: 抓住那个红色杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=None, actual=obj-75eb05
- Execution: expected=False, actual=True
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=red, actual=blue)
- [CRITICAL] **dangerous_error_pass_through**: DANGEROUS: execution allowed when it should be blocked (expected=blocked, actual=allowed)

### ❌ B87 [conflict]: 把重物放到精密仪器旁边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b87a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b87b' not propagated to BT/CG (expected=obj-b87b, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Place'])
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

### ❌ B88 [conflict]: 抓住杯子，但我不想让你碰桌子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b88a, actual=obj-6dbc3c
- Execution: expected=True, actual=True
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b88b' not propagated to BT/CG (expected=obj-b88b, actual=['obj-fe6cd7', 'table'])
- [CRITICAL] **dangerous_error_pass_through**: Execution allowed with missing critical roles: ['recipient', 'support_surface'] (expected=blocked (missing roles), actual=allowed)

### ❌ B90 [conflict]: 用不超过1N的力量抓住铁块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b90, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
- [HIGH] **conditional_sequential_understanding**: Plan status inconsistent with execution_allowed (expected=READY or READY_WITH_SAFE_SUBSTITUTION, actual=NEEDS_CLARIFICATION)

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

### ⚠️ B102 [mixed_colloquial]: grasp the red bottle for me
- Action: expected=GRASP, actual=CUSTOM
- Entity: expected=obj-b102, actual=obj-9f4c93
- Execution: expected=True, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected GRASP, got CUSTOM (expected=GRASP, actual=CUSTOM)

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
- Action: expected=GRASP, actual=CUSTOM
- Entity: expected=obj-b108a, actual=obj-6eeb9e
- Execution: expected=True, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected GRASP, got CUSTOM (expected=GRASP, actual=CUSTOM)
- [CRITICAL] **negation_constraint_retention**: Negation/avoid 'obj-b108b' not propagated to BT/CG (expected=obj-b108b, actual=none)
- [HIGH] **negation_constraint_retention**: Obstacles present but no PlanPath in BT (expected=PlanPath in BT, actual=['Reach', 'Grasp', 'MoveTo', 'Release'])

### ⚠️ B109 [mixed_colloquial]: 抓抓杯子
- Action: expected=GRASP, actual=CUSTOM
- Entity: expected=obj-b109, actual=obj-eb338d
- Execution: expected=True, actual=True
- [HIGH] **action_recognition**: Action mismatch: expected GRASP, got CUSTOM (expected=GRASP, actual=CUSTOM)

### ❌ B110 [mixed_colloquial]: s'il vous plait, 把那个bouteille拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b110, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **entity_grounding**: Theme not grounded to any scene object (expected=grounded in scene, actual=None)
