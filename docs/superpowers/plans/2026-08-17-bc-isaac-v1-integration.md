# B-C Isaac v1 Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make B emit a safe five-primitive `pick_and_place` strategy that C can execute and D can evaluate, while preserving A and D unchanged.

**Architecture:** Start from the latest `origin/main`, migrate the already-tested C perception/executor implementation, then change only B's public adapter to lower a READY task into C/D-compatible primitives. Prove the boundary with contract tests and a real A/B/C/D offline pipeline test.

**Tech Stack:** Python 3.11+ standard library, `unittest`, repository JSON contracts, `huawei` Conda environment, C deterministic Mock backend.

**Spec:** `docs/superpowers/specs/2026-08-17-bc-isaac-v1-integration-design.md`

## Global Constraints

- Modify B and C business code only; A, D, contract schemas, and `integration/pipeline.py` remain unchanged.
- Phase one accepts only `READY + pick_and_place` with exactly one stable target ID and one stable destination ID.
- Public `strategy.v1.code` is always `null`; generated Python is never passed to or executed by C.
- B emits exactly `detect_object`, `move_to_object`, `grasp`, `move_to_target`, `release`.
- Grasp recovery uses `max_attempts=1`, a one-step retry, and `on_exhausted="stop"`.
- Run repository tests in the `huawei` Conda environment; reserve `isaacsim` for Kit/Isaac runtime work.
- Do not push until the user reviews the explanation, diff, and test evidence.

---

### Task 1: Migrate the existing C implementation onto current main

**Files:**
- Create: `integration/contract_validation.py`
- Create: `integration/adapters/perception.py`
- Create: `integration/adapters/executor.py`
- Create: `modules/perception/*.py`
- Create: `modules/executor/*.py`
- Create: `testdata/daily/stacking_scene.json`
- Create: `testdata/daily/stacking_strategy.json`
- Create: `tests/unit/test_*.py`
- Create: `tests/contract/test_contract_validation.py`
- Create: `tests/contract/test_perception_adapter.py`
- Create: `tests/contract/test_execution_adapter.py`
- Create: `tests/integration/test_mock_isaac_pipeline.py`

**Interfaces:**
- Consumes: existing `contracts/v1/{perception,strategy,execution}.schema.json`.
- Produces: `perception.run(dict) -> perception.v1` and `ExecutorAdapter.run(strategy.v1) -> execution.v1`.

- [ ] **Step 1: Cherry-pick the C test and implementation commits**

Run:

```powershell
git cherry-pick 22cbc47 1a735e8 d69e133 386a628 e22d5f3 91a232b
```

Keep current-main package `__init__.py` contents if add/add conflicts occur; do not change A, B, D, schemas, or Pipeline.

- [ ] **Step 2: Run all migrated C tests**

Run:

```powershell
D:\App\Business\Coding\Python\Miniconda\envs\huawei\python.exe -m unittest discover -s tests -t . -v
```

Expected: current-main 19 tests plus migrated C tests pass.

### Task 2: Specify the B and full-pipeline behavior with failing tests

**Files:**
- Modify: `tests/contract/test_strategy_schema.py`
- Modify: `testdata/daily/strategy_normal_pick.json`
- Create: `tests/e2e/test_abcd_pick_and_place_e2e.py`

**Interfaces:**
- Consumes: A `task.v1`, B `strategy.run`, C `ExecutorAdapter`, D `tracecoder.run`.
- Produces: executable B contract expectations and one real offline A/B/C/D acceptance test.

- [ ] **Step 1: Replace the old high-level-code expectation with literal primitive expectations**

The contract test must compare the actions to this literal list:

```python
["detect_object", "move_to_object", "grasp", "move_to_target", "release"]
```

It must assert `code is None`, the detect/target alias arguments carry stable IDs, and grasp recovery is exactly:

```python
{
    "max_attempts": 1,
    "steps": [{
        "step_id": "task-001-retry-grasp",
        "action": "grasp",
        "arguments": {"object_id": "$task-001-detect.object_id"},
    }],
    "on_exhausted": "stop",
}
```

- [ ] **Step 2: Add rejection tests for unsupported or unresolved READY tasks**

Use literal inputs for `push`, multiple `target_ids`, missing `target_ids`, and missing `destination_id`. Each result must have `blocked=True`, `success=False`, `steps=[]`, and `code=None`.

