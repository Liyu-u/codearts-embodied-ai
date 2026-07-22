# Intent Understanding — Evaluation Report (v2.0)

**Dataset**: golden_dataset.json
**Date**: 2026-07-20
**Total**: 28 | **Passed**: 23 | **Failed**: 5
**Severe Veto**: 3 cases failed by CRITICAL-only
**Pass Rate**: 82.1%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH | 2 |
| MEDIUM | 0 |
| LOW | 0 |
| INFO | 0 |

## 13-Dimension Accuracy

| # | Dimension | Accuracy | Applicable | Critical | High |
|---|-----------|----------|------------|----------|------|
| 1. Action Recognition | 100.0% | 28 | 0 | 0 |
| 2. Role Extraction | 0.0% | 2 | 1 | 1 |
| 3. Entity Grounding | 0.0% | 2 | 2 | 0 |
| 4. Multi-Object Disambiguation | 100.0% | 28 | 0 | 0 |
| 5. Negation Constraint Retention | 100.0% | 28 | 0 | 0 |
| 6. Conditional/Sequential Understanding | 100.0% | 28 | 0 | 0 |
| 7. Numeric/Operator/Unit Accuracy | 0.0% | 1 | 0 | 1 |
| 8. Perception Factual Fidelity | 100.0% | 28 | 0 | 0 |
| 9. Robot Capability Constraint | 100.0% | 28 | 0 | 0 |
| 10. BT/IR Cross-Field Consistency | 100.0% | 28 | 0 | 0 |
| 11. Schema Validity | 100.0% | 28 | 0 | 0 |
| 12. Dangerous Error Pass-Through | 100.0% | 28 | 0 | 0 |

## Latency

| Avg | P50 | P95 | P99 |
|-----|-----|-----|-----|
| 4.9ms | 4.0ms | 5.5ms | 29.8ms |

## By Category

| Category | Total | Passed | Critical | High |
|----------|-------|--------|----------|------|
| unknown | 28 | 23 | 3 | 2 |

## Legacy Metrics (backward compatible)

| Metric | Accuracy | Cases |
|--------|----------|-------|
| Action | 100.0% | 23 |
| Entity Grounding | 95.5% | 22 |
| Force Parsing | 85.7% | 7 |
| Role Detection | 91.3% | 23 |
| Schema Pass | 100.0% | 28 |
| Overall | 82.1% | 28 |

## Failed Cases

### ⚠️ G002 [unknown]: 用5N力量把盒子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-002, actual=obj-3ba74c
- Execution: expected=None, actual=False
- [HIGH] **numeric_operator_unit**: Velocity constraint not extracted from NL (expected=0.2, actual=not parsed)

### ❌ N003 [unknown]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=None, actual=obj-481ee5
- Execution: expected=None, actual=False
- [CRITICAL] **role_extraction**: Support surface fabricated when not in scene (expected=none, actual=桌)

### ❌ N004 [unknown]: 把红色玻璃杯拿过来
- Action: expected=, actual=FETCH
- Entity: expected=None, actual=obj-3ce05b
- Execution: expected=None, actual=False
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=red, actual=blue)

### ⚠️ M004 [unknown]: 把杯子放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-m04a, actual=obj-d79cc8
- Execution: expected=None, actual=False
- [HIGH] **role_extraction**: Support surface entity_id not a scene object UUID: '桌' (expected=obj-m04b, actual=桌)

### ❌ R005 [unknown]: 用5N力量把蓝色药瓶放到桌子上
- Action: expected=PLACE, actual=PLACE
- Entity: expected=obj-r05, actual=obj-e8fcdc
- Execution: expected=None, actual=False
- [CRITICAL] **entity_grounding**: Color mismatch in grounding (expected=blue, actual=red)
