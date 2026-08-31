"""Run one same-scene DeepSeek -> CodeArts -> Isaac Sim bridge acceptance case."""

from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.live_intelligent_e2e import (
    audit_documents,
    generate_intelligent_ab,
    generate_rule_ab,
    load_evidence_directory,
)
from tools.real_isaac_experiment import load_experiment_config, select_case


def build_container_command(
    *,
    remote_root: str,
    container_name: str,
    task_config: str,
    case_id: str,
    variant_id: str,
    seed: int,
    gpu_index: str,
    strategy_wait_s: int,
) -> str:
    strategy_args = f"--strategy-file /workspace/live_strategy.json --strategy-wait-s {int(strategy_wait_s)} "
    runner = (
        "cd /isaac-sim && ./python.sh /workspace/tools/run_ground_truth_executor_acceptance_v4.py "
        f"--device cuda --gpu-index {shlex.quote(gpu_index)} --seed {seed} "
        f"--experiment-run-id {shlex.quote(container_name)} --result-dir /workspace/results "
        f"--task-config /workspace/{shlex.quote(task_config)} "
        + strategy_args
        +
        f"--variant-id {shlex.quote(variant_id)} --case-id {shlex.quote(case_id)} --/app/headless=true "
        "--/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0"
    )
    return " ".join([
        "docker run -d --no-healthcheck",
        f"--name {shlex.quote(container_name)}",
        f"--gpus {shlex.quote('device=' + gpu_index)}",
        "--network none -u 1234:1234",
        "-e ACCEPT_EULA=Y -e PRIVACY_CONSENT=N",
        "-e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0",
        f"-v {shlex.quote(remote_root + ':/workspace')}",
        "-v /data/stu_01/isaac_assets:/isaacsim_assets:ro",
        "--entrypoint bash nvcr.io/nvidia/isaac-sim:6.0.0",
        "-lc",
        shlex.quote(runner),
    ])


