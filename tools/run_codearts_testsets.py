"""Run repeatable CodeArts strategy test sets.

Offline mode checks the adapter contract without network calls.  ``--live``
switches only the positive cases to the real CodeArts CLI and records provider
provenance, critic passes, contract failures, fallback and repeat stability.

Examples::

    python tools/run_codearts_testsets.py --set normal_quality
    python tools/run_codearts_testsets.py --set normal_quality --live \
        --policy quality --limit 2 --pure
    python tools/run_codearts_testsets.py --set stability_repeat --live \
        --policy planner --repeats 3 --pure
"""

from __future__ import annotations

import argparse
import json
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

from integration.adapters import strategy as strategy_adapter  # noqa: E402
from modules.strategy_generation.codearts_agent import validate_strategy  # noqa: E402


POLICY_CRITIC_PASSES = {"planner": 0, "quality": 1, "max": 2}


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


def _run_case(
    case: dict[str, Any],
    *,
    live: bool,
    policy: str,
    repeats: int,
    model: str | None,
    timeout_s: int | None,
    pure: bool,
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
    with _temporary_environment(values):
        with provider_context as client_class:
            if client_class is not None:
                _fault_setup(case, task, client_class)
            for repeat in range(1, repeats + 1):
                started = time.perf_counter()
                output = strategy_adapter.run(task)
                elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
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
                        "elapsed_ms": elapsed_ms,
                        "success": bool(output.get("success")),
                        "blocked": bool(output.get("blocked")),
                        "mode": output.get("mode"),
                        "strategy_policy": output.get("strategy_policy"),
                        "provider": provider,
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
    return {
        "id": case["id"],
        "task_id": task.get("task_id"),
        "passed": all_checks_pass,
        "stable": len(set(signatures)) <= 1,
        "observations": observations,
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
) -> dict[str, Any]:
    if repeats is not None and repeats < 1:
        raise ValueError("repeats 必须大于 0")
    results = []
    for name in names:
        document = load_testset(name)
        chosen_policy = _policy_name(document, policy)
        case_repeats = repeats or int(document.get("default_repeats", 1))
        if case_repeats < 1:
            raise ValueError(f"{name} 的 default_repeats 必须大于 0")
        cases = document["cases"][:limit] if limit else document["cases"]
        case_results = [
            _run_case(
                case,
                live=live,
                policy=chosen_policy,
                repeats=case_repeats,
                model=model,
                timeout_s=timeout_s,
                pure=pure,
            )
            for case in cases
        ]
        observations = [item for result in case_results for item in result["observations"]]
        provider_calls = sum(
            1
            for item in observations
            if item["provider"] == "huaweicloud-codearts-agent"
            and not item["provider_mocked"]
        )
        provider_attempts = sum(
            1 for item in observations if item["provider"] == "huaweicloud-codearts-agent"
        )
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
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "codearts_testsets.json",
    )
    args = parser.parse_args()
    if args.list:
        print("\n".join(list_testsets()))
        return 0
    names = args.testset or list_testsets()
    report = run_testsets(
        names,
        live=args.live,
        policy=args.policy,
        repeats=args.repeats,
        limit=args.limit,
        model=args.model,
        timeout_s=args.timeout_s,
        pure=args.pure,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["all_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
