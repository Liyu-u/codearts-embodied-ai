"""Shared configuration and strategy selection for real Isaac experiments.

This module deliberately contains no Isaac Sim imports.  The local tests can
therefore verify that the experiment variants really differ before a remote
run starts.
"""

from __future__ import annotations

import json
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
    },
    "V2_FULL_NO_D": {
        "name": "完整系统去掉D",
        "modules": ["A", "B", "C"],
        "safety_gate_enabled": True,
        "repair_enabled": False,
        "simulation_only": False,
    },
    "V3_FULL_NO_GATE": {
        "name": "完整系统去掉安全门禁",
        "modules": ["A", "B", "C", "D"],
        "safety_gate_enabled": False,
        "repair_enabled": True,
        "simulation_only": True,
    },
    "V4_FULL": {
        "name": "完整系统",
        "modules": ["A", "B", "C", "D"],
        "safety_gate_enabled": True,
        "repair_enabled": True,
        "simulation_only": False,
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
