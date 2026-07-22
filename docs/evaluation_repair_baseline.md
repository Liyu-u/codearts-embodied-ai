# Evaluation Repair Baseline Report

**Run ID**: `baseline-phase0-20260721`
**Date**: 2026-07-21
**Git Branch**: `main`
**Git Commit**: `c229b54f4fd048b1dd1c08f391c73f68e6174c20`
**Git Message**: "存在bug需要修改"
**Python**: 3.11.9
**Key Dependencies**: pydantic==2.13.4, pytest==9.1.1, gradio==6.20.0, numpy==2.4.4

---

## 1. Pytest Results (Pre-Repair)

```
462 passed, 3 failed, 6 skipped in 10.57s
```

**3 Pre-existing Failures:**
| Test | Reason |
|------|--------|
| `test_reasoning_cases.py::TestPresetBinding::test_preset_updates_both_inputs` | ImportError: cannot import name 'PRESET_CASES' from web_ui |
| `test_reasoning_cases.py::TestPresetBinding::test_all_presets_executable` | ImportError: cannot import name 'PRESET_CASES' from web_ui |
| `test_semantic_regressions.py::test_handover_ui_never_shows_normal_when_blocked` | AssertionError: UI must show blocked/clarification status |

These 3 failures are pre-existing and NOT caused by this repair. They will be tracked but not fixed in this phase.

---

## 2. Golden Dataset (28 cases) — Conflicting Results

### EvalRunner (legacy, runner.py)
```
Total: 28 | Passed: 28 | Failed: 0 | Overall: 100.0%
Action Accuracy: 100.0% (23 cases)
Entity Grounding: 95.45% (22 cases)
Force Parsing: 100.0% (7 cases)
Role Detection: 100.0% (10 cases)
Schema Pass Rate: 92.86%
```

### UpgradedEvalRunner (upgraded_runner.py)
```
Total: 28 | Passed: 25 | Failed: 3 | Pass Rate: 89.3%
Severe Veto: 2 cases
CRITICAL: 2 | HIGH: 1
```

**Dimension Accuracy (UpgradedEvalRunner):**
| # | Dimension | Accuracy | Applicable | Correct | C | H |
|---|-----------|----------|------------|---------|---|---|
| 1 | action_recognition | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 2 | role_extraction | 0.0% | 1 | 0 | 1 | 0 |
| 3 | entity_grounding | 0.0% | 1 | 0 | 1 | 0 |
| 4 | multi_object_disambiguation | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 5 | negation_constraint_retention | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 6 | conditional_sequential_understanding | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 7 | numeric_operator_unit | 0.0% | 1 | 0 | 0 | 1 |
| 8 | perception_factual_fidelity | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 9 | robot_capability_constraint | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 10 | bt_ir_cross_field_consistency | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 11 | schema_validity | 100.0% | **28** ⚠️ | 28 | 0 | 0 |
| 12 | dangerous_error_pass_through | 100.0% | **28** ⚠️ | 28 | 0 | 0 |

⚠️ = Inflated: dimension had 0 findings but ALL 28 cases were counted as applicable+correct.

### Conflict: 28/28 vs 25/28
The EvalRunner reports 100% pass, but UpgradedEvalRunner reports 89.3%. The 3 failing cases are due to role_extraction (1 CRITICAL), entity_grounding (1 CRITICAL), and numeric_operator_unit (1 HIGH) errors that the legacy runner does not detect.

---

## 3. Blind Dataset (110 cases) — Conflicting Results

### BlindEvaluator (legacy, blind_runner.py)
```
Total: 110 | Passed: 71 | Failed: 39 | Pass Rate: 64.5%
CRITICAL: 24 | HIGH: 22 | MEDIUM: 2 | LOW: 0
Action Accuracy: 97.96% (98 cases)
Entity Grounding: 90.22% (92 cases)
Force Accuracy: 100.0% (8 cases)
Execution Gate: 87.16% (109 cases)
```

### UpgradedEvalRunner (upgraded_runner.py)
```
Total: 110 | Passed: 78 | Failed: 32 | Pass Rate: 70.9%
Severe Veto: 14 cases
CRITICAL: 24 | HIGH: 18 | MEDIUM: 3
```

**Dimension Accuracy (UpgradedEvalRunner):**
| # | Dimension | Accuracy | Applicable | Correct | C | H |
|---|-----------|----------|------------|---------|---|---|
| 1 | action_recognition | 0.0% | 2 ✓ | 0 | 0 | 2 |
| 2 | role_extraction | 50.0% | 4 ✓ | 2 | 1 | 1 |
| 3 | entity_grounding | 0.0% | 9 ✓ | 0 | 9 | 0 |
| 4 | multi_object_disambiguation | 100.0% | **110** ⚠️ | 110 | 0 | 0 |
| 5 | negation_constraint_retention | 0.0% | 15 ✓ | 0 | 11 | 4 |
| 6 | conditional_sequential_understanding | 8.3% | 12 ✓ | 1 | 0 | 11 |
| 7 | numeric_operator_unit | 100.0% | **110** ⚠️ | 110 | 0 | 0 |
| 8 | perception_factual_fidelity | 100.0% | **110** ⚠️ | 110 | 0 | 0 |
| 9 | robot_capability_constraint | 100.0% | **110** ⚠️ | 110 | 0 | 0 |
| 10 | bt_ir_cross_field_consistency | 100.0% | **110** ⚠️ | 110 | 0 | 0 |
| 11 | schema_validity | 100.0% | **110** ⚠️ | 110 | 0 | 0 |
| 12 | dangerous_error_pass_through | 0.0% | 3 ✓ | 0 | 3 | 0 |

