"""Compare the local strategy baseline with real CodeArts generation.

This is intentionally an opt-in benchmark: normal test discovery never makes
network calls.  Run it from the repository root after configuring the CodeArts
CLI credentials::

    python tools/benchmark_codearts.py --live --repeats 2

The report records whether the provider was actually used, whether every
strategy passed the local contract gate, and latency/success/fallback metrics.
It does not print or persist credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.adapters import strategy as strategy_adapter  # noqa: E402
from modules.strategy_generation.codearts_agent import validate_strategy  # noqa: E402


BASE_TASK = json.loads(
    (ROOT / "testdata" / "daily" / "strategy_normal_pick.json").read_text(
        encoding="utf-8"
    )
)


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _tasks(case_count: int) -> list[dict[str, Any]]:
    tasks = []
    for index in range(1, case_count + 1):
        task = deepcopy(BASE_TASK)
        task["task_id"] = f"codearts-compare-{index:03d}"
        tasks.append(task)
    return tasks


def _run_one(task: dict[str, Any], mode: str, repeat: int) -> dict[str, Any]:
    started = time.perf_counter()
    with _temporary_environment({"CODEARTS_STRATEGY_MODE": mode}):
        output = strategy_adapter.run(task)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    provider = (output.get("provenance") or {}).get("provider")
    strategy = output if output.get("schema_version") == "strategy.v1" else {}
    contract_errors = (
        validate_strategy(strategy, task)
        if output.get("success") and not output.get("blocked")
        else []
    )
    return {
        "task_id": task["task_id"],
        "repeat": repeat,
        "mode_requested": mode,
        "elapsed_ms": elapsed_ms,
        "success": bool(output.get("success")) and not bool(output.get("blocked")),
        "blocked": bool(output.get("blocked")),
        "strategy_mode": output.get("mode"),
        "provider": provider,
        "transport": (output.get("provenance") or {}).get("transport"),
        "error": (output.get("blocking_reasons") or [None])[0]
        if output.get("blocked")
        else output.get("provider_error"),
        "contract_errors": contract_errors,
        "code_is_null": output.get("code") is None,
        "actions": [step.get("action") for step in output.get("steps", [])],
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) * percentile) + 0.9999) - 1))
    return round(ordered[index], 1)


def _summary(records: list[dict[str, Any]], expected_mode: str) -> dict[str, Any]:
    total = len(records)
    successes = sum(1 for record in records if record["success"])
    provider_calls = sum(
        1
        for record in records
        if record["provider"] == "huaweicloud-codearts-agent"
    )
    fallback_count = sum(
        1 for record in records if record["strategy_mode"] == "primitive_plan_fallback"
    )
    latencies = [record["elapsed_ms"] for record in records]
    contract_failures = sum(1 for record in records if record["contract_errors"])
    return {
        "total": total,
        "successes": successes,
        "success_rate": round(successes / total, 4) if total else 0.0,
        "provider_calls": provider_calls,
        "provider_call_rate": round(provider_calls / total, 4) if total else 0.0,
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / total, 4) if total else 0.0,
        "contract_failure_count": contract_failures,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "all_requested_modes_match": all(
            record["mode_requested"] == expected_mode for record in records
        ),
        "all_successful_strategies_are_safe": all(
            record["code_is_null"]
            and not record["contract_errors"]
            for record in records
            if record["success"]
        ),
    }


def run_benchmark(
    *,
    repeats: int = 2,
    case_count: int = 3,
    live: bool = True,
    model: str | None = None,
    timeout_s: int | None = None,
    pure: bool = False,
) -> dict[str, Any]:
    if repeats < 1 or case_count < 1:
        raise ValueError("repeats 和 case_count 必须大于 0")
    tasks = _tasks(case_count)
    baseline_records = [
        _run_one(task, "off", repeat)
        for repeat in range(1, repeats + 1)
        for task in tasks
    ]

    codearts_records: list[dict[str, Any]] = []
    if live:
        values = {"CODEARTS_STRATEGY_MODE": "required"}
        if model:
            values["CODEARTS_STRATEGY_MODEL"] = model
        if timeout_s:
            values["CODEARTS_STRATEGY_TIMEOUT_S"] = str(timeout_s)
        if pure:
            values["CODEARTS_CLI_PURE"] = "1"
        for repeat in range(1, repeats + 1):
            for task in tasks:
                with _temporary_environment(values):
                    codearts_records.append(_run_one(task, "required", repeat))

    baseline = _summary(baseline_records, "off")
    codearts = _summary(codearts_records, "required") if live else None
    comparison = {
        "provider_calls_prove_codearts_intervened": bool(
            codearts and codearts["provider_calls"] > 0
        ),
        "all_codearts_runs_accepted": bool(
            codearts
            and codearts["successes"] == codearts["total"]
            and codearts["provider_calls"] == codearts["total"]
        ),
        "success_rate_delta_vs_baseline": round(
            (codearts["success_rate"] - baseline["success_rate"]), 4
        )
        if codearts
        else None,
        "stable": bool(
            codearts
            and codearts["total"] > 0
            and codearts["provider_calls"] == codearts["total"]
            and codearts["successes"] == codearts["total"]
            and codearts["contract_failure_count"] == 0
            and codearts["fallback_count"] == 0
            and codearts["all_successful_strategies_are_safe"]
        ),
    }
    return {
        "schema_version": "codearts-comparison.v1",
        "benchmark": {
            "live": live,
            "repeats": repeats,
            "case_count": case_count,
            "task_action": "pick_and_place",
            "model": model or os.environ.get("CODEARTS_STRATEGY_MODEL") or None,
            "cli_pure": pure,
        },
        "baseline": baseline,
        "codearts": codearts,
        "comparison": comparison,
        "records": {"baseline": baseline_records, "codearts": codearts_records},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="调用真实 CodeArts CLI")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--cases", type=int, default=3, dest="case_count")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-s", type=int, default=90)
    parser.add_argument(
        "--pure",
        action="store_true",
        help="设置 CODEARTS_CLI_PURE=1，避免加载项目插件",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "codearts_comparison.json",
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("为避免误发网络请求，请显式指定 --live")

    report = run_benchmark(
        repeats=args.repeats,
        case_count=args.case_count,
        live=True,
        model=args.model,
        timeout_s=args.timeout_s,
        pure=args.pure,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "baseline": report["baseline"],
        "codearts": report["codearts"],
        "comparison": report["comparison"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["comparison"]["stable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
