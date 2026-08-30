"""Summarize real Isaac Sim closed-loop acceptance evidence.

This tool only reads saved per-run reports. It never retries a run and never
infers success from a missing artifact. Transport/auth failures are separated
from business, safety, contract, and planning outcomes.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _p95(values: Iterable[float]) -> float | None:
    rows = sorted(float(value) for value in values if isinstance(value, (int, float)) and math.isfinite(float(value)))
    if not rows:
        return None
    index = max(0, math.ceil(0.95 * len(rows)) - 1)
    return round(rows[index], 3)


def classify(record: dict[str, Any]) -> str:
    stages = record.get("stages") or {}
    remote_stage = stages.get("C") or stages.get("c") or {}
    # A safety stop is an execution outcome, not a contract failure.  Check
    # this before stale/derived contract flags so old reports are reclassified
    # consistently when they are summarized again.
    if str(remote_stage.get("status") or "").upper() == "SAFE_STOP":
        return "safety_stop"
    message = " ".join(
        str(value)
        for value in (
            record.get("message"),
            record.get("error"),
            (record.get("remote_run") or {}).get("message"),
        )
        if value
    ).lower()
    # Transport/auth failures must win over the derived "task id mismatch"
    # flag, because no business contract was actually exercised in that case.
    if any(token in message for token in ("permission denied", "publickey", "connecttimeout", "timed out", "connection", "scp", "ssh")):
        return "transport_auth"
    # A successful A/B plan with no C artifact means the remote runner did
    # not produce an execution result.  It is not evidence of a task-id
    # contract mismatch; keep this distinction stable even for older reports
    # that stored a derived false contract flag.
    if (
        str(record.get("expected_status") or "").upper() == "SUCCEEDED"
        and not remote_stage.get("status")
    ):
        return "runner"
    explicit = str(record.get("failure_class") or "").strip()
    if explicit:
        return explicit
    if record.get("status") == "SUCCEEDED":
        return "success"
    contract = record.get("contract_checks") or {}
    if contract and any(value is False for value in contract.values()):
        return "contract"
    remote = remote_stage
    if str(remote.get("status") or "").upper() == "FAILED":
        return "execution"
    a_stage = stages.get("A") or stages.get("a") or {}
    b_stage = stages.get("B") or stages.get("b") or {}
    if str(a_stage.get("status") or "").upper() not in {"READY", "SUCCEEDED", ""} or str(b_stage.get("status") or "").upper() not in {"SUCCEEDED", ""}:
        return "planning"
    return "runner"


def load_records(root: Path, pattern: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for directory in sorted(path for path in root.glob(pattern) if path.is_dir()):
        status_path = directory / "full_test_status.json"
        record = _read_json(status_path)
        if record is None:
            remote = _read_json(directory / "remote_run.json")
            if remote is None:
                continue
            record = {"run_id": directory.name, "status": remote.get("status"), "remote_run": remote}
        record.setdefault("run_id", directory.name)
        record["failure_class"] = classify(record)
        records.append(record)
    return records


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    passed = sum(record.get("status") == "SUCCEEDED" for record in records)
    completed = sum(classify(record) not in {"transport_auth", "runner"} for record in records)
    contract_rows = [
        record
        for record in records
        if any(isinstance(value, bool) for value in (record.get("contract_checks") or {}).values())
        and classify(record) not in {"transport_auth", "runner"}
    ]
    contract_passed = sum(
        all(
            value
            for value in (record.get("contract_checks") or {}).values()
            if isinstance(value, bool)
        )
        for record in contract_rows
    )
    c_stages = [(record.get("stages") or {}).get("C") or {} for record in records]
    feedback_stages = [(record.get("stages") or {}).get("feedback") or {} for record in records]
    wall_ms = [stage.get("wall_ms") for stage in c_stages]
    request_counts: list[float] = []
    total_tokens: list[float] = []
    for record in records:
        for stage_name in ("A", "B", "feedback"):
            stage = (record.get("stages") or {}).get(stage_name) or {}
            for key, target in (("request_count", request_counts), ("total_tokens", total_tokens)):
                value = stage.get(key)
                if isinstance(value, (int, float)):
                    target.append(float(value))
    by_class: dict[str, int] = {}
    for record in records:
        key = classify(record)
        by_class[key] = by_class.get(key, 0) + 1
    safe_stop_rows = [
        (record, stage)
        for record, stage in zip(records, c_stages)
        if str(record.get("expected_status") or "").upper() == "SAFE_STOP"
    ]
    safe_stop_correct = sum(str(stage.get("status") or "").upper() == "SAFE_STOP" for _, stage in safe_stop_rows)
    return {
        "sample_count": total,
        "completed_count": completed,
        "passed_count": passed,
        "pass_rate": passed / total if total else None,
        "completed_pass_rate": passed / completed if completed else None,
        "contract_checked_count": len(contract_rows),
        "contract_pass_rate": contract_passed / len(contract_rows) if contract_rows else None,
        "safe_stop_expected_count": len(safe_stop_rows),
        "safe_stop_correct_rate": safe_stop_correct / len(safe_stop_rows) if safe_stop_rows else None,
        "isaac_wall_ms_p95": _p95(wall_ms),
        "request_count_p95": _p95(request_counts),
        "total_tokens_p95": _p95(total_tokens),
        "failure_classes": by_class,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("reports"))
    parser.add_argument("--pattern", default="real-acceptance-*")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    records = load_records(args.root, args.pattern)
    result = {
        "schema_version": "real-acceptance-summary.v1",
        "root": str(args.root),
        "pattern": args.pattern,
        "summary": summarize(records),
        "records": records,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(json.dumps({"output": str(args.output) if args.output else None, "summary": result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
