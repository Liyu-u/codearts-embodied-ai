"""tools/orchestrate/orchestrator.py —— 一键远程编排主编排。

流程：PREPARE(本地A/B) → UPLOAD(白名单打包+上传) → EXECUTE(远程容器)
→ DOWNLOAD(证据+契约校验) → FEEDBACK(本地D) → CLEANUP(容器回收)。

任一阶段失败均进入 CLEANUP（finally 语义），并输出 `remote-isaac-run.v1`
制品与失败现场文件。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from integration.adapters import intent as intent_adapter
from integration.adapters import strategy as strategy_adapter
from integration.adapters import tracecoder as tracecoder_adapter
from integration.strategy_policy import normalize_capabilities, validate_strategy

from tools.orchestrate.bundle import BundleBuilder, SensitiveFileError
from tools.orchestrate.remote import RemoteChannel
from tools.orchestrate.supervisor import StageError, StageSupervisor
from tools.orchestrate.types import (
    OrchestrationConfig,
    OrchestrationResult,
    RemoteIsaacRunArtifact,
    StageReport,
    artifact_to_dict,
    validate_config,
    validate_run_id,
)
from tools.orchestrate.validate import EvidenceValidator

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PERCEPTION_SOURCE = "mock.scene"

_EXECUTOR_ENTRYPOINT = "tools/run_executor_acceptance.py"


@dataclass
class _StageArtifacts:
    perception: dict | None = None
    task: dict | None = None
    strategy: dict | None = None
    execution: dict | None = None
    feedback: dict | None = None
    perception_path: Path | None = None
    task_path: Path | None = None
    strategy_path: Path | None = None
    execution_path: Path | None = None
    remote_root: str = ""
    container_id: str | None = None


def orchestrate(
    config: OrchestrationConfig,
    *,
    logger=None,
    command_runner=None,
) -> OrchestrationResult:
    logger = logger or (lambda text: None)
    errors = validate_config(config)
    if errors:
        raise ValueError("配置非法: " + "; ".join(errors))
    validate_run_id(config.out_dir.name if config.out_dir else (uuid4().hex[:8]))

    run_id = (
        config.out_dir.name
        if config.out_dir is not None and config.out_dir.name
        else _new_run_id()
    )
    validate_run_id(run_id)
    out_dir = config.out_dir or (REPO_ROOT / "reports" / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    supervisor = StageSupervisor(config=config, logger=logger)
    artifacts = _StageArtifacts(remote_root=f"{config.remote_base}/codearts-{run_id}")
    bundle = BundleBuilder(repo_root=REPO_ROOT)
    channel = RemoteChannel(config, command_runner=command_runner)
    validator = EvidenceValidator()

    failure_class: str | None = None
    status = "SUCCEEDED"
    retry_command: str | None = None

    try:
        _prepare(config, out_dir, artifacts, supervisor)
        if config.backend == "remote_isaac":
            _upload(config, out_dir, bundle, channel, artifacts, supervisor, run_id)
            _execute(config, channel, artifacts, supervisor, run_id)
            _download(config, out_dir, channel, artifacts, supervisor, validator)
        else:
            _execute_mock(config, out_dir, artifacts, supervisor)
        _feedback(config, out_dir, artifacts, supervisor)
    except StageError as exc:
        status = "FAILED"
        failure_class = exc.failure_class
        logger(f"[FAILED] failure_class={exc.failure_class}: {exc.message}")
    except SensitiveFileError as exc:
        status = "FAILED"
        failure_class = "runner"
        logger(f"[FAILED] {exc}")
    except Exception as exc:  # noqa: BLE001 - boundary keeps runner classification
        status = "FAILED"
        failure_class = "runner"
        logger(f"[FAILED] 未预期异常: {exc}")
    finally:
        _cleanup(config, channel, artifacts, supervisor, run_id, out_dir, logger)

    result = _assemble_result(
        config,
        run_id,
        status,
        failure_class,
        supervisor.report(),
        out_dir,
        artifacts,
    )
    _write_artifacts(config, run_id, out_dir, artifacts, result)
    return result


def _new_run_id() -> str:
    return "orchestrate-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# 阶段实现
# --------------------------------------------------------------------------


def _prepare(
    config: OrchestrationConfig,
    out_dir: Path,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
) -> None:
    def work() -> None:
        from modules.perception.service import observe_scene

        previous_mode = None
        if config.backend == "mock":
            previous_mode = os.environ.get("CODEARTS_STRATEGY_MODE")
            os.environ["CODEARTS_STRATEGY_MODE"] = "off"

        try:
            perception = observe_scene({"scene_id": config.scene_id})
            artifacts.perception = perception
            artifacts.perception_path = _write_json(out_dir, "perception_input.json", perception)

            intent_input = {
                "instruction": config.instruction,
                "perception": perception,
                "correlation_id": config.scene_id,
            }
            task = intent_adapter.run(intent_input)
            artifacts.task = task
            artifacts.task_path = _write_json(out_dir, "task.json", task)
            if task.get("status") != "READY":
                reasons = task.get("blocking_reasons") or ["task status != READY"]
                raise StageError(
                    "A 意图理解未就绪: " + "; ".join(reasons),
                    failure_class="runner",
                )

            strategy = strategy_adapter.run({**task, "capabilities": normalize_capabilities()})
            artifacts.strategy = strategy
            artifacts.strategy_path = _write_json(out_dir, "strategy.json", strategy)
            if strategy.get("blocked") or strategy.get("success") is False:
                reasons = strategy.get("blocking_reasons") or ["strategy blocked"]
                raise StageError(
                    "B 策略生成被阻断: " + "; ".join(reasons),
                    failure_class="runner",
                )
            validation = validate_strategy(
                strategy,
                task=task,
                capabilities=normalize_capabilities(),
            )
            if not validation["passed"]:
                raise StageError(
                    "策略未通过共享安全校验: " + "; ".join(validation["errors"]),
                    failure_class="runner",
                )
        finally:
            if previous_mode is not None:
                os.environ["CODEARTS_STRATEGY_MODE"] = previous_mode
            else:
                os.environ.pop("CODEARTS_STRATEGY_MODE", None)

    supervisor.run_stage("PREPARE", "本地 A/B 生成 task.v1 与 strategy.v1", work)


def _upload(
    config: OrchestrationConfig,
    out_dir: Path,
    bundle: BundleBuilder,
    channel: RemoteChannel,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
    run_id: str,
) -> None:
    def work() -> None:
        bundle_path = out_dir / f"codearts-{run_id}.tar.gz"
        bundle.build(bundle_path)
        mkdir_command = (
            f"mkdir -p '{artifacts.remote_root}/results' "
            f"&& chmod 777 '{artifacts.remote_root}' '{artifacts.remote_root}/results'"
        )
        mkdir_result = channel.run_command(mkdir_command, timeout_s=config.ssh_timeout_s)
        if not mkdir_result.ok:
            raise StageError(
                "远程目录创建失败: " + mkdir_result.stderr.strip(),
                failure_class="transport_auth",
                retryable=True,
            )
        strategy_path = out_dir / "strategy.json"
        upload_result = channel.upload(bundle_path, f"{artifacts.remote_root}/codearts-bundle.tar.gz")
        if not upload_result.ok:
            raise StageError(
                "代码包上传失败: " + upload_result.stderr.strip(),
                failure_class="transport_auth",
                retryable=True,
            )
        strategy_upload = channel.upload(strategy_path, f"{artifacts.remote_root}/strategy.json")
        if not strategy_upload.ok:
            raise StageError(
                "策略上传失败: " + strategy_upload.stderr.strip(),
                failure_class="transport_auth",
                retryable=True,
            )

    supervisor.run_stage(
        "UPLOAD",
        "白名单打包与 SSH/SCP 上传",
        work,
        timeout_s=config.ssh_timeout_s,
        failure_class="transport_auth",
        retryable=True,
    )


def _execute(
    config: OrchestrationConfig,
    channel: RemoteChannel,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
    run_id: str,
) -> None:
    def work() -> None:
        remote = artifacts.remote_root
        script = _build_remote_run_script(config, remote, run_id)
        result = channel.run_command(
            script,
            timeout_s=config.ssh_timeout_s + config.execution_timeout_s + 60,
        )
        if not result.ok:
            raise StageError(
                "远程容器执行失败: " + result.stderr.strip(),
                failure_class="runner",
            )
        if "REPORT_READY" not in result.stdout:
            raise StageError(
                "远程执行未产出 REPORT_READY: " + result.stdout.strip()[-500:],
                failure_class="runner",
            )

    supervisor.run_stage(
        "EXECUTE",
        "远程 Isaac 容器执行与轮询",
        work,
        timeout_s=config.container_timeout_s + config.execution_timeout_s,
        failure_class="runner",
    )


def _build_remote_run_script(config: OrchestrationConfig, remote: str, run_id: str) -> str:
    device = config.device
    return "\n".join(
        [
            "set -eu",
            f"rm -f '{remote}/results/execution.json' '{remote}/results/progress.jsonl' '{remote}/results/container.log'",
            f"tar -xzf '{remote}/codearts-bundle.tar.gz' -C '{remote}'",
            f"nohup setsid docker run --rm --entrypoint bash --gpus 'device=0' --network none -u 1234:1234 \\",
            "  -e ACCEPT_EULA=Y -e PRIVACY_CONSENT=N \\",
            "  -e ISAACSIM_ASSET_ROOT=/isaacsim_assets/Assets/Isaac/6.0 \\",
            f"  -v '{remote}:/workspace' \\",
            "  -v /data/stu_01/isaac_assets:/isaacsim_assets:ro \\",
            "  nvcr.io/nvidia/isaac-sim:6.0.0 \\",
            f"  -lc 'cd /isaac-sim && ./python.sh /workspace/{_EXECUTOR_ENTRYPOINT} --device {device} --result-dir /workspace/results --strategy-file /workspace/strategy.json --/app/headless=true --/persistent/isaac/asset_root/default=/isaacsim_assets/Assets/Isaac/6.0' \\",
            f"  > '{remote}/results/container.log' 2>&1 < /dev/null &",
            "docker_pid=$!",
            f"echo \"$docker_pid\" > '{remote}/results/docker.pid'",
            "for attempt in $(seq 1 600); do",
            f"  if [ -s '{remote}/results/execution.json' ]; then",
            "    echo REPORT_READY",
            "    exit 0",
            "  fi",
            '  if ! kill -0 "$docker_pid" 2>/dev/null; then',
            "    echo CONTAINER_EXITED",
            f"    tail -n 80 '{remote}/results/container.log' || true",
            "    exit 1",
            "  fi",
            "  sleep 2",
            "done",
            "echo ISAAC_TIMEOUT",
            f"tail -n 80 '{remote}/results/container.log' || true",
            'kill "$docker_pid" 2>/dev/null || true',
            "exit 1",
        ]
    )


def _download(
    config: OrchestrationConfig,
    out_dir: Path,
    channel: RemoteChannel,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
    validator: EvidenceValidator,
) -> None:
    def work() -> None:
        remote = artifacts.remote_root
        files = {
            "execution.json": "execution.json",
            "perception.json": "perception.json",
            "progress.jsonl": "progress.jsonl",
            "container.log": "container.log",
        }
        for remote_name, local_name in files.items():
            result = channel.download(
                f"{remote}/results/{remote_name}",
                out_dir / local_name,
            )
            if not result.ok and remote_name not in {"perception.json", "progress.jsonl", "container.log"}:
                raise StageError(
                    f"证据下载失败 {remote_name}: {result.stderr.strip()}",
                    failure_class="transport_auth",
                    retryable=True,
                )
        execution_path = out_dir / "execution.json"
        if not execution_path.exists():
            raise StageError("execution.json 未下载成功", failure_class="contract")
        artifacts.execution_path = execution_path
        if (out_dir / "perception.json").exists():
            artifacts.perception_path = out_dir / "perception.json"

        verdict, perception_verdict = validator.validate_all(
            artifacts.perception_path, execution_path
        )
        errors: list[str] = list(verdict.errors)
        if perception_verdict is not None and not perception_verdict.passed:
            errors.extend(perception_verdict.errors)
        if not verdict.passed:
            raise StageError(
                "执行证据校验未通过: " + "; ".join(errors),
                failure_class=verdict.failure_class or "contract",
            )
        with execution_path.open("r", encoding="utf-8") as handle:
            artifacts.execution = json.load(handle)

    supervisor.run_stage(
        "DOWNLOAD",
        "下载执行证据与契约校验",
        work,
        timeout_s=max(120, config.ssh_timeout_s * 2),
        failure_class="contract",
    )


def _execute_mock(
    config: OrchestrationConfig,
    out_dir: Path,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
) -> None:
    def work() -> None:
        from integration.contract_validation import assert_contract
        from modules.executor.mock_backend import MockBackend
        from modules.executor.strategy_interpreter import StrategyInterpreter

        backend = MockBackend.from_perception(artifacts.perception)
        interpreter = StrategyInterpreter(backend)
        execution = interpreter.run(artifacts.strategy)
        assert_contract(execution, "execution.v1")
        artifacts.execution = execution
        artifacts.execution_path = _write_json(out_dir, "execution.json", execution)

    supervisor.run_stage(
        "EXECUTE",
        "Mock 后端本地执行",
        work,
        timeout_s=config.execution_timeout_s,
        failure_class="runner",
    )


def _feedback(
    config: OrchestrationConfig,
    out_dir: Path,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
) -> None:
    def work() -> None:
        if artifacts.execution is None:
            raise StageError("缺少 execution 证据，无法运行 D 反馈", failure_class="runner")
        feedback_input = {
            "task": artifacts.task,
            "strategy": artifacts.strategy,
            "execution": artifacts.execution,
            "perception": artifacts.perception,
            "capabilities": normalize_capabilities(),
            "run_id": config.out_dir.name if config.out_dir else "",
            "retry_count": 0,
            "tracecoder_context": {"retry_count": 0},
        }
        feedback = tracecoder_adapter.run(feedback_input)
        artifacts.feedback = feedback
        _write_json(out_dir, "feedback.json", feedback)

    supervisor.run_stage(
        "FEEDBACK",
        "本地 D TraceCoder 反馈回跑",
        work,
        timeout_s=300,
        failure_class="safety_or_execution",
    )


def _cleanup(
    config: OrchestrationConfig,
    channel: RemoteChannel,
    artifacts: _StageArtifacts,
    supervisor: StageSupervisor,
    run_id: str,
    out_dir: Path,
    logger,
) -> None:
    def work() -> None:
        if config.backend == "remote_isaac":
            result = channel.cleanup_containers([artifacts.remote_root])
            if result.warnings:
                logger("[CLEANUP] 清理告警: " + "; ".join(result.warnings))
            channel.run_command(
                f"rm -f '{artifacts.remote_root}/codearts-bundle.tar.gz'",
                timeout_s=config.ssh_timeout_s,
            )

    try:
        supervisor.run_stage(
            "CLEANUP",
            "容器回收与远程 bundle 清理",
            work,
            timeout_s=60,
            failure_class="runner",
        )
    except StageError as exc:
        logger(f"[CLEANUP] 清理失败，不阻塞退出: {exc.message}")


# --------------------------------------------------------------------------
# 制品汇总与落盘
# --------------------------------------------------------------------------


def _assemble_result(
    config: OrchestrationConfig,
    run_id: str,
    status: str,
    failure_class: str | None,
    stages: list[StageReport],
    out_dir: Path,
    artifacts: _StageArtifacts,
) -> OrchestrationResult:
    artifact_paths: dict[str, Path] = {}
    mapping = {
        "task": artifacts.task_path,
        "strategy": artifacts.strategy_path,
        "execution": artifacts.execution_path,
    }
    for name, path in mapping.items():
        if path is not None:
            artifact_paths[name] = path
    artifact_paths["remote-isaac-run"] = out_dir / "remote-isaac-run.json"
    artifact_paths["stage-report"] = out_dir / "stage_report.json"
    if artifacts.perception_path is not None:
        artifact_paths["perception"] = artifacts.perception_path
    if (out_dir / "feedback.json").exists():
        artifact_paths["feedback"] = out_dir / "feedback.json"
    retry_command = None
    if status == "FAILED":
        retry_command = _build_retry_command(config)
    return OrchestrationResult(
        run_id=run_id,
        status=status,
        failure_class=failure_class,
        stages=stages,
        artifact_paths=artifact_paths,
        retry_command=retry_command,
    )


def _build_retry_command(config: OrchestrationConfig) -> str:
    parts = [
        "python -m tools.orchestrate",
        f"--instruction {shlex_quote(config.instruction)}",
        f"--scene {config.scene_id}",
    ]
    if config.backend == "remote_isaac":
        parts += [
            f"--server {config.server}",
            f"--port {config.port}",
            f"--user {config.user}",
            f"--remote-base {config.remote_base}",
        ]
    parts.append(f"--backend {config.backend}")
    return " ".join(parts)


def shlex_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _write_artifacts(
    config: OrchestrationConfig,
    run_id: str,
    out_dir: Path,
    artifacts: _StageArtifacts,
    result: OrchestrationResult,
) -> None:
    artifact = RemoteIsaacRunArtifact(
        run_id=run_id,
        server=config.server,
        port=config.port,
        user=config.user,
        auth_mode=config.auth_mode,
        device=config.device,
        status=result.status,
        failure_class=result.failure_class,
        perception_source=DEFAULT_PERCEPTION_SOURCE,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_json(out_dir, "remote-isaac-run.json", artifact_to_dict(artifact))
    _write_json(
        out_dir,
        "stage_report.json",
        {"run_id": run_id, "stages": [vars(stage) for stage in result.stages]},
    )


def _write_json(out_dir: Path, name: str, payload: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path