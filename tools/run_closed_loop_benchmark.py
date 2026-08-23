"""Run the multi-task P/A/B/C/D benchmark and write an auditable report.

The benchmark is intentionally separate from the unit/contract suites:

* ``--mode baseline`` uses the deterministic local strategy provider.
* ``--mode codearts`` uses the real CodeArts provider and requires credentials.
* ``--compare`` runs both modes and puts both summaries in one report.

The default backend is Mock.  Isaac Sim results are kept as a separate backend
because the remote runner has its own lifecycle; a future batch Isaac runner
can merge records using the same per-run fields written here.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_PATH = ROOT / "testdata" / "benchmark" / "closed_loop_cases.json"
ACCEPTANCE_ROOT = ROOT / "testdata" / "acceptance"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.evaluator.tracecoder.llm_provider import try_load_dotenv  # noqa: E402

try_load_dotenv()
from demo.scenarios import get_scenario  # noqa: E402
from demo.server import (  # noqa: E402
    _DemoStrategyAdapter,
    _IsolatedTraceCoderAdapter,
)
from integration.adapters import intent, perception, strategy, tracecoder  # noqa: E402
from integration.adapters.executor import ExecutorAdapter  # noqa: E402
from integration.contract_validation import assert_contract  # noqa: E402
from integration.pipeline import run_pipeline  # noqa: E402
from modules.executor.mock_backend import MockBackend  # noqa: E402
from tests.e2e.test_closed_loop_acceptance import _load_json, _load_scene, _run_case  # noqa: E402


DEFAULT_ACTIONS = [
    "detect_object",
    "move_to_object",
    "grasp",
    "move_to_target",
    "release",
]


@contextmanager
def temporary_environment(values: dict[str, str]) -> Iterator[None]:
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


def load_manifest() -> dict[str, Any]:
    document = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    if document.get("schema_version") != "closed-loop-benchmark.v1":
        raise ValueError("benchmark manifest schema_version 错误")
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) < 30:
        raise ValueError("benchmark cases 必须至少包含30道题")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ValueError("每道 benchmark 题必须有非空 id")
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark case id 不能重复")
    return document


def _demo_scene(case: dict[str, Any]) -> dict[str, Any]:
    scenario = get_scenario(case["scene_id"])
    scene = scenario["scene"]
    if case["scene_id"] in {"stacking_cubes", "sorting_workcell"}:
        scene = perception.run({"scene_id": case["scene_id"], "backend": "mock"})
    return scene


def _run_demo_case(case: dict[str, Any], request_id: str) -> dict[str, Any]:
    scenario = get_scenario(case["scene_id"])
    scene = _demo_scene(case)
    failures = case.get("failures")
    if failures is None:
        failures = scenario.get("executor_failures")
    backend = MockBackend.from_perception(scene, failures=failures)
    strategy_adapter = (
        _DemoStrategyAdapter()
        if scenario.get("tracecoder_repair")
        else strategy
    )
    adapters = {
        "intent": intent,
        "strategy": strategy_adapter,
        "executor": ExecutorAdapter(backend),
        "tracecoder": _IsolatedTraceCoderAdapter(),
    }
    result = run_pipeline(
        scene,
        case["instruction"],
        adapters,
        engine=os.getenv("RIA_PLANNER_ENGINE", "rule"),
        request_id=request_id,
    )
    return {
        "result": result,
        "scene": scene,
        "backend_snapshot": backend.snapshot(),
    }


def _run_acceptance_case(case: dict[str, Any]) -> dict[str, Any]:
    source = _load_json(ACCEPTANCE_ROOT / case["path"])
    intelligent = os.getenv("RIA_PLANNER_ENGINE", "rule").strip().lower() == "llm"
    if intelligent and source.get("mode") != "tracecoder_fixture":
        scene = _load_scene(source)
        failures = (source.get("executor") or {}).get("failures")
        backend = MockBackend.from_perception(scene, failures=failures)
        adapters = {
            "intent": intent,
            "strategy": strategy,
            "executor": ExecutorAdapter(backend),
            "tracecoder": tracecoder,
        }
        result = run_pipeline(
            scene,
            source["instruction"],
            adapters,
            engine="llm",
            request_id=source.get("case_id"),
        )
    else:
        result, backend = _run_case(source)
    return {
        "result": result,
        "source_case": source,
        "backend_snapshot": backend.snapshot() if backend is not None else None,
    }


def _extract_feedback_status(feedback: Any) -> str | None:
    if not isinstance(feedback, dict):
        return None
    if feedback.get("safety_stop") is True or feedback.get("execution_status") == "SAFE_STOP":
        return "C_SAFE_STOP"
    if isinstance(feedback.get("status"), str):
        return feedback["status"]
    try:
        diagnosis = json.loads(feedback.get("diagnosis", "{}"))
    except (TypeError, json.JSONDecodeError):
        return None
    if diagnosis.get("final_passed") is True:
        return "D_ACCEPTED"
    if diagnosis.get("retryable") is True or feedback.get("retryable") is True:
        return "D_RETRYABLE"
    return "D_REJECTED"


def _strategy_info(result: dict[str, Any]) -> dict[str, Any]:
    candidate = result.get("strategy") or {}
    provenance = candidate.get("provenance") or {}
    critics = provenance.get("critics") or candidate.get("critics") or []
    try:
        assert_contract(candidate, "strategy.v1")
        contract_valid = True
        contract_error = None
    except Exception as exc:  # pragma: no cover - defensive report boundary
        contract_valid = False
        contract_error = f"{type(exc).__name__}: {exc}"
    return {
        "mode": candidate.get("mode"),
        "policy": candidate.get("strategy_policy"),
        "provider": provenance.get("provider"),
        "source": provenance.get("source"),
        "transport": provenance.get("transport"),
        "request_id": provenance.get("request_id"),
        "fallback": bool(provenance.get("fallback")),
        "latency_ms": provenance.get("latency_ms"),
        "critic_passes": len(critics),
        "code_null": candidate.get("code") in {None, ""},
        "contract_valid": contract_valid,
        "contract_error": contract_error,
        "actions": [step.get("action") for step in candidate.get("steps", [])],
    }


def _signature(result: dict[str, Any]) -> tuple[Any, ...]:
    strategy_info = _strategy_info(result)
    task = result.get("task") or {}
    execution = result.get("execution") or {}
    safety_types = tuple(
        sorted(event.get("type") for event in execution.get("safety_events", []))
    )
    return (
        result.get("status"),
        task.get("status"),
        task.get("action"),
        tuple(task.get("target_ids") or []),
        task.get("destination_id"),
        tuple(strategy_info["actions"]),
        execution.get("status"),
        _extract_feedback_status(result.get("feedback")),
        result.get("retry_count", 0),
        safety_types,
    )


def _run_one(case: dict[str, Any], repeat: int) -> dict[str, Any]:
    request_id = f"benchmark-{case['id']}-r{repeat}"
    started = time.perf_counter()
    if case["source"] in {"demo", "demo_override"}:
        payload = _run_demo_case(case, request_id)
    elif case["source"] == "acceptance":
        payload = _run_acceptance_case(case)
    else:
        raise ValueError(f"未知 benchmark source: {case['source']}")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    result = payload["result"]
    strategy_info = _strategy_info(result)
    execution = result.get("execution") or {}
    feedback = result.get("feedback") or {}
    expected_status = case["expected_status"]
    status_passed = result.get("status") == expected_status
    retry_passed = (
        "expected_retry_count" not in case
        or result.get("retry_count", 0) == case["expected_retry_count"]
    )
    passed = status_passed and retry_passed
    task_diagnostics = (result.get("task") or {}).get("diagnostics") or {}
    engine_trace = task_diagnostics.get("engine_trace") if isinstance(task_diagnostics, dict) else {}
    if not isinstance(engine_trace, dict):
        engine_trace = {}
    feedback_provenance = feedback.get("provenance") if isinstance(feedback, dict) else {}
    if not isinstance(feedback_provenance, dict):
        feedback_provenance = {}
    tracecoder_stats = feedback_provenance.get("llm_stats") or {}
    tracecoder_invoked = feedback_provenance.get("source") != "tracecoder_skipped"
    return {
        "case_id": case["id"],
        "category": case["category"],
        "source": case["source"],
        "repeat": repeat,
        "request_id": request_id,
        "expected_status": expected_status,
        "actual_status": result.get("status"),
        "passed": passed,
        "status_passed": status_passed,
        "retry_passed": retry_passed,
        "elapsed_ms": elapsed_ms,
        "backend": "mock",
        "task": result.get("task"),
        "strategy": strategy_info,
        "intent": {
            "requested_engine": task_diagnostics.get("requested_engine"),
            "actual_engine": engine_trace.get("actual_engine"),
            "llm_call_attempted": bool(engine_trace.get("llm_call_attempted")),
            "llm_call_succeeded": bool(engine_trace.get("llm_call_succeeded")),
            "fallback_used": bool(engine_trace.get("fallback_used")),
            "model": engine_trace.get("model") or engine_trace.get("model_name"),
        },
        "feedback_provenance": {
            "source": feedback_provenance.get("source"),
            "mode": feedback_provenance.get("mode"),
            "profile": feedback_provenance.get("profile"),
            "routing_mode": feedback_provenance.get("routing_mode"),
            "trigger_reasons": feedback_provenance.get("trigger_reasons") or [],
            "model": feedback_provenance.get("model"),
            "fallback": bool(feedback_provenance.get("fallback")),
            "latency_ms": feedback_provenance.get("latency_ms"),
            "tracecoder_invoked": tracecoder_invoked,
            "llm_stats": tracecoder_stats,
        },
        "execution": {
            "status": execution.get("status"),
            "total_duration_ms": execution.get("total_duration_ms"),
            "step_count": len(execution.get("steps") or []),
            "safety_events": execution.get("safety_events") or [],
        },
        "feedback": {
            "status": _extract_feedback_status(feedback),
            "execution_status": feedback.get("execution_status"),
            "safety_stop": bool(feedback.get("safety_stop")),
            "stop_reason": feedback.get("stop_reason"),
            "retryable": feedback.get("retryable"),
            "final_passed": feedback.get("final_passed"),
        },
        "tracecoder_invoked": tracecoder_invoked,
        "tracecoder_requests": int(tracecoder_stats.get("calls", 0) or 0),
        "retry_count": result.get("retry_count", 0),
        "d_repair_required": bool(case.get("requires_d_repair", False)),
        "attempt_count": len(result.get("attempts") or []),
        "stop_reason": result.get("stop_reason"),
        "signature": list(_signature(result)),
        "replay": {
            "task": result.get("task"),
            "strategy": result.get("strategy"),
            "execution": result.get("execution"),
            "feedback": result.get("feedback"),
            "attempts": result.get("attempts"),
        },
    }


def _summarize(records: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    def percentile(values: list[float], fraction: float) -> float | None:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
        return round(ordered[index], 1)

    def latency_stats(values: list[Any]) -> dict[str, Any]:
        numbers = [float(value) for value in values if isinstance(value, (int, float))]
        return {
            "count": len(numbers),
            "p50_ms": percentile(numbers, 0.50),
            "p95_ms": percentile(numbers, 0.95),
            "max_ms": round(max(numbers), 1) if numbers else None,
        }

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record["category"], []).append(record)
    case_results = {}
    for case in cases:
        rows = [item for item in records if item["case_id"] == case["id"]]
        signatures = {tuple(item["signature"]) for item in rows}
        case_results[case["id"]] = {
            "runs": len(rows),
            "passed": all(item["passed"] for item in rows),
            "stable": len(signatures) <= 1,
        }

    strategy_records = [item for item in records if item["strategy"]["actions"]]
    execution_records = [item for item in records if item["execution"]["status"]]
    safe_stop_records = [item for item in records if item["category"] == "safe_stop"]
    d_repair_case_ids = {
        case["id"]
        for case in cases
        if case["category"] == "recoverable_failure"
        and case.get("requires_d_repair", False)
    }
    repair_records = [item for item in records if item["case_id"] in d_repair_case_ids]
    provider_calls = sum(
        1 for item in records if item["strategy"]["provider"] == "huaweicloud-codearts-agent"
    )
    provider_attempts = sum(
        1
        for item in records
        if item["strategy"]["provider"] == "huaweicloud-codearts-agent"
        or item["strategy"]["fallback"]
    )
    provider_latencies = [
        item["strategy"].get("latency_ms")
        for item in records
        if item["strategy"].get("provider") == "huaweicloud-codearts-agent"
    ]
    end_to_end_latencies = [item.get("elapsed_ms") for item in records]
    tracecoder_stats = [item["feedback_provenance"]["llm_stats"] for item in records]
    tracecoder_latency = [stats.get("total_latency_ms") for stats in tracecoder_stats]
    tracecoder_invocations = sum(1 for item in records if item["tracecoder_invoked"])
    tracecoder_requests = sum(int(stats.get("calls", 0) or 0) for stats in tracecoder_stats)
    tracecoder_prompt_tokens = sum(int(stats.get("prompt_tokens", 0) or 0) for stats in tracecoder_stats)
    tracecoder_completion_tokens = sum(int(stats.get("completion_tokens", 0) or 0) for stats in tracecoder_stats)
    tracecoder_reasoning_tokens = sum(int(stats.get("reasoning_tokens", 0) or 0) for stats in tracecoder_stats)
    tracecoder_total_tokens = sum(int(stats.get("total_tokens", 0) or 0) for stats in tracecoder_stats)
    summary = {
        "cases": len(cases),
        "intent_llm_attempts": sum(1 for item in records if item["intent"]["llm_call_attempted"]),
        "intent_llm_successes": sum(1 for item in records if item["intent"]["llm_call_succeeded"]),
        "intent_fallback_count": sum(1 for item in records if item["intent"]["fallback_used"]),
        "tracecoder_llm_runs": sum(1 for item in records if item["feedback_provenance"]["mode"] in {"optional", "required"}),
        "tracecoder_invocations": tracecoder_invocations,
        "tracecoder_skipped_runs": len(records) - tracecoder_invocations,
        "tracecoder_request_count": tracecoder_requests,
        "tracecoder_prompt_tokens": tracecoder_prompt_tokens,
        "tracecoder_completion_tokens": tracecoder_completion_tokens,
        "tracecoder_reasoning_tokens": tracecoder_reasoning_tokens,
        "tracecoder_total_tokens": tracecoder_total_tokens,
        "tracecoder_latency_ms": latency_stats(tracecoder_latency),
        "tracecoder_fallback_count": sum(1 for item in records if item["feedback_provenance"]["fallback"]),
        "runs": len(records),
        "passed_runs": sum(1 for item in records if item["passed"]),
        "pass_rate": rate(sum(1 for item in records if item["passed"]), len(records)),
        "passed_cases": sum(1 for item in case_results.values() if item["passed"]),
        "stable_cases": sum(1 for item in case_results.values() if item["stable"]),
        "case_pass_rate": rate(sum(1 for item in case_results.values() if item["passed"]), len(case_results)),
        "case_stability_rate": rate(sum(1 for item in case_results.values() if item["stable"]), len(case_results)),
        "strategy_checked": len(strategy_records),
        "strategy_contract_pass_rate": rate(sum(1 for item in strategy_records if item["strategy"]["contract_valid"]), len(strategy_records)),
        "code_null_rate": rate(sum(1 for item in strategy_records if item["strategy"]["code_null"]), len(strategy_records)),
        "provider_calls": provider_calls,
        "provider_attempts": provider_attempts,
        "provider_latency_ms": latency_stats(provider_latencies),
        "end_to_end_latency_ms": latency_stats(end_to_end_latencies),
        "fallback_count": sum(1 for item in records if item["strategy"]["fallback"]),
        "execution_attempts": len(execution_records),
        "execution_success_rate": rate(sum(1 for item in execution_records if item["execution"]["status"] == "SUCCEEDED"), len(execution_records)),
        "repair_cases": len(repair_records),
        "repair_success_rate": rate(sum(1 for item in repair_records if item["actual_status"] == "SUCCEEDED" and item["retry_count"] > 0), len(repair_records)),
        "safe_stop_cases": len(safe_stop_records),
        "safe_stop_correct_rate": rate(sum(1 for item in safe_stop_records if item["actual_status"] == "SAFE_STOP"), len(safe_stop_records)),
        "safety_event_runs": sum(1 for item in records if item["execution"]["safety_events"]),
        "by_category": {},
        "case_results": case_results,
    }
    for category, rows in groups.items():
        summary["by_category"][category] = {
            "runs": len(rows),
            "passed_runs": sum(1 for item in rows if item["passed"]),
            "pass_rate": rate(sum(1 for item in rows if item["passed"]), len(rows)),
            "actual_statuses": {
                status: sum(1 for item in rows if item["actual_status"] == status)
                for status in sorted({item["actual_status"] for item in rows})
            },
        }
    return summary


def run_benchmark(
    *,
    mode: str,
    repeats: int,
    policy: str,
    model: str | None,
    timeout_s: int,
    pure: bool,
    limit: int | None = None,
    representative: bool = False,
) -> dict[str, Any]:
    manifest = load_manifest()
    all_cases = manifest["cases"]
    if representative:
        cases = []
        seen_categories: set[str] = set()
        for case in all_cases:
            category = str(case.get("category", ""))
            if category in seen_categories:
                continue
            seen_categories.add(category)
            cases.append(case)
    elif limit is not None:
        cases = all_cases[:limit]
    else:
        cases = all_cases
    # ``tracecoder.py`` loads tracecoder_llm.env at import time.  Environment
    # variables set below therefore cannot change its already-created config;
    # use the adapter's explicit runtime override to keep this benchmark
    # deterministic and offline for both baseline and CodeArts-B comparisons.
    tracecoder.configure_llm(mode="required" if mode == "intelligent" else "off")
    env = {
        "CODEARTS_STRATEGY_MODE": "required" if mode in {"codearts", "intelligent"} else "off",
        "CODEARTS_STRATEGY_POLICY": policy,
        "CODEARTS_STRATEGY_TIMEOUT_S": str(timeout_s),
        # The benchmark measures B and the closed-loop safety behavior.  D's
        # optional online LLM must be disabled here; otherwise importing the
        # repository-local tracecoder_llm.env can silently turn an offline
        # Mock run into a network benchmark.
        "TRACECODER_LLM_MODE": "required" if mode == "intelligent" else "off",
        "RIA_PLANNER_ENGINE": "llm" if mode == "intelligent" else "rule",
    }
    if model:
        env["CODEARTS_STRATEGY_MODEL"] = model
    if pure:
        env["CODEARTS_CLI_PURE"] = "1"
    records: list[dict[str, Any]] = []
    with temporary_environment(env):
        if mode == 'intelligent':
            # Recreate D's provider after the benchmark environment is applied so
            # timeout/retry values are honored for this bounded real-model run.
            from modules.evaluator.tracecoder.llm_provider import LLMConfig, LLMProvider
            tracecoder.configure_llm(mode='required', provider=LLMProvider(LLMConfig.from_env()))
        for case in cases:
            for repeat in range(1, repeats + 1):
                records.append(_run_one(case, repeat))
    return {
        "schema_version": "closed-loop-benchmark-report.v1",
        "benchmark": manifest["name"],
        "mode": mode,
        "backend": "mock",
        "policy": policy,
        "repeats": repeats,
        "summary": _summarize(records, cases),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "codearts", "intelligent"), default="baseline")
    parser.add_argument("--compare", action="store_true", help="依次运行baseline和codearts并输出对照报告")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--policy", choices=("planner", "quality", "max"), default="quality")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--limit", type=int, default=None, help="run only first N cases")
    parser.add_argument("--representative", action="store_true", help="run first case from each category")
    parser.add_argument("--pure", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "closed_loop_benchmark.json")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    if args.limit is not None and args.representative:
        parser.error("limit and representative are mutually exclusive")
    modes = ["baseline", "codearts"] if args.compare else [args.mode]
    if args.compare and os.environ.get("CODEARTS_BENCHMARK_ALLOW_LIVE") != "1":
        parser.error("--compare 会调用真实 CodeArts；请先设置 CODEARTS_BENCHMARK_ALLOW_LIVE=1")
    reports = [
        run_benchmark(
            mode=mode,
            repeats=args.repeats,
            policy=args.policy,
            model=args.model,
            timeout_s=args.timeout_s,
            pure=args.pure,
            limit=args.limit,
            representative=args.representative,
        )
        for mode in modes
    ]
    output = {
        "schema_version": "closed-loop-benchmark-suite-report.v1",
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summaries": [report["summary"] for report in reports]}, ensure_ascii=False, indent=2))
    return 0 if all(report["summary"]["pass_rate"] == 1.0 for report in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
