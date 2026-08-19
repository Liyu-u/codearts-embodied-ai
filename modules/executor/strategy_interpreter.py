from __future__ import annotations

from copy import deepcopy
from typing import Any

from integration.contract_validation import assert_contract
from modules.executor.action_catalog import ALLOWED_ACTIONS, validate_action_arguments
from modules.executor.models import ExecutionLimits, ExecutorBackend, StepOutcome


class ReferenceResolutionError(ValueError):
    pass


class _ActionLimitExceeded(RuntimeError):
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


_EVIDENCE_KEYS = ("pose", "velocity_m_s", "collisions", "grasp_force_n")


def _evidence_fields(result: dict) -> dict:
    """从后端动作结果中提取执行证据字段，写入 execution.v1 的步骤记录。"""
    extra: dict = {}
    for key in _EVIDENCE_KEYS:
        if key in result:
            extra[key] = deepcopy(result[key])
    return extra


def _safety_gate_reason(result: dict) -> str:
    """从后端安全事件中提取安全门触发原因。"""
    for event in result.get("safety_events") or []:
        if isinstance(event, dict) and event.get("type"):
            return event["type"]
    return "SAFETY_GATE_TRIGGERED"


class StrategyInterpreter:
    def __init__(
        self,
        backend: ExecutorBackend,
        limits: ExecutionLimits | None = None,
    ) -> None:
        self.backend = backend
        self.limits = limits or ExecutionLimits()

    def run(self, strategy: dict) -> dict:
        if strategy.get("code") not in (None, ""):
            raise ValueError("strategy.code must be empty")
        def _check_steps(steps: list[dict]) -> None:
            for step in steps:
                if not isinstance(step, dict):
                    continue
                action = step.get("action")
                if action not in ALLOWED_ACTIONS:
                    raise ValueError(f"UNKNOWN_ACTION:{action}")
                recovery = step.get("on_failure")
                if isinstance(recovery, dict):
                    # Keep the interpreter's stable safety error even when the
                    # JSON schema would otherwise reject the value first.
                    max_attempts = recovery.get("max_attempts")
                    if (
                        isinstance(max_attempts, bool)
                        or not isinstance(max_attempts, int)
                        or not 1 <= max_attempts <= self.limits.max_recovery_attempts
                    ):
                        raise ValueError("max_attempts must be between 1 and 3")
                    if recovery.get("on_exhausted") != "stop":
                        raise ValueError("on_exhausted must be stop")
                    _check_steps(recovery.get("steps", []) or [])

        _check_steps(strategy.get("steps", []) or [])
        assert_contract(strategy, "strategy.v1")
        self._preflight(strategy)

        records: list[dict] = []
        results: dict[str, dict] = {}
        counter = [0]
        safety_events: list[dict] = []
        backend_events: list[dict] = []
        overall_status = "SUCCEEDED"

        for index, step in enumerate(strategy["steps"]):
            try:
                outcome = self._execute_step(step, results, "main", counter,
                                             backend_events)
            except _ActionLimitExceeded:
                self._append_skipped(
                    strategy["steps"][index:],
                    records,
                    "ACTION_LIMIT_EXCEEDED",
                )
                self._enter_safe_stop(
                    "ACTION_LIMIT_EXCEEDED",
                    step["step_id"],
                    records,
                    safety_events,
                )
                overall_status = "SAFE_STOP"
                break

            records.append(outcome.record)
            if outcome.result.get("status") == "SUCCESS":
                continue

            # 安全门事件（越界、碰撞、超时、限速等）不进入有限恢复，直接安全停止。
            if outcome.result.get("safety_events"):
                self._append_skipped(strategy["steps"][index + 1 :], records)
                self._enter_safe_stop(
                    _safety_gate_reason(outcome.result),
                    step["step_id"],
                    records,
                    safety_events,
                )
                overall_status = "SAFE_STOP"
                break

            recovery = step.get("on_failure")
            if recovery is None:
                self._append_skipped(strategy["steps"][index + 1 :], records)
                overall_status = "FAILED"
                break

            try:
                recovered = self._execute_recovery(
                    recovery,
                    results,
                    records,
                    counter,
                    backend_events,
                )
            except _ActionLimitExceeded:
                self._append_skipped(
                    strategy["steps"][index + 1 :],
                    records,
                    "ACTION_LIMIT_EXCEEDED",
                )
                self._enter_safe_stop(
                    "ACTION_LIMIT_EXCEEDED",
                    step["step_id"],
                    records,
                    safety_events,
                )
                overall_status = "SAFE_STOP"
                break

            if recovered:
                continue

            self._append_skipped(strategy["steps"][index + 1 :], records)
            self._enter_safe_stop(
                "RECOVERY_EXHAUSTED",
                step["step_id"],
                records,
                safety_events,
            )
            overall_status = "SAFE_STOP"
            break

        return self._build_output(
            strategy["task_id"],
            overall_status,
            records,
            safety_events,
            backend_events,
        )

    def _build_output(
        self,
        task_id: str,
        status: str,
        records: list[dict],
        safety_events: list[dict],
        backend_events: list[dict],
    ) -> dict:
        return {
            "schema_version": "execution.v1",
            "task_id": task_id,
            "status": status,
            "steps": records,
            "trajectory_points": self.backend.trajectory_points(),
            "total_duration_ms": sum(item["duration_ms"] for item in records),
            # 后端（Isaac/真机）在动作执行期间产生的碰撞、超时、越界等事件，
            # 按时间顺序排在前，解释器自身的安全停止事件排在后。
            "safety_events": backend_events + safety_events,
        }

    def _execute_step(
        self,
        step: dict,
        results: dict[str, dict],
        phase: str,
        counter: list[int],
        backend_events: list[dict],
    ) -> StepOutcome:
        try:
            arguments = resolve_arguments(step["arguments"], results)
        except ReferenceResolutionError as exc:
            result = {"status": "FAILED", "reason": str(exc), "duration_ms": 0}
            return StepOutcome(
                self._record(step, step["arguments"], "FAILED", str(exc), 0, phase),
                result,
            )

        if counter[0] >= self.limits.max_action_calls:
            raise _ActionLimitExceeded
        counter[0] += 1
        result = self.backend.execute(step["action"], arguments)
        results[step["step_id"]] = deepcopy(result)
        for event in result.get("safety_events") or []:
            if isinstance(event, dict):
                backend_events.append(deepcopy(event))
        return StepOutcome(
            self._record(
                step,
                arguments,
                result["status"],
                result.get("reason") or None,
                result.get("duration_ms", 0),
                phase,
                _evidence_fields(result),
            ),
            result,
        )

    def _execute_recovery(
        self,
        recovery: dict,
        results: dict[str, dict],
        records: list[dict],
        counter: list[int],
        backend_events: list[dict],
    ) -> bool:
        for attempt in range(1, recovery["max_attempts"] + 1):
            attempt_ok = True
            for recovery_step in recovery["steps"]:
                outcome = self._execute_step(
                    recovery_step,
                    results,
                    phase=f"recovery_{attempt}",
                    counter=counter,
                    backend_events=backend_events,
                )
                records.append(outcome.record)
                if outcome.result.get("status") != "SUCCESS":
                    attempt_ok = False
                    break
            if attempt_ok:
                return True
        return False

    def _enter_safe_stop(
        self,
        reason: str,
        step_id: str,
        records: list[dict],
        safety_events: list[dict],
    ) -> None:
        stop_result = self.backend.safe_stop(reason)
        records.append(
            {
                "step_id": "safe_stop",
                "phase": "safe_stop",
                "action": "stop",
                "arguments": {},
                "status": "SUCCESS",
                "reason": stop_result.get("reason") or reason,
                "duration_ms": stop_result.get("duration_ms", 0),
            }
        )
        safety_events.append(
            {
                "type": reason,
                "severity": "error",
                "step_id": step_id,
                "message": f"{reason}; executor entered safe stop",
            }
        )

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
                max_attempts = recovery.get("max_attempts")
                if (
                    not isinstance(max_attempts, int)
                    or isinstance(max_attempts, bool)
                    or not 1 <= max_attempts <= self.limits.max_recovery_attempts
                ):
                    raise ValueError("max_attempts must be between 1 and 3")
                if not recovery_steps:
                    raise ValueError("recovery steps must not be empty")
                if len(recovery_steps) > self.limits.max_recovery_steps:
                    raise ValueError("RECOVERY_STEP_LIMIT_EXCEEDED")
                if recovery.get("on_exhausted") != "stop":
                    raise ValueError("on_exhausted must be stop")
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
        extra: dict | None = None,
    ) -> dict:
        record = {
            "step_id": step["step_id"],
            "phase": phase,
            "action": step["action"],
            "arguments": deepcopy(arguments),
            "status": status,
            "reason": reason,
            "duration_ms": duration_ms,
        }
        if extra:
            record.update(extra)
        return record

    @classmethod
    def _append_skipped(
        cls,
        steps: list[dict],
        records: list[dict],
        reason: str = "PREVIOUS_STEP_FAILED",
    ) -> None:
        for step in steps:
            records.append(
                cls._record(
                    step,
                    step["arguments"],
                    "SKIPPED",
                    reason,
                    0,
                )
            )
