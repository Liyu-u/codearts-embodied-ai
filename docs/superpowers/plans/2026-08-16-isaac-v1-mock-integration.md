# Isaac v1 Mock Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build C 模块的 `perception.v1` 与 `execution.v1` 适配器，用确定性 Mock 后端在无 Isaac Sim、无服务器、无 A/B 真实模块的条件下跑通第一条安全策略闭环。

**Architecture:** `modules/perception` 把场景对象和虚拟目标区转换成 `perception.v1`；`modules/executor` 用白名单策略解释器驱动可替换后端。第一阶段只实现 `MockBackend`，但 `ExecutorAdapter.run(strategy_v1)` 的接口与后续离线 Isaac 后端保持一致；后端在构造适配器时绑定到同一份 perception 状态，因此不修改公共 `integration/pipeline.py` 的调用方式。

**Tech Stack:** Python 3.11-compatible standard library, `unittest`, JSON Schema 子集校验器，现有 `contracts/v1`，GitHub Actions；Windows 日常开发使用 `huawei` Conda 环境。

## Global Constraints

- 第一阶段不得导入 Isaac Sim、Omniverse、CUDA 或服务器工具。
- 不新增运行时第三方依赖；CI 以 Python 3.11 标准库运行。
- 适配器统一暴露 `run(input_json: dict) -> dict` 与 `health() -> dict`；executor 使用实现这两个方法的实例。
- 只允许 `detect_object`、`move_to_object`、`grasp`、`move_to_target`、`release` 五个动作。
- `strategy.v1.code` 只允许 `null` 或空字符串；任何非空代码必须在执行前拒绝。
- `task_id` 必须从 `strategy.v1` 原样写入 `execution.v1`。
- 物理长度单位为米，第一阶段坐标系固定为 `world`。
- 上游不得直接提交任意目标坐标；`move_to_target` 只接受 perception 中标记为 `valid_destination` 的 ID。
- 主步骤失败且无恢复时立即停止，剩余主步骤记为 `SKIPPED`。
- 恢复耗尽、动作超限或安全门禁失败时输出 `SAFE_STOP`。
- 不提交服务器地址、SSH 端口、账号、密码、个人绝对路径、API Key、运行日志或截图。
- 每项行为变更严格执行 RED → GREEN → REFACTOR；提交只保存在本地分支，未经吴昌庆最终确认不得 `git push`。
- 第二阶段的缓存权限修复、Isaac 探针和真实服务器执行另写计划，不混入本计划。

---

## File Map

| File | Responsibility |
|---|---|
| `integration/contract_validation.py` | 无第三方依赖地加载并校验当前 `contracts/v1` 使用到的 JSON Schema 子集 |
| `modules/perception/mock_scene.py` | 提供确定性的 `stacking_cubes` 原始场景与虚拟安全目标区 |
| `modules/perception/service.py` | 原始场景 → `perception.v1` |
| `integration/adapters/perception.py` | perception 模块的协议校验和 `run()` / `health()` |
| `modules/executor/models.py` | 执行限制、步骤结果和后端 Protocol |
| `modules/executor/action_catalog.py` | 五个动作的参数约束与过渡别名 |
| `modules/executor/mock_backend.py` | 确定性状态机、失败注入、轨迹和安全停止 |
| `modules/executor/strategy_interpreter.py` | 策略预检、引用解析、主流程、恢复流程和 `execution.v1` 组装 |
| `integration/adapters/executor.py` | 绑定后端的 executor 适配器及输入/输出契约校验 |
| `testdata/daily/stacking_scene.json` | 第一条 perception 样例 |
| `testdata/daily/stacking_strategy.json` | 第一条 strategy 样例 |
| `tests/contract/` | Schema 边界和适配器契约测试 |
| `tests/unit/` | perception、Mock 状态机、解释器与恢复单元测试 |
| `tests/integration/test_mock_isaac_pipeline.py` | Mock A/B + C perception/executor 的公共 pipeline 联调测试 |
| `docs/Isaac执行器接口说明.md` | 面向 A/B/D 的正式接口手册 |
| `modules/perception/README.md` | perception 模块使用说明 |
| `modules/executor/README.md` | executor 模块使用说明 |
| `.github/workflows/integration-contract.yml` | 在 CI 中运行 JSON 检查和全部标准库测试 |
| `Makefile` | 统一 `contract-test`、`integration-test`、`e2e` 和 `test` 命令 |

---

### Task 1: 标准库契约校验器

**Files:**
- Create: `integration/contract_validation.py`
- Create: `tests/__init__.py`
- Create: `tests/contract/__init__.py`
- Create: `tests/contract/test_contract_validation.py`

**Interfaces:**
- Consumes: `contracts/v1/{perception,strategy,execution}.schema.json`
- Produces: `load_contract(schema_version: str) -> dict`, `validate_contract(value: object, schema_version: str) -> list[str]`, `assert_contract(value: object, schema_version: str) -> None`, `ContractValidationError`

- [ ] **Step 1: Write the failing contract-loader and validator tests**

