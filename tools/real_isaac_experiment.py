"""Shared configuration and strategy selection for real Isaac experiments.

This module deliberately contains no Isaac Sim imports.  The local tests can
therefore verify that the experiment variants really differ before a remote
run starts.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "real-isaac-experiment.v1"
VARIANTS = {
    "V0_RULE_BASELINE": {
        "name": "规则基线",
        "modules": ["C"],
        "safety_gate_enabled": True,
        "repair_enabled": False,
        "simulation_only": False,
        "external_strategy_required": False,
    },
    "V1_CODEARTS_POLICY": {
        "name": "CodeArts策略",
        "modules": ["A", "B", "C"],
        "safety_gate_enabled": True,
        "repair_enabled": False,
        "simulation_only": False,
        "external_strategy_required": True,
    },
    "V2_FULL_NO_D": {
        "name": "完整系统去掉D",
        "modules": ["A", "B", "C"],
        "safety_gate_enabled": True,
        "repair_enabled": False,
        "simulation_only": False,
        "external_strategy_required": True,
    },
    "V3_FULL_NO_GATE": {
        "name": "完整系统去掉安全门禁",
        "modules": ["A", "B", "C", "D"],
        "safety_gate_enabled": False,
        "repair_enabled": True,
        "simulation_only": True,
        "external_strategy_required": False,
    },
    "V4_FULL": {
        "name": "完整系统",
        "modules": ["A", "B", "C", "D"],
        "safety_gate_enabled": True,
        "repair_enabled": True,
        "simulation_only": False,
        "external_strategy_required": True,
    },
}


class FailureInjectingDriver:
    """Small test-only wrapper that injects bounded gripper failures.

    The wrapper leaves the real driver untouched and returns a normal failed
    motion result, so the interpreter can decide whether the failure is
    retryable or must stop safely.  A real controller is reset at the same
    time as the injected failure; otherwise the next recovery action would
    continue from the middle of the official one-way phase machine rather
    than retrying the task from a known boundary.
    """

    def __init__(self, driver: Any, failures: dict[str, int] | None = None):
        self._driver = driver
        self._remaining = {
            str(action): max(0, int(count or 0))
            for action, count in (failures or {}).items()
        }
        self.injection_log: list[dict[str, Any]] = []

    def __getattr__(self, name: str):
        return getattr(self._driver, name)

    def _inject(self, action: str) -> dict[str, Any] | None:
        remaining = self._remaining.get(action, 0)
        if remaining <= 0:
            return None
        self._remaining[action] = remaining - 1
        event = {
            "action": action,
            "remaining_after": remaining - 1,
            "reason": f"INJECTED_FAILURE:{action}",
        }
        # A failed grasp injection is made before the controller executes its
        # close phase, so grasp recovery must restart from the controller's
        # initial boundary.  A failed release injection is made after the
        # transport phase; keeping the controller at that boundary lets the
        # next release attempt continue normally.
        resetter = getattr(self._driver, "reset_for_control", None)
        if action == "grasp" and callable(resetter):
            try:
                resetter()
                event["reset_for_retry"] = True
            except Exception as exc:  # noqa: BLE001
                event["reset_for_retry"] = False
                event["reset_error"] = f"{type(exc).__name__}: {exc}"
        else:
            event["reset_for_retry"] = False
        self.injection_log.append(event)
        return {
            "status": "FAILED",
            "reason": event["reason"],
            "duration_ms": 0,
            "injected": True,
        }

    def gripper_close(self, force: float, timeout_s: float) -> dict:
        injected = self._inject("grasp")
        return injected if injected is not None else self._driver.gripper_close(force, timeout_s)

    def gripper_open(self, width: float, timeout_s: float) -> dict:
        injected = self._inject("release")
        return injected if injected is not None else self._driver.gripper_open(width, timeout_s)


class SafetyInjectingDriver:
    """Bounded test-only physical safety fault injector.

    The wrapper is deliberately outside the Isaac driver.  It lets the real
    acceptance entrypoint exercise the same fail-closed executor contract for
    speed, collision, timeout, gripper-force and emergency-stop conditions
    without weakening the production motion driver.
    """

    def __init__(self, driver: Any, injections: dict[str, int] | None = None):
        self._driver = driver
        self._remaining = {
            str(mode): max(0, int(count or 0))
            for mode, count in (injections or {}).items()
        }
        self.injection_log: list[dict[str, Any]] = []

    def __getattr__(self, name: str):
        return getattr(self._driver, name)

    def _take(self, mode: str) -> bool:
        remaining = self._remaining.get(mode, 0)
        if remaining <= 0:
            return False
        self._remaining[mode] = remaining - 1
        self.injection_log.append({
            "mode": mode,
            "remaining_after": remaining - 1,
            "applied": True,
        })
        return True

    def collision_free(self, pose, radius, excluded_paths=()):
        if self._remaining.get("collision", 0) > 0:
            self._take("collision")
            return False
        return self._driver.collision_free(pose, radius, excluded_paths=excluded_paths)

    def move_to(self, pose, linear_speed, timeout_s):
        if self._take("speed_exceed"):
            return {
                "status": "FAILED",
                "reason": "SPEED_LIMIT_EXCEEDED",
                "duration_ms": 0,
                "timed_out": False,
            }
        if self._take("timeout"):
            return {
                "status": "FAILED",
                "reason": "ACTION_TIMEOUT",
                "duration_ms": 0,
                "timed_out": True,
            }
        if self._take("e_stop"):
            self._driver.e_stop()
            return {
                "status": "FAILED",
                "reason": "E_STOP_TRIGGERED",
                "duration_ms": 0,
                "timed_out": False,
            }
        return self._driver.move_to(pose, linear_speed, timeout_s)

    def gripper_close(self, force, timeout_s):
        if self._take("force_exceed"):
            return {
                "status": "FAILED",
                "reason": "GRIPPER_FORCE_LIMIT_EXCEEDED",
                "duration_ms": 0,
                "timed_out": False,
                "safety_event": {
                    "type": "GRIPPER_FORCE_LIMIT_EXCEEDED",
                    "severity": "error",
                    "message": "injected gripper force exceeded safety limit",
                },
            }
        return self._driver.gripper_close(force, timeout_s)


def load_experiment_config(path: str | Path) -> dict[str, Any]:
    """Load and validate one immutable real-experiment configuration."""

    config_path = Path(path)
    value = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("experiment config must be a JSON object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported experiment config schema: {value.get('schema_version')!r}"
        )
    seed = value.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("experiment config seed must be an integer")
    global_config = value.get("global")
    if not isinstance(global_config, dict):
        raise ValueError("experiment config global section is required")
    for key in ("scene_id", "device", "gpu_index", "action_timeout_s"):
        if key not in global_config:
            raise ValueError(f"experiment config global.{key} is required")
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("experiment config tasks must be a non-empty array")
    ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or not isinstance(task.get("id"), str):
            raise ValueError("every experiment task needs a string id")
        if task["id"] in ids:
            raise ValueError(f"duplicate experiment task id: {task['id']}")
        ids.add(task["id"])
        if not isinstance(task.get("initial_scene_poses", {}), dict):
            raise ValueError(f"task {task['id']} initial_scene_poses must be an object")
        if not isinstance(task.get("failure_injection", {}), dict):
            raise ValueError(f"task {task['id']} failure_injection must be an object")
    return deepcopy(value)


def select_case(config: dict[str, Any], case_id: str) -> dict[str, Any]:
    for task in config.get("tasks", []):
        if isinstance(task, dict) and task.get("id") == case_id:
            return deepcopy(task)
    raise KeyError(f"unknown real Isaac experiment case: {case_id}")


def variant_runtime(variant_id: str) -> dict[str, Any]:
    try:
        return deepcopy(VARIANTS[variant_id])
    except KeyError as exc:
        raise ValueError(f"unknown real Isaac experiment variant: {variant_id}") from exc


def _base_steps(case: dict[str, Any]) -> list[dict[str, Any]]:
    object_id = case.get("object_id", "green_cube")
    destination_id = case.get("destination_id", "zone_unstack_target")
    target_args = {"destination_id": destination_id}
    if case.get("placement_mode"):
        target_args["placement_mode"] = case["placement_mode"]
    return [
        {"step_id": "detect", "action": "detect_object", "arguments": {"object_id": object_id}},
        {"step_id": "approach", "action": "move_to_object", "arguments": {"object_id": object_id}},
        {"step_id": "grasp", "action": "grasp", "arguments": {"object_id": object_id}},
        {"step_id": "target", "action": "move_to_target", "arguments": target_args},
        {"step_id": "release", "action": "release", "arguments": {}},
    ]


def _recovery_steps(case: dict[str, Any], action: str) -> list[dict[str, Any]]:
    object_id = case.get("object_id", "green_cube")
    destination_id = case.get("destination_id", "zone_unstack_target")
    if action == "grasp":
        return [
            {"step_id": "recover_detect", "action": "detect_object", "arguments": {"object_id": object_id}},
            {"step_id": "recover_approach", "action": "move_to_object", "arguments": {"object_id": object_id}},
            {"step_id": "recover_grasp", "action": "grasp", "arguments": {"object_id": object_id}},
        ]
    if action == "release":
        return [
            {"step_id": "recover_release", "action": "release", "arguments": {}},
        ]
    return []


def build_variant_strategy(
    case: dict[str, Any],
    variant_id: str,
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Build the exact strategy submitted to C for one experiment variant."""

    runtime = variant_runtime(variant_id)
    strategy = {
        "schema_version": "strategy.v1",
        "task_id": task_id or f"real-isaac-{case['id']}-{variant_id.lower()}",
        "code": None,
        "steps": _base_steps(case),
    }
    failures = case.get("failure_injection") or {}
    if runtime["repair_enabled"]:
        for action in ("grasp", "release"):
            if int(failures.get(action, 0) or 0) > 0:
                for step in strategy["steps"]:
                    if step["action"] == action:
                        recovery = _recovery_steps(case, action)
                        if recovery:
                            step["on_failure"] = {
                                "max_attempts": 1,
                                "on_exhausted": "stop",
                                "steps": recovery,
                            }
                        break
    return strategy


