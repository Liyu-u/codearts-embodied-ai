# Evaluation Repair — Final Report

**Date**: 2026-07-21
**Git Branch**: `main`
**Git Commit**: `c229b54f4fd048b1dd1c08f391c73f68e6174c20`
**Python**: 3.11.9 | **pytest**: 9.1.1 | **pydantic**: 2.13.4

---

## Executive Summary

Repaired the evaluation trustworthiness and capability convergence of the robot_intent_agent intent understanding module across 5 phases. Fixed a critical dimension counting bug that inflated 6-9 dimensions to 100% accuracy with full coverage, unified the evaluation system, and improved entity grounding and negation propagation.

### Key Results

| Metric | Baseline | After Repair | Change |
|--------|----------|-------------|--------|
| **Blind Pass Rate (UpgradedEvalRunner)** | 70.9% (78/110) | **76.4% (84/110)** | **+5.5% (+6 cases)** |
| Blind CRITICAL errors | 24 | **16** | **-8** |
| Entity Grounding CRITICAL | 9 | **6** | **-3** |
| Negation CRITICAL | 11 | **6** | **-5** |
| Dimension counting bug | 15 inflated | **0 inflated** | **FIXED** |
| MEDIUM error tracking | Ignored | **Tracked** | **FIXED** |
| Object ID through pipeline | Lost | **Preserved** | **FIXED** |

### Test Results: 472 passed, 3 failed (pre-existing), 6 skipped — 0 regressions

---

## Phase-by-Phase Summary

### Phase 1: Evaluator Fix — Dimension Counting & Unification
**Files**: `upgraded_runner.py`, `runner.py`, `blind_runner.py`, `test_upgraded_evaluator.py`

- **Fixed**: `_compute_metrics()` bug that inflated `applicable` to ALL cases when 0 findings triggered
- **Fixed**: MEDIUM errors now properly counted (previously silently ignored)
- **Added**: Deprecation notices on legacy runners pointing to UpgradedEvalRunner
- **Tests**: 4 new tests (TestDimensionApplicableCount)

### Phase 2: Object ID/UUID/Name Tracking
**Files**: `semantic_scene_builder.py`, `upgraded_runner.py`, `test_scene_builder.py`

- **Added**: `object_id` field to `RawObjectPercept` dataclass
- **Added**: `_perception_object_id` preserved in SceneObject attributes
- **Added**: `_build_scene_id_map()` method for `{dataset_object_id: scene_uuid}` mapping
- **Tests**: 6 new tests (TestObjectIdPreservation)

### Phase 3: Entity Grounding — Pinyin/Mixed Terms
**Files**: `task_semantics.py`

- **Expanded**: `_CN_CATEGORY_ALIASES` with pinyin aliases (beizi, bei, ping, bouteille, etc.)
- **Added**: Partial character-segment matching in `_score_category` for mixed-language terms
- **Fixed**: 3 entity grounding cases (B105, B106, B110)
- **Entity Grounding CRITICAL**: 9 → 6

### Phase 4: Negation Propagation to BT/CG
**Files**: `constraint_compiler.py`, `upgraded_runner.py`

- **Added**: `_inject_obstacle_constraints()` — creates collision_avoid CG nodes from parsed_task.obstacle
- **Enhanced**: `_check_5_negation` to use scene_id_map for reverse lookup of dataset object_ids
- **Fixed**: 4 negation cases (B20, B39, B50, B88)
- **Negation CRITICAL**: 11 → 6

### Phase 5: Plan Status / Conditional Logic — Partial
**Files**: `constraint_compiler.py`

- **Improved**: `_resolve_constraints()` more selective about NEEDS_CLARIFICATION
- Only truly blocking roles (theme, recipient, recipient_pose) now trigger it
- Operational gaps (delivery_pose_or_fetch_zone) no longer unnecessarily block
- Conditional errors unchanged — deeper architectural fix needed

---

## Dimension Accuracy: Before → After (Blind 110 cases)

| Dimension | Baseline | After Repair | Notes |
|-----------|----------|-------------|-------|
| action_recognition | 0.0% (app:110⚠️→2) | 0.0% (app:2) | Counting fixed |
| role_extraction | 50.0% (app:4) | 0.0% (app:4) | MEDIUM now tracked |
| entity_grounding | 0.0% (app:9) | 0.0% (app:6) | -3 CRITICAL |
| multi_object_disambiguation | 100.0% (app:110⚠️) | 100.0% (app:0) | Was inflated |
| negation_constraint_retention | 0.0% (app:15) | 0.0% (app:10) | -5 CRITICAL |
| conditional_sequential_understanding | 8.3% (app:12) | 0.0% (app:12) | MEDIUM tracked |
| numeric_operator_unit | 100.0% (app:110⚠️) | 100.0% (app:0) | Was inflated |
| perception_factual_fidelity | 100.0% (app:110⚠️) | 100.0% (app:0) | Was inflated |
| robot_capability_constraint | 100.0% (app:110⚠️) | 100.0% (app:0) | Was inflated |
| bt_ir_cross_field_consistency | 100.0% (app:110⚠️) | 100.0% (app:0) | Was inflated |
| schema_validity | 100.0% (app:110⚠️) | 100.0% (app:0) | Was inflated |
| dangerous_error_pass_through | 0.0% (app:3) | 0.0% (app:3) | unchanged |

⚠️ = Was inflated to all 110 cases; now correctly shows 0 (no checks triggered)

---

## Remaining Issues (for future phases)

| Dimension | Count | Root Cause |
|-----------|-------|-----------|
| Entity Grounding CRITICAL | 6 | Demonstrative/colloquial terms (B09, B19, B37, B44, B45, B104) need deeper NL resolution |
| Negation CRITICAL | 6 | Obstacle entity_id doesn't match expected dataset object_id in mapping chain (B16, B33, B41, B44, B87, B108) |
| Conditional HIGH | 11 | FinalPlanValidator.validate() recomputes status; interplay with constraint_compiler needs architectural refactor |
| Dangerous Pass-Through CRITICAL | 3 | Execution allowed when missing critical roles or theme not in scene (B48, B86, B88) |
| Pre-existing test failures | 3 | PRESET_CASES import (×2), UI handover assertion (×1) — not caused by this repair |

---

## Files Modified

```
robot_intent_agent/eval/upgraded_runner.py       (evaluator fix + ID mapping + negation check)
robot_intent_agent/eval/runner.py                 (deprecation notice)
robot_intent_agent/eval/blind_runner.py           (deprecation notice)
robot_intent_agent/scene_builder/semantic_scene_builder.py  (object_id preservation)
robot_intent_agent/task_semantics.py              (pinyin aliases + partial match)
robot_intent_agent/constraint/constraint_compiler.py  (obstacle injection + selective blocking)
robot_intent_agent/tests/test_upgraded_evaluator.py    (4 new tests)
robot_intent_agent/tests/test_scene_builder.py         (6 new tests)
```

## Reports Generated

```
docs/evaluation_repair_baseline.md   (Phase 0: baseline snapshot)
docs/phase1_report.md                (Phase 1: evaluator fix)
docs/phase2_report.md                (Phase 2: object ID tracking)
docs/phase3_report.md                (Phase 3: entity grounding)
docs/phase4_report.md                (Phase 4: negation propagation)
docs/phase5_report.md                (Phase 5: plan status)
docs/evaluation_repair_final.md      (this report)
```
