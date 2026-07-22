# Blind Evaluation Report

**Date**: 2026-07-20
**Total cases**: 110
**Passed**: 73 | **Failed**: 37
**Exceptions**: 0
**Overall pass rate**: 66.4%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 26 |
| HIGH | 29 |
| MEDIUM | 1 |
| LOW | 0 |

## Accuracy by Dimension

| Dimension | Accuracy | Cases |
|-----------|----------|-------|
| Action Recognition | 98.0% | 98 |
| Entity Grounding | 84.8% | 92 |
| Force/Constraint Parsing | 100.0% | 8 |
| Execution Gate | 82.6% | 109 |

## Accuracy by Category

| Category | Total | Passed | Critical Errors | High Errors |
|----------|-------|--------|-----------------|-------------|
| conflict | 10 | 6 | 3 | 3 |
| disambiguation | 10 | 7 | 4 | 2 |
| invalid_input | 10 | 9 | 1 | 0 |
| missing_target | 10 | 10 | 0 | 0 |
| mixed_colloquial | 10 | 7 | 2 | 2 |
| negation_condition | 10 | 3 | 8 | 4 |
| numeric_constraints | 10 | 8 | 0 | 2 |
| robot_state | 10 | 5 | 0 | 5 |
| roles | 10 | 5 | 5 | 4 |
| simple_action | 10 | 6 | 1 | 4 |
| spatial_descriptive | 10 | 7 | 2 | 3 |

## Failed Cases

### ⚠️ B03 [simple_action]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b03a, actual=obj-029522
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b03b, got obj-aa56d4

### ⚠️ B07 [simple_action]: 把蓝色积木放到托盘上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b07a, actual=obj-948c16
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b07b, got None

### ❌ B09 [simple_action]: 拿起那个东西
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b09, actual=None
- Execution: expected=None, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ⚠️ B10 [simple_action]: 把杯子放到桌面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b10a, actual=obj-992463
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b10b, got obj-c2c87f

### ❌ B16 [disambiguation]: 抓住前面的杯子，不要后面的
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b16a, actual=obj-767a64
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b16b' not found in BT/CG

### ❌ B19 [disambiguation]: 把那个小的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b19b, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B20 [disambiguation]: 抓住玻璃杯，别碰塑料杯
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b20a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b20b' not found in BT/CG

### ⚠️ B23 [spatial_descriptive]: 把杯子放到桌子左边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b23a, actual=obj-9fde80
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b23b, got obj-5ef5d1

### ❌ B25 [spatial_descriptive]: 抓住高处那个瓶子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b25a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ❌ B29 [spatial_descriptive]: 抓住那个又大又红的方块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b29b, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ❌ B33 [roles]: 把盒子拿过来，别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b33a, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b33b' not found in BT/CG

### ⚠️ B35 [roles]: 把蓝色方块放到红色方块上面
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b35a, actual=obj-f2a676
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ❌ B37 [roles]: 把这个拿给用户
- Action: expected=HANDOVER, actual=HANDOVER
- Entity: expected=obj-b37, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ⚠️ B38 [roles]: 把杯子放到支撑面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b38a, actual=obj-61e34d
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b38b, got None

### ❌ B39 [roles]: 抓住杯子，避开那个盒子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b39a, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b39b' not found in BT/CG

### ❌ B41 [negation_condition]: 把盒子拿过来，千万别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b41a, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b41b' not found in BT/CG

### ⚠️ B43 [negation_condition]: 先抓住杯子，再放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b43a, actual=obj-98bc75
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b43b, got obj-12bd8b

### ❌ B44 [negation_condition]: 不要碰那个红色的，把蓝色的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b44b, actual=obj-dadf5b
- Execution: expected=False, actual=False
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b44a' not found in BT/CG
- [CRITICAL] **wrong_color_grounding**: Instruction requested 'red' but grounded to 'blue' object

### ❌ B45 [negation_condition]: 除非夹爪是空的，否则不要抓取
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b45, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ⚠️ B47 [negation_condition]: 把杯子放到桌上，但不要放在边缘
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b47a, actual=obj-9ce09a
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b47b, got obj-281f6e

### ❌ B48 [negation_condition]: 如果看到红色药瓶就先拿它，否则拿蓝色盒子
- Action: expected=FETCH, actual=CUSTOM
- Entity: expected=obj-b48a, actual=obj-eed718
- Execution: expected=False, actual=False
- [HIGH] **wrong_action**: Expected FETCH, got CUSTOM
- [MEDIUM] **missing_role_not_detected**: Expected missing role 'delivery_pose_or_fetch_zone' not detected. Actual missing: []
- [CRITICAL] **wrong_color_grounding**: Instruction requested 'red' but grounded to 'blue' object

### ❌ B50 [negation_condition]: 不要碰任何东西，把最右边的杯子拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b50c, actual=obj-c25d68
- Execution: expected=False, actual=False
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b50a' not found in BT/CG
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b50b' not found in BT/CG

### ⚠️ B54 [numeric_constraints]: 用3到5N的力量抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b54, actual=obj-47d036
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B56 [numeric_constraints]: 以0.15m/s的速度移动杯子
- Action: expected=CUSTOM, actual=CUSTOM
- Entity: expected=obj-b56, actual=obj-77f7cd
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B62 [robot_state]: 抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b62, actual=obj-f3cbe6
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B63 [robot_state]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b63a, actual=obj-c3428b
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b63b, got obj-036d91

### ⚠️ B64 [robot_state]: 抓住很重的铁块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b64, actual=obj-92136b
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B65 [robot_state]: 把药片从瓶子里倒出来
- Action: expected=CUSTOM, actual=CUSTOM
- Entity: expected=obj-b65, actual=obj-3a451d
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B70 [robot_state]: 把杯子夹住并翻转过来
- Action: expected=CUSTOM, actual=GRASP
- Entity: expected=obj-b70, actual=obj-a9048a
- Execution: expected=True, actual=True
- [HIGH] **wrong_action**: Expected CUSTOM, got GRASP

### ❌ B86 [conflict]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b86a, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B87 [conflict]: 把重物放到精密仪器旁边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b87a, actual=obj-6a75b4
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b87b' not found in BT/CG

### ❌ B88 [conflict]: 抓住杯子，但我不想让你碰桌子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b88a, actual=obj-7bb92d
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b88b' not found in BT/CG

### ⚠️ B90 [conflict]: 用不超过1N的力量抓住铁块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b90, actual=obj-1d2198
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ❌ B94 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=obj-bdacd1
- Execution: expected=False, actual=True
- [CRITICAL] **execution_allowed_when_should_be_blocked**: Execution allowed=True but expected=False

### ⚠️ B103 [mixed_colloquial]: grab那个红色的bottle然后放到table上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b103a, actual=obj-b07d8a
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b103b, got None

### ❌ B104 [mixed_colloquial]: 把那玩意儿拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b104, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B108 [mixed_colloquial]: grasp the cup, but don't touch anything else!
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b108a, actual=obj-7467d7
- Execution: expected=True, actual=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b108b' not found in BT/CG