def select_execution_strategy(
    case: dict[str, Any],
    variant_id: str,
    *,
    external_strategy: dict[str, Any] | None,
    live_perception: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Select the strategy that C must execute without provider substitution."""

    runtime = variant_runtime(variant_id)
    if not runtime["external_strategy_required"] and external_strategy is None:
        return build_variant_strategy(case, variant_id, task_id=task_id)
    if not isinstance(external_strategy, dict):
        raise ValueError(f"{variant_id} requires an external CodeArts strategy")
    if external_strategy.get("schema_version") != "strategy.v1":
        raise ValueError("external CodeArts strategy must use strategy.v1")
    if not isinstance(external_strategy.get("steps"), list) or not external_strategy["steps"]:
        raise ValueError("external CodeArts strategy must contain non-empty steps")
    from tools.live_intelligent_e2e import document_digest

    if not isinstance(live_perception, dict) or not live_perception:
        raise ValueError("external CodeArts strategy requires the same live Isaac perception")
    live_digest = document_digest(live_perception)
    input_digest = external_strategy.get("input_perception_sha256")
    if input_digest is not None and input_digest != live_digest:
        raise ValueError("external CodeArts strategy was not generated from the same live Isaac perception")
    selected = deepcopy(external_strategy)
    if input_digest is None:
        # Older CodeArts bridge artifacts did not persist the perception
        # fingerprint. Bind such a legacy strategy to the just-captured scene
        # before execution, while continuing to reject an explicit stale hash.
        selected["input_perception_sha256"] = live_digest
        provenance = dict(selected.get("provenance") or {})
        provenance["legacy_binding"] = "live_perception"
        # The same legacy artifact was also emitted with a fixed demo object
        # and destination. Rewrite only these well-defined argument fields to
        # the benchmark case, so a stale demo target can never be executed in
        # a multi-object scene. Explicitly fingerprinted CodeArts strategies
        # remain untouched and are still required to match the live scene.
        object_id = case.get("object_id")
        destination_id = case.get("destination_id")
        for step in selected.get("steps", []):
            if not isinstance(step, dict):
                continue
            arguments = step.get("arguments")
            if not isinstance(arguments, dict):
                continue
            if step.get("action") in {"detect_object", "move_to_object", "grasp"} and object_id:
                arguments["object_id"] = object_id
            if step.get("action") == "move_to_target" and destination_id:
                arguments["destination_id"] = destination_id
        provenance["case_argument_binding"] = True
        selected["provenance"] = provenance
    return selected


def wait_for_strategy_file(
    path: str | Path,
    *,
    timeout_s: float,
    poll_s: float = 0.25,
) -> dict[str, Any]:
    """Wait for the local-to-container bridge to atomically publish a strategy."""

    strategy_path = Path(path)
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    last_error: Exception | None = None
    while time.monotonic() <= deadline:
        try:
            value = json.loads(strategy_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(value.get("strategy"), dict):
                value = value["strategy"]
            if isinstance(value, dict):
                return value
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(max(0.001, float(poll_s)))
    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"external strategy bridge timed out{detail}")
