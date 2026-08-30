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
import hashlib
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

from demo.scenarios import get_scenario  # noqa: E402
from demo.server import (  # noqa: E402
    _DemoStrategyAdapter,
    _IsolatedTraceCoderAdapter,
)
from integration.adapters import intent, perception, strategy, tracecoder  # noqa: E402
from integration.config.local_env import temporary_local_env  # noqa: E402
from integration.adapters.executor import ExecutorAdapter  # noqa: E402
from integration.contract_validation import assert_contract  # noqa: E402
from integration.pipeline import run_pipeline  # noqa: E402
from modules.executor.mock_backend import MockBackend  # noqa: E402
from tests.e2e.test_closed_loop_acceptance import (  # noqa: E402
    _load_json,
    _load_scene,
    _tracecoder_fixture_adapters,
)


DEFAULT_ACTIONS = [
    "detect_object",
    "move_to_object",
    "grasp",
    "move_to_target",
    "release",
]

PROTOCOL_VERSION = "1.0.0"
VARIANT_AUTO = "auto"
AVAILABLE_VARIANTS = (
    "V0_RULE_BASELINE",
    "V1_CODEARTS_B",
    "V2_FULL_NO_D",
    "V4_FULL",
)


def _default_variant(mode: str) -> str:
    return {
        "baseline": "V0_RULE_BASELINE",
        "codearts": "V1_CODEARTS_B",
        "intelligent": "V4_FULL",
    }[mode]


def _resolve_variant(mode: str, variant_id: str | None) -> str:
    variant = variant_id or _default_variant(mode)
    if variant not in AVAILABLE_VARIANTS:
        raise ValueError(f"当前运行器不支持变体: {variant}")
    compatible = {
        "V0_RULE_BASELINE": {"baseline"},
        "V1_CODEARTS_B": {"codearts"},
        "V2_FULL_NO_D": {"intelligent"},
        "V4_FULL": {"intelligent"},
    }
    if mode not in compatible[variant]:
        raise ValueError(f"变体 {variant} 与运行模式 {mode} 不匹配")
    return variant


def _stable_seed(case: dict[str, Any]) -> int:
    value = case.get("seed")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    digest = hashlib.sha256(str(case["id"]).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 2_147_483_647


def _new_experiment_id() -> str:
    return time.strftime("exp-%Y%m%dT%H%M%SZ", time.gmtime())


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


@contextmanager
def benchmark_environment(
    values: dict[str, str], *, load_online_credentials: bool
) -> Iterator[None]:
    """Scope local provider files and benchmark overrides to one run."""
    if load_online_credentials:
        with temporary_local_env("codearts.env", ".env", "tracecoder_llm.env"):
            with temporary_environment(values):
                yield
        return
    with temporary_environment(values):
        yield


def load_manifest(path: Path = BENCHMARK_PATH) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
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


def _run_demo_case(
    case: dict[str, Any],
    request_id: str,
    variant_id: str,
) -> dict[str, Any]:
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
    }
    if variant_id == "V4_FULL":
        adapters["tracecoder"] = _IsolatedTraceCoderAdapter()
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


def _run_acceptance_case(case: dict[str, Any], variant_id: str) -> dict[str, Any]:
    source = _load_json(ACCEPTANCE_ROOT / case["path"])
    intelligent = os.getenv("RIA_PLANNER_ENGINE", "rule").strip().lower() == "llm"
    if source.get("mode") == "tracecoder_fixture":
        # The fixture deliberately produces one failed execution that D can
        # repair.  V0/V1/V2 must stop after the first attempt; V4 keeps D.
        fixture_adapters = _tracecoder_fixture_adapters()
        if variant_id != "V4_FULL":
            fixture_adapters.pop("tracecoder", None)
        perception_input = {
            "schema_version": "perception.v1",
            "scene_id": "acceptance_tracecoder_fixture",
            "objects": [],
        }
        result = run_pipeline(
            perception_input,
            source["instruction"],
            fixture_adapters,
            engine="llm" if intelligent else None,
            request_id=source.get("case_id"),
        )
        return {
            "result": result,
            "source_case": source,
            "backend_snapshot": None,
        }
    scene = _load_scene(source)
    failures = (source.get("executor") or {}).get("failures")
    backend = MockBackend.from_perception(scene, failures=failures)
    adapters = {
        "intent": intent,
        "strategy": strategy,
        "executor": ExecutorAdapter(backend),
    }
    if variant_id == "V4_FULL":
        adapters["tracecoder"] = tracecoder
    result = run_pipeline(
        scene,
        source["instruction"],
        adapters,
        engine="llm" if intelligent else source.get("engine", "rule"),
        request_id=source.get("case_id"),
    )
    return {
        "result": result,
        "source_case": source,
        "backend_snapshot": backend.snapshot() if backend is not None else None,
    }


