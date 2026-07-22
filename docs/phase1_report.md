# Phase 1 Report: Evaluator Fix — Dimension Counting & Unification

**Run ID**: `phase1-20260721`
**Date**: 2026-07-21
**Git Commit**: `c229b54f4fd048b1dd1c08f391c73f68e6174c20`

---

## Changes Made

### File 1: `robot_intent_agent/eval/upgraded_runner.py`
- **Line 78-87**: Added `medium_errors: int = 0` to `DimensionScore` dataclass
- **Line 86-90**: Updated `compute()` docstring to clarify 0 applicable = unchecked (not 100% correct)
- **Lines 845-868**: Rewrote `_compute_metrics()` dimension counting:
  - **Removed**: `if d.applicable == 0: d.applicable = len(self.verdicts)` (the inflation bug)
  - **Added**: `medium_errors` tracking in the finding loop
  - **Changed**: `correct = applicable - critical - high` → `correct = max(0, applicable - critical - high - medium)`
- **Line 129**: Added `medium_errors` to `to_dict()` dimension output
- **Lines 1013-1014**: Added "Medium" column to report markdown table header
- **Line 1032**: Added `{d.medium_errors}` to report markdown row

### File 2: `robot_intent_agent/eval/runner.py`
- Added deprecation notice in module docstring pointing to UpgradedEvalRunner

### File 3: `robot_intent_agent/eval/blind_runner.py`
- Added deprecation notice in module docstring pointing to UpgradedEvalRunner

### File 4: `robot_intent_agent/tests/test_upgraded_evaluator.py`
- Added `TestDimensionApplicableCount` class with 4 tests:
  - `test_zero_findings_dimension_has_zero_applicable`
  - `test_dimension_with_errors_has_applicable_gt_zero`
  - `test_no_dimension_inflated_to_total`
  - `test_applicable_never_exceeds_total`

---

## Metrics: Before → After

### Golden Dataset (28 cases)

| Dimension | Applicable Before | Applicable After | Status |
|-----------|------------------|------------------|--------|
| action_recognition | **28** | 0 | FIXED |
| role_extraction | 1 | 1 | unchanged |
| entity_grounding | 1 | 1 | unchanged |
| multi_object_disambiguation | **28** | 0 | FIXED |
| negation_constraint_retention | **28** | 0 | FIXED |
| conditional_sequential_understanding | **28** | 0 | FIXED |
| numeric_operator_unit | 1 | 1 | unchanged |
| perception_factual_fidelity | **28** | 0 | FIXED |
| robot_capability_constraint | **28** | 0 | FIXED |
| bt_ir_cross_field_consistency | **28** | 0 | FIXED |
| schema_validity | **28** | 0 | FIXED |
| dangerous_error_pass_through | **28** | 0 | FIXED |

**Pass rate**: 25/28 (89.3%) — unchanged (scoring logic not modified)

### Blind Dataset (110 cases)

| Dimension | Applicable Before | Applicable After | Status |
|-----------|------------------|------------------|--------|
| action_recognition | 2 | 2 | unchanged |
| role_extraction | 4 | 4 | unchanged (acc 50%→0%, MEDIUM now counted) |
| entity_grounding | 9 | 9 | unchanged |
| multi_object_disambiguation | **110** | 0 | FIXED |
| negation_constraint_retention | 15 | 15 | unchanged |
| conditional_sequential_understanding | 12 | 12 | unchanged (acc 8.3%→0%, MEDIUM now counted) |
| numeric_operator_unit | **110** | 0 | FIXED |
| perception_factual_fidelity | **110** | 0 | FIXED |
| robot_capability_constraint | **110** | 0 | FIXED |
| bt_ir_cross_field_consistency | **110** | 0 | FIXED |
| schema_validity | **110** | 0 | FIXED |
| dangerous_error_pass_through | 3 | 3 | unchanged |

**Pass rate**: 78/110 (70.9%) — unchanged (scoring logic not modified)

---

## Test Results

```
466 passed, 3 failed, 6 skipped in 10.53s
```

- **New tests**: 4 passed (TestDimensionApplicableCount)
- **Pre-existing failures**: 3 (PRESET_CASES import ×2, UI handover assertion ×1) — unchanged
- **Regressions**: 0

---

## Key Finding: MEDIUM Errors Were Silently Ignored

The `role_extraction` dimension accuracy dropped from 50% → 0% and `conditional_sequential_understanding` from 8.3% → 0% because MEDIUM-severity errors were previously NOT subtracted from the correct count. With the fix:
- `role_extraction`: 1 CRITICAL + 1 HIGH + 2 MEDIUM = 4 errors → correct=0
- `conditional_sequential_understanding`: 0 CRITICAL + 11 HIGH + 1 MEDIUM = 12 errors → correct=0

This is the **correct** behavior — all error severities matter.

---

## Unresolved Issues (carried to next phases)

1. Entity grounding: 9 CRITICAL failures (colloquial/pinyin terms)
2. Negation propagation: 11 CRITICAL failures
3. Dangerous pass-through: 3 CRITICAL failures
4. Conditional understanding: 11 HIGH failures
5. Object ID/UUID/name mixing in evaluation
