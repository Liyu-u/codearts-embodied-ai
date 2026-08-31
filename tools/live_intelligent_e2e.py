"""Fail-closed evidence audit and orchestration for live intelligent E2E tests."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


INTELLIGENT_VARIANTS = {"V1_CODEARTS_POLICY", "V2_FULL_NO_D", "V4_FULL"}
COMPARISON_VARIANTS = ("V0_RULE_BASELINE", "V1_CODEARTS_POLICY", "V2_FULL_NO_D", "V4_FULL")


def build_normal_schedule(cases: list[dict[str, Any]], *, repeats: int, seed: int) -> list[dict[str, Any]]:
    if len(cases) != 20:
        raise ValueError("normal acceptance requires exactly 20 cases")
    schedule: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        for repeat in range(1, repeats + 1):
            run_seed = int(seed) + case_index * 100 + repeat
            for variant_id in COMPARISON_VARIANTS:
                schedule.append({"case_id": str(case["id"]), "repeat": repeat, "seed": run_seed, "variant_id": variant_id})
    return schedule


def document_digest(document: dict[str, Any]) -> str:
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def strategy_digest(strategy: dict[str, Any]) -> str:
    return document_digest(strategy)


def _successful_action_after_stop(execution: dict[str, Any]) -> bool:
    if str(execution.get("status", "")).upper() != "SAFE_STOP":
        return False
    stopped = False
    stop_tokens = ("STOP", "E_STOP", "COLLISION", "TIMEOUT", "LIMIT", "WORKSPACE")
    for step in execution.get("steps") or []:
        reason = str(step.get("reason") or step.get("stop_reason") or "").upper()
        status = str(step.get("status") or "").upper()
        if status in {"FAILED", "SAFE_STOP", "BLOCKED"} and any(token in reason for token in stop_tokens):
            stopped = True
            continue
        if stopped and status == "SUCCESS":
            return True
    return False


def audit_documents(documents: dict[str, Any], variant_id: str) -> dict[str, Any]:
    errors: list[str] = []
    for key in ("input", "task", "strategy", "perception", "execution", "progress", "final_pose"):
        if not documents.get(key):
            errors.append(f"{key.upper()}_EVIDENCE_MISSING")
    if not documents.get("container_log_present"):
        errors.append("CONTAINER_LOG_MISSING")

    api_calls = documents.get("api_calls") or {}
    if variant_id in INTELLIGENT_VARIANTS:
        intent = api_calls.get("intent") or {}
        strategy_call = api_calls.get("strategy") or {}
        if not intent.get("succeeded") or int(intent.get("network_calls") or 0) < 1 or intent.get("fallback"):
            errors.append("DEEPSEEK_INTENT_NOT_PROVEN")
        if not intent.get("request_id"):
            errors.append("DEEPSEEK_INTENT_REQUEST_ID_MISSING")
        if not strategy_call.get("succeeded") or int(strategy_call.get("calls") or 0) < 1 or strategy_call.get("fallback"):
            errors.append("CODEARTS_CALL_NOT_PROVEN")
        if not strategy_call.get("request_id"):
            errors.append("CODEARTS_REQUEST_ID_MISSING")
    if variant_id == "V4_FULL":
        feedback = api_calls.get("feedback") or {}
        if not feedback.get("succeeded") or int(feedback.get("network_calls") or 0) < 1 or feedback.get("fallback"):
            errors.append("DEEPSEEK_FEEDBACK_NOT_PROVEN")
        if not feedback.get("request_id"):
            errors.append("DEEPSEEK_FEEDBACK_REQUEST_ID_MISSING")

    strategy = documents.get("strategy") or {}
    execution = documents.get("execution") or {}
    perception = documents.get("perception") or {}
    if strategy and execution.get("input_strategy_sha256") != strategy_digest(strategy):
        errors.append("EXECUTED_STRATEGY_MISMATCH")
    if variant_id in INTELLIGENT_VARIANTS and strategy.get("input_perception_sha256") != document_digest(perception):
        errors.append("STRATEGY_PERCEPTION_MISMATCH")
    if strategy.get("task_id") and execution.get("task_id") != strategy.get("task_id"):
        errors.append("TASK_ID_MISMATCH")
    perception_backend = (
        (perception.get("provenance") or {}).get("backend")
        or (execution.get("provenance") or {}).get("perception_backend")
    )
    if perception_backend != "isaac_ground_truth":
        errors.append("REAL_ISAAC_GROUND_TRUTH_NOT_PROVEN")
    if _successful_action_after_stop(execution):
        errors.append("ACTION_AFTER_TERMINAL_STOP")
    return {"eligible": not errors, "variant_id": variant_id, "errors": errors}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def compute_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("population") or "unknown"), []).append(record)
    result: dict[str, Any] = {}
    for population, rows in grouped.items():
        count = len(rows)
        durations = [float(row["duration_ms"]) for row in rows if row.get("duration_ms") is not None]
        normal = sum(str(row.get("status")).upper() == "SUCCEEDED" for row in rows)
        safe_stops = sum(str(row.get("status")).upper() in {"SAFE_STOP", "BLOCKED"} for row in rows)
        result[population] = {
            "sample_count": count,
            "eligible_count": sum(bool(row.get("eligible")) for row in rows),
            "codearts_api_success_rate": _rate(sum(bool(row.get("api_ok")) for row in rows), count),
            "strategy_contract_pass_rate": _rate(sum(bool(row.get("contract_ok")) for row in rows), count),
            "target_binding_accuracy": _rate(sum(bool(row.get("binding_ok")) for row in rows), count),
            "physical_task_success_rate": _rate(normal, count),
            "failure_recovery_rate": _rate(sum(bool(row.get("recovery_succeeded")) for row in rows), sum(row.get("recovery_succeeded") is not None for row in rows)),
            "safe_stop_correct_rate": _rate(safe_stops, count),
            "dangerous_action_execution_rate": _rate(sum(bool(row.get("dangerous_action_executed")) for row in rows), count),
            "average_duration_ms": round(sum(durations) / len(durations), 3) if durations else None,
            "api_call_failure_rate": _rate(sum(not bool(row.get("api_ok")) for row in rows), count),
            "status_counts": dict(Counter(str(row.get("status") or "UNKNOWN") for row in rows)),
        }
    return result


def api_call_evidence(task: dict[str, Any], strategy: dict[str, Any] | None) -> dict[str, Any]:
    engine_trace = ((task.get("diagnostics") or {}).get("engine_trace") or {})
    provenance = (strategy or {}).get("provenance") or {}
    return {
        "intent": {
            "provider": "deepseek",
            "network_calls": int(engine_trace.get("llm_network_calls") or 0),
            "succeeded": bool(engine_trace.get("llm_call_succeeded")),
            "request_id": engine_trace.get("llm_request_id"),
            "request_id_source": engine_trace.get("llm_request_id_source"),
            "fallback": bool(engine_trace.get("fallback_used")),
            "model": engine_trace.get("model"),
        },
        "strategy": {
            "provider": provenance.get("provider") or provenance.get("source"),
            "calls": 1 if provenance and not provenance.get("fallback") else 0,
            "succeeded": bool(strategy and strategy.get("success") and not strategy.get("blocked")),
            "request_id": provenance.get("request_id") or provenance.get("run_id"),
            "request_id_source": "codearts_client_trace",
            "fallback": bool(provenance.get("fallback")),
            "model": provenance.get("model"),
        },
    }


def generate_intelligent_ab(
    instruction: str,
    perception: dict[str, Any],
    *,
    correlation_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    """Call real A and B once and return sanitized, auditable call evidence."""

    import os

    from integration.config.local_env import load_codearts_env, load_local_env

    load_local_env(".env", override=True)
    load_codearts_env(override=True)
    os.environ["RIA_PLANNER_ENGINE"] = "llm"
    # Live acceptance runs must fail closed when the real DeepSeek response
    # cannot be parsed or violates the semantic contract.  Leaving the
    # default ``fallback`` policy enabled would silently replace a provider
    # result with local rules and invalidate the real-API evidence audit.
    os.environ["RIA_LLM_FAILURE_POLICY"] = "block"
    os.environ["CODEARTS_STRATEGY_MODE"] = "required"
    # Import adapters only after the live environment is loaded.  The intent
    # adapter caches settings at import time; importing it first would retain
    # the default fallback policy and silently downgrade a real run.
    from integration.adapters.strategy import run as strategy_run
    from integration.strategy_policy import DEFAULT_CAPABILITIES
    from modules.intent_understanding.robot_intent_agent.config.settings import get_settings
    from modules.intent_understanding.adapter import run as intent_run
    get_settings.cache_clear()
    task = intent_run({
        "instruction": instruction,
        "perception": perception,
        "engine": "llm",
        "correlation_id": correlation_id,
    })
    # A provider rejection/fallback is recorded as a failed live-A result;
    # never replace it with local semantics.  The bridge writes this task and
    # its request evidence before applying the downstream A/B gate, so the
    # failed attempt remains auditable and is excluded from eligible scores.
    strategy = None
    if task.get("status") == "READY":
        strategy = strategy_run({**task, "capabilities": DEFAULT_CAPABILITIES})
        if isinstance(strategy, dict):
            strategy["input_perception_sha256"] = document_digest(perception)
    return task, strategy, api_call_evidence(task, strategy)


def generate_rule_ab(
    instruction: str,
    perception: dict[str, Any],
    *,
    correlation_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    import os

    from integration.adapters.strategy import run as strategy_run
    from integration.strategy_policy import DEFAULT_CAPABILITIES
    from modules.intent_understanding.adapter import run as intent_run

    task = intent_run({"instruction": instruction, "perception": perception, "engine": "rule", "correlation_id": correlation_id})
    if task.get("status") != "READY":
        raise RuntimeError(f"V0 local rule intent blocked: {task.get('blocking_reasons')}")
    previous = os.environ.get("CODEARTS_STRATEGY_MODE")
    os.environ["CODEARTS_STRATEGY_MODE"] = "off"
    try:
        strategy = strategy_run({**task, "capabilities": DEFAULT_CAPABILITIES})
    finally:
        if previous is None:
            os.environ.pop("CODEARTS_STRATEGY_MODE", None)
        else:
            os.environ["CODEARTS_STRATEGY_MODE"] = previous
    strategy["input_perception_sha256"] = document_digest(perception)
    calls = {
        "intent": {"provider": "local_rules", "network_calls": 0, "succeeded": True, "request_id": correlation_id, "fallback": False},
        "strategy": {"provider": "local_rules", "calls": 0, "succeeded": bool(strategy.get("success")), "request_id": strategy.get("task_id"), "fallback": False},
    }
    return task, strategy, calls


def load_evidence_directory(path: Path) -> dict[str, Any]:
    def read_json(name: str) -> Any:
        candidate = path / name
        if not candidate.is_file():
            return None
        return json.loads(candidate.read_text(encoding="utf-8"))

    progress_path = path / "progress.jsonl"
    progress = []
    if progress_path.is_file():
        for line in progress_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                progress.append(json.loads(line))
    return {
        "input": read_json("input.json"),
        "api_calls": read_json("api_calls.json"),
        "task": read_json("task.json"),
        "strategy": read_json("strategy.json"),
        "perception": read_json("perception.json"),
        "execution": read_json("execution.json"),
        "progress": progress,
        "container_log_present": (path / "container.log").is_file(),
        "final_pose": read_json("final_pose.json"),
    }


def write_summary_report(path: Path, records: list[dict[str, Any]], *, required_runs: int) -> dict[str, Any]:
    eligible = [row for row in records if row.get("audit", {}).get("eligible")]
    summary = {
        "schema_version": "live-intelligent-e2e-summary.v1",
        "status": "COMPLETE" if len(records) >= required_runs else "INCOMPLETE",
        "required_runs": required_runs,
        "completed_runs": len(records),
        "eligible_runs": len(eligible),
        "records": records,
        "metrics": compute_metrics([row for row in eligible if row.get("metrics")]),
        "populations": {"mock": 0, "offline": 0, "real_api_real_isaac": len(eligible)},
        "acceptance_conclusion": "NOT_ACCEPTED" if len(records) < required_runs else "REVIEW_REQUIRED",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown = [
        "# 真实智能模式端到端验收汇总",
        "",
        f"状态：`{summary['status']}`；已完成 {len(records)}/{required_runs} 次；合格证据 {len(eligible)} 次。",
        "",
        "本报告只汇总通过真实性审计的真实 API + 真实 Isaac 运行；Mock/离线结果不进入总体成功率。",
        "",
        "## 指标公式",
        "",
        "- CodeArts API 调用成功率 = 成功调用次数 / 尝试次数。",
        "- 策略契约通过率 = 契约通过策略数 / 生成策略数。",
        "- 目标绑定准确率 = 正确目标绑定次数 / 可判定绑定次数。",
        "- 真实物理成功率 = 最终位姿成功次数 / 正常真实执行次数。",
        "- 故障恢复率 = 修复后成功次数 / 可恢复故障次数。",
        "- 安全停机正确率 = 正确停机次数 / 危险任务次数。",
        "- 危险动作误执行率 = 危险动作执行次数 / 危险任务次数。",
        "- API 调用失败率 = 失败调用次数 / API 尝试次数。",
        "",
        "## 运行记录",
        "",
    ]
    for row in records:
        audit = row.get("audit") or {}
        markdown.append(f"- `{row.get('run_id')}` {row.get('variant_id')} {row.get('case_id')}: eligible={audit.get('eligible')} status={row.get('status')} errors={','.join(audit.get('errors') or [])}")
    path.with_suffix(".md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return summary
