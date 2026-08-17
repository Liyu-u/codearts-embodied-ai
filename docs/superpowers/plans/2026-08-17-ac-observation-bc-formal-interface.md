# A-C Observation and B-C Formal Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept A's existing `perception_observation 1.0.0` message unchanged and make B→C strategy arguments use only canonical stable IDs.

**Architecture:** Add a strict wire schema and a C-owned normalizer that converts the external observation into the repository's internal `perception.v1`. Keep the current executor abstraction, but tighten B output and C validation to canonical `object_id`/`destination_id` fields.

**Tech Stack:** Python 3.13 (`huawei` Conda), unittest/pytest, JSON Schema subset used by `integration.contract_validation`.

**Spec:** `docs/superpowers/specs/2026-08-17-ac-observation-bc-formal-interface-design.md`

## Global Constraints

- A's wire JSON field names and `schema_version="1.0.0"` remain unchanged.
- Internal adapters continue to consume `perception.v1`.
- Object IDs must survive normalization unchanged.
- B and C use only `object_id` and `destination_id`; no executable Python is accepted.
- A and D business code are not modified in this implementation.
- Every production behavior is implemented only after its failing test is observed.

---

### Task 1: A observation wire contract

**Files:**
- Create: `contracts/v1/perception_observation.schema.json`
- Create: `testdata/integration/a_perception_observation_v1.json`
- Modify: `integration/contract_validation.py`
- Create: `tests/contract/test_perception_observation_schema.py`

**Interfaces:**
- Consumes: A's exact `perception_observation 1.0.0` object.
- Produces: `validate_contract(value, "perception_observation.1.0.0") -> list[str]`.

- [x] Write tests that accept the exact fixture and reject wrong `message_type`, missing `object_id`, and missing quaternion `w`.
- [x] Run `python -m pytest tests/contract/test_perception_observation_schema.py -q` and confirm failure because the contract is not registered.
- [x] Add the schema and contract mapping.
- [x] Re-run the focused test and confirm pass.

### Task 2: C observation normalizer

**Files:**
- Create: `modules/perception/observation_normalizer.py`
- Modify: `integration/adapters/perception.py`
- Create: `tests/unit/test_perception_observation_normalizer.py`
- Modify: `tests/contract/test_perception_adapter.py`

**Interfaces:**
- Consumes: validated `perception_observation.1.0.0`.
- Produces: `normalize_observation(observation: dict) -> dict` returning `perception.v1`.

- [x] Write tests asserting literal normalized values: ID `obj_001`, category `cup`, color `transparent`, pose `(0.35, 0.12, 0.75)`, `xyzw` quaternion, dimensions and observation metadata.
- [x] Run the focused tests and confirm failure because the normalizer does not exist.
- [x] Implement candidate selection, numeric validation and metadata preservation.
- [x] Make `perception.run()` dispatch external messages to the normalizer while preserving existing Mock requests.
- [x] Re-run normalizer and perception contract tests.

### Task 3: Formal B→C stable-ID arguments

**Files:**
- Modify: `tests/contract/test_strategy_schema.py`
- Modify: `tests/unit/test_strategy_interpreter.py`
- Modify: `tests/unit/test_mock_executor_backend.py`
- Modify: `integration/adapters/strategy.py`
- Modify: `modules/executor/action_catalog.py`
- Modify: `modules/executor/mock_backend.py`
- Modify: `modules/strategy_generation/README.md`
- Modify: `modules/executor/README.md`
- Modify: `docs/BC联调接口说明.md`

**Interfaces:**
- Consumes: task target and destination stable IDs.
- Produces: canonical detect and move-target arguments.

- [x] Change tests to expect `detect_object.arguments={"object_id":"obj-001"}` and `move_to_target.arguments={"destination_id":"zone-001"}`.
- [x] Add tests proving C rejects legacy `object_name` and `target` arguments.
- [x] Run focused tests and confirm they fail against the transitional implementation.
- [x] Change B output and C validation/backend handlers to canonical fields.
- [x] Re-run B and C tests and keep the full Mock task successful.

### Task 4: Teammate requirement documents

**Files:**
- Create: `docs/interfaces/C对A修改需求-2026-08-17.md`
- Create: `docs/interfaces/C对B修改需求-2026-08-17.md`
- Create: `docs/interfaces/C对D修改需求-2026-08-17.md`

**Interfaces:**
- Produces: exact fields, success/failure examples, owner boundaries, tests and acceptance criteria for each teammate.

- [x] Document A's unchanged wire format plus remaining agreements: ID lifetime, units, full-scene snapshots, destination/obstacle inclusion and evaluation-only ground truth.
- [x] Document B's canonical five actions, argument schemas, invalid-output behavior and CodeArts-required evidence.
- [x] Document D's canonical field migration, execution evidence, error codes, supported patches and SAFE_STOP rules.

### Task 5: Regression verification

**Files:**
- Test: all `tests/`

- [x] Run focused A-C and B-C tests.
- [x] Run `python -m pytest -q` in the `huawei` environment.
- [x] Run `git diff --check` and review the exact diff.
- [x] Commit only the interface implementation, fixtures, tests and three requirement documents.
