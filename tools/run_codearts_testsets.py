"""Run repeatable CodeArts strategy test sets.

Offline mode checks the adapter contract without network calls.  ``--live``
switches only the positive cases to the real CodeArts CLI and records provider
provenance, critic passes, contract failures, fallback and repeat stability.

Examples::

    python tools/run_codearts_testsets.py --set normal_quality
    python tools/run_codearts_testsets.py --set normal_quality --live \
        --policy quality --limit 2 --pure
    python tools/run_codearts_testsets.py --live --policy quality \
        --repeats 3 --transport-retries 2 --resume --pure
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TESTSET_ROOT = ROOT / "testdata" / "codearts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.config.local_env import temporary_local_env  # noqa: E402

from integration.adapters import strategy as strategy_adapter  # noqa: E402
from modules.strategy_generation.codearts_agent import validate_strategy  # noqa: E402


POLICY_CRITIC_PASSES = {"planner": 0, "quality": 1, "max": 2}

# The online acceptance contract is intentionally limited to the four normal
# scale sets.  Legacy/offline sets remain available through ``--set`` and are
# still included by the offline default.
LIVE_SCALE_TESTSETS = (
    "normal_scale_functional",
    "normal_scale_semantic",
    "normal_scale_safety",
    "normal_scale_stability",
    "normal_scale_resilience",
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


@contextmanager
def _provider_environment(values: dict[str, str], *, live: bool) -> Iterator[None]:
    """Keep live CodeArts credentials scoped to one testset case."""
    if live:
        with temporary_local_env("codearts.env"):
            with _temporary_environment(values):
                yield
        return
    with _temporary_environment(values):
        yield


def load_testset(name: str) -> dict[str, Any]:
    path = TESTSET_ROOT / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"测试集不存在: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    _validate_testset(document, path)
    return document


def list_testsets() -> list[str]:
    return sorted(path.stem for path in TESTSET_ROOT.glob("*.json"))


def _validate_testset(document: Any, path: Path) -> None:
    if not isinstance(document, dict) or document.get("schema_version") != "codearts-testset.v1":
        raise ValueError(f"{path} 缺少 codearts-testset.v1")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path} cases 必须是非空数组")
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError(f"{path} cases[{index}] 缺少 id")
        task = case.get("task")
        expect = case.get("expect")
        if not isinstance(task, dict) or not isinstance(expect, dict):
            raise ValueError(f"{path} cases[{index}] 必须包含 task/expect")
        if expect.get("success") not in {True, False} or expect.get("blocked") not in {True, False}:
            raise ValueError(f"{path} cases[{index}] expect.success/blocked 必须是布尔值")
        if "actions" in expect and not (
            isinstance(expect["actions"], list)
            and all(isinstance(action, str) and action for action in expect["actions"])
        ):
            raise ValueError(f"{path} cases[{index}] expect.actions 必须是字符串数组")
        for field in ("target_id", "destination_id"):
            if field in expect and not isinstance(expect[field], str):
                raise ValueError(f"{path} cases[{index}] expect.{field} 必须是字符串")


def _policy_name(document: dict[str, Any], requested: str | None) -> str:
    policy = requested or document.get("default_policy", "planner")
    if policy not in POLICY_CRITIC_PASSES:
        raise ValueError(f"不支持的 CodeArts policy: {policy}")
    return policy


def _local_provider_result(task: dict[str, Any]) -> dict[str, Any]:
    """Build a valid candidate for deterministic critic fault injection."""
    with _temporary_environment(
        {"CODEARTS_STRATEGY_MODE": "off", "CODEARTS_STRATEGY_POLICY": "planner"}
    ):
        candidate = strategy_adapter.run(task)
    candidate.update(
        {
            "mode": "codearts_agent",
            "provenance": {
                "provider": "huaweicloud-codearts-agent",
                "transport": "codearts-cli",
            },
        }
    )
    return {
        "success": True,
        "strategy": candidate,
        "error": None,
        "trace": candidate["provenance"],
    }


def _fault_setup(case: dict[str, Any], task: dict[str, Any], client_class: Any) -> None:
    fault = case.get("fault") or {}
    error = fault.get("error", "CODEARTS_CLI_TIMEOUT")
    failure = {
        "success": False,
        "strategy": None,
        "review": None,
        "error": error,
        "trace": {
            "provider": "huaweicloud-codearts-agent",
            "transport": "codearts-cli",
        },
    }
    if fault.get("phase") == "critic":
        client_class.return_value.generate.return_value = _local_provider_result(task)
        client_class.return_value.review.return_value = failure
    else:
        client_class.return_value.generate.return_value = failure


def _signature(output: dict[str, Any]) -> tuple[Any, ...]:
    return (
        bool(output.get("success")),
        bool(output.get("blocked")),
        output.get("mode"),
        output.get("strategy_policy"),
        tuple(step.get("action") for step in output.get("steps", [])),
        len(output.get("critics") or []),
    )


def _provider_error_text(output: dict[str, Any]) -> str:
    provenance = output.get("provenance") or {}
    return " ".join(
        str(value)
        for value in (
            output.get("provider_error"),
            output.get("error"),
            *(output.get("blocking_reasons") or []),
            provenance.get("error"),
        )
        if value
    ).lower()


def _is_transient_provider_error(output: dict[str, Any]) -> bool:
    """Return whether a failed live provider call is safe to retry.

    Semantic validation failures and expected safety blocks are deliberately
    excluded.  Only transport/quota/temporary-service signals are retried;
    this keeps the batch runner from hiding a genuine contract regression.
    """
    if output.get("success"):
        return False
    text = _provider_error_text(output)
    return bool(text) and any(
        token in text
        for token in (
            "timeout",
            "timed out",
            "connection",
            "econn",
            "429",
            "rate limit",
            "temporarily unavailable",
            "service unavailable",
            "server busy",
            " 500",
            " 502",
            " 503",
            " 504",
        )
    )


def _percentile(values: list[float], percentile: float) -> float | None:
    rows = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not rows:
        return None
    index = max(0, math.ceil(percentile * len(rows)) - 1)
    return round(rows[index], 1)


def _partial_path(output: Path | None) -> Path | None:
    return Path(f"{output}.partial.jsonl") if output is not None else None


def _load_partial(path: Path | None) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    completed: dict[tuple[str, str], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("testset") and value.get("id"):
            completed[(str(value["testset"]), str(value["id"]))] = value
    return completed


def _append_partial(path: Path | None, result: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False) + "\n")


def _run_case(
    case: dict[str, Any],
    *,
    live: bool,
    policy: str,
    repeats: int,
    model: str | None,
    timeout_s: int | None,
    pure: bool,
    transport_retries: int,
    retry_backoff_s: float,
) -> dict[str, Any]:
    task = case["task"]
    expect = case["expect"]
    values = {
        "CODEARTS_STRATEGY_MODE": case.get("mode")
        or ("required" if live else "off"),
        "CODEARTS_STRATEGY_POLICY": policy,
    }
    if model:
        values["CODEARTS_STRATEGY_MODEL"] = model
    if timeout_s:
        values["CODEARTS_STRATEGY_TIMEOUT_S"] = str(timeout_s)
    if pure:
        values["CODEARTS_CLI_PURE"] = "1"

    observations: list[dict[str, Any]] = []
    provider_patch = patch(
        "integration.adapters.strategy.CodeArtsStrategyClient"
    ) if case.get("fault") else None
    provider_context = provider_patch if provider_patch else _null_context()
    with _provider_environment(values, live=live):
        with provider_context as client_class:
            if client_class is not None:
                _fault_setup(case, task, client_class)
            for repeat in range(1, repeats + 1):
                attempt = 0
                while True:
                    attempt += 1
                    started = time.perf_counter()
                    try:
                        output = strategy_adapter.run(task)
                    except Exception as exc:  # noqa: BLE001 - classify before retrying
                        failure = {
                            "success": False,
                            "blocked": True,
                            "code": None,
                            "mode": "codearts_blocked",
                            "steps": [],
                            "critics": [],
                            "provider_error": f"{type(exc).__name__}: {exc}",
                            "blocking_reasons": [],
                            "provenance": {"provider": "huaweicloud-codearts-agent"},
                        }
                        if not _is_transient_provider_error(failure):
                            raise
                        output = failure
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
                    if (
                        not live
                        or case.get("fault")
                        or not _is_transient_provider_error(output)
                        or attempt > transport_retries
                    ):
                        break
                    delay = retry_backoff_s * (2 ** (attempt - 1))
                    if delay > 0:
                        time.sleep(min(delay, 30.0))
                provider = (output.get("provenance") or {}).get("provider")
                contract_errors = (
                    validate_strategy(output, task)
                    if output.get("success") and not output.get("blocked")
                    else []
                )
                critic_passes = len(output.get("critics") or [])
                actual_actions = [step.get("action") for step in output.get("steps", [])]
                detected_target = next(
                    (
                        (step.get("arguments") or {}).get("object_id")
                        for step in output.get("steps", [])
                        if step.get("action") == "detect_object"
                    ),
                    None,
                )
                moved_destination = next(
                    (
                        (step.get("arguments") or {}).get("destination_id")
                        for step in output.get("steps", [])
                        if step.get("action") == "move_to_target"
                    ),
                    None,
                )
                expected_success = bool(expect["success"])
                expected_blocked = bool(expect["blocked"])
                provider_expected = bool(live and expect.get("provider_on_live", False))
                provider_attempted = bool(expect.get("provider_attempted", False))
                error_text = " ".join(
                    [
                        *(output.get("blocking_reasons") or []),
                        output.get("provider_error") or "",
                    ]
                )
                checks = {
                    "success": bool(output.get("success")) == expected_success,
                    "blocked": bool(output.get("blocked")) == expected_blocked,
                    "code_null": output.get("code") is None,
                    "contract": not contract_errors,
                    "provider": (provider == "huaweicloud-codearts-agent")
                    if provider_expected or provider_attempted
                    else provider is None,
                    "critic_passes": (
                        critic_passes == POLICY_CRITIC_PASSES[policy]
                        if live and expected_success and not case.get("fault")
                        else True
                    ),
                    "actions": (
                        actual_actions == expect["actions"]
                        if "actions" in expect
                        else True
                    ),
                    "target_id": (
                        detected_target == expect["target_id"]
                        if "target_id" in expect
                        else True
                    ),
                    "destination_id": (
                        moved_destination == expect["destination_id"]
                        if "destination_id" in expect
                        else True
                    ),
                    "mode": output.get("mode") == expect["mode"]
                    if "mode" in expect
                    else True,
                    "error": expect.get("error_contains", "") in error_text
                    if expect.get("error_contains")
                    else True,
                }
                observations.append(
                    {
                        "repeat": repeat,
                        "attempts": attempt,
                        "transport_retries": max(0, attempt - 1),
                        "elapsed_ms": elapsed_ms,
                        "success": bool(output.get("success")),
                        "blocked": bool(output.get("blocked")),
                        "mode": output.get("mode"),
                        "strategy_policy": output.get("strategy_policy"),
                        "provider": provider,
                        "provider_attempts": attempt if provider == "huaweicloud-codearts-agent" else 0,
                        "provider_mocked": bool(case.get("fault")),
                        "critic_passes": critic_passes,
                        "actions": actual_actions,
                        "target_id": detected_target,
                        "destination_id": moved_destination,
                        "contract_errors": contract_errors,
                        "provider_error": output.get("provider_error"),
                        "blocking_reasons": output.get("blocking_reasons", []),
                        "checks": checks,
                        "signature": _signature(output),
                    }
                )

    all_checks_pass = all(all(item["checks"].values()) for item in observations)
    signatures = [item["signature"] for item in observations]
    expected_provider_blocks = sum(
        1
        for item in observations
        if _is_transient_provider_error(item)
        and bool(expect["blocked"])
        and bool(item.get("blocked"))
        and not bool(item.get("success"))
    )
    return {
        "id": case["id"],
        "task_id": task.get("task_id"),
        "passed": all_checks_pass,
        "stable": len(set(signatures)) <= 1,
        "observations": observations,
        "transport_retries": sum(item["transport_retries"] for item in observations),
        "provider_failures": sum(
            1
            for item in observations
            if _is_transient_provider_error(item)
            and not (
                bool(expect["blocked"])
                and bool(item.get("blocked"))
                and not bool(item.get("success"))
            )
        ),
        "expected_provider_blocks": expected_provider_blocks,
    }


@contextmanager
def _null_context() -> Iterator[None]:
    yield None


def run_testsets(
    names: list[str],
    *,
    live: bool = False,
    policy: str | None = None,
    repeats: int | None = None,
    limit: int | None = None,
    model: str | None = None,
    timeout_s: int | None = None,
    pure: bool = False,
    transport_retries: int = 2,
    retry_backoff_s: float = 2.0,
    output: Path | None = None,
    resume: bool = False,
) -> dict[str, Any]:
    if repeats is not None and repeats < 1:
        raise ValueError("repeats 必须大于 0")
    if transport_retries < 0 or transport_retries > 5:
        raise ValueError("transport_retries 必须在 0..5 之间")
    if retry_backoff_s < 0:
        raise ValueError("retry_backoff_s 不能小于 0")
    if resume and output is None:
        raise ValueError("resume 需要 output")
    partial = _partial_path(output)
    if partial is not None and not resume:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    resumed = _load_partial(partial) if resume else {}
    results = []
    for name in names:
        document = load_testset(name)
        chosen_policy = _policy_name(document, policy)
        case_repeats = repeats or int(document.get("default_repeats", 1))
        if case_repeats < 1:
            raise ValueError(f"{name} 的 default_repeats 必须大于 0")
        cases = document["cases"][:limit] if limit else document["cases"]
        config = {
            "live": live,
            "policy": chosen_policy,
            "repeats": case_repeats,
            "limit": limit,
            "model": model,
            "timeout_s": timeout_s,
            "pure": pure,
            "transport_retries": transport_retries,
        }
        case_results = []
        for case in cases:
            key = (name, str(case["id"]))
            previous = resumed.get(key)
            if previous and previous.get("run_config") == config:
                case_result = previous
            else:
                case_result = _run_case(
                    case,
                    live=live,
                    policy=chosen_policy,
                    repeats=case_repeats,
                    model=model,
                    timeout_s=timeout_s,
                    pure=pure,
                    transport_retries=transport_retries,
                    retry_backoff_s=retry_backoff_s,
                )
                case_result["testset"] = name
                case_result["run_config"] = config
                _append_partial(partial, case_result)
            case_results.append(case_result)
        observations = [item for result in case_results for item in result["observations"]]
        provider_calls = sum(
            1
            for item in observations
            if item["provider"] == "huaweicloud-codearts-agent"
            and not item["provider_mocked"]
        )
        provider_attempts = sum(int(item.get("provider_attempts", 0)) for item in observations)
        latency_ms = [float(item["elapsed_ms"]) for item in observations]
        results.append(
            {
                "name": name,
                "category": document.get("category", "uncategorized"),
                "description": document.get("description"),
                "policy": chosen_policy,
                "cases": len(case_results),
                "passed_cases": sum(1 for item in case_results if item["passed"]),
                "stable_cases": sum(1 for item in case_results if item["stable"]),
                "provider_calls": provider_calls,
                "provider_attempts": provider_attempts,
                "transport_retries": sum(
                    int(item.get("transport_retries", 0)) for item in observations
                ),
                "provider_failures": sum(
                    int(item.get("provider_failures", 0)) for item in case_results
                ),
                "expected_provider_blocks": sum(
                    int(item.get("expected_provider_blocks", 0)) for item in case_results
                ),
                "latency_ms_p50": _percentile(latency_ms, 0.50),
                "latency_ms_p95": _percentile(latency_ms, 0.95),
                "contract_failures": sum(
                    1 for item in observations if item["contract_errors"]
                ),
                "case_results": case_results,
            }
        )
    total_cases = sum(item["cases"] for item in results)
    passed_cases = sum(item["passed_cases"] for item in results)
    return {
        "schema_version": "codearts-testset-report.v1",
        "live": live,
        "repeats": repeats if repeats is not None else "per_testset_default",
        "results": results,
        "summary": {
            "testsets": len(results),
            "cases": total_cases,
            "passed_cases": passed_cases,
            "pass_rate": round(passed_cases / total_cases, 4) if total_cases else 0.0,
            "provider_calls": sum(item["provider_calls"] for item in results),
            "provider_attempts": sum(item["provider_attempts"] for item in results),
            "transport_retries": sum(item["transport_retries"] for item in results),
            "provider_failures": sum(item["provider_failures"] for item in results),
            "expected_provider_blocks": sum(
                item["expected_provider_blocks"] for item in results
            ),
            "latency_ms_p50": _percentile(
                [
                    float(observation["elapsed_ms"])
                    for result in results
                    for case in result["case_results"]
                    for observation in case["observations"]
                ],
                0.50,
            ),
            "latency_ms_p95": _percentile(
                [
                    float(observation["elapsed_ms"])
                    for result in results
                    for case in result["case_results"]
                    for observation in case["observations"]
                ],
                0.95,
            ),
            "contract_failures": sum(item["contract_failures"] for item in results),
            "all_passed": bool(total_cases) and passed_cases == total_cases,
            "all_stable": all(
                item["stable_cases"] == item["cases"] for item in results
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", dest="testset", action="append", choices=list_testsets())
    parser.add_argument("--list", action="store_true", help="列出可用测试集")
    parser.add_argument("--live", action="store_true", help="调用真实 CodeArts CLI")
    parser.add_argument("--policy", choices=tuple(POLICY_CRITIC_PASSES), default=None)
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout-s", type=int, default=180)
    parser.add_argument("--pure", action="store_true")
    parser.add_argument("--transport-retries", type=int, default=2)
    parser.add_argument("--retry-backoff-s", type=float, default=2.0)
    parser.add_argument("--resume", action="store_true", help="从 output.partial.jsonl 继续未完成的 case")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "codearts_testsets.json",
    )
    args = parser.parse_args()
    if args.list:
        print("\n".join(list_testsets()))
        return 0
    names = args.testset or (list(LIVE_SCALE_TESTSETS) if args.live else list_testsets())
    if args.transport_retries < 0 or args.transport_retries > 5:
        parser.error("--transport-retries 必须在 0..5 之间")
    if args.retry_backoff_s < 0:
        parser.error("--retry-backoff-s 不能小于 0")
    report = run_testsets(
        names,
        live=args.live,
        policy=args.policy,
        repeats=args.repeats,
        limit=args.limit,
        model=args.model,
        timeout_s=args.timeout_s,
        pure=args.pure,
        transport_retries=args.transport_retries,
        retry_backoff_s=args.retry_backoff_s,
        output=args.output,
        resume=args.resume,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    partial = _partial_path(args.output)
    if partial is not None:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
