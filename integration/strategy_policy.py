"""Shared strategy and patch safety policy for B/C/D/Pipeline.

The policy is deliberately deterministic and provider-independent.  Model
outputs and TraceCoder patches are untrusted inputs; this module is the single
semantic gate that runs after the JSON schema check and before execution.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from integration.contract_validation import validate_contract


ALLOWED_ACTIONS = frozenset(
    {
        "detect_object",
        "move_to_object",
        "grasp",
        "move_to_target",
        "release",
    }
)

DEFAULT_CAPABILITIES = {
    "allowed_actions": sorted(ALLOWED_ACTIONS),
    "max_recovery_attempts": 3,
    "max_retries": 2,
}


def normalize_capabilities(capabilities: dict | None = None) -> dict:
    value = dict(DEFAULT_CAPABILITIES)
    if isinstance(capabilities, dict):
        value.update(capabilities)
    actions = value.get("allowed_actions")
    if not isinstance(actions, (list, tuple, set, frozenset)):
        actions = DEFAULT_CAPABILITIES["allowed_actions"]
    value["allowed_actions"] = sorted({str(item) for item in actions})
    for key, default in (("max_recovery_attempts", 3), ("max_retries", 2)):
        raw = value.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            value[key] = default
        else:
            value[key] = raw
    return value


def validate_action_arguments(action: str, arguments: Any) -> list[str]:
    if action not in ALLOWED_ACTIONS:
        return [f"UNKNOWN_ACTION:{action}"]
    if not isinstance(arguments, dict):
        return [f"INVALID_ARGUMENT:{action}:arguments must be an object"]
    keys = set(arguments)
    if action == "detect_object":
        if keys != {"object_id"} or not _non_empty_string(arguments.get("object_id")):
            return ["INVALID_ARGUMENT:detect_object:object_id is required"]
    elif action in {"move_to_object", "grasp"}:
        if keys != {"object_id"} or not _non_empty_string(arguments.get("object_id")):
            return [f"INVALID_ARGUMENT:{action}:object_id is required"]
    elif action == "move_to_target":
        allowed = {"destination_id", "placement_mode"}
        if keys - allowed or not _non_empty_string(arguments.get("destination_id")):
            return ["INVALID_ARGUMENT:move_to_target:destination_id is required"]
        placement_mode = arguments.get("placement_mode", "direct")
        if placement_mode not in {"direct", "stack_on"}:
            return [
                "INVALID_ARGUMENT:move_to_target:placement_mode must be direct or stack_on"
            ]
    elif action == "release" and keys:
        return ["INVALID_ARGUMENT:release:arguments must be empty"]
    return []


def validate_strategy(
    strategy: Any,
    *,
    task: dict | None = None,
    capabilities: dict | None = None,
) -> dict:
    """Validate a complete strategy.v1 document and return structured errors."""

    errors = validate_contract(strategy, "strategy.v1") if isinstance(strategy, dict) else [
        "$: expected strategy.v1 object"
    ]
    if not isinstance(strategy, dict):
        return _result(errors)

    caps = normalize_capabilities(capabilities)
    if strategy.get("code") is not None:
        errors.append("CODE_NOT_ALLOWED:strategy.code must be null")
    if task and strategy.get("task_id") != task.get("task_id"):
        errors.append("TASK_ID_MISMATCH:strategy.task_id does not match task.task_id")

    allowed = set(caps["allowed_actions"])
    seen: set[str] = set()
    for path, step in _walk_steps(strategy.get("steps")):
        if not isinstance(step, dict):
            errors.append(f"{path}: step must be an object")
            continue
        step_id = step.get("step_id")
        prior_seen = set(seen)
        if not _non_empty_string(step_id):
            errors.append(f"{path}.step_id: required stable step ID")
        elif step_id in seen:
            errors.append(f"DUPLICATE_STEP_ID:{step_id}")
        else:
            seen.add(step_id)
        action = step.get("action")
        if action not in allowed:
            errors.append(f"ACTION_NOT_CAPABLE:{action}")
        errors.extend(f"{path}.{item}" for item in validate_action_arguments(action, step.get("arguments")))
        errors.extend(_validate_references(step, prior_seen, path))
        recovery = step.get("on_failure")
        if recovery is not None:
            errors.extend(_validate_recovery(recovery, caps, path))

    if not isinstance(strategy.get("steps"), list) or not strategy.get("steps"):
        errors.append("STEPS_EMPTY:strategy.steps must be a non-empty array")
    return _result(errors, caps)


def validate_patch(
    patch: Any,
    *,
    current_strategy: dict | None = None,
    task: dict | None = None,
    capabilities: dict | None = None,
) -> dict:
    """Validate a D-produced complete strategy patch and require a change."""

    result = validate_strategy(patch, task=task, capabilities=capabilities)
    if not result["passed"]:
        return result
    if current_strategy is not None and execution_shape(patch) == execution_shape(current_strategy):
        result["passed"] = False
        result["errors"].append("PATCH_UNCHANGED:patch does not change execution shape")
    return result


def execution_shape(strategy: dict | None) -> dict:
    if not isinstance(strategy, dict):
        return {}
    return {
        "schema_version": strategy.get("schema_version"),
        "task_id": strategy.get("task_id"),
        "steps": deepcopy(strategy.get("steps") or []),
        "code": strategy.get("code"),
    }


def _walk_steps(steps: Any):
    if not isinstance(steps, list):
        return
    for index, step in enumerate(steps):
        path = f"$.steps[{index}]"
        yield path, step
        if isinstance(step, dict):
            recovery = step.get("on_failure")
            if isinstance(recovery, dict):
                for nested_index, nested in enumerate(recovery.get("steps") or []):
                    yield f"{path}.on_failure.steps[{nested_index}]", nested


def _validate_recovery(recovery: Any, capabilities: dict, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(recovery, dict):
        return [f"{path}.on_failure: must be an object"]
    attempts = recovery.get("max_attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int):
        errors.append(f"{path}.on_failure.max_attempts: must be an integer")
    elif not 1 <= attempts <= capabilities["max_recovery_attempts"]:
        errors.append(
            f"RECOVERY_LIMIT_EXCEEDED:{path}.on_failure.max_attempts>{capabilities['max_recovery_attempts']}"
        )
    if not isinstance(recovery.get("steps"), list) or not recovery.get("steps"):
        errors.append(f"{path}.on_failure.steps: must be non-empty")
    if recovery.get("on_exhausted") != "stop":
        errors.append(f"{path}.on_failure.on_exhausted: must be stop")
    return errors


def _validate_references(step: dict, seen: set[str], path: str) -> list[str]:
    errors: list[str] = []
    arguments = step.get("arguments") or {}
    for key, value in arguments.items():
        if not isinstance(value, str) or not value.startswith("$"):
            continue
        reference = value[1:]
        source_step, separator, field = reference.partition(".")
        if not separator or not source_step or not field or source_step not in seen:
            errors.append(f"UNRESOLVED_REFERENCE:{path}.arguments.{key}:{value}")
        elif field != "object_id":
            errors.append(f"INVALID_REFERENCE_FIELD:{path}.arguments.{key}:{value}")
    return errors


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _result(errors: list[str], capabilities: dict | None = None) -> dict:
    return {
        "passed": not errors,
        "errors": list(dict.fromkeys(errors)),
        "capabilities": normalize_capabilities(capabilities),
    }