- [ ] **Step 3: Add the real A/B/C/D E2E test before changing production behavior**

The test obtains C's `stacking_cubes` perception, sends `把绿色方块放到桌子上` through real A and B, executes with C's `MockBackend`, and invokes real D. Assert stable IDs, the five actions, `SUCCEEDED`, `feedback.v1`, and a final object pose equal to the destination pose.

- [ ] **Step 4: Run the new tests and verify RED**

Run:

```powershell
D:\App\Business\Coding\Python\Miniconda\envs\huawei\python.exe -m unittest tests.contract.test_strategy_schema tests.e2e.test_abcd_pick_and_place_e2e -v
```

Expected: failures show the old B emits one `pick_and_place` step/non-null code and the current C scene labels do not yet give A an executable binding.

### Task 3: Make C perception semantically consumable by A

**Files:**
- Modify: `modules/perception/mock_scene.py`
- Modify: `testdata/daily/stacking_scene.json`

**Interfaces:**
- Consumes: the existing `stacking_cubes` scene shape.
- Produces: the same stable IDs and execution metadata with A-groundable Chinese categories.

- [ ] **Step 1: Change only the three semantic category values**

Use `红色方块`, `绿色方块`, and `桌子`; preserve IDs, poses, dimensions, attributes, and execution flags.

- [ ] **Step 2: Re-run the E2E test and inspect the next expected failure**

Run:

```powershell
D:\App\Business\Coding\Python\Miniconda\envs\huawei\python.exe -m unittest tests.e2e.test_abcd_pick_and_place_e2e -v
```

Expected: A now returns READY with `green_cube` and `zone_unstack_target`; execution still fails at B's old high-level strategy or non-null code.

### Task 4: Lower B output to the safe primitive contract

**Files:**
- Modify: `integration/adapters/strategy.py`
- Test: `tests/contract/test_strategy_schema.py`
- Test: `tests/e2e/test_abcd_pick_and_place_e2e.py`

**Interfaces:**
- Consumes: READY `task.v1` with one `target_ids` entry and non-empty `destination_id`.
- Produces: `strategy.v1` with five primitive steps and `code=None`.

- [ ] **Step 1: Restrict executable validation to the phase-one task shape**

Reject every action except `pick_and_place`, reject a target count other than one, and reject an empty destination ID. Return `_blocked_result(...)` for all rejection paths.

- [ ] **Step 2: Add `_build_pick_and_place_steps(task_id, target_id, destination_id)`**

Return literal step dictionaries with IDs `<task>-detect`, `<task>-approach`, `<task>-grasp`, `<task>-move-target`, and `<task>-release`; add `<task>-retry-grasp` only inside grasp recovery.

- [ ] **Step 3: Replace public generated-code output with a primitive plan result**

Return `success=True`, `blocked=False`, `mode="primitive_plan"`, `validation={}`, and `code=None`. Keep the internal generator and `health()` available, but never expose or execute generated code in `run()`.

- [ ] **Step 4: Run B contract and E2E tests and verify GREEN**

Run:

```powershell
D:\App\Business\Coding\Python\Miniconda\envs\huawei\python.exe -m unittest tests.contract.test_strategy_schema tests.e2e.test_abcd_pick_and_place_e2e -v
```

Expected: all selected tests pass.

### Task 5: Document the interface and verify the entire branch

**Files:**
- Create: `docs/BC联调接口说明.md`
- Create: `modules/perception/README.md`
- Create: `modules/executor/README.md`

**Interfaces:**
- Consumes: the accepted B/C implementation.
- Produces: teammate-facing fields, ownership boundaries, local commands, server boundary, and known limitations.

- [ ] **Step 1: Write the interface document**

Include one complete task/strategy/execution/feedback example, the action whitelist, stable-ID alias explanation, `huawei`/`isaacsim` environment split, and the Pipeline blocked-path limitation.

- [ ] **Step 2: Run the full test suite**

Run:

```powershell
D:\App\Business\Coding\Python\Miniconda\envs\huawei\python.exe -m unittest discover -s tests -t . -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Review scope and repository state**

Run:

```powershell
git diff --check
git status --short
git diff --name-only origin/main...HEAD
```

Confirm no A, D, Pipeline, schema, secret, server address, or personal absolute path is part of the product changes.

- [ ] **Step 4: Stop before push**

Present the branch path, changed files, interface explanation, test counts, and remaining Isaac/server validation to the user. Do not push.
