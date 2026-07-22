# Blind Evaluation Report

**Date**: 2026-07-20
**Total cases**: 110
**Passed**: 71 | **Failed**: 39
**Exceptions**: 0
**Overall pass rate**: 64.5%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 24 |
| HIGH | 22 |
| MEDIUM | 2 |
| LOW | 0 |

## Accuracy by Dimension

| Dimension | Accuracy | Cases |
|-----------|----------|-------|
| Action Recognition | 98.0% | 98 |
| Entity Grounding | 90.2% | 92 |
| Force/Constraint Parsing | 100.0% | 8 |
| Execution Gate | 87.2% | 109 |

## Accuracy by Category

| Category | Total | Passed | Critical Errors | High Errors |
|----------|-------|--------|-----------------|-------------|
| conflict | 10 | 6 | 4 | 2 |
| disambiguation | 10 | 7 | 3 | 0 |
| invalid_input | 10 | 6 | 0 | 4 |
| missing_target | 10 | 9 | 1 | 0 |
| mixed_colloquial | 10 | 4 | 5 | 1 |
| negation_condition | 10 | 3 | 7 | 4 |
| numeric_constraints | 10 | 9 | 0 | 1 |
| robot_state | 10 | 6 | 0 | 4 |
| roles | 10 | 6 | 3 | 2 |
| simple_action | 10 | 6 | 1 | 3 |
| spatial_descriptive | 10 | 9 | 0 | 1 |

## Failed Cases

### ⚠️ B03 [simple_action]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b03a, actual=obj-8c6784
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b03b, got obj-7011a4

### ⚠️ B07 [simple_action]: 把蓝色积木放到托盘上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b07a, actual=obj-fdee0b
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b07b, got obj-5e402b

### ❌ B09 [simple_action]: 拿起那个东西
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b09, actual=None
- Execution: expected=None, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ⚠️ B10 [simple_action]: 把杯子放到桌面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b10a, actual=obj-83dc14
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b10b, got obj-8870f6

### ❌ B16 [disambiguation]: 抓住前面的杯子，不要后面的
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b16a, actual=obj-576dc9
- Execution: expected=True, actual=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b16b' not found in BT/CG

### ❌ B19 [disambiguation]: 把那个小的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b19b, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B20 [disambiguation]: 抓住玻璃杯，别碰塑料杯
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b20a, actual=obj-c9e239
- Execution: expected=True, actual=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b20b' not found in BT/CG

### ⚠️ B23 [spatial_descriptive]: 把杯子放到桌子左边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b23a, actual=obj-9ddc17
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b23b, got obj-591806

### ❌ B33 [roles]: 把盒子拿过来，别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b33a, actual=obj-5b677d
- Execution: expected=False, actual=False
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b33b' not found in BT/CG

### ❌ B37 [roles]: 把这个拿给用户
- Action: expected=HANDOVER, actual=HANDOVER
- Entity: expected=obj-b37, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ⚠️ B38 [roles]: 把杯子放到支撑面上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b38a, actual=obj-50affa
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b38b, got None

### ❌ B39 [roles]: 抓住杯子，避开那个盒子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b39a, actual=obj-0afbe5
- Execution: expected=True, actual=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b39b' not found in BT/CG

### ❌ B41 [negation_condition]: 把盒子拿过来，千万别碰玻璃杯
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b41a, actual=obj-773d29
- Execution: expected=False, actual=False
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b41b' not found in BT/CG

### ⚠️ B43 [negation_condition]: 先抓住杯子，再放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b43a, actual=obj-3498c3
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b43b, got obj-28fa50

### ❌ B44 [negation_condition]: 不要碰那个红色的，把蓝色的拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b44b, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b44a' not found in BT/CG

### ❌ B45 [negation_condition]: 除非夹爪是空的，否则不要抓取
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b45, actual=None
- Execution: expected=True, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B47 [negation_condition]: 把杯子放到桌上，但不要放在边缘
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b47a, actual=obj-43b103
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b47b, got obj-2c83d9

