# Intent Understanding — Evaluation Report (v2.0)

**Run ID**: `eval-e8408f3a2ce2`
**Dataset**: holdout_v3.json
**Date**: 2026-07-21
**Total**: 150 | **Passed**: 150 | **Failed**: 0
**Severe Veto**: 0 cases failed by CRITICAL-only
**Pass Rate**: 100.0%

## Severity Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH | 0 |
| MEDIUM | 0 |
| LOW | 0 |
| INFO | 0 |

## 13-Dimension Accuracy

| # | Dimension | Accuracy | Applicable | Critical | High | Medium |
|---|-----------|----------|------------|----------|------|--------|
| 1. Action Recognition | N/A | 0 | 0 | 0 | 0 |
| 2. Role Extraction | 100.0% | 30 | 0 | 0 | 0 |
| 3. Entity Grounding | N/A | 0 | 0 | 0 | 0 |
| 4. Multi-Object Disambiguation | 100.0% | 35 | 0 | 0 | 0 |
| 5. Negation Constraint Retention | 100.0% | 15 | 0 | 0 | 0 |
| 6. Conditional/Sequential Understanding | 100.0% | 31 | 0 | 0 | 0 |
| 7. Numeric/Operator/Unit Accuracy | 100.0% | 10 | 0 | 0 | 0 |
| 8. Perception Factual Fidelity | 100.0% | 149 | 0 | 0 | 0 |
| 9. Robot Capability Constraint | 100.0% | 21 | 0 | 0 | 0 |
| 10. BT/IR Cross-Field Consistency | 100.0% | 149 | 0 | 0 | 0 |
| 11. Schema Validity | 100.0% | 149 | 0 | 0 | 0 |
| 12. Dangerous Error Pass-Through | N/A | 0 | 0 | 0 | 0 |

## Latency

| Avg | P50 | P95 | P99 |
|-----|-----|-----|-----|
| 5.2ms | 5.4ms | 7.6ms | 8.6ms |

## By Category

| Category | Total | Passed | Critical | High |
|----------|-------|--------|----------|------|
| ambiguity | 10 | 10 | 0 | 0 |
| conflicting_input | 10 | 10 | 0 | 0 |
| if_else | 10 | 10 | 0 | 0 |
| missing_role | 10 | 10 | 0 | 0 |
| multi_object | 12 | 12 | 0 | 0 |
| negation | 12 | 12 | 0 | 0 |
| numeric_constraints | 10 | 10 | 0 | 0 |
| ordinal_reference | 10 | 10 | 0 | 0 |
| robot_state | 10 | 10 | 0 | 0 |
| role_binding | 12 | 12 | 0 | 0 |
| sequence | 10 | 10 | 0 | 0 |
| simple_action | 12 | 12 | 0 | 0 |
| size_color_spatial | 12 | 12 | 0 | 0 |
| unless | 10 | 10 | 0 | 0 |

## Legacy Metrics (backward compatible)

| Metric | Accuracy | Cases |
|--------|----------|-------|
| Action | 100.0% | 0 |
| Entity Grounding | 100.0% | 0 |
| Force Parsing | 100.0% | 0 |
| Role Detection | 100.0% | 30 |
| Schema Pass | 100.0% | 150 |
| Overall | 100.0% | 150 |

## Failed Cases
