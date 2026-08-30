"""Run the final, evidence-based simulation-platform acceptance matrix.

The command deliberately distinguishes PASS, PARTIAL and BLOCKED.  Missing
cloud or Isaac prerequisites are never converted into a local pass.  The
matrix is intentionally simulation-only; no real-robot profile is executed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "testdata" / "acceptance" / "final_acceptance_matrix_v1.json"
DEFAULT_MANIFEST = ROOT / "testdata" / "benchmark" / "abcd_closed_loop_v1.json"
GENERALIZATION_MANIFEST = ROOT / "testdata" / "benchmark" / "llm_generalization_v1.json"
REPORT_ROOT = ROOT / "reports"

PROFILE_IDS = (
    "offline_regression",
    "codearts_online",
    "llm_generalization",
    "isaac_hil_ground_truth",
    "camera_perception_hil",
)


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _env_file_value(filename: str, key: str) -> str:
    path = ROOT / filename
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    return os.environ.get(key, "").strip()


def _run(command: list[str], log_stem: Path, timeout_s: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        exit_code = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        exit_code = 124
        timed_out = True
    log_stem.parent.mkdir(parents=True, exist_ok=True)
    log_stem.with_suffix(".stdout.log").write_text(stdout, encoding="utf-8", errors="replace")
    log_stem.with_suffix(".stderr.log").write_text(stderr, encoding="utf-8", errors="replace")
    return {
        "command": command,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "stdout_log": str(log_stem.with_suffix(".stdout.log")),
        "stderr_log": str(log_stem.with_suffix(".stderr.log")),
    }


def _closed_loop_summary(report_path: Path) -> dict[str, Any]:
    report = _json(report_path) or {}
    reports = report.get("reports") or []
    return dict((reports[0] or {}).get("summary") or {}) if reports else {}


def _gate(summary: dict[str, Any], exact: dict[str, float] | None = None, greater: dict[str, float] | None = None) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for key, expected in (exact or {}).items():
        actual = summary.get(key)
        if actual != expected:
            failures.append(f"{key}={actual!r}, expected {expected!r}")
    for key, minimum in (greater or {}).items():
        actual = summary.get(key)
        if not isinstance(actual, (int, float)) or actual <= minimum:
            failures.append(f"{key}={actual!r}, expected > {minimum!r}")
    return not failures, failures


def _profile_result(profile_id: str, status: str, reason: str = "", **fields: Any) -> dict[str, Any]:
    return {"id": profile_id, "status": status, "reason": reason, **fields}


def run_offline(out_dir: Path, repeats: int, reuse_dir: Path | None = None) -> dict[str, Any]:
    report_path = (reuse_dir or out_dir) / "offline_regression.json"
    if reuse_dir is not None and report_path.is_file():
        run = {
            "command": [],
            "exit_code": 0,
            "timed_out": False,
            "elapsed_ms": 0.0,
            "reused": True,
        }
    else:
        command = [
            sys.executable,
            "tools/run_closed_loop_benchmark.py",
            "--manifest",
            str(DEFAULT_MANIFEST.relative_to(ROOT)),
            "--mode",
            "baseline",
            "--repeats",
            str(repeats),
            "--output",
            str(report_path.relative_to(ROOT)),
        ]
        run = _run(command, out_dir / "offline_regression", timeout_s=900)
    summary = _closed_loop_summary(report_path)
    ok, failures = _gate(
        summary,
        exact={
            "pass_rate": 1.0,
            "case_stability_rate": 1.0,
            "strategy_contract_pass_rate": 1.0,
            "code_null_rate": 1.0,
            "repair_success_rate": 1.0,
            "safe_stop_correct_rate": 1.0,
        },
    )
    return _profile_result(
        "offline_regression",
        "PASS" if run["exit_code"] == 0 and ok else "FAIL",
        "; ".join(failures),
        run=run,
        report=str(report_path),
        summary=summary,
    )


def run_codearts(
    out_dir: Path,
    allow_live: bool,
    limit: int | None,
    reuse_dir: Path | None = None,
) -> dict[str, Any]:
    if not allow_live:
        return _profile_result("codearts_online", "BLOCKED", "需要 --allow-live 才允许真实 CodeArts 调用")

    focused_path = (reuse_dir or out_dir) / "codearts_testsets.json"
    if reuse_dir is not None:
        focused_run = {
            "command": [],
            "exit_code": 0,
            "timed_out": False,
            "elapsed_ms": 0.0,
            "reused": True,
        }
    else:
        focused_command = [
            sys.executable,
            "tools/run_codearts_testsets.py",
            "--set",
            "normal_scale_functional",
            "--set",
            "normal_scale_semantic",
            "--set",
            "normal_scale_safety",
            "--set",
            "normal_scale_stability",
            "--set",
            "normal_scale_resilience",
            "--live",
            "--policy",
            "quality",
            "--repeats",
            "3",
            "--transport-retries",
            "2",
            "--retry-backoff-s",
            "2",
            "--pure",
            "--resume",
            "--output",
            str(focused_path.relative_to(ROOT)),
        ]
        if limit is not None:
            focused_command.extend(["--limit", str(limit)])
        focused_run = _run(focused_command, out_dir / "codearts_testsets", timeout_s=7200)
    focused = _json(focused_path) or {}
    focused_summary = dict(focused.get("summary") or {})

    integrated_path = (reuse_dir or out_dir) / "codearts_closed_loop.json"
    if reuse_dir is not None:
        integrated_run = {
            "command": [],
            "exit_code": 0,
            "timed_out": False,
            "elapsed_ms": 0.0,
            "reused": True,
        }
    else:
        integrated_command = [
            sys.executable,
            "tools/run_closed_loop_benchmark.py",
            "--manifest",
            str(DEFAULT_MANIFEST.relative_to(ROOT)),
            "--mode",
            "codearts",
            "--repeats",
            "3",
            "--policy",
            "quality",
            "--pure",
            "--transport-retries",
            "2",
            "--resume",
            "--output",
            str(integrated_path.relative_to(ROOT)),
        ]
        if limit is not None:
            integrated_command.extend(["--limit", str(limit)])
        integrated_run = _run(integrated_command, out_dir / "codearts_closed_loop", timeout_s=14400)
    integrated_summary = _closed_loop_summary(integrated_path)
    focused_ok, focused_failures = _gate(
        focused_summary,
        exact={"all_passed": True, "all_stable": True, "contract_failures": 0},
        greater={"provider_calls": 0},
    )
    integrated_ok, integrated_failures = _gate(
        integrated_summary,
        exact={"pass_rate": 1.0, "case_stability_rate": 1.0, "strategy_contract_pass_rate": 1.0, "code_null_rate": 1.0},
        greater={"provider_calls": 0},
    )
    ok = focused_run["exit_code"] == 0 and integrated_run["exit_code"] == 0 and focused_ok and integrated_ok
    status = "PASS" if ok and limit is None else ("PARTIAL" if ok else "FAIL")
    reason = "; ".join(focused_failures + integrated_failures)
    if ok and limit is not None:
        reason = "有界在线冒烟通过，尚未完成全量 CodeArts 验收"
    if reuse_dir is not None:
        reason = (reason + "; " if reason else "") + f"复用既有在线证据目录: {reuse_dir}"
    return _profile_result(
        "codearts_online",
        status,
        reason,
        focused_run=focused_run,
        focused_summary=focused_summary,
        integrated_run=integrated_run,
        integrated_report=str(integrated_path),
        integrated_summary=integrated_summary,
    )


def run_llm_generalization(
    out_dir: Path,
    allow_live: bool,
    limit: int | None,
    reuse_dir: Path | None = None,
) -> dict[str, Any]:
    if not allow_live:
        return _profile_result("llm_generalization", "BLOCKED", "需要 --allow-live 才允许 A/B/D LLM 调用")
    missing: list[str] = []
    if not _env_file_value(".env", "RIA_DEEPSEEK_API_KEY"):
        missing.append("RIA_DEEPSEEK_API_KEY")
    if _env_file_value("tracecoder_llm.env", "TRACECODER_LLM_MODE").lower() != "required":
        missing.append("TRACECODER_LLM_MODE=required")
    if not _env_file_value("tracecoder_llm.env", "TRACECODER_LLM_API_KEY"):
        missing.append("TRACECODER_LLM_API_KEY")
    if not _env_file_value("tracecoder_llm.env", "TRACECODER_LLM_MODEL"):
        missing.append("TRACECODER_LLM_MODEL")
    if missing:
        return _profile_result("llm_generalization", "BLOCKED", "缺少必要 LLM 配置: " + ", ".join(missing))

    report_path = (reuse_dir or out_dir) / "llm_generalization.json"
    if reuse_dir is not None and report_path.is_file() and limit is None:
        run = {
            "command": [],
            "exit_code": 0,
            "timed_out": False,
            "elapsed_ms": 0.0,
            "reused": True,
        }
    else:
        command = [
            sys.executable,
            "tools/run_closed_loop_benchmark.py",
            "--manifest",
            str(GENERALIZATION_MANIFEST.relative_to(ROOT)),
            "--mode",
            "intelligent",
            "--repeats",
            "3",
            "--policy",
            "quality",
            "--pure",
            "--transport-retries",
            "2",
            "--resume",
            "--output",
            str(report_path.relative_to(ROOT)),
        ]
        if limit is not None:
            command.extend(["--limit", str(limit)])
        run = _run(command, out_dir / "llm_generalization", timeout_s=7200)
    summary = _closed_loop_summary(report_path)
    ok, failures = _gate(
        summary,
        exact={"pass_rate": 1.0, "case_stability_rate": 1.0, "strategy_contract_pass_rate": 1.0},
        greater={"intent_llm_successes": 0, "provider_calls": 0, "tracecoder_llm_runs": 0},
    )
    status = "PASS" if run["exit_code"] == 0 and ok and limit is None else ("PARTIAL" if run["exit_code"] == 0 and ok else "FAIL")
    reason = "; ".join(failures)
    if run["exit_code"] == 0 and ok and limit is not None:
        reason = "有界泛化冒烟通过，尚未完成 30 道全量未见组合验收"
    return _profile_result(
        "llm_generalization",
        status,
        reason,
        run=run,
        report=str(report_path),
        summary=summary,
    )


def _run_real_profile(profile_id: str, out_dir: Path, allow_live: bool, ssh_key: str | None, camera: bool) -> dict[str, Any]:
    if not allow_live:
        return _profile_result(profile_id, "BLOCKED", "需要 --allow-live 才允许远程 Isaac Sim 调用")
    if not ssh_key:
        return _profile_result(profile_id, "BLOCKED", "未提供 SSH 私钥；为避免密码交互和不可审计运行，未启动远程验收")
    manifest = "testdata/benchmark/real_camera_isaac_cases.json" if camera else "testdata/benchmark/real_isaac_cases.json"
    runner = "tools/run_real_camera_acceptance_batch_v3.py" if camera else "tools/run_real_acceptance_batch.py"
    remote_runner = "tools/run_remote_camera_acceptance_v7.ps1" if camera else "tools/run_remote_ground_truth_acceptance_final.ps1"
    report_path = out_dir / ("camera_isaac.json" if camera else "isaac_ground_truth.json")
    command = [
        sys.executable,
        runner,
        "--manifest",
        manifest,
        "--repeats",
        "1",
        "--ssh-key",
        ssh_key,
        "--remote-runner",
        remote_runner,
        "--output",
        str(report_path.relative_to(ROOT)),
    ]
    run = _run(command, out_dir / ("camera_isaac" if camera else "isaac_ground_truth"), timeout_s=3600)
    report = _json(report_path) or {}
    summary = dict(report.get("summary") or {})
    if camera:
        camera_rows = [
            (record.get("stages") or {}).get("C") or {}
            for record in report.get("records") or []
            if ((record.get("stages") or {}).get("C") or {}).get("status")
        ]
        summary["camera_source_verified"] = bool(camera_rows) and all(
            row.get("perception_source") == "isaac_camera_rgbd"
            and row.get("online_pose_source") == "rgbd_depth_backprojection"
            for row in camera_rows
        )
        summary["ground_truth_used_for_online_pose"] = any(
            row.get("ground_truth_used_for_online_pose") is not False for row in camera_rows
        )
        expected = {
            "pass_rate": 1.0,
            "contract_pass_rate": 1.0,
            "camera_source_verified": True,
            "ground_truth_used_for_online_pose": False,
        }
    else:
        expected = {"pass_rate": 1.0, "contract_pass_rate": 1.0}
    ok, failures = _gate(summary, exact=expected)
    return _profile_result(
        profile_id,
        "PASS" if run["exit_code"] == 0 and ok else "FAIL",
        "; ".join(failures),
        run=run,
        report=str(report_path),
        summary=summary,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", choices=PROFILE_IDS, dest="profiles")
    parser.add_argument("--allow-live", action="store_true", help="允许真实 CodeArts、LLM 或远程 Isaac 调用")
    parser.add_argument("--offline-repeats", type=int, default=3)
    parser.add_argument("--online-limit", type=int, default=None, help="每个在线清单最多运行 N 道；设置后结果最多为 PARTIAL")
    parser.add_argument("--llm-limit", type=int, default=None, help="泛化清单最多运行 N 道；设置后结果最多为 PARTIAL")
    parser.add_argument(
        "--reuse-codearts-dir",
        type=Path,
        default=None,
        help="复用指定目录中已完成的 codearts_testsets.json 和 codearts_closed_loop.json，不重复调用云端",
    )
    parser.add_argument(
        "--reuse-acceptance-dir",
        type=Path,
        default=None,
        help="复用指定最终验收目录中的离线、CodeArts 和 LLM JSON，不重复调用已完成的流程",
    )
    parser.add_argument("--ssh-key", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if args.offline_repeats < 1:
        parser.error("--offline-repeats 必须大于 0")
    if args.online_limit is not None and args.online_limit < 1:
        parser.error("--online-limit 必须大于 0")
    if args.llm_limit is not None and args.llm_limit < 1:
        parser.error("--llm-limit 必须大于 0")
    profiles = args.profiles or list(PROFILE_IDS)
    reuse_dir = args.reuse_acceptance_dir or args.reuse_codearts_dir
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPORT_ROOT / f"final-acceptance-{timestamp}"
    results: list[dict[str, Any]] = []
    for profile in profiles:
        if profile == "offline_regression":
            results.append(run_offline(out_dir, args.offline_repeats, reuse_dir))
        elif profile == "codearts_online":
            results.append(run_codearts(out_dir, args.allow_live, args.online_limit, reuse_dir))
        elif profile == "llm_generalization":
            results.append(run_llm_generalization(out_dir, args.allow_live, args.llm_limit, reuse_dir))
        elif profile == "isaac_hil_ground_truth":
            results.append(_run_real_profile(profile, out_dir, args.allow_live, args.ssh_key, False))
        elif profile == "camera_perception_hil":
            results.append(_run_real_profile(profile, out_dir, args.allow_live, args.ssh_key, True))

    passed = sum(item["status"] == "PASS" for item in results)
    report = {
        "schema_version": "final-acceptance-report.v1",
        "matrix": str(MATRIX_PATH),
        "started_at": timestamp,
        "allow_live": args.allow_live,
        "profiles": results,
        "summary": {
            "profiles": len(results),
            "passed": passed,
            "blocked": sum(item["status"] == "BLOCKED" for item in results),
            "partial": sum(item["status"] == "PARTIAL" for item in results),
            "failed": sum(item["status"] == "FAIL" for item in results),
            "verdict": "PASS" if passed == len(results) else "NOT_READY",
        },
    }
    output = args.output or (out_dir / "final_acceptance_report.json")
    _write(output, report)
    print(json.dumps({"output": str(output), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
