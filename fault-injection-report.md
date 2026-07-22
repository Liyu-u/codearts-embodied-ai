# Fault Injection Test Report

**Total**: 48 | **Passed**: 48 | **Failed**: 0
**Dangerous False Allows**: 0 | **Safety Stops**: 24

## Results by Category

| Category | Total | Passed | Failed |
|----------|-------|--------|--------|
| plan_validity | 1 | 1 | 0 |
| scene_mutation | 10 | 10 | 0 |
| perception_tracking | 9 | 9 | 0 |
| numeric_constraints | 12 | 12 | 0 |
| runtime_safety | 8 | 8 | 0 |
| planner_llm_fallback | 5 | 5 | 0 |
| concurrency_replay | 3 | 3 | 0 |

## Case Details

### ✅ FI-A1: Plan expired before execution
- **Category**: plan_validity
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: plan_expired

### ✅ FI-A2: Scene revision mismatch
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: scene_revision_mismatch

### ✅ FI-A3: Target removed after planning
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_missing

### ✅ FI-A4: Target entity_id replaced
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_missing

### ✅ FI-A5: Target position jumped beyond threshold
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_position_jumped

### ✅ FI-A6: Target material changed to fragile
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: fragile_force_exceeded

### ✅ FI-A7: Ambiguous object added (requires re-grounding)
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-A8: Similar objects swapped identities (requires re-grounding)
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-A9: FETCH delivery zone invalidated
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_missing

### ✅ FI-A10: HANDOVER recipient zone invalidated
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_missing

### ✅ FI-A11: PLACE support surface removed
- **Category**: scene_mutation
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_missing

### ✅ FI-B12: Perception data stale
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: perception_stale

### ✅ FI-B13: Perception timestamp went backwards
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: perception_timestamp_invalid

### ✅ FI-B14: Duplicate perception frame
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-B15: Tracking lost (requires runtime monitor)
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-B16: Tracking confidence below threshold (requires runtime monitor)
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-B17: Position sudden jump detected
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: target_position_jumped

### ✅ FI-B18: Velocity sudden spike (requires runtime monitor)
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-B19: Target occluded before execution (requires runtime monitor)
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-B20: Target lost during execution (requires runtime monitor)
- **Category**: perception_tracking
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-C21: Force NaN
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: force_n_not_finite

### ✅ FI-C22: Force Infinity
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: force_n_not_finite; force_n_exceeds_limit

### ✅ FI-C23: Force negative
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: force_n_negative

### ✅ FI-C24: Force excessive value
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: force_n_exceeds_limit

### ✅ FI-C25: Velocity NaN
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: velocity_ms_not_finite

### ✅ FI-C26: Velocity Infinity
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: velocity_ms_not_finite; velocity_ms_exceeds_limit

### ✅ FI-C27: Velocity negative
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: velocity_ms_negative

### ✅ FI-C28: Velocity exceeds stage hard limit
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: velocity_ms_exceeds_limit

### ✅ FI-C29: Safety config missing force limit (requires config validation)
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-C30: Safety config missing velocity limit (requires config validation)
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-C31: Multiple conflicting constraints
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-C32: Compiled constraint structure incomplete (requires constraint validation)
- **Category**: numeric_constraints
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D33: Slip detected after grasp (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D34: Grasp force feedback exceeds limit (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D35: Dynamic target speed exceeds allowable (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D36: Obstacle enters planned path (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D37: Human enters safety zone (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D38: Runtime guard unavailable
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: runtime_guard_unavailable

### ✅ FI-D39: Stop acknowledgement timeout (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-D40: BT action references invalid entity_id (requires runtime monitor)
- **Category**: runtime_safety
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-E41: RuleEngine produces valid plan with grounded target
- **Category**: planner_llm_fallback
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-E42: FETCH without delivery zone → NEEDS_CLARIFICATION
- **Category**: planner_llm_fallback
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-E43: HANDOVER without recipient pose → NEEDS_CLARIFICATION
- **Category**: planner_llm_fallback
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-E44: PLACE without support surface → NEEDS_CLARIFICATION
- **Category**: planner_llm_fallback
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-E45: 50N on glass → READY_WITH_SAFE_SUBSTITUTION
- **Category**: planner_llm_fallback
- **Seed**: 42
- **Expected exec_allowed**: True
- **Actual exec_allowed**: True
- **Dangerous**: False
- **Stop requested**: False
- **Stop reason**: 

### ✅ FI-F54: Plan consumed → cannot replay
- **Category**: concurrency_replay
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: plan_already_consumed

### ✅ FI-F55: Plan revoked → cannot execute
- **Category**: concurrency_replay
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: plan_already_consumed; plan_revoked

### ✅ FI-F56: Old scene revision used for execution request
- **Category**: concurrency_replay
- **Seed**: 42
- **Expected exec_allowed**: False
- **Actual exec_allowed**: False
- **Dangerous**: False
- **Stop requested**: True
- **Stop reason**: plan_already_consumed; plan_revoked; scene_revision_mismatch