def _extract_feedback_status(feedback: Any) -> str | None:
    if not isinstance(feedback, dict) or not feedback:
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


def _expected_binding(case: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in case:
            return case[name]
    expected = case.get("expect")
    if isinstance(expected, dict):
        for name in names:
            if name in expected:
                return expected[name]
    return None


def _normalize_ids(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    return None


def _infer_failure_class(result: dict[str, Any], expected_status: str) -> str | None:
    actual_status = result.get("status")
    if actual_status == "SUCCEEDED":
        return None
    if actual_status == "SAFE_STOP":
        return "safety_stop"
    execution = result.get("execution") or {}
    if expected_status in {"BLOCKED", "NEEDS_CLARIFICATION"} and not execution:
        return "intent_or_strategy_block"
    if execution.get("status") in {"FAILED", "ERROR"}:
        return "execution_failure"
    strategy = result.get("strategy") or {}
    if strategy.get("provider") == "huaweicloud-codearts-agent" and strategy.get("fallback"):
        return "provider_fallback"
    stop_reason = result.get("stop_reason")
    return str(stop_reason).lower() if stop_reason else "unknown_failure"


def _close_pose(left: Any, right: Any, tolerance: float = 1e-6) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    return all(
        isinstance(left.get(axis), (int, float))
        and isinstance(right.get(axis), (int, float))
        and abs(float(left[axis]) - float(right[axis])) <= tolerance
        for axis in ("x", "y", "z")
    )


def _verify_world_state(
    case: dict[str, Any],
    result: dict[str, Any],
    snapshot: Any,
) -> bool | None:
    """Verify the final Mock state when the case declares a checkable goal."""

    expected = case.get("expected_world_state")
    if not isinstance(expected, dict):
        return None
    expected_type = expected.get("type")
    actual_status = result.get("status")
    execution = result.get("execution") or {}
    if expected_type == "not_executed":
        return actual_status == "BLOCKED" and not execution
    if expected_type == "execution_failed":
        return actual_status == "FAILED"
    if expected_type == "safe_stop":
        return (
            actual_status == "SAFE_STOP"
            and isinstance(snapshot, dict)
            and snapshot.get("safe_stopped") is True
        )
    if not isinstance(snapshot, dict):
        return None
    object_id = expected.get("object_id")
    raw_objects = snapshot.get("objects")
    if isinstance(raw_objects, list):
        objects = {
            item.get("id"): item
            for item in raw_objects
            if isinstance(item, dict) and item.get("id")
        }
        robot = snapshot.get("robot") or {}
        if expected_type == "held":
            return actual_status == "SUCCEEDED" and robot.get("gripper_object") == object_id
        if expected_type not in {"placed", "stacked"}:
            return None
        item = objects.get(object_id)
        return (
            actual_status == "SUCCEEDED"
            and isinstance(item, dict)
            and item.get("container") == expected.get("destination_id")
        )
    if not isinstance(raw_objects, dict):
        return None
    objects = raw_objects
    if expected_type == "held":
        return actual_status == "SUCCEEDED" and snapshot.get("held_id") == object_id
    if expected_type not in {"placed", "stacked"}:
        return None
    if actual_status != "SUCCEEDED" or object_id not in objects:
        return False
    object_pose = objects[object_id].get("pose")
    destination_id = expected.get("destination_id")
    destination = objects.get(destination_id) if destination_id else None
    if not isinstance(destination, dict):
        return False
    destination_pose = destination.get("pose")
    if expected_type == "placed":
        return _close_pose(object_pose, destination_pose)
    if not isinstance(object_pose, dict) or not isinstance(destination_pose, dict):
        return False
    return (
        abs(float(object_pose.get("x", 0.0)) - float(destination_pose.get("x", 0.0))) <= 1e-6
        and abs(float(object_pose.get("y", 0.0)) - float(destination_pose.get("y", 0.0))) <= 1e-6
        and float(object_pose.get("z", 0.0)) > float(destination_pose.get("z", 0.0))
    )


def _attach_protocol_fields(
    record: dict[str, Any],
    case: dict[str, Any],
    *,
    experiment_id: str,
    protocol_version: str,
    variant_id: str,
    git_sha: str | None,
    manifest: str,
    model: str | None,
    policy: str,
) -> dict[str, Any]:
    """Add the fields needed to compare, audit, and replay one run."""

    task = record.get("task") or {}
    strategy_info = record.get("strategy") or {}
    execution = record.get("execution") or {}
    feedback = record.get("feedback") or {}
    expected_status = record.get("expected_status", case.get("expected_status"))
    expected_target_ids = _normalize_ids(
        _expected_binding(case, ("expected_target_ids", "target_ids", "expected_target_id"))
    )
    expected_destination_ids = _normalize_ids(
        _expected_binding(
            case,
            ("expected_destination_ids", "destination_ids", "expected_destination_id"),
        )
    )
    actual_target_ids = _normalize_ids(task.get("target_ids"))
    actual_destination_id = task.get("destination_id")
    actual_destination_ids = (
        [actual_destination_id] if isinstance(actual_destination_id, str) else None
    )
    target_exact_match = (
        actual_target_ids == expected_target_ids
        if expected_target_ids is not None
        else None
    )
    destination_exact_match = (
        actual_destination_ids == expected_destination_ids
        if expected_destination_ids is not None
        else None
    )
    world_state_verified = record.get("world_state_verified")
    if world_state_verified is None:
        for candidate in (execution, feedback):
            value = candidate.get("world_state_verified")
            if isinstance(value, bool):
                world_state_verified = value
                break
    execution_status = execution.get("status")
    provider = strategy_info.get("provider")
    provider_present = bool(provider) or bool(strategy_info.get("fallback"))
    feedback_present = any(
        feedback.get(key) is not None
        for key in ("status", "execution_status", "stop_reason", "retryable", "final_passed")
    ) or bool(feedback.get("safety_stop"))
    trace_complete = (
        bool(task.get("task_id"))
        and bool(strategy_info)
        and bool(execution)
        and feedback_present
    )
    record.update(
        {
            "experiment_id": experiment_id,
            "protocol_version": protocol_version,
            "variant_id": variant_id,
            "git_sha": git_sha,
            "manifest": manifest,
            "seed": _stable_seed(case),
            "expected_world_state": case.get("expected_world_state"),
            "model": model or strategy_info.get("model"),
            "policy": policy,
            "target_id_expected": expected_target_ids,
            "target_id_actual": actual_target_ids,
            "destination_id_expected": expected_destination_ids,
            "destination_id_actual": actual_destination_id,
            "target_exact_match": target_exact_match,
            "destination_exact_match": destination_exact_match,
            "strategy_contract_passed": bool(strategy_info.get("contract_valid")),
            "code_null": bool(strategy_info.get("code_null")),
            "provider_calls": int(record.get("provider_calls", 1 if provider == "huaweicloud-codearts-agent" else 0)),
            "provider_attempts": int(record.get("provider_attempts", 1 if provider_present else 0)),
            "provider_error_class": record.get("provider_error_class"),
            "execution_attempts": int(record.get("execution_attempts", record.get("attempt_count", 0))),
            "execution_status": execution_status,
            "failure_class": record.get("failure_class")
            or (None if record.get("actual_status") == "SUCCEEDED" else _infer_failure_class(record, expected_status)),
            "safe_stop_expected": expected_status == "SAFE_STOP",
            "safe_stop_actual": record.get("actual_status") == "SAFE_STOP",
            "unsafe_execution": expected_status in {"BLOCKED", "NEEDS_CLARIFICATION"}
            and bool(execution_status),
            "world_state_verified": world_state_verified,
            "trace_complete": trace_complete,
            "manual_intervention_count": int(record.get("manual_intervention_count", 0)),
            "raw_evidence_path": record.get("evidence_path") or None,
        }
    )
    return record


def _run_one(case: dict[str, Any], repeat: int, variant_id: str) -> dict[str, Any]:
    request_id = f"benchmark-{case['id']}-r{repeat}"
    started = time.perf_counter()
    if case["source"] in {"demo", "demo_override"}:
        payload = _run_demo_case(case, request_id, variant_id)
    elif case["source"] == "acceptance":
        payload = _run_acceptance_case(case, variant_id)
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
    tracecoder_invoked = bool(feedback_provenance) and feedback_provenance.get("source") != "tracecoder_skipped"
    world_state_verified = _verify_world_state(
        case,
        result,
        payload.get("backend_snapshot")
        or (result.get("execution") or {}).get("final_state"),
    )
    strategy_steps = (result.get("strategy") or {}).get("steps") or []
    c_internal_recovery = any(
        isinstance(step.get("on_failure"), dict) for step in strategy_steps
    )
    d_repair_attempted = bool(result.get("retry_count", 0) > 0)
    d_repair_succeeded = (
        d_repair_attempted and result.get("status") == "SUCCEEDED"
    )
    return {
        "case_id": case["id"],
        "category": case["category"],
        "source": case["source"],
        "repeat": repeat,
        "request_id": request_id,
        "run_id": request_id,
        "expected_status": expected_status,
        "actual_status": result.get("status"),
        "passed": passed,
        "status_passed": status_passed,
        "retry_passed": retry_passed,
        "elapsed_ms": elapsed_ms,
        "backend": "mock",
        "failure_class": None,
        "original_error": None,
        "evidence_path": None,
        "c_internal_recovery": c_internal_recovery,
        "d_repair_attempted": d_repair_attempted,
        "d_repair_succeeded": d_repair_succeeded,
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
        "world_state_verified": world_state_verified,
        "execution": {
            "status": execution.get("status"),
            "total_duration_ms": execution.get("total_duration_ms"),
            "step_count": len(execution.get("steps") or []),
            "safety_events": execution.get("safety_events") or [],
            "world_state_verified": execution.get("world_state_verified"),
            "final_state": execution.get("final_state"),
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
    safe_stop_records = [item for item in records if item["expected_status"] == "SAFE_STOP"]
    valid_task_records = [item for item in records if item["expected_status"] == "SUCCEEDED"]
    binding_records = [
        item
        for item in records
        if item.get("target_exact_match") is not None
        or item.get("destination_exact_match") is not None
    ]
    dangerous_records = [
        item
        for item in records
        if item["expected_status"] in {"BLOCKED", "NEEDS_CLARIFICATION"}
    ]
    world_state_records = [
        item for item in records if isinstance(item.get("world_state_verified"), bool)
    ]
    trace_complete_count = sum(1 for item in records if item.get("trace_complete"))
    d_repair_case_ids = {
        case["id"]
        for case in cases
        if case["category"] == "recoverable_failure"
        and case.get("requires_d_repair", False)
    }
    repair_records = [item for item in records if item["case_id"] in d_repair_case_ids]
    transport_records = [
        item for item in records if item.get("failure_class") == "transport_auth"
    ]
    business_records = [
        item for item in records if item.get("failure_class") != "transport_auth"
    ]
    contract_failure_records = [
        item for item in business_records if item["strategy"]["contract_valid"] is False
    ]
    safe_stop_records_all = [
        item for item in business_records if item["actual_status"] == "SAFE_STOP"
    ]
    c_internal_records = [item for item in records if item.get("c_internal_recovery")]
    d_repair_attempt_records = [item for item in records if item.get("d_repair_attempted")]
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
    end_to_end_latency_stats = latency_stats(end_to_end_latencies)
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
        "pass_rate": rate(sum(1 for item in business_records if item["passed"]), len(business_records)),
        "transport_failures": len(transport_records),
        "business_failures": sum(1 for item in business_records if not item["passed"]),
        "contract_failures": len(contract_failure_records),
        "safety_failures": len(safe_stop_records_all),
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
        "end_to_end_latency_ms": end_to_end_latency_stats,
        "fallback_count": sum(1 for item in records if item["strategy"]["fallback"]),
        "execution_attempts": len(execution_records),
        "execution_success_rate": rate(sum(1 for item in execution_records if item["execution"]["status"] == "SUCCEEDED"), len(execution_records)),
        "valid_task_success_rate": rate(
            sum(1 for item in valid_task_records if item["actual_status"] == "SUCCEEDED"),
            len(valid_task_records),
        ),
        "semantic_exact_match_rate": rate(
            sum(
                1
                for item in binding_records
                if all(
                    value is True
                    for value in (
                        item.get("target_exact_match"),
                        item.get("destination_exact_match"),
                    )
                    if value is not None
                )
            ),
            len(binding_records),
        ),
        "unsafe_false_execution_rate": rate(
            sum(1 for item in dangerous_records if item.get("unsafe_execution")),
            len(dangerous_records),
        ),
        "repair_cases": len(repair_records),
        "repair_success_rate": rate(sum(1 for item in repair_records if item["actual_status"] == "SUCCEEDED" and item["retry_count"] > 0), len(repair_records)),
        "c_internal_recovery_rate": rate(
            sum(1 for item in c_internal_records if item["actual_status"] == "SUCCEEDED"),
            len(c_internal_records),
        ),
        "d_repair_success_rate": rate(
            sum(1 for item in d_repair_attempt_records if item["actual_status"] == "SUCCEEDED"),
            len(d_repair_attempt_records),
        ),
        "recoverable_failure_recovery_rate": rate(
            sum(
                1
                for item in records
                if item["category"] == "recoverable_failure"
                and item["actual_status"] == "SUCCEEDED"
            ),
            sum(1 for item in records if item["category"] == "recoverable_failure"),
        ),
        "safe_stop_cases": len(safe_stop_records),
        "safe_stop_correct_rate": rate(sum(1 for item in safe_stop_records if item["actual_status"] == "SAFE_STOP"), len(safe_stop_records)),
        "false_success_rate": rate(
            sum(
                1
                for item in world_state_records
                if item["actual_status"] == "SUCCEEDED"
                and item["world_state_verified"] is False
            ),
            sum(1 for item in world_state_records if item["actual_status"] == "SUCCEEDED"),
        ),
        "trace_completeness_rate": rate(trace_complete_count, len(records)),
        "manual_intervention_count": sum(
            int(item.get("manual_intervention_count", 0) or 0) for item in records
        ),
        "safety_event_runs": sum(1 for item in records if item["execution"]["safety_events"]),
        "p50_latency_ms": end_to_end_latency_stats.get("p50_ms"),
        "p95_latency_ms": end_to_end_latency_stats.get("p95_ms"),
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


def _partial_path(output: Path | None) -> Path | None:
    if output is None:
        return None
    return Path(str(output) + ".partial.jsonl")


def _load_partial(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        records.append(json.loads(line))
    return records


def _append_partial(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _run_one_remote(
    case: dict[str, Any],
    repeat: int,
    remote: dict[str, Any] | None,
    transport_retries: int,
) -> dict[str, Any]:
    """远程 Isaac 批量后端：复用编排器远程通道逐样本闭环执行。"""
    from tools.orchestrate.orchestrator import orchestrate
    from tools.orchestrate.types import OrchestrationConfig

    remote = remote or {}
    config = OrchestrationConfig(
        instruction=case["instruction"],
        scene_id=case["scene_id"],
        server=str(remote.get("server", "")),
        port=int(remote.get("port", 5122)),
        user=str(remote.get("user", "")),
        remote_base=str(remote.get("remote_base", "")),
        auth_mode="batch",
        key_path=None,
        transport_retries=transport_retries,
        backend="remote_isaac",
        out_dir=None,
    )
    started = time.perf_counter()
    result = orchestrate(
        config,
        command_runner=remote.get("command_runner"),
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    request_id = f"benchmark-{case['id']}-r{repeat}"
    actual_status = "SUCCEEDED" if result.status == "SUCCEEDED" else "FAILED"
    expected_status = case["expected_status"]
    passed = result.status == "SUCCEEDED" and actual_status == expected_status
    return {
        "case_id": case["id"],
        "category": case.get("category", ""),
        "source": case.get("source", "remote_isaac"),
        "repeat": repeat,
        "request_id": request_id,
        "run_id": request_id,
        "expected_status": expected_status,
        "actual_status": actual_status,
        "passed": passed,
        "status_passed": actual_status == expected_status,
        "retry_passed": True,
        "elapsed_ms": elapsed_ms,
        "backend": "remote_isaac",
        "failure_class": result.failure_class,
        "original_error": None,
        "evidence_path": str(result.artifact_paths.get("execution") or ""),
        "c_internal_recovery": False,
        "d_repair_attempted": False,
        "d_repair_succeeded": None,
        "task": {"status": "READY" if passed else "FAILED"},
        "strategy": {
            "mode": "remote_isaac",
            "provider": None,
            "contract_valid": passed,
            "code_null": True,
            "actions": [],
            "fallback": False,
            "latency_ms": None,
        },
        "intent": {
            "requested_engine": None,
            "actual_engine": None,
            "llm_call_attempted": False,
            "llm_call_succeeded": False,
            "fallback_used": False,
            "model": None,
        },
        "feedback_provenance": {
            "source": None,
            "mode": None,
            "fallback": False,
            "tracecoder_invoked": False,
            "llm_stats": {},
        },
        "execution": {
            "status": "SUCCEEDED" if passed else "FAILED",
            "safety_events": [],
        },
        "feedback": {"status": "D_ACCEPTED" if passed else "D_REJECTED"},
        "tracecoder_invoked": False,
        "tracecoder_requests": 0,
        "retry_count": 0,
        "d_repair_required": bool(case.get("requires_d_repair", False)),
        "attempt_count": 1,
        "stop_reason": "EXECUTION_SUCCEEDED" if passed else result.failure_class,
        "signature": [actual_status],
        "replay": {"strategy": None, "execution": None},
    }


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
    manifest_path: Path = BENCHMARK_PATH,
    transport_retries: int = 2,
    backend: str = "mock",
    interactive_remote: bool = False,
    resume: bool = False,
    output: Path | None = None,
    remote: dict[str, Any] | None = None,
    variant_id: str | None = None,
    experiment_id: str | None = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> dict[str, Any]:
    variant_id = _resolve_variant(mode, variant_id)
    experiment_id = experiment_id or _new_experiment_id()
    manifest = load_manifest(manifest_path)
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
    d_enabled = variant_id == "V4_FULL"
    tracecoder.configure_llm(
        mode="required" if mode == "intelligent" and d_enabled else "off"
    )
    env = {
        "CODEARTS_STRATEGY_MODE": "required" if mode in {"codearts", "intelligent"} else "off",
        "CODEARTS_STRATEGY_POLICY": policy,
        "CODEARTS_STRATEGY_TIMEOUT_S": str(timeout_s),
        # The benchmark measures B and the closed-loop safety behavior.  D's
        # optional online LLM must be disabled here; otherwise importing the
        # repository-local tracecoder_llm.env can silently turn an offline
        # Mock run into a network benchmark.
        "TRACECODER_LLM_MODE": "required" if mode == "intelligent" and d_enabled else "off",
        "RIA_PLANNER_ENGINE": "llm" if mode == "intelligent" else "rule",
    }
    if model:
        env["CODEARTS_STRATEGY_MODEL"] = model
    if pure:
        env["CODEARTS_CLI_PURE"] = "1"
    if backend == "remote_isaac":
        env["CODEARTS_STRATEGY_MODE"] = "off"
    from tools.reporting.report_models import collect_metadata

    metadata = collect_metadata(
        profile=mode,
        manifest_path=str(manifest_path),
        repeats=repeats,
        argv=sys.argv,
    )
    records: list[dict[str, Any]] = []
    partial_path = _partial_path(output)
    run_config = {
        "mode": mode,
        "repeats": repeats,
        "policy": policy,
        "model": model,
        "timeout_s": timeout_s,
        "pure": pure,
        "limit": limit,
        "representative": representative,
        "manifest": str(manifest_path.resolve()),
        "transport_retries": transport_retries,
        "backend": backend,
        "experiment_id": experiment_id,
        "protocol_version": protocol_version,
        "variant_id": variant_id,
    }
    completed: set[tuple[str, str]] = set()
    if resume and partial_path and partial_path.exists():
        loaded = _load_partial(partial_path)
        if any(item.get("run_config") != run_config for item in loaded):
            raise ValueError(
                "partial benchmark 配置与当前运行不一致；请使用新的 output，"
                "或删除对应的 .partial.jsonl 后重新开始"
            )
        records.extend(loaded)
        completed = {(item["case_id"], item["run_id"]) for item in loaded}
    elif partial_path and partial_path.exists():
        partial_path.unlink()
    with benchmark_environment(
        env,
        load_online_credentials=mode in {"codearts", "intelligent"},
    ):
        if mode == "intelligent":
            # The online .env is loaded after this module is imported.  Clear
            # cached A settings so the live run cannot reuse offline config
            # from an earlier call in the same Python process.
            from modules.intent_understanding.robot_intent_agent.config.settings import get_settings
            from modules.intent_understanding import adapter as intent_core
            get_settings.cache_clear()
            intent_core._LLM_PLANNER_CACHE.clear()
        if mode == 'intelligent' and d_enabled:
            # Recreate D's provider after the benchmark environment is applied so
            # timeout/retry values are honored for this bounded real-model run.
            from modules.evaluator.tracecoder.llm_provider import LLMConfig, LLMProvider
            tracecoder.configure_llm(mode='required', provider=LLMProvider(LLMConfig.from_env()))
        for case in cases:
            for repeat in range(1, repeats + 1):
                request_id = f"benchmark-{case['id']}-r{repeat}"
                if (case["id"], request_id) in completed:
                    continue
                record = (
                    _run_one_remote(case, repeat, remote, transport_retries)
                    if backend == "remote_isaac"
                    else _run_one(case, repeat, variant_id)
                )
                record = _attach_protocol_fields(
                    record,
                    case,
                    experiment_id=experiment_id,
                    protocol_version=protocol_version,
                    variant_id=variant_id,
                    git_sha=metadata.git_sha,
                    manifest=str(manifest_path.resolve()),
                    model=model,
                    policy=policy,
                )
                record["run_config"] = run_config
                records.append(record)
                if partial_path is not None:
                    _append_partial(partial_path, record)
    return {
        "schema_version": "closed-loop-benchmark-report.v1",
        "benchmark": manifest["name"],
        "mode": mode,
        "variant_id": variant_id,
        "experiment_id": experiment_id,
        "protocol_version": protocol_version,
        "backend": backend,
        "policy": policy,
        "repeats": repeats,
        "metadata": {
            "git_sha": metadata.git_sha,
            "profile": metadata.profile,
            "manifest_path": metadata.manifest_path,
            "repeats": metadata.repeats,
            "timestamp": metadata.timestamp,
            "command": list(metadata.command),
        },
        "summary": _summarize(records, cases),
        "records": records,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "codearts", "intelligent"), default="baseline")
    parser.add_argument("--compare", action="store_true", help="依次运行baseline和codearts并输出对照报告")
    parser.add_argument(
        "--variant",
        choices=(VARIANT_AUTO, *AVAILABLE_VARIANTS),
        default=VARIANT_AUTO,
        help="实验变体；默认根据 --mode 自动选择",
    )
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="实验批次编号；不传则自动生成，并写入每条记录",
    )
    parser.add_argument(
        "--protocol-version",
        default=PROTOCOL_VERSION,
        help="统一实验协议版本",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--policy", choices=("planner", "quality", "max"), default="quality")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--limit", type=int, default=None, help="run only first N cases")
    parser.add_argument("--representative", action="store_true", help="run first case from each category")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=BENCHMARK_PATH,
        help="benchmark manifest; defaults to testdata/benchmark/closed_loop_cases.json",
    )
    parser.add_argument("--pure", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "closed_loop_benchmark.json")
    parser.add_argument("--transport-retries", type=int, default=2)
    parser.add_argument("--resume", action="store_true", help="读取 <output>.partial.jsonl 续跑")
    parser.add_argument(
        "--backend", choices=("mock", "remote_isaac"), default="mock"
    )
    parser.add_argument(
        "--interactive-remote",
        action="store_true",
        help="仅单次人工冒烟允许交互认证；统计模式默认强制 BatchMode",
    )
    parser.add_argument("--server", default="")
    parser.add_argument("--port", type=int, default=5122)
    parser.add_argument("--user", default="")
    parser.add_argument("--remote-base", default="")
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("repeats must be positive")
    if args.limit is not None and args.limit < 1:
        parser.error("limit must be positive")
    if args.limit is not None and args.representative:
        parser.error("limit and representative are mutually exclusive")
    if args.variant != VARIANT_AUTO:
        if args.compare:
            parser.error("--compare 不能同时指定单一 --variant")
        try:
            _resolve_variant(args.mode, args.variant)
        except ValueError as exc:
            parser.error(str(exc))
    if args.backend == "remote_isaac":
        if not args.interactive_remote:
            parser.error(
                "--backend remote_isaac 需要显式 --interactive-remote；"
                "统计模式默认强制 BatchMode，隐式远程执行一律拒绝"
            )
        if not (args.server and args.user and args.remote_base):
            parser.error(
                "--interactive-remote 仍需 --server/--user/--remote-base 完整参数"
            )
    remote = {
        "server": args.server,
        "port": args.port,
        "user": args.user,
        "remote_base": args.remote_base,
    }
    modes = ["baseline", "codearts"] if args.compare else [args.mode]
    if args.compare and os.environ.get("CODEARTS_BENCHMARK_ALLOW_LIVE") != "1":
        parser.error("--compare 会调用真实 CodeArts；请先设置 CODEARTS_BENCHMARK_ALLOW_LIVE=1")
    experiment_id = args.experiment_id or _new_experiment_id()
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
            manifest_path=args.manifest,
            transport_retries=args.transport_retries,
            backend=args.backend,
            interactive_remote=args.interactive_remote,
            resume=args.resume,
            output=args.output,
            remote=remote,
            variant_id=(None if args.variant == VARIANT_AUTO else args.variant),
            experiment_id=experiment_id,
            protocol_version=args.protocol_version,
        )
        for mode in modes
    ]
    output = {
        "schema_version": "closed-loop-benchmark-suite-report.v1",
        "reports": reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    partial_path = _partial_path(args.output)
    if partial_path is not None:
        try:
            partial_path.unlink()
        except FileNotFoundError:
            pass
    print(json.dumps({"output": str(args.output), "summaries": [report["summary"] for report in reports]}, ensure_ascii=False, indent=2))
    return 0 if all(report["summary"]["pass_rate"] == 1.0 for report in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())
