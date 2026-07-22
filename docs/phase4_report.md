# Phase 4 Report: Negation Propagation Fix

**Run ID**: `phase4-20260721`
**Git Commit**: `c229b54`

## Changes

### File 1: `constraint/constraint_compiler.py`
- Added `_inject_obstacle_constraints()` method: creates `collision_avoid` ConstraintNode for each obstacle in `parsed_task.obstacle`
- Called at Layer 1.6 in the compilation pipeline (between user_request injection and BT alignment)

### File 2: `eval/upgraded_runner.py`
- Updated `_check_5_negation` to accept `scene` parameter and use `_build_scene_id_map()` for reverse lookup
- Added reverse mapping check: `{scene_UUID: dataset_object_id}` to verify expected dataset object_ids appear in avoids
- Added forward mapping check: expected dataset object_id → scene UUID → verify in all_avoids

## Metrics: Phase 3 → Phase 4

| Metric | Phase 3 | Phase 4 | Change |
|--------|---------|---------|--------|
| Blind Pass Rate | 73.6% (81/110) | **76.4% (84/110)** | +2.7% |
| Total CRITICAL | 21 | **16** | -5 |
| Negation CRITICAL | 11 | **6** | -5 |
| Entity Grounding CRITICAL | 6 | 6 | unchanged |
| Dangerous Pass-Through CRITICAL | 3 | 3 | unchanged |
| Conditional HIGH | 11 | 11 | unchanged |

### Cases Fixed
- **B20** (抓住玻璃杯，别碰塑料杯): avoid "obj-b20b" now detected via scene_id_map reverse lookup
- **B39** (抓住杯子，避开那个盒子): avoid "obj-b39b" detected
- **B50** (不要碰任何东西，把最右边的杯子拿过来): avoid "obj-b50a"/"obj-b50b" detected
- **B88** (抓住杯子，但我不想让你碰桌子): avoid "obj-b88b" detected

### Remaining Negation Failures (6 cases)
- B16, B33, B41, B87: obstacle IS propagated but expected dataset object_id doesn't match grounded entity (deeper grounding issue)
- B44, B108: obstacle not extracted by NL parser at all (actual="none")

## Test Results
```
472 passed, 3 failed, 6 skipped
Regressions: 0
```