### ❌ B48 [negation_condition]: 如果看到红色药瓶就先拿它，否则拿蓝色盒子
- Action: expected=FETCH, actual=CUSTOM
- Entity: expected=obj-b48a, actual=obj-101a25
- Execution: expected=False, actual=True
- [HIGH] **wrong_action**: Expected FETCH, got CUSTOM
- [CRITICAL] **execution_allowed_when_should_be_blocked**: Execution allowed=True but expected=False
- [MEDIUM] **missing_role_not_detected**: Expected missing role 'delivery_pose_or_fetch_zone' not detected. Actual missing: []

### ❌ B50 [negation_condition]: 不要碰任何东西，把最右边的杯子拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b50c, actual=obj-693fe9
- Execution: expected=False, actual=False
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b50a' not found in BT/CG
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b50b' not found in BT/CG

### ⚠️ B54 [numeric_constraints]: 用3到5N的力量抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b54, actual=obj-29dce7
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B62 [robot_state]: 抓住杯子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b62, actual=obj-f55a16
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B63 [robot_state]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b63a, actual=obj-10656e
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b63b, got obj-cb90cb

### ⚠️ B64 [robot_state]: 抓住很重的铁块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b64, actual=obj-8ce831
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B70 [robot_state]: 把杯子夹住并翻转过来
- Action: expected=CUSTOM, actual=GRASP
- Entity: expected=obj-b70, actual=obj-4983fa
- Execution: expected=True, actual=True
- [HIGH] **wrong_action**: Expected CUSTOM, got GRASP

### ❌ B76 [missing_target]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b76, actual=obj-828677
- Execution: expected=False, actual=False
- [CRITICAL] **fabricated_support_surface**: Support surface fabricated: 桌

### ❌ B86 [conflict]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b86a, actual=obj-26d116
- Execution: expected=False, actual=True
- [CRITICAL] **execution_allowed_when_should_be_blocked**: Execution allowed=True but expected=False
- [MEDIUM] **missing_role_not_detected**: Expected missing role 'support_surface' not detected. Actual missing: []

### ❌ B87 [conflict]: 把重物放到精密仪器旁边
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b87a, actual=obj-0009b8
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b87b' not found in BT/CG

### ❌ B88 [conflict]: 抓住杯子，但我不想让你碰桌子
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b88a, actual=obj-09e65d
- Execution: expected=True, actual=True
- [CRITICAL] **execution_allowed_with_missing_roles**: Missing critical roles ['recipient'] but execution_allowed=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b88b' not found in BT/CG

### ⚠️ B90 [conflict]: 用不超过1N的力量抓住铁块
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b90, actual=obj-bff5dd
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B93 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=None
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B95 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=None
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B96 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=None
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B97 [invalid_input]: 抓住杯子
- Action: expected=, actual=GRASP
- Entity: expected=None, actual=obj-b037f8
- Execution: expected=True, actual=False
- [HIGH] **execution_blocked_falsely**: Execution allowed=False but expected=True

### ⚠️ B103 [mixed_colloquial]: grab那个红色的bottle然后放到table上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-b103a, actual=obj-82735f
- Execution: expected=True, actual=True
- [HIGH] **wrong_support_surface**: Expected support_surface=obj-b103b, got obj-060329

### ❌ B104 [mixed_colloquial]: 把那玩意儿拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b104, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B105 [mixed_colloquial]: 把beizi拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b105, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B106 [mixed_colloquial]: 帮我把那个玻璃bei拿过来行不
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b106, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)

### ❌ B108 [mixed_colloquial]: grasp the cup, but don't touch anything else!
- Action: expected=GRASP, actual=GRASP
- Entity: expected=obj-b108a, actual=obj-832833
- Execution: expected=True, actual=True
- [CRITICAL] **ignored_negation**: Expected avoid object 'obj-b108b' not found in BT/CG

### ❌ B110 [mixed_colloquial]: s'il vous plait, 把那个bouteille拿过来
- Action: expected=FETCH, actual=FETCH
- Entity: expected=obj-b110, actual=None
- Execution: expected=False, actual=False
- [CRITICAL] **theme_not_grounded**: Theme not grounded to any scene object (expected grounding, actual=None)
