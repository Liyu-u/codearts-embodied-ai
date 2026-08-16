from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExecutionLimits:
    max_main_steps: int = 50
    max_recovery_steps: int = 10
    max_recovery_attempts: int = 3
    max_action_calls: int = 100


@dataclass(frozen=True)
class StepOutcome:
    record: dict
    result: dict


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