class Remote:
    def __init__(self, server: str, port: int, user: str, key: Path):
        self.spec = f"{user}@{server}"
        self.base = ["ssh", "-n", "-T", "-p", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=no", "-i", str(key)]
        self.scp_base = ["scp", "-P", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "-o", "StrictHostKeyChecking=no", "-i", str(key)]

    def run(self, command: str, *, check: bool = True) -> subprocess.CompletedProcess[str]:
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        remote = f"printf '%s' '{encoded}' | base64 -d | bash"
        return subprocess.run([*self.base, self.spec, remote], text=True, capture_output=True, check=check)

    def upload(self, local: Path, remote: str) -> None:
        subprocess.run([*self.scp_base, str(local), f"{self.spec}:{remote}"], check=True)

    def download(self, remote: str, local: Path) -> None:
        subprocess.run([*self.scp_base, f"{self.spec}:{remote}", str(local)], check=True)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _bundle(path: Path, task_config: Path) -> None:
    members = ["contracts", "integration", "modules", "tools/__init__.py", "tools/run_executor_acceptance.py", "tools/real_isaac_experiment.py", "tools/live_intelligent_e2e.py", "tools/run_ground_truth_executor_acceptance.py", "tools/run_ground_truth_executor_acceptance_v4.py", str(task_config.relative_to(ROOT)).replace("\\", "/")]
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            archive.add(ROOT / member, arcname=member)


def _wait_remote_file(remote: Remote, path: str, *, timeout_s: int, container: str) -> None:
    deadline = time.monotonic() + timeout_s
    last_notice = 0.0
    while time.monotonic() < deadline:
        probe = remote.run(f"test -s {shlex.quote(path)}", check=False)
        if probe.returncode == 0:
            return
        running = remote.run(f"docker inspect -f '{{{{.State.Running}}}}' {shlex.quote(container)} 2>/dev/null || echo false", check=False)
        if "true" not in running.stdout.lower():
            # The runner writes execution/final-pose immediately before the
            # Isaac process exits.  Re-probe once after observing termination
            # so a normal fast shutdown cannot discard already-written proof.
            final_probe = remote.run(f"test -s {shlex.quote(path)}", check=False)
            if final_probe.returncode == 0:
                return
            raise RuntimeError(f"Isaac container exited before evidence file: {path}")
        if time.monotonic() - last_notice >= 30:
            print(json.dumps({"waiting_for": path, "container": container}, ensure_ascii=False), flush=True)
            last_notice = time.monotonic()
        time.sleep(2)
    raise TimeoutError(f"timed out waiting for {path}")


def run_case(args: argparse.Namespace) -> int:
    task_config = Path(args.task_config).resolve()
    config = load_experiment_config(task_config)
    case = select_case(config, args.case_id)
    out_dir = (ROOT / "reports" / args.run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    _write(out_dir / "input.json", {"run_id": args.run_id, "variant_id": args.variant, "case_id": args.case_id, "instruction": case["instruction"], "seed": args.seed})
    remote_root = f"{args.remote_base.rstrip('/')}/codearts-{args.run_id}"
    container = f"codearts-{args.run_id}"
    remote = Remote(args.server, args.port, args.user, Path(args.ssh_key).resolve())
    task: dict[str, Any] | None = None
    strategy: dict[str, Any] | None = None
    api_calls: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / "bundle.tar.gz"
        _bundle(bundle, task_config)
        remote.run(f"mkdir -p {shlex.quote(remote_root + '/results')} && chmod 777 {shlex.quote(remote_root)} {shlex.quote(remote_root + '/results')} && rm -f {shlex.quote(remote_root + '/live_strategy.json')}")
        remote.upload(bundle, remote_root + "/bundle.tar.gz")
        remote.run(f"tar -xzf {shlex.quote(remote_root + '/bundle.tar.gz')} -C {shlex.quote(remote_root)} && docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true")
        command = build_container_command(remote_root=remote_root, container_name=container, task_config=str(task_config.relative_to(ROOT)).replace("\\", "/"), case_id=args.case_id, variant_id=args.variant, seed=args.seed, gpu_index=args.gpu_index, strategy_wait_s=args.strategy_wait_s)
        started = remote.run(command)
        if not started.stdout.strip():
            raise RuntimeError("remote docker start returned no container id")
        try:
            perception_remote = remote_root + "/results/perception.json"
            _wait_remote_file(remote, perception_remote, timeout_s=args.startup_timeout_s, container=container)
            remote.download(perception_remote, out_dir / "perception.json")
            perception = json.loads((out_dir / "perception.json").read_text(encoding="utf-8"))
            if args.variant == "V0_RULE_BASELINE":
                task, strategy, api_calls = generate_rule_ab(case["instruction"], perception, correlation_id=args.run_id)
                _write(out_dir / "task.json", task)
                _write(out_dir / "strategy.json", strategy)
                _write(out_dir / "api_calls.json", api_calls)
                remote.upload(out_dir / "strategy.json", remote_root + "/live_strategy.json")
            else:
                task, strategy, api_calls = generate_intelligent_ab(case["instruction"], perception, correlation_id=args.run_id)
                _write(out_dir / "task.json", task)
                _write(out_dir / "api_calls.json", api_calls)
                if (
                    not strategy
                    or not api_calls["intent"]["succeeded"]
                    or api_calls["intent"].get("fallback")
                    or not api_calls["strategy"]["succeeded"]
                    or api_calls["strategy"].get("fallback")
                ):
                    raise RuntimeError("real A/B provider gate failed; Isaac execution remains blocked")
                _write(out_dir / "strategy.json", strategy)
                remote.upload(out_dir / "strategy.json", remote_root + "/live_strategy.json")
            execution_remote = remote_root + "/results/execution.json"
            _wait_remote_file(remote, execution_remote, timeout_s=args.execution_timeout_s, container=container)
            remote.run(f"docker logs {shlex.quote(container)} > {shlex.quote(remote_root + '/results/container.log')} 2>&1 || true", check=False)
            for name in ("execution.json", "strategy.json", "progress.jsonl", "container.log", "final_pose.json"):
                remote.download(remote_root + "/results/" + name, out_dir / name)
        finally:
            remote.run(f"docker logs {shlex.quote(container)} > {shlex.quote(remote_root + '/results/container.log')} 2>&1 || true", check=False)
            try:
                remote.download(remote_root + "/results/container.log", out_dir / "container.log")
            except subprocess.CalledProcessError:
                pass
            remote.run(f"docker rm -f {shlex.quote(container)} >/dev/null 2>&1 || true", check=False)
    if args.variant == "V4_FULL" and task and strategy:
        import os

        from integration.config.local_env import load_local_env

        load_local_env("tracecoder_llm.env", override=True)
        os.environ["TRACECODER_LLM_MODE"] = "required"
        # The adapter owns a process-level provider cache.  Rebuild it after
        # loading the dedicated TraceCoder environment so a prior import (for
        # example through an integration test) cannot silently retain an
        # unconfigured provider and skip the real DeepSeek feedback call.
        from modules.evaluator.tracecoder.llm_provider import LLMConfig, LLMProvider
        from integration.adapters.tracecoder import configure_llm, run as feedback_run
        configure_llm(mode="required", provider=LLMProvider(LLMConfig.from_env()))
        execution = json.loads((out_dir / "execution.json").read_text(encoding="utf-8"))
        perception = json.loads((out_dir / "perception.json").read_text(encoding="utf-8"))
        feedback = feedback_run({"task": task, "strategy": strategy, "execution": execution, "perception": perception, "run_id": args.run_id, "live_acceptance": True})
        _write(out_dir / "feedback.json", feedback)
        provenance = feedback.get("provenance") or {}
        stats = provenance.get("llm_stats") or {}
        request_ids = provenance.get("request_ids") or []
        api_calls["feedback"] = {
            "provider": "deepseek",
            "network_calls": int(stats.get("ok_calls") or 0),
            "failed_calls": int(stats.get("failed_calls") or 0),
            "total_calls": int(stats.get("calls") or 0),
            # A bounded provider retry may contain an individual transport
            # failure while still yielding a valid repair response.  Record
            # the failure count separately, but treat the feedback stage as
            # successful when at least one real call completed and produced a
            # request ID; the audit must not confuse retry telemetry with a
            # full-stage fallback.
            "succeeded": int(stats.get("ok_calls") or 0) > 0,
            "request_id": request_ids[0] if request_ids else None,
            "request_ids": request_ids,
            "request_id_source": "provider_response",
            "fallback": int(stats.get("fallback_calls") or 0) > 0,
            "model": provenance.get("model"),
        }
        _write(out_dir / "api_calls.json", api_calls)
    documents = load_evidence_directory(out_dir)
    audit = audit_documents(documents, args.variant)
    _write(out_dir / "audit.json", audit)
    _write(out_dir / "remote_run.json", {"run_id": args.run_id, "server": args.server, "variant_id": args.variant, "case_id": args.case_id, "audit_eligible": audit["eligible"]})
    print(json.dumps({"run_id": args.run_id, "audit": audit}, ensure_ascii=False), flush=True)
    return 0 if audit["eligible"] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-config", default="testdata/benchmark/real_isaac_supplement_v2.json")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--variant", choices=("V0_RULE_BASELINE", "V1_CODEARTS_POLICY", "V2_FULL_NO_D", "V4_FULL"), required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--server", default="10.16.0.40")
    parser.add_argument("--port", type=int, default=5122)
    parser.add_argument("--user", default="stu_01")
    parser.add_argument("--remote-base", default="/data/stu_01/workspace")
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--gpu-index", choices=("0", "1"), default="0")
    parser.add_argument("--strategy-wait-s", type=int, default=600)
    parser.add_argument("--startup-timeout-s", type=int, default=900)
    parser.add_argument("--execution-timeout-s", type=int, default=900)
    args = parser.parse_args()
    return run_case(args)


if __name__ == "__main__":
    raise SystemExit(main())
