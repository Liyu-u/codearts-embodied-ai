"""Run reproducible real online A/B/Isaac Sim closed-loop acceptance cases."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.config.local_env import load_codearts_env, load_local_env
from integration.strategy_policy import DEFAULT_CAPABILITIES
from tools.summarize_real_acceptance import classify, summarize


LINE_END = chr(10)


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + LINE_END, encoding="utf-8")


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "case"


def _powershell() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"


def _is_transport_error(text: str) -> bool:
    value = text.lower()
    return any(token in value for token in ("permission denied", "publickey", "connecttimeout", "connection timed out", "connection reset", "429", " 500", " 502", " 503", " 504", "scp", "ssh"))


def _load_perception(document: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    relative = case.get("perception_file") or document.get("perception_source")
    source = ROOT / str(relative)
    value = _json(source)
    if value and isinstance(value.get("scene"), dict):
        return value["scene"]
    if value and value.get("schema_version") == "perception.v1":
        return value
    raise ValueError(f"perception 文件无效: {source}")


def _run_ab(case: dict[str, Any], perception: dict[str, Any], run_id: str, out_dir: Path) -> dict[str, Any]:
    from integration.adapters.strategy import run as strategy_run
    from modules.intent_understanding.adapter import run as intent_run

    started = time.monotonic()
    task = intent_run({"instruction": str(case["instruction"]), "perception": perception, "engine": "llm", "correlation_id": run_id})
    intent_ms = round((time.monotonic() - started) * 1000, 3)
    strategy = None
    strategy_ms = None
    if task.get("status") == "READY":
        started = time.monotonic()
        strategy = strategy_run({**task, "capabilities": DEFAULT_CAPABILITIES})
        strategy_ms = round((time.monotonic() - started) * 1000, 3)
    report = {
        "schema_version": "real-acceptance.ab.v1",
        "run_id": run_id,
        "case_id": case["id"],
        "category": case["category"],
        "expected_status": case["expected_status"],
        "instruction": case["instruction"],
        "task": task,
        "strategy": strategy,
        "latency_ms": {"intent": intent_ms, "strategy": strategy_ms},
        "seed": case.get("seed"),
        "online_assertions": {"intent_engine": "llm", "codearts_mode": os.environ.get("CODEARTS_STRATEGY_MODE")},
    }
    _write(out_dir / "ab.json", report)
    return report


def _run_remote(
    run_id: str,
    out_dir: Path,
    *,
    runner: Path,
    ssh_key: str | None,
    device: str,
    interactive: bool,
    retries: int,
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    relative_strategy = str((out_dir / "ab.json").relative_to(ROOT)).replace("\\", "/")
    remote_run_id = _safe_id(f"isaac-{run_id}")
    remote_dir = ROOT / "reports" / remote_run_id
    # Do not let a previous run with the same deterministic id masquerade as
    # fresh execution evidence after an SSH/container failure.
    for stale_name in ("execution.json", "perception.json", "remote_run.json"):
        stale_path = remote_dir / stale_name
        try:
            stale_path.unlink()
        except FileNotFoundError:
            pass
    local_copy = out_dir / "isaac"
    if local_copy.exists():
        shutil.rmtree(local_copy)
    command = [_powershell(), "-NoProfile", "-File", str(runner), "-StrategyFile", relative_strategy, "-RunId", remote_run_id, "-Device", device]
    if ssh_key:
        command += ["-SshKeyPath", ssh_key]
    if interactive:
        command += ["-InteractiveAuth"]
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    attempts = 0
    while True:
        attempts += 1
        if interactive:
            completed = subprocess.run(command, cwd=ROOT, text=True, check=False)
            completed_stdout = ""
            completed_stderr = ""
        else:
            completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            completed_stdout = completed.stdout or ""
            completed_stderr = completed.stderr or ""
        stdout_parts.append(completed_stdout)
        stderr_parts.append(completed_stderr)
        combined = completed_stdout + completed_stderr
        if completed.returncode == 0 or attempts > retries or not _is_transport_error(combined):
            break
    (out_dir / "remote_runner.stdout.log").write_text(LINE_END.join(stdout_parts), encoding="utf-8")
    (out_dir / "remote_runner.stderr.log").write_text(LINE_END.join(stderr_parts), encoding="utf-8")
    if remote_dir.is_dir():
        shutil.copytree(remote_dir, local_copy)
    remote_run = _json(remote_dir / "remote_run.json")
    if completed.returncode != 0 and remote_run is None:
        remote_run = {
            "schema_version": "remote-isaac-run.v1",
            "run_id": remote_run_id,
            "status": "FAILED",
            "failure_class": "runner",
            "message": f"远程运行器退出码 {completed.returncode}",
        }
        _write(local_copy / "remote_run.json", remote_run)
    return _json(remote_dir / "execution.json"), remote_run_id, remote_run


def _feedback(ab: dict[str, Any], perception: dict[str, Any], execution: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    load_local_env("tracecoder_llm.env", override=True)
    from integration.adapters.tracecoder import run as feedback_run

    feedback = feedback_run({"task": ab["task"], "strategy": ab["strategy"], "execution": execution, "perception": perception, "run_id": ab["run_id"]})
    _write(out_dir / "feedback.json", feedback)
    return feedback


def _make_status(
    ab: dict[str, Any],
    case: dict[str, Any],
    execution: dict[str, Any] | None,
    feedback: dict[str, Any] | None,
    remote_run_id: str | None,
    out_dir: Path,
    remote_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = ab.get("task") or {}
    strategy = ab.get("strategy") or {}
    expected = str(case.get("expected_status") or "").upper()
    if execution:
        actual = str(execution.get("status") or "UNKNOWN").upper()
    elif task.get("status") != "READY":
        actual = str(task.get("status") or "BLOCKED").upper()
    elif strategy.get("blocked") or strategy.get("success") is False:
        actual = "BLOCKED"
    else:
        actual = "NOT_EXECUTED"
    diagnosis: dict[str, Any] = {}
    if feedback and isinstance(feedback.get("diagnosis"), str):
        try:
            diagnosis = json.loads(feedback["diagnosis"])
        except json.JSONDecodeError:
            diagnosis = {}
    ids_match = None
    if execution:
        ids_match = bool(task.get("task_id") == strategy.get("task_id") == execution.get("task_id"))
    if feedback and ids_match is not None:
        ids_match = ids_match and feedback.get("task_id") == task.get("task_id")
    execution_steps_ok = (
        all(str(step.get("status", "")).upper() == "SUCCESS" for step in execution.get("steps") or [])
        if execution
        else None
    )
    if expected == "SUCCEEDED":
        accepted = actual == "SUCCEEDED" and execution_steps_ok and ids_match and bool(feedback and feedback.get("final_passed") is True)
    elif expected in {"BLOCKED", "NEEDS_CLARIFICATION", "SAFE_STOP", "FAILED"}:
        accepted = actual == expected or (expected == "BLOCKED" and actual == "NEEDS_CLARIFICATION")
    else:
        accepted = False
    result = {
        "schema_version": "real-acceptance.result.v1",
        "run_id": ab["run_id"],
        "case_id": case["id"],
        "category": case["category"],
        "expected_status": expected,
        "actual_status": actual,
        "status": "SUCCEEDED" if accepted else "FAILED",
        "seed": case.get("seed"),
        "task": task,
        "strategy": strategy,
        "stages": {
            "A": {"status": task.get("status"), "latency_ms": ab.get("latency_ms", {}).get("intent"), "requested_engine": task.get("diagnostics", {}).get("requested_engine"), "actual_engine": task.get("diagnostics", {}).get("actual_engine")},
            "B": {"status": "SUCCEEDED" if strategy.get("success") and not strategy.get("blocked") else "BLOCKED", "latency_ms": ab.get("latency_ms", {}).get("strategy"), "provider": strategy.get("provenance", {}).get("provider"), "fallback": strategy.get("provenance", {}).get("fallback")},
            "C": {"status": execution.get("status") if execution else None, "steps": len(execution.get("steps") or []) if execution else 0, "all_steps_success": execution_steps_ok, "cube_moved_m": execution.get("cube_moved_m") if execution else None, "wall_ms": execution.get("wall_ms") if execution else None, "perception_source": execution.get("provenance", {}).get("perception_backend") if execution else None, "online_pose_source": execution.get("provenance", {}).get("online_pose_source") if execution else None, "ground_truth_used_for_online_pose": execution.get("provenance", {}).get("ground_truth_used_for_online_pose") if execution else None},
            "feedback": {"status": diagnosis.get("status") if diagnosis else None, "final_passed": feedback.get("final_passed") if feedback else None, "tracecoder_invoked": diagnosis.get("tracecoder_invoked") if diagnosis else None},
        },
        "contract_checks": ({"task_strategy_execution_feedback_task_id_match": ids_match, "all_execution_steps_success": execution_steps_ok} if execution else {}),
        "remote_run_id": remote_run_id,
        "remote_run": remote_run,
        "evidence_dir": str(out_dir),
    }
    result["failure_class"] = classify(result)
    _write(out_dir / "full_test_status.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "testdata" / "benchmark" / "real_isaac_cases.json")
    parser.add_argument("--repeats", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--remote-runner", type=Path, default=ROOT / "tools" / "run_remote_ground_truth_acceptance_final.ps1")
    parser.add_argument("--ssh-key", default=None)
    parser.add_argument("--interactive-remote", action="store_true")
    parser.add_argument("--device", choices=("cpu", "cuda", "cuda:0"), default="cuda")
    parser.add_argument("--transport-retries", type=int, default=1)
    parser.add_argument("--preflight-only", action="store_true", help="只校验清单、运行器和 SSH 参数，不调用在线模型或 Isaac Sim")
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "real-acceptance-summary.json")
    args = parser.parse_args()
    if args.repeats is not None and args.repeats < 1:
        parser.error("--repeats 必须大于 0")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit 必须大于 0")
    if args.transport_retries < 0 or args.transport_retries > 2:
        parser.error("--transport-retries 必须在 0..2 之间")
    document = _json(args.manifest)
    if not document or document.get("schema_version") != "real-isaac-acceptance.v1":
        parser.error("manifest schema_version 必须为 real-isaac-acceptance.v1")
    cases = list(document.get("cases") or [])
    case_ids = [str(case.get("id") or "") for case in cases]
    if not cases or len(set(case_ids)) != len(case_ids) or any(not value for value in case_ids):
        parser.error("manifest cases 必须非空且 id 唯一")
    if not args.remote_runner.is_file():
        parser.error(f"远端运行器不存在: {args.remote_runner}")
    if args.ssh_key and not Path(args.ssh_key).is_file():
        parser.error(f"SSH 私钥不存在: {args.ssh_key}")
    if any(bool(case.get("run_remote")) for case in cases) and not args.ssh_key and not args.interactive_remote:
        parser.error("真实 Isaac Sim 批量验收需要 --ssh-key；密码只能通过 --interactive-remote 用于人工冒烟")
    if args.preflight_only:
        print(json.dumps({"preflight": "OK", "case_count": len(cases), "remote_cases": sum(bool(case.get("run_remote")) for case in cases), "seed": args.seed if args.seed is not None else document.get("seed"), "ssh_mode": "key" if args.ssh_key else "interactive"}, ensure_ascii=False))
        return 0
    if args.limit is not None:
        cases = cases[: args.limit]
    repeats = args.repeats or int(document.get("default_repeats", 1))
    seed = args.seed if args.seed is not None else int(document.get("seed", 0))
    load_local_env(".env", override=False)
    load_codearts_env(override=True)
    os.environ["RIA_PLANNER_ENGINE"] = "llm"
    os.environ["CODEARTS_STRATEGY_MODE"] = "required"
    os.environ["CODEARTS_STRATEGY_POLICY"] = "planner"
    records: list[dict[str, Any]] = []
    for case_index, case in enumerate(cases):
        perception = _load_perception(document, case)
        for repeat in range(1, repeats + 1):
            case_seed = seed + case_index * 1000 + repeat
            run_id = _safe_id(f"real-acceptance-{case['id']}-r{repeat}-{case_seed}")
            out_dir = ROOT / "reports" / run_id
            out_dir.mkdir(parents=True, exist_ok=True)
            case_with_seed = {**case, "seed": case_seed}
            try:
                ab = _run_ab(case_with_seed, perception, run_id, out_dir)
                strategy = ab.get("strategy") or {}
                execution = None
                feedback = None
                remote_run_id = None
                if strategy.get("success") and not strategy.get("blocked") and case.get("run_remote", False):
                    execution, remote_run_id, remote_run = _run_remote(run_id, out_dir, runner=args.remote_runner, ssh_key=args.ssh_key, device=args.device, interactive=args.interactive_remote, retries=args.transport_retries)
                    if execution:
                        feedback = _feedback(ab, perception, execution, out_dir)
                result = _make_status(ab, case_with_seed, execution, feedback, remote_run_id, out_dir, remote_run)
            except Exception as exc:
                result = {"schema_version": "real-acceptance.result.v1", "run_id": run_id, "case_id": case["id"], "category": case["category"], "expected_status": case.get("expected_status"), "status": "FAILED", "failure_class": "runner", "message": f"{type(exc).__name__}: {exc}", "evidence_dir": str(out_dir)}
                _write(out_dir / "full_test_status.json", result)
            records.append(result)
            print(json.dumps({"run_id": run_id, "case_id": case["id"], "status": result.get("status"), "failure_class": result.get("failure_class")}, ensure_ascii=False), flush=True)
    report = {"schema_version": "real-acceptance-suite.v1", "manifest": str(args.manifest), "seed": seed, "repeats": repeats, "summary": summarize(records), "records": records}
    _write(args.output, report)
    return 0 if report["summary"]["pass_rate"] == 1.0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
