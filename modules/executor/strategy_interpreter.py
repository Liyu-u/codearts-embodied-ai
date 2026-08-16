from __future__ import annotations

from copy import deepcopy
from typing import Any

from integration.contract_validation import assert_contract
from modules.executor.action_catalog import validate_action_arguments
from modules.executor.models import ExecutionLimits, ExecutorBackend


class ReferenceResolutionError(ValueError):
    pass


def _resolve_value(value: Any, results: dict[str, dict]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        reference = value[1:]
        parts = reference.split(".")
        if len(parts) < 2 or not parts[0]:
            raise ReferenceResolutionError(f"UNRESOLVED_REFERENCE:{value}")
        current: Any = results.get(parts[0])
        if current is None:
            raise ReferenceResolutionError(f"UNRESOLVED_REFERENCE:{value}")
        for part in parts[1:]:
            if not isinstance(current, dict) or part not in current:
                raise ReferenceResolutionError(f"UNRESOLVED_REFERENCE:{value}")
            current = current[part]
        return deepcopy(current)
    if isinstance(value, dict):
        return {key: _resolve_value(item, results) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, results) for item in value]
    return deepcopy(value)


def resolve_arguments(arguments: dict, results: dict[str, dict]) -> dict:
    return _resolve_value(arguments, results)


class StrategyInterpreter:
    def __init__(
        self,
        backend: ExecutorBackend,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self.backend = backend
        self.limits = limits or ExecutionLimits()

    def run(self, strategy: dict) -> dict:
        assert_contract(strategy, "strategy.v1")
        if strategy.get("code") not in (None, ""):
            raise ValueError("strategy.code must be empty")
        self._preflight(strategy)

        records: list[dict] = []
        results: dict[str, dict] = {}
        failed = False

        for index, step in enumerate(strategy["steps"]):
            try:
                arguments = resolve_arguments(step["arguments"], results)
            except ReferenceResolutionError as exc:
                records.append(
                    self._record(
                        step,
                        step["arguments"],
                        "FAILED",
                        str(exc),
                        0,
                    )
                )
                self._append_skipped(strategy["steps"][index + 1 :], records)
                failed = True
                break

            backend_result = self.backend.execute(step["action"], arguments)
            results[step["step_id"]] = deepcopy(backend_result)
            records.append(
                self._record(
                    step,
                    arguments,
                    backend_result["status"],
                    backend_result.get("reason") or None,
                    backend_result.get("duration_ms", 0),
                )
            )
            if backend_result["status"] != "SUCCESS":
                self._append_skipped(strategy["steps"][index + 1 :], records)
                failed = True
                break

        return {
            "schema_version": "execution.v1",
            "task_id": strategy["task_id"],
            "status": "FAILED" if failed else "SUCCEEDED",
            "steps": records,
            "trajectory_points": self.backend.trajectory_points(),
            "total_duration_ms": sum(item["duration_ms"] for item in records),
            "safety_events": [],
        }

    def _preflight(self, strategy: dict) -> None:
        steps = strategy["steps"]
        if len(steps) > self.limits.max_main_steps:
            raise ValueError("MAIN_STEP_LIMIT_EXCEEDED")

        seen: set[str] = set()
        all_steps: list[dict] = []
        for step in steps:
            all_steps.append(step)
            recovery = step.get("on_failure")
            if recovery is not None:
                if not isinstance(recovery, dict):
                    raise ValueError("INVALID_RECOVERY:on_failure must be an object")
                recovery_steps = recovery.get("steps", [])
                if not isinstance(recovery_steps, list):
                    raise ValueError("INVALID_RECOVERY:steps must be an array")
                all_steps.extend(recovery_steps)

        for step in all_steps:
            step_id = step.get("step_id")
            if step_id in seen:
                raise ValueError(f"DUPLICATE_STEP_ID:{step_id}")
            seen.add(step_id)
            errors = validate_action_arguments(
                step.get("action", ""),
                step.get("arguments"),
            )
            if errors:
                raise ValueError(errors[0])

    @staticmethod
    def _record(
        step: dict,
        arguments: dict,
        status: str,
        reason: str | None,
        duration_ms: int,
        phase: str = "main",
    ) -> dict:
        return {
            "step_id": step["step_id"],
            "phase": phase,
            "action": step["action"],
            "arguments": deepcopy(arguments),
            "status": status,
            "reason": reason,
            "duration_ms": duration_ms,
        }

    @classmethod
    def _append_skipped(cls, steps: list[dict], records: list[dict]) -> None:
        for step in steps:
            records.append(
                cls._record(
                    step,
                    step["arguments"],
                    "SKIPPED",
                    "PREVIOUS_STEP_FAILED",
                    0,
                )
            )
