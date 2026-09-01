from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class RunState(str, Enum):
    CREATED = "CREATED"
    PREPARING_SCENE = "PREPARING_SCENE"
    PERCEIVING = "PERCEIVING"
    UNDERSTANDING = "UNDERSTANDING"
    PLANNING = "PLANNING"
    QUEUED_C = "QUEUED_C"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    SAFE_STOPPED = "SAFE_STOPPED"
    CANCELLED = "CANCELLED"


class JobState(str, Enum):
    QUEUED = "QUEUED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATES = {
    RunState.SUCCEEDED,
    RunState.BLOCKED,
    RunState.FAILED,
    RunState.SAFE_STOPPED,
    RunState.CANCELLED,
}

_NEXT_RUN_STATES: dict[RunState, set[RunState]] = {
    RunState.CREATED: {RunState.PREPARING_SCENE},
    RunState.PREPARING_SCENE: {RunState.PERCEIVING},
    RunState.PERCEIVING: {RunState.UNDERSTANDING},
    RunState.UNDERSTANDING: {RunState.PLANNING},
    RunState.PLANNING: {RunState.QUEUED_C},
    RunState.QUEUED_C: {RunState.EXECUTING},
    RunState.EXECUTING: {RunState.VERIFYING},
    RunState.VERIFYING: {RunState.SUCCEEDED, RunState.QUEUED_C},
}

_PUBLIC_RUN_FIELDS = (
    "run_id",
    "scene_id",
    "instruction",
    "state",
    "stage",
    "current_action",
    "created_at",
    "updated_at",
    "started_at",
    "finished_at",
    "error_code",
    "error_message",
    "result",
    "audit_eligible",
    "repair_attempts",
)


def _run_state(value: RunState | str) -> RunState:
    try:
        return value if isinstance(value, RunState) else RunState(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unknown run state: {value!r}") from exc


def assert_transition(current: RunState | str, target: RunState | str) -> RunState:
    """Return the normalized target when the run transition is legal."""

    current_state = _run_state(current)
    target_state = _run_state(target)
    if current_state in TERMINAL_RUN_STATES:
        raise ValueError(f"terminal run {current_state.value} cannot transition")

    allowed = set(_NEXT_RUN_STATES.get(current_state, set()))
    allowed.update(
        {
            RunState.BLOCKED,
            RunState.FAILED,
            RunState.SAFE_STOPPED,
            RunState.CANCELLED,
        }
    )
    if target_state not in allowed:
        raise ValueError(
            f"illegal run transition: {current_state.value} -> {target_state.value}"
        )
    return target_state


def public_run_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the browser-safe portion of one persisted run row."""

    snapshot = {key: row[key] for key in _PUBLIC_RUN_FIELDS if key in row}
    state = snapshot.get("state")
    if isinstance(state, Enum):
        snapshot["state"] = state.value
    return snapshot
