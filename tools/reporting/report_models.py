"""tools/reporting/report_models.py —— 批量统计与评估报告数据模型。

对齐 spec 6.2 必填字段与 design 2.2.2.2 类型签名，全部为冻结数据类。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportMetadata:
    git_sha: str
    profile: str
    manifest_path: str
    repeats: int
    timestamp: str
    command: list[str]


@dataclass(frozen=True)
class SampleRecord:
    case_id: str
    run_id: str
    model: str | None = None
    provider: str | None = None
    request_count: int = 0
    latency_ms: float | None = None
    failure_class: str | None = None
    original_error: dict | None = None
    evidence_path: str | None = None
    c_internal_recovery: bool = False
    d_repair_attempted: bool = False
    d_repair_succeeded: bool | None = None


@dataclass
class AcceptanceReport:
    metadata: ReportMetadata
    summary: dict[str, Any] = field(default_factory=dict)
    records: list[SampleRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metadata": {
                "git_sha": self.metadata.git_sha,
                "profile": self.metadata.profile,
                "manifest_path": self.metadata.manifest_path,
                "repeats": self.metadata.repeats,
                "timestamp": self.metadata.timestamp,
                "command": list(self.metadata.command),
            },
            "summary": dict(self.summary),
            "records": [
                {
                    "case_id": record.case_id,
                    "run_id": record.run_id,
                    "model": record.model,
                    "provider": record.provider,
                    "request_count": record.request_count,
                    "latency_ms": record.latency_ms,
                    "failure_class": record.failure_class,
                    "original_error": record.original_error,
                    "evidence_path": record.evidence_path,
                    "c_internal_recovery": record.c_internal_recovery,
                    "d_repair_attempted": record.d_repair_attempted,
                    "d_repair_succeeded": record.d_repair_succeeded,
                }
                for record in self.records
            ],
        }


def collect_metadata(*, profile: str, manifest_path: str, repeats: int, argv: list[str]) -> ReportMetadata:
    git_sha = _git_sha()
    import datetime

    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return ReportMetadata(
        git_sha=git_sha,
        profile=profile,
        manifest_path=manifest_path,
        repeats=repeats,
        timestamp=timestamp,
        command=list(argv),
    )


def _git_sha() -> str:
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
    except Exception:
        pass
    return "unknown"


REQUIRED_REPORT_FIELDS = [
    "manifest",
    "repeats",
    "pass_rate",
    "case_stability_rate",
    "strategy_contract_pass_rate",
    "code_null_rate",
    "repair_success_rate",
    "safe_stop_correct_rate",
    "p50_latency_ms",
    "p95_latency_ms",
    "git_sha",
    "profile",
    "timestamp",
    "command",
]


def missing_report_fields(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary") or {}
    metadata = report.get("metadata") or report
    present = set(summary) | set(metadata)
    if isinstance(metadata, dict):
        present |= set(metadata)
    return [name for name in REQUIRED_REPORT_FIELDS if name not in present]