```python
# tests/contract/test_contract_validation.py
import unittest

from integration.contract_validation import (
    ContractValidationError,
    assert_contract,
    load_contract,
    validate_contract,
)


class ContractValidationTests(unittest.TestCase):
    def test_loads_strategy_contract_by_schema_version(self):
        schema = load_contract("strategy.v1")
        self.assertEqual(schema["$id"], "robot-system/strategy/v1")

    def test_valid_strategy_has_no_errors(self):
        value = {
            "schema_version": "strategy.v1",
            "task_id": "task-001",
            "steps": [],
            "code": None,
        }
        self.assertEqual(validate_contract(value, "strategy.v1"), [])

    def test_missing_required_field_reports_json_path(self):
        value = {"schema_version": "strategy.v1", "steps": []}
        self.assertEqual(
            validate_contract(value, "strategy.v1"),
            ["$.task_id: required property is missing"],
        )

    def test_wrong_const_and_nested_type_are_reported(self):
        value = {
            "schema_version": "strategy.v2",
            "task_id": "task-001",
            "steps": [{"step_id": "s1", "action": "grasp", "arguments": []}],
        }
        errors = validate_contract(value, "strategy.v1")
        self.assertIn("$.schema_version: expected constant 'strategy.v1'", errors)
        self.assertIn("$.steps[0].arguments: expected type object", errors)

    def test_assert_contract_raises_one_stable_error(self):
        with self.assertRaisesRegex(
            ContractValidationError,
            "strategy.v1 validation failed",
        ):
            assert_contract({}, "strategy.v1")

    def test_unknown_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported schema version"):
            load_contract("unknown.v1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
python -m unittest tests.contract.test_contract_validation -v
```

Expected: `ModuleNotFoundError: No module named 'integration.contract_validation'`.

- [ ] **Step 3: Implement the minimal recursive validator**

```python
# integration/contract_validation.py
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_FILES = {
    "perception.v1": "perception.schema.json",
    "task.v1": "task.schema.json",
    "strategy.v1": "strategy.schema.json",
    "execution.v1": "execution.schema.json",
    "feedback.v1": "feedback.schema.json",
}


class ContractValidationError(ValueError):
    pass


@lru_cache(maxsize=None)
def load_contract(schema_version: str) -> dict:
    filename = CONTRACT_FILES.get(schema_version)
    if filename is None:
        raise ValueError(f"unsupported schema version: {schema_version}")
    path = ROOT / "contracts" / "v1" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _matches_type(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return checks[expected](value)


def _validate(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(value, item) for item in allowed):
            rendered = " or ".join(allowed)
            errors.append(f"{path}: expected type {rendered}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path}: string is shorter than {schema['minLength']}")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property is missing")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                _validate(child, properties[name], f"{path}.{name}", errors)
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: additional property is not allowed")

    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{path}[{index}]", errors)


def validate_contract(value: object, schema_version: str) -> list[str]:
    errors: list[str] = []
    _validate(value, load_contract(schema_version), "$", errors)
    return errors


def assert_contract(value: object, schema_version: str) -> None:
    errors = validate_contract(value, schema_version)
    if errors:
        raise ContractValidationError(
            f"{schema_version} validation failed: " + "; ".join(errors)
        )
```

- [ ] **Step 4: Run focused and full baseline tests**

Run:

```bash
python -m unittest tests.contract.test_contract_validation -v
python -m unittest discover -s tests -t . -v
```

Expected: 6 tests pass, 0 failures.

- [ ] **Step 5: Commit locally**

```bash
git add integration/contract_validation.py tests/__init__.py tests/contract/__init__.py tests/contract/test_contract_validation.py
git commit -m "test: add runtime contract validation"
```

---

### Task 2: `perception.v1` Mock 场景与适配器

**Files:**
- Create: `modules/__init__.py`
- Create: `modules/perception/__init__.py`
- Create: `modules/perception/mock_scene.py`
- Create: `modules/perception/service.py`
- Create: `integration/adapters/__init__.py`
- Create: `integration/adapters/perception.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_perception_service.py`
- Create: `tests/contract/test_perception_adapter.py`
- Create: `testdata/daily/stacking_scene.json`

**Interfaces:**
- Consumes: `{"scene_id": "stacking_cubes", "backend": "mock"}`
- Produces: `get_mock_scene(scene_id: str) -> dict`, `observe_scene(request: dict) -> dict`, module-level adapter `run(input_json: dict) -> dict` and `health() -> dict`

- [ ] **Step 1: Write failing perception service tests**

```python
# tests/unit/test_perception_service.py
import unittest

from modules.perception.service import observe_scene


class PerceptionServiceTests(unittest.TestCase):
    def test_stacking_scene_uses_stable_ids_and_world_frame(self):
        result = observe_scene({"scene_id": "stacking_cubes", "backend": "mock"})
        self.assertEqual(result["schema_version"], "perception.v1")
        self.assertEqual(result["scene_id"], "stacking_cubes")
        self.assertEqual(result["coordinate_frame"], "world")
        by_id = {item["id"]: item for item in result["objects"]}
        self.assertEqual(by_id["green_cube"]["attributes"]["color"], "green")
        self.assertTrue(by_id["green_cube"]["execution"]["graspable"])
        self.assertTrue(
            by_id["zone_unstack_target"]["execution"]["valid_destination"]
        )

    def test_unknown_scene_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported mock scene"):
            observe_scene({"scene_id": "missing", "backend": "mock"})

    def test_non_mock_backend_is_rejected_in_phase_one(self):
        with self.assertRaisesRegex(ValueError, "backend must be mock"):
            observe_scene({"scene_id": "stacking_cubes", "backend": "isaac"})
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python -m unittest tests.unit.test_perception_service -v
```

