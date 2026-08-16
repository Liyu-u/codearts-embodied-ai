"""Policy execution and structured trace collection."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .simulator import RobotSimulator


def _lookup_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def resolve_arguments(arguments: dict, results: dict) -> dict:
    resolved = {}
    for name, value in arguments.items():
        if isinstance(value, str) and value.startswith("$") and "." in value:
            step_id, result_path = value[1:].split(".", 1)
            resolved[name] = _lookup_path(results[step_id], result_path)
        else:
            resolved[name] = deepcopy(value)
    return resolved


def _run_action(
    simulator: RobotSimulator,
    step: dict,
    results: dict,
    trace: list[dict],
    api_counter: list[int],
    phase: str = "main",
) -> dict:
    before = deepcopy(simulator.state)
    try:
        arguments = resolve_arguments(step.get("arguments", {}), results)
        result = simulator.execute(step["action"], arguments)
    except KeyError as error:
        arguments = deepcopy(step.get("arguments", {}))
        result = {"status": "FAILED", "reason": f"UNRESOLVED_REFERENCE:{error.args[0]}"}

    api_counter[0] += 1
    trace.append({
        "step_id": step["id"],
        "phase": phase,
        "action": step["action"],
        "arguments": arguments,
        "before": before,
        "result": deepcopy(result),
        "after": deepcopy(simulator.state),
        "duration_ms": result.get("duration_ms", 0),
    })
    results[step["id"]] = deepcopy(result)
    return result


def execute_strategy(
    strategy: dict,
    initial_state: dict,
    scenario: dict | None = None,
    observation_advice: dict | None = None,
) -> dict:
    simulator = RobotSimulator(initial_state, scenario)
    results: dict[str, dict] = {}
    trace: list[dict] = []
    api_counter = [0]
    stopped = False

    for step in strategy.get("steps", []):
        result = _run_action(simulator, step, results, trace, api_counter)
        if result.get("status") == "SUCCESS":
            if step.get("action") == "stop":
                stopped = True
                break
            continue

        recovery = step.get("on_failure")
        if not recovery:
            continue

        recovered = False
        max_attempts = recovery.get("max_attempts", 1)
        recovery_steps = recovery.get("steps", [])
        for attempt in range(1, max_attempts + 1):
            attempt_ok = True
            for recovery_index, recovery_step in enumerate(recovery_steps, start=1):
                local_step = deepcopy(recovery_step)
                local_step.setdefault(
                    "id", f"{step['id']}_recovery_{attempt}_{recovery_index}"
                )
                recovery_result = _run_action(
                    simulator,
                    local_step,
                    results,
                    trace,
                    api_counter,
                    phase=f"recovery_{attempt}",
                )
                if recovery_result.get("status") != "SUCCESS":
                    attempt_ok = False
                    break
            if attempt_ok:
                recovered = True
                break

        if not recovered and recovery.get("on_exhausted") == "stop":
            stop_step = {
                "id": f"{step['id']}_safe_stop",
                "action": "stop",
                "arguments": {},
            }
            _run_action(
                simulator, stop_step, results, trace, api_counter, phase="safe_stop"
            )
            stopped = True
            break

    total_duration_ms = sum(event.get("duration_ms", 0) for event in trace)
    return {
        "scenario": (scenario or {}).get("name", "default"),
        "trace": trace,
        "final_state": deepcopy(simulator.state),
        "step_results": results,
        "api_call_count": api_counter[0],
        "total_duration_ms": total_duration_ms,
        "trajectory_points": deepcopy(simulator.trajectory_points),
        "stopped": stopped,
        "observation_advice": deepcopy(observation_advice or {}),
    }
