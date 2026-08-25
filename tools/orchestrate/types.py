"""tools/orchestrate/types.py —— 编排器强类型数据模型。

字段与 design 2.2.2.1 / spec 6.1 完全对齐，全部使用冻结数据类。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

STAGE_NAMES = ("PREPARE", "UPLOAD", "EXECUTE", "DOWNLOAD", "FEEDBACK", "CLEANUP")

FailureClass = Literal[
    "transport_auth", "contract", "safety_or_execution", "runner"
]

Device = Literal["cpu", "cuda", "cuda:0"]
AuthMode = Literal["key", "interactive", "batch"]

_EXIT_CODES = {
    "transport_auth": 10,
    "contract": 20,
    "safety_or_execution": 30,
    "runner": 40,
}

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class OrchestrationConfig:
    instruction: str
    scene_id: str
    server: str
    port: int
    user: str
    remote_base: str
    device: Device = "cuda"
    auth_mode: AuthMode = "key"
    key_path: Path | None = None
    ssh_timeout_s: int = 30
    container_timeout_s: int = 180
    execution_timeout_s: int = 900
    transport_retries: int = 2
    backend: Literal["mock", "remote_isaac"] = "remote_isaac"
    out_dir: Path | None = None


@dataclass(frozen=True)
class StageReport:
    stage: Literal["PREPARE", "UPLOAD", "EXECUTE", "DOWNLOAD", "FEEDBACK", "CLEANUP"]
    action: str
    duration_ms: int
    outcome: str
    failure_class: str | None = None


@dataclass(frozen=True)
class RemoteIsaacRunArtifact:
    run_id: str
    server: str
    port: int
    user: str
    auth_mode: str
    device: str
    status: str
    failure_class: str | None
    perception_source: str
    completed_at: str


@dataclass(frozen=True)
class OrchestrationResult:
    run_id: str
    status: Literal["SUCCEEDED", "FAILED"]
    failure_class: str | None
    stages: list[StageReport]
    artifact_paths: dict[str, Path]
    retry_command: str | None


def exit_code_for(failure_class: str | None) -> int:
    if failure_class is None:
        return 0
    return _EXIT_CODES.get(failure_class, 40)


def validate_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not run_id or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(
            "run_id 仅允许字母/数字/点/下划线/短横线: %r" % run_id
        )


def validate_config(config: OrchestrationConfig) -> list[str]:
    errors: list[str] = []
    if not config.instruction or not config.instruction.strip():
        errors.append("instruction 必须是非空自然语言指令")
    if not config.scene_id or not config.scene_id.strip():
        errors.append("scene_id 必须是非空场景 ID")
    if config.backend == "remote_isaac":
        if not config.server or not config.server.strip():
            errors.append("server 必填")
        if not (1 <= config.port <= 65535):
            errors.append("port 必须在 1-65535")
        if not config.user or not config.user.strip():
            errors.append("user 必填")
        if not config.remote_base or not config.remote_base.strip():
            errors.append("remote_base 必填")
        if config.auth_mode == "key" and config.key_path is None:
            errors.append("key 认证模式必须提供 --key-path")
    if config.ssh_timeout_s <= 0 or config.container_timeout_s <= 0 or (
        config.execution_timeout_s <= 0
    ):
        errors.append("各阶段超时必须为正整数")
    if config.transport_retries < 0:
        errors.append("transport_retries 不能为负数")
    return errors


def artifact_to_dict(artifact: RemoteIsaacRunArtifact) -> dict:
    return asdict(artifact)


def stage_reports_to_dicts(stages: list[StageReport]) -> list[dict]:
    return [asdict(stage) for stage in stages]