Expected: import fails because `modules.perception.service` does not exist.

- [ ] **Step 3: Implement the raw scene and pure conversion service**

```python
# modules/perception/mock_scene.py
from copy import deepcopy

STACKING_CUBES = {
    "scene_id": "stacking_cubes",
    "coordinate_frame": "world",
    "objects": [
        {
            "id": "red_cube",
            "category": "cube",
            "pose": {"x": 0.25, "y": 0.0, "z": 0.04},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "红色方块", "color": "red"},
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "green_cube",
            "category": "cube",
            "pose": {"x": 0.25, "y": 0.0, "z": 0.12},
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "绿色方块", "color": "green"},
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "zone_unstack_target",
            "category": "target_zone",
            "pose": {"x": 0.4, "y": 0.0, "z": 0.03},
            "attributes": {"purpose": "safe_placement"},
            "execution": {
                "movable": False,
                "graspable": False,
                "valid_destination": True,
            },
        },
    ],
}


def get_mock_scene(scene_id: str) -> dict:
    if scene_id != "stacking_cubes":
        raise ValueError(f"unsupported mock scene: {scene_id}")
    return deepcopy(STACKING_CUBES)
```

```python
# modules/perception/service.py
from modules.perception.mock_scene import get_mock_scene


def observe_scene(request: dict) -> dict:
    if not isinstance(request, dict):
        raise TypeError("perception request must be an object")
    if request.get("backend", "mock") != "mock":
        raise ValueError("phase-one backend must be mock")
    raw = get_mock_scene(request.get("scene_id", ""))
    return {
        "schema_version": "perception.v1",
        "scene_id": raw["scene_id"],
        "coordinate_frame": raw["coordinate_frame"],
        "objects": raw["objects"],
        "execution_context": {"backend": "mock", "scene_revision": "1"},
    }
```

- [ ] **Step 4: Run service tests and verify GREEN**

Run:

```bash
python -m unittest tests.unit.test_perception_service -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Write failing adapter contract tests**

```python
# tests/contract/test_perception_adapter.py
import unittest

from integration.adapters import perception
from integration.contract_validation import validate_contract


class PerceptionAdapterContractTests(unittest.TestCase):
    def test_run_returns_valid_perception_v1(self):
        output = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        self.assertEqual(validate_contract(output, "perception.v1"), [])

    def test_health_reports_mock_mode(self):
        self.assertEqual(
            perception.health(),
            {
                "status": "ok",
                "module": "perception",
                "version": "1.0.0",
                "backend": "mock",
            },
        )
```

- [ ] **Step 6: Run and verify RED**

Run:

```bash
python -m unittest tests.contract.test_perception_adapter -v
```

Expected: import fails because the adapter does not exist.

- [ ] **Step 7: Implement adapter and create the checked-in sample**

```python
# integration/adapters/perception.py
from integration.contract_validation import assert_contract
from modules.perception.service import observe_scene


def run(input_json: dict) -> dict:
    output = observe_scene(input_json)
    assert_contract(output, "perception.v1")
    return output


def health() -> dict:
    return {
        "status": "ok",
        "module": "perception",
        "version": "1.0.0",
        "backend": "mock",
    }
```

Generate `testdata/daily/stacking_scene.json` by serializing `perception.run({"scene_id": "stacking_cubes", "backend": "mock"})` with UTF-8, `ensure_ascii=False`, indentation 2 and a trailing newline. The generated file must be byte-for-byte stable when regenerated.

- [ ] **Step 8: Verify service, adapter, fixture, and full suite**

Run:

```bash
python -m unittest tests.unit.test_perception_service -v
python -m unittest tests.contract.test_perception_adapter -v
python -c "import json; from pathlib import Path; json.loads(Path('testdata/daily/stacking_scene.json').read_text(encoding='utf-8')); print('stacking_scene.json: OK')"
python -m unittest discover -s tests -t . -v
```

Expected: all tests pass and the JSON sample parses.

- [ ] **Step 9: Commit locally**

```bash
git add modules/__init__.py modules/perception integration/adapters/__init__.py integration/adapters/perception.py tests testdata/daily/stacking_scene.json
git commit -m "feat: add perception v1 mock adapter"
```

---

### Task 3: Mock 执行后端状态机

**Files:**
- Create: `modules/executor/__init__.py`
- Create: `modules/executor/models.py`
- Create: `modules/executor/mock_backend.py`
- Create: `tests/unit/test_mock_executor_backend.py`

**Interfaces:**
- Consumes: a validated `perception.v1` dictionary and action calls
- Produces: `ExecutionLimits`, `ExecutorBackend` Protocol, `MockBackend.from_perception(perception, failures=None)`, `execute(action, arguments) -> dict`, `snapshot() -> dict`, `trajectory_points() -> list[dict]`, `safe_stop(reason) -> dict`

- [ ] **Step 1: Write failing state-machine tests**

```python
# tests/unit/test_mock_executor_backend.py
import unittest