⚠️ = Inflated: dimension had 0 findings but ALL 110 cases were counted as applicable+correct.
✓ = Real applicable count.

### Conflict: 71/110 vs 78/110
The BlindEvaluator reports 64.5% pass, but UpgradedEvalRunner reports 70.9%. The 7-case difference is due to different check logic and aggregation between the two runners.

---

## 4. Confirmed Dimension Applicable Counting Bug

**Root cause**: `upgraded_runner.py` `_compute_metrics()` lines 855-862:

```python
for key, d in dims.items():
    if d.applicable == 0:
        # No cases triggered checks on this dimension → 100% accurate
        d.applicable = len(self.verdicts)  # BUG: inflates to all cases
        d.correct = len(self.verdicts)      # BUG: all marked correct
    else:
        d.correct = d.applicable - d.critical_errors - d.high_errors
        # BUG: MEDIUM errors NOT subtracted
```

**Two bugs in one block:**
1. When `d.applicable == 0`, it sets applicable = ALL cases (28 or 110) — inflating coverage
2. When `d.applicable > 0`, only CRITICAL + HIGH are subtracted; MEDIUM errors are silently ignored

**Affected dimensions (golden, 28 cases):** 9 of 12 dimensions inflated
**Affected dimensions (blind, 110 cases):** 6 of 12 dimensions inflated

---

## 5. Bug Manifestation: Inflated Dimensions

| Dataset | Dimension | Reported | Real |
|---------|-----------|----------|------|
| Golden | action_recognition | 28 applicable, 28 correct | 0 applicable (no checks triggered) |
| Golden | multi_object_disambiguation | 28 applicable, 28 correct | 0 applicable |
| Golden | negation_constraint_retention | 28 applicable, 28 correct | 0 applicable |
| Golden | conditional_sequential_understanding | 28 applicable, 28 correct | 0 applicable |
| Golden | perception_factual_fidelity | 28 applicable, 28 correct | 0 applicable |
| Golden | robot_capability_constraint | 28 applicable, 28 correct | 0 applicable |
| Golden | bt_ir_cross_field_consistency | 28 applicable, 28 correct | 0 applicable |
| Golden | schema_validity | 28 applicable, 28 correct | 0 applicable |
| Golden | dangerous_error_pass_through | 28 applicable, 28 correct | 0 applicable |
| Blind | multi_object_disambiguation | 110 applicable, 110 correct | 0 applicable |
| Blind | numeric_operator_unit | 110 applicable, 110 correct | 0 applicable |
| Blind | perception_factual_fidelity | 110 applicable, 110 correct | 0 applicable |
| Blind | robot_capability_constraint | 110 applicable, 110 correct | 0 applicable |
| Blind | bt_ir_cross_field_consistency | 110 applicable, 110 correct | 0 applicable |
| Blind | schema_validity | 110 applicable, 110 correct | 0 applicable |

---

## 6. Known Failure Patterns (from failures.csv)

### Entity Grounding Failures (9 CRITICAL)
Colloquial/pinyin/mixed terms fail to ground:
- B09: "那玩意儿" → None
- B19: "那个小的" → None
- B37: "这个" → None
- B44: "那个红色的" / "蓝色的" → None
- B45: (conditional, theme not grounded) → None
- B104: "那玩意儿" → None
- B105: "beizi" → None
- B106: "玻璃bei" → None
- B110: "bouteille" → None

### Negation Propagation Failures (11 CRITICAL)
Avoid objects not propagated to BT/CG:
- B16, B20, B33, B39, B41, B44, B50, B87, B88, B108

### Dangerous Pass-Through (3 CRITICAL)
Execution allowed when should be blocked:
- B48, B86, B88

### Conditional/Sequential Understanding (11 HIGH)
Plan status inconsistent with execution_allowed:
- B38, B45, B48, B54, B62, B64, B86, B87, B93, B95, B96, B97

---

## 7. Baseline Artifacts Saved

```
docs/baseline_phase0/
├── summary_blind_upgraded.json          # UpgradedEvalRunner blind summary
├── case_results_blind_upgraded.json     # UpgradedEvalRunner blind case results
├── failures_blind_upgraded.csv          # UpgradedEvalRunner blind failures
├── report_blind_upgraded.md             # UpgradedEvalRunner blind report
├── blind_eval_results_legacy.json       # BlindEvaluator legacy results
├── blind_eval_report_legacy.md          # BlindEvaluator legacy report
├── summary.json                         # UpgradedEvalRunner golden summary
├── case_results.json                    # UpgradedEvalRunner golden case results
├── failures.csv                         # UpgradedEvalRunner golden failures
├── report.md                            # UpgradedEvalRunner golden report
├── eval_results_golden_legacy.json      # EvalRunner legacy golden results
└── eval_report_golden_legacy.md         # EvalRunner legacy golden report
```

---

## 8. Acceptance Checklist

- [x] Git branch, commit hash recorded
- [x] Python version and key dependency versions recorded
- [x] Golden dataset (28 cases) — both runner results saved
- [x] Blind dataset (110 cases) — both runner results saved
- [x] Full pytest results saved (462 passed, 3 failed, 6 skipped)
- [x] Conflicting results documented (golden: 28/28 vs 25/28, blind: 71/110 vs 78/110)
- [x] Dimension applicable counting bug isolated with line numbers and evidence
- [x] Known failure patterns categorized by dimension
- [x] All baseline artifacts saved to docs/baseline_phase0/
- [x] No production code modified
