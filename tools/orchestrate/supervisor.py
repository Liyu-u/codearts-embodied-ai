"""tools/orchestrate/supervisor.py —— 阶段状态机与超时/重试控制。

任一阶段失败必达 CLEANUP（finally 语义）；仅传输类失败允许重试；
失败分类与退出码映射见 types.exit_code_for。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from tools.orchestrate.types import OrchestrationConfig, StageReport


@dataclass
class StageOutcome:
    report: StageReport
    failure_class: str | None = None
    retryable: bool = False


class StageError(RuntimeError):
    def __init__(self, message: str, failure_class: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.failure_class = failure_class
        self.retryable = retryable


@dataclass
class StageSupervisor:
    config: OrchestrationConfig
    logger: Callable[[str], None] = field(default=lambda text: None)
    stages: list[StageReport] = field(default_factory=list)

    def log(self, stage: str, action: str) -> None:
        self.logger(f"[{stage}] {action}")

    def run_stage(
        self,
        stage: str,
        action: str,
        fn: Callable[[], None],
        *,
        timeout_s: int | None = None,
        failure_class: str = "runner",
        retryable: bool = False,
    ) -> StageReport:
        started = time.monotonic()
        self.log(stage, action)
        remaining = timeout_s or self.config.ssh_timeout_s
        attempt = 0
        while True:
            attempt += 1
            try:
                fn()
                duration_ms = int((time.monotonic() - started) * 1000)
                report = StageReport(
                    stage=stage,
                    action=action,
                    duration_ms=duration_ms,
                    outcome="SUCCEEDED",
                )
                self.stages.append(report)
                return report
            except StageError as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                report = StageReport(
                    stage=stage,
                    action=action,
                    duration_ms=duration_ms,
                    outcome="FAILED",
                    failure_class=exc.failure_class,
                )
                self.stages.append(report)
                if exc.retryable and attempt <= self.config.transport_retries:
                    self.log(stage, f"传输失败，第 {attempt} 次重试: {exc.message}")
                    continue
                raise
            except TimeoutError as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                report = StageReport(
                    stage=stage,
                    action=action,
                    duration_ms=duration_ms,
                    outcome="TIMEOUT",
                    failure_class=failure_class,
                )
                self.stages.append(report)
                raise StageError(str(exc), failure_class) from exc
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                report = StageReport(
                    stage=stage,
                    action=action,
                    duration_ms=duration_ms,
                    outcome="FAILED",
                    failure_class=failure_class,
                )
                self.stages.append(report)
                raise StageError(str(exc), failure_class) from exc
            finally:
                remaining = (timeout_s or self.config.ssh_timeout_s) - int(
                    time.monotonic() - started
                )
                if remaining <= 0:
                    raise StageError(f"{stage} 阶段超时", failure_class)

    def mark_skipped(self, stage: str, action: str) -> StageReport:
        report = StageReport(
            stage=stage,
            action=action,
            duration_ms=0,
            outcome="SKIPPED",
        )
        self.stages.append(report)
        return report

    def report(self) -> list[StageReport]:
        return list(self.stages)