from integration.adapters import perception
from modules.executor.mock_backend import MockBackend


class MockExecutorBackendTests(unittest.TestCase):
    def setUp(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        self.backend = MockBackend.from_perception(scene)

    def test_complete_pick_and_place_updates_object_position(self):
        detected = self.backend.execute("detect_object", {"object_id": "green_cube"})
        self.assertEqual(detected["object_id"], "green_cube")
        self.assertEqual(
            self.backend.execute("move_to_object", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            self.backend.execute("grasp", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            self.backend.execute(
                "move_to_target", {"destination_id": "zone_unstack_target"}
            )["status"],
            "SUCCESS",
        )
        self.assertEqual(self.backend.execute("release", {})["status"], "SUCCESS")
        state = self.backend.snapshot()
        self.assertEqual(
            state["objects"]["green_cube"]["pose"],
            state["objects"]["zone_unstack_target"]["pose"],
        )

    def test_grasp_without_approach_fails(self):
        result = self.backend.execute("grasp", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "OBJECT_NOT_APPROACHED")

    def test_target_must_be_declared_safe_destination(self):
        result = self.backend.execute("move_to_target", {"destination_id": "red_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "INVALID_DESTINATION:red_cube")

    def test_failure_injection_is_counted(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        backend = MockBackend.from_perception(scene, failures={"grasp": 1})
        backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(backend.execute("grasp", {"object_id": "green_cube"})["status"], "FAILED")
        self.assertEqual(backend.execute("grasp", {"object_id": "green_cube"})["status"], "SUCCESS")

    def test_safe_stop_prevents_further_actions(self):
        self.backend.safe_stop("test")
        result = self.backend.execute("detect_object", {"object_id": "green_cube"})
        self.assertEqual(result["reason"], "BACKEND_SAFE_STOPPED")
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python -m unittest tests.unit.test_mock_executor_backend -v
```

Expected: import fails because `modules.executor.mock_backend` does not exist.

- [ ] **Step 3: Implement limits and backend protocol**

```python
# modules/executor/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExecutionLimits:
    max_main_steps: int = 50
    max_recovery_steps: int = 10
    max_recovery_attempts: int = 3
    max_action_calls: int = 100


class ExecutorBackend(Protocol):
    mode: str

    def execute(self, action: str, arguments: dict) -> dict:
        raise NotImplementedError

    def safe_stop(self, reason: str) -> dict:
        raise NotImplementedError

    def trajectory_points(self) -> list[dict]:
        raise NotImplementedError

    def snapshot(self) -> dict:
        raise NotImplementedError
```

- [ ] **Step 4: Implement the deterministic Mock state machine**

`MockBackend.from_perception()` must:

1. deep-copy objects into a dictionary keyed by object ID;
2. reject duplicate object IDs;
3. initialize `approached_id`, `held_id`, `target_id` to `None`;
4. initialize end-effector position to `{x: 0.0, y: 0.0, z: 0.35}`;
5. copy failure counters without mutating caller data.

`execute()` must use an explicit dispatch dictionary and return only dictionaries with `status`, `reason`, `duration_ms`, plus action-specific fields. Deterministic durations are:

```python
DURATIONS_MS = {
    "detect_object": 10,
    "move_to_object": 100,
    "grasp": 120,
    "move_to_target": 150,
    "release": 80,
}
```

`detect_object` resolves canonical `object_id` first and accepts `object_name` only as a compatibility alias; an ambiguous name fails. `move_to_target` resolves canonical `destination_id` first and accepts `target` only as a compatibility alias. Movement actions append trajectory points with increasing deterministic timestamps. `release` copies the destination pose into the held physical object before clearing `held_id`.

- [ ] **Step 5: Run focused and full tests**

Run:

```bash
python -m unittest tests.unit.test_mock_executor_backend -v
python -m unittest discover -s tests -t . -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit locally**

```bash
git add modules/executor tests/unit/test_mock_executor_backend.py
git commit -m "feat: add deterministic executor backend"
```

---

### Task 4: 白名单、引用解析和主流程解释器

**Files:**
- Create: `modules/executor/action_catalog.py`
- Create: `modules/executor/strategy_interpreter.py`
- Create: `tests/unit/test_strategy_interpreter.py`
- Create: `testdata/daily/stacking_strategy.json`

**Interfaces:**
- Consumes: `strategy.v1`, an `ExecutorBackend`, optional `ExecutionLimits`
- Produces: `validate_action_arguments(action: str, arguments: dict) -> list[str]`, `resolve_arguments(arguments: dict, results: dict) -> dict`, `StrategyInterpreter.run(strategy: dict) -> dict`

- [ ] **Step 1: Write failing action and reference tests**

```python
# tests/unit/test_strategy_interpreter.py
import json
import unittest
from pathlib import Path

from integration.adapters import perception
from modules.executor.mock_backend import MockBackend
from modules.executor.strategy_interpreter import StrategyInterpreter


ROOT = Path(__file__).resolve().parents[2]


def load_strategy():
    return json.loads(
        (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
            encoding="utf-8"
        )
    )


def make_interpreter(failures=None):
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    return StrategyInterpreter(MockBackend.from_perception(scene, failures=failures))


class StrategyInterpreterTests(unittest.TestCase):
    def test_successful_strategy_resolves_object_reference(self):
        output = make_interpreter().run(load_strategy())
        self.assertEqual(output["status"], "SUCCEEDED")
        self.assertEqual(output["task_id"], "stacking-demo-001")
        grasp = next(item for item in output["steps"] if item["step_id"] == "grasp_green")
        self.assertEqual(grasp["arguments"]["object_id"], "green_cube")

    def test_unknown_action_is_rejected_before_backend_execution(self):
        strategy = load_strategy()
        strategy["steps"][0]["action"] = "run_shell"
        with self.assertRaisesRegex(ValueError, "UNKNOWN_ACTION:run_shell"):
            make_interpreter().run(strategy)

    def test_non_empty_code_is_rejected(self):
        strategy = load_strategy()
        strategy["code"] = "import os"
        with self.assertRaisesRegex(ValueError, "strategy.code must be empty"):
            make_interpreter().run(strategy)

    def test_unresolved_reference_fails_and_skips_remaining_steps(self):
        strategy = load_strategy()
        strategy["steps"][1]["arguments"]["object_id"] = "$missing.object_id"
        output = make_interpreter().run(strategy)
        self.assertEqual(output["status"], "FAILED")
        self.assertEqual(output["steps"][1]["status"], "FAILED")
        self.assertTrue(output["steps"][1]["reason"].startswith("UNRESOLVED_REFERENCE"))
        self.assertTrue(all(item["status"] == "SKIPPED" for item in output["steps"][2:]))

    def test_duplicate_step_id_is_rejected(self):
        strategy = load_strategy()
        strategy["steps"][1]["step_id"] = strategy["steps"][0]["step_id"]
        with self.assertRaisesRegex(ValueError, "DUPLICATE_STEP_ID"):
            make_interpreter().run(strategy)
```

- [ ] **Step 2: Add the exact strategy fixture and verify RED**

Create `testdata/daily/stacking_strategy.json` with the five-step strategy from the approved design, including `code: null` and the one-attempt `grasp` recovery block.

Run:

```bash
python -m unittest tests.unit.test_strategy_interpreter -v
```

Expected: import fails because `strategy_interpreter.py` does not exist.

- [ ] **Step 3: Implement the action catalog**

```python
# modules/executor/action_catalog.py
ALLOWED_ACTIONS = {
    "detect_object",
    "move_to_object",
    "grasp",
    "move_to_target",
    "release",
}


def validate_action_arguments(action: str, arguments: dict) -> list[str]:
    if action not in ALLOWED_ACTIONS:
        return [f"UNKNOWN_ACTION:{action}"]
    if not isinstance(arguments, dict):
        return [f"INVALID_ARGUMENT:{action}:arguments must be an object"]
    keys = set(arguments)
    if action == "detect_object":
        allowed = {"object_id", "object_name"}
        if len(keys & allowed) != 1 or keys - allowed:
            return ["INVALID_ARGUMENT:detect_object:use exactly one object_id or object_name"]
    elif action in {"move_to_object", "grasp"}:
        if keys != {"object_id"}:
            return [f"INVALID_ARGUMENT:{action}:object_id is required"]
    elif action == "move_to_target":
        allowed = {"destination_id", "target"}
        if len(keys & allowed) != 1 or keys - allowed:
            return ["INVALID_ARGUMENT:move_to_target:use exactly one destination_id or target"]
    elif action == "release" and keys:
        return ["INVALID_ARGUMENT:release:arguments must be empty"]
    return []
```

- [ ] **Step 4: Implement reference resolution and fail-fast main flow**

`resolve_arguments()` must deep-copy literal values and resolve strings matching `$step_id.path`. `StrategyInterpreter.run()` must:

1. call `assert_contract(strategy, "strategy.v1")`;
2. reject non-empty `code`;
3. validate unique IDs, main-step count, action names, action arguments and recovery structure before the first backend call;
4. execute steps in order;
5. store backend results by `step_id` for later references;
6. on a failed step without recovery, append `SKIPPED` records for all remaining main steps and stop;
7. assemble `schema_version`, `task_id`, top-level status, steps, trajectory points, total duration and safety events.

The initial implementation may route a recovery-bearing failure through the same fail-fast path; Task 5 replaces that branch with actual recovery semantics.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
python -m unittest tests.unit.test_strategy_interpreter -v
python -c "import json; from pathlib import Path; json.loads(Path('testdata/daily/stacking_strategy.json').read_text(encoding='utf-8')); print('stacking_strategy.json: OK')"
python -m unittest discover -s tests -t . -v
```

Expected: all tests pass except the recovery-specific behavior that is introduced only in Task 5; the normal fixture succeeds because no failure is injected.

- [ ] **Step 6: Commit locally**

```bash
git add modules/executor/action_catalog.py modules/executor/strategy_interpreter.py tests/unit/test_strategy_interpreter.py testdata/daily/stacking_strategy.json
git commit -m "feat: interpret trusted strategy actions"
```

---

### Task 5: `on_failure`、调用上限和安全停止

**Files:**
- Modify: `modules/executor/strategy_interpreter.py`
- Modify: `modules/executor/models.py`
- Create: `tests/unit/test_strategy_recovery.py`

**Interfaces:**
- Consumes: TraceCoder-compatible `on_failure = {max_attempts, steps, on_exhausted}`
- Produces: recovery phases, `SAFE_STOP`, `safety_events`, bounded action count

- [ ] **Step 1: Write failing recovery tests**

```python
# tests/unit/test_strategy_recovery.py
import json
import unittest
from pathlib import Path

from integration.adapters import perception
from modules.executor.mock_backend import MockBackend
from modules.executor.models import ExecutionLimits
from modules.executor.strategy_interpreter import StrategyInterpreter

ROOT = Path(__file__).resolve().parents[2]


def strategy():
    return json.loads(
        (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
            encoding="utf-8"
        )
    )


def interpreter(failures=None, limits=None):
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    backend = MockBackend.from_perception(scene, failures=failures)
    return StrategyInterpreter(backend, limits=limits)


class StrategyRecoveryTests(unittest.TestCase):
    def test_one_grasp_failure_is_recovered(self):
        output = interpreter(failures={"grasp": 1}).run(strategy())
        self.assertEqual(output["status"], "SUCCEEDED")
        recovery = [item for item in output["steps"] if item["phase"] == "recovery_1"]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0]["action"], "grasp")
        self.assertEqual(recovery[0]["status"], "SUCCESS")

    def test_persistent_grasp_failure_safe_stops(self):
        output = interpreter(failures={"grasp": 10}).run(strategy())
        self.assertEqual(output["status"], "SAFE_STOP")
        self.assertEqual(output["steps"][-1]["phase"], "safe_stop")
        self.assertEqual(output["steps"][-1]["action"], "stop")
        self.assertEqual(output["safety_events"][0]["type"], "RECOVERY_EXHAUSTED")

    def test_invalid_recovery_limit_is_rejected_before_execution(self):
        value = strategy()
        value["steps"][2]["on_failure"]["max_attempts"] = 4
        with self.assertRaisesRegex(ValueError, "max_attempts must be between 1 and 3"):
            interpreter().run(value)

    def test_action_call_limit_causes_safe_stop(self):
        limits = ExecutionLimits(max_action_calls=2)
        output = interpreter(limits=limits).run(strategy())
        self.assertEqual(output["status"], "SAFE_STOP")
        self.assertEqual(output["safety_events"][0]["type"], "ACTION_LIMIT_EXCEEDED")

    def test_only_stop_is_allowed_on_exhausted(self):
        value = strategy()
        value["steps"][2]["on_failure"]["on_exhausted"] = "continue"
        with self.assertRaisesRegex(ValueError, "on_exhausted must be stop"):
            interpreter().run(value)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python -m unittest tests.unit.test_strategy_recovery -v
```

Expected: recovery tests fail because failed actions currently stop without executing recovery.

- [ ] **Step 3: Implement recovery execution**

Add `_execute_recovery(step, results, records, counter)` with these exact semantics:

```python
for attempt in range(1, recovery["max_attempts"] + 1):
    attempt_ok = True
    for recovery_step in recovery["steps"]:
        outcome = self._execute_step(
            recovery_step,
            results,
            phase=f"recovery_{attempt}",
        )
        records.append(outcome.record)
        counter[0] += 1
        if outcome.result.get("status") != "SUCCESS":
            attempt_ok = False
            break
    if attempt_ok:
        return True
return False
```

If recovery returns `False`, call `backend.safe_stop("RECOVERY_EXHAUSTED")`, append a `safe_stop` step record, append one `RECOVERY_EXHAUSTED` safety event and mark remaining main steps `SKIPPED`.

Before every backend call, compare the counter with `limits.max_action_calls`; if exhausted, safe-stop with `ACTION_LIMIT_EXCEEDED` without dispatching the next action.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
python -m unittest tests.unit.test_strategy_recovery -v
python -m unittest tests.unit.test_strategy_interpreter -v
python -m unittest discover -s tests -t . -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit locally**

```bash
git add modules/executor/models.py modules/executor/strategy_interpreter.py tests/unit/test_strategy_recovery.py
git commit -m "feat: add bounded strategy recovery"
```

---

### Task 6: Executor 适配器、契约输出和公共 pipeline 联调

**Files:**
- Create: `integration/adapters/executor.py`
- Create: `tests/contract/test_execution_adapter.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_mock_isaac_pipeline.py`

**Interfaces:**
- Consumes: `ExecutorAdapter(backend).run(strategy_v1)`; existing `run_pipeline(perception, instruction, adapters)`
- Produces: contract-valid `execution.v1`, `ExecutorAdapter.health() -> dict`

- [ ] **Step 1: Write failing executor contract tests**

```python
# tests/contract/test_execution_adapter.py
import json
import unittest
from pathlib import Path

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from integration.contract_validation import validate_contract
from modules.executor.mock_backend import MockBackend

ROOT = Path(__file__).resolve().parents[2]


class ExecutionAdapterContractTests(unittest.TestCase):
    def setUp(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        self.adapter = ExecutorAdapter(MockBackend.from_perception(scene))
        self.strategy = json.loads(
            (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
                encoding="utf-8"
            )
        )

    def test_execution_validates_against_execution_v1(self):
        output = self.adapter.run(self.strategy)
        self.assertEqual(validate_contract(output, "execution.v1"), [])
        self.assertEqual(output["task_id"], self.strategy["task_id"])

    def test_health_reports_bound_mock_backend(self):
        health = self.adapter.health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["backend"], "mock")
        self.assertEqual(health["supported_actions"], [
            "detect_object",
            "grasp",
            "move_to_object",
            "move_to_target",
            "release",
        ])
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
python -m unittest tests.contract.test_execution_adapter -v
```

Expected: import fails because `integration.adapters.executor` does not exist.

- [ ] **Step 3: Implement the bound-backend adapter**

```python
# integration/adapters/executor.py
from integration.contract_validation import assert_contract
from modules.executor.action_catalog import ALLOWED_ACTIONS
from modules.executor.strategy_interpreter import StrategyInterpreter


class ExecutorAdapter:
    def __init__(self, backend):
        self._backend = backend
        self._interpreter = StrategyInterpreter(backend)

    def run(self, input_json: dict) -> dict:
        assert_contract(input_json, "strategy.v1")
        output = self._interpreter.run(input_json)
        assert_contract(output, "execution.v1")
        return output

    def health(self) -> dict:
        return {
            "status": "ok",
            "module": "executor",
            "version": "1.0.0",
            "backend": self._backend.mode,
            "supported_actions": sorted(ALLOWED_ACTIONS),
        }
```

- [ ] **Step 4: Verify adapter contract GREEN**

Run:

```bash
python -m unittest tests.contract.test_execution_adapter -v
```

Expected: both tests pass.

- [ ] **Step 5: Write failing public pipeline integration test**

```python
# tests/integration/test_mock_isaac_pipeline.py
import json
import unittest
from pathlib import Path

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from integration.pipeline import run_pipeline
from modules.executor.mock_backend import MockBackend

ROOT = Path(__file__).resolve().parents[2]


class StaticAdapter:
    def __init__(self, value):
        self.value = value

    def run(self, input_json):
        return json.loads(json.dumps(self.value))

    def health(self):
        return {"status": "ok"}


class MockIsaacPipelineTests(unittest.TestCase):
    def test_pipeline_reaches_executor_without_changing_public_signature(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        strategy = json.loads(
            (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
                encoding="utf-8"
            )
        )
        task = {
            "schema_version": "task.v1",
            "task_id": "stacking-demo-001",
            "action": "pick_and_place",
            "target_ids": ["green_cube"],
            "destination_id": "zone_unstack_target",
            "constraints": [],
            "status": "READY",
            "blocking_reasons": [],
        }
        adapters = {
            "intent": StaticAdapter(task),
            "strategy": StaticAdapter(strategy),
            "executor": ExecutorAdapter(MockBackend.from_perception(scene)),
        }
        output = run_pipeline(scene, "把绿色方块移到安全区", adapters)
        self.assertEqual(output["status"], "SUCCEEDED")
        self.assertEqual(output["execution"]["task_id"], "stacking-demo-001")
        self.assertIsNone(output["feedback"])
```

- [ ] **Step 6: Run the integration test**

Run:

```bash
python -m unittest tests.integration.test_mock_isaac_pipeline -v
```

Expected: test passes without modifying `integration/pipeline.py`.

- [ ] **Step 7: Run all tests and commit locally**

Run:

```bash
python -m unittest discover -s tests -t . -v
```

Expected: all tests pass.

Commit:

```bash
git add integration/adapters/executor.py tests/contract/test_execution_adapter.py tests/integration
git commit -m "feat: connect executor to mock pipeline"
```

---

### Task 7: 对队友的接口文档、统一命令和 CI

**Files:**
- Create: `modules/perception/README.md`
- Create: `modules/executor/README.md`
- Create: `docs/Isaac执行器接口说明.md`
- Modify: `README.md`
- Modify: `Makefile`
- Modify: `.github/workflows/integration-contract.yml`
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/test_mock_stacking_e2e.py`

**Interfaces:**
- Consumes: all implemented public APIs and checked-in JSON examples
- Produces: team-facing interface guide and dependency-free CI entrypoints

- [ ] **Step 1: Write the three interface documents**

`modules/perception/README.md` must contain:

- responsibility and non-responsibility;
- request `{"scene_id": "stacking_cubes", "backend": "mock"}`;
- complete perception output field table;
- stable ID and virtual target-zone rules;
- `run()` and `health()` examples;
- current Mock limitation.

`modules/executor/README.md` must contain:

- `ExecutorAdapter(MockBackend.from_perception(perception))` construction example;
- exact action table and canonical arguments;
- `$step_id.field` resolution rules;
- `on_failure` example and safety-stop semantics;
- overall versus step status table;
- statement `未经确认不得执行 strategy.code`;
- Mock/Offline-Isaac separation.

`docs/Isaac执行器接口说明.md` must be written for A/B/D and include:

1. one diagram of `perception.v1 → task.v1 → strategy.v1 → execution.v1 → feedback.v1`;
2. both checked-in JSON examples;
3. all five actions with accepted aliases and success outputs;
4. failure and safety-event table;
5. how A uses IDs, how B generates steps, and how D consumes evidence;
6. exact local verification command `python -m unittest discover -s tests -t . -v`;
7. clear statement that phase one does not prove real Isaac motion.

- [ ] **Step 2: Update root README and Makefile**

Add links from the root README’s C module section to the three new README/interface documents.

Replace Makefile test recipes with dependency-free commands:

```make
.PHONY: contract-test integration-test e2e test

contract-test:
	python -m unittest discover -s tests/contract -t . -v

integration-test:
	python -m unittest discover -s tests/integration -t . -v

e2e:
	python -m unittest discover -s tests/e2e -t . -v

test:
	python -m unittest discover -s tests -t . -v
```

- [ ] **Step 3: Extend GitHub Actions without adding packages**

After the existing JSON syntax step, add:

```yaml
      - name: Run contract and integration tests
        run: python -m unittest discover -s tests -t . -v
```

- [ ] **Step 4: Verify document links, real entrypoints, and JSON examples**

Human-facing prose is reviewed directly rather than tested by grepping exact wording. Verify every local Markdown link introduced by this task resolves to an existing repository path, then run the actual entrypoints documented for teammates.

Because `unittest discover` exits with status 5 when no tests are found, add one real acceptance test in `tests/e2e/test_mock_stacking_e2e.py`. It must run the checked-in scene and strategy through the public pipeline, assert `execution.v1` succeeds, and assert the final `green_cube` pose equals `zone_unstack_target` rather than accepting action return values alone.

Run:

```bash
python -m unittest discover -s tests/contract -t . -v
python -m unittest discover -s tests/integration -t . -v
python -m unittest discover -s tests/e2e -t . -v
python -m unittest discover -s tests -t . -v
python -c "import json; from pathlib import Path; files=list(Path('contracts').rglob('*.json'))+list(Path('testdata').rglob('*.json')); [json.loads(p.read_text(encoding='utf-8')) for p in files]; print(f'JSON syntax: OK ({len(files)} files)')"
```

Expected: all document links resolve; the e2e suite runs one real stacking acceptance test; all tests pass; all contract and testdata JSON parses.

- [ ] **Step 5: Commit locally**

```bash
git add modules/perception/README.md modules/executor/README.md docs/Isaac执行器接口说明.md README.md Makefile .github/workflows/integration-contract.yml tests/e2e docs/superpowers/plans/2026-08-16-isaac-v1-mock-integration.md
git commit -m "docs: explain isaac v1 integration interface"
```

---

### Task 8: 第一阶段最终验收与上传前检查点

**Files:**
- Modify only if verification reveals a tested defect; any fix must first add a failing regression test.

**Interfaces:**
- Consumes: complete phase-one branch
- Produces: evidence bundle for 吴昌庆 review; no remote mutation

- [ ] **Step 1: Run the full dependency-free test suite from a clean shell**

```bash
python -m unittest discover -s tests -t . -v
```

Expected: all discovered tests pass, 0 failures, 0 errors.

- [ ] **Step 2: Run JSON and bytecode validation**

```bash
python -c "import json; from pathlib import Path; files=list(Path('contracts').rglob('*.json'))+list(Path('testdata').rglob('*.json')); [json.loads(p.read_text(encoding='utf-8')) for p in files]; print(f'JSON syntax: OK ({len(files)} files)')"
python -m compileall -q integration modules tests
```

Expected: JSON syntax OK; compileall exits 0.

- [ ] **Step 3: Run security and repository hygiene checks**

```bash
git diff --check origin/main...HEAD
git status --short
git grep -n -I -E "10\\.16\\.|password|API_KEY=|D:\\\\CodingData|C:\\\\Users" -- . ':!docs/superpowers/**'
```

Expected:

- `git diff --check` prints nothing;
- worktree is clean after local commits;
- secret/path scan finds no real server address, credential assignment or personal absolute path in deliverable files.

- [ ] **Step 4: Produce a review summary without pushing**

Report to 吴昌庆:

- local branch and commit list;
- exact files changed;
- test count and outputs;
- one successful `execution.v1` sample;
- one `SAFE_STOP` sample;
- known limitation: real Isaac probe still blocked by cache-directory permissions;
- exact diff against `origin/main`.

- [ ] **Step 5: Stop at the upload gate**

Do not run `git push`. Ask 吴昌庆 to review the code, documents and evidence. Only after an explicit “同意上传” may a later step push `feature/executor-isaac-v1` and prepare a Pull Request.

---

## Deferred Follow-on Plan

After Task 8 is accepted, create a separate plan for the second stage. That plan begins with a read-only server UID/GID and mount-permission probe, then fixes only project-owned cache directories, regenerates a fresh run ID, reruns the compatibility probe, and finally implements the offline Isaac backend. None of those server actions are authorized by this first-stage plan.
