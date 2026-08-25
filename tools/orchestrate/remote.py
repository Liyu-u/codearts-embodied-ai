"""tools/orchestrate/remote.py —— 远程通道（系统 ssh/scp 薄封装）。

仅封装系统 `ssh`/`scp`/`docker` 命令，不引入 paramiko 新依赖。
默认注入 `-o BatchMode=yes` 私钥认证，禁止交互密码输入。
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from tools.orchestrate.types import OrchestrationConfig


@dataclass(frozen=True)
class RemoteCommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class TransferResult:
    local: Path
    remote: str
    exit_code: int
    stderr: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class CleanupResult:
    scanned: int
    cleaned: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.warnings


class RemoteChannel:
    """对系统 ssh/scp/docker 命令的强类型薄封装。

    ``command_runner`` 可注入假执行器（测试用）；默认为 subprocess 执行。
    """

    def __init__(
        self,
        config: OrchestrationConfig,
        command_runner=None,
    ) -> None:
        self.config = config
        self._run_raw = command_runner or self._default_run

    @property
    def remote_spec(self) -> str:
        return f"{self.config.user}@{self.config.server}"

    def _base_ssh_args(self) -> list[str]:
        args = [
            "-n",
            "-T",
            "-p",
            str(self.config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=%d" % max(1, self.config.ssh_timeout_s),
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if self.config.auth_mode == "key" and self.config.key_path is not None:
            args += ["-i", str(self.config.key_path)]
        elif self.config.auth_mode == "interactive":
            args = [
                item
                for item in args
                if not item.startswith("BatchMode")
            ]
        return args

    def _default_run(
        self, argv: list[str], timeout_s: float
    ) -> RemoteCommandResult:
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return RemoteCommandResult(
                command=shlex.join(argv),
                exit_code=124,
                stdout=(exc.stdout or ""),
                stderr=(exc.stderr or "") + "\n[remote command timed out]",
            )
        except OSError as exc:
            return RemoteCommandResult(
                command=shlex.join(argv),
                exit_code=127,
                stdout="",
                stderr=f"failed to launch ssh/scp: {exc}",
            )
        return RemoteCommandResult(
            command=shlex.join(argv),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def run_command(self, command: str, timeout_s: int | None = None) -> RemoteCommandResult:
        timeout = float(timeout_s or self.config.ssh_timeout_s)
        argv = ["ssh", *self._base_ssh_args(), self.remote_spec, command]
        return self._run_raw(argv, timeout)

    def upload(self, local: Path, remote: str) -> TransferResult:
        local = Path(local)
        timeout = float(max(30, self.config.ssh_timeout_s * 2))
        argv = [
            "scp",
            "-P",
            str(self.config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=%d" % max(1, self.config.ssh_timeout_s),
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if self.config.auth_mode == "key" and self.config.key_path is not None:
            argv += ["-i", str(self.config.key_path)]
        argv += [str(local), f"{self.remote_spec}:{remote}"]
        result = self._run_raw(argv, timeout)
        return TransferResult(
            local=local,
            remote=remote,
            exit_code=result.exit_code,
            stderr=result.stderr,
        )

    def download(self, remote: str, local: Path) -> TransferResult:
        local = Path(local)
        timeout = float(max(30, self.config.ssh_timeout_s * 2))
        argv = [
            "scp",
            "-P",
            str(self.config.port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=%d" % max(1, self.config.ssh_timeout_s),
            "-o",
            "StrictHostKeyChecking=accept-new",
        ]
        if self.config.auth_mode == "key" and self.config.key_path is not None:
            argv += ["-i", str(self.config.key_path)]
        argv += [f"{self.remote_spec}:{remote}", str(local)]
        result = self._run_raw(argv, timeout)
        return TransferResult(
            local=local,
            remote=remote,
            exit_code=result.exit_code,
            stderr=result.stderr,
        )

    def cleanup_containers(self, workspace_roots: list[str]) -> CleanupResult:
        """扫描按工作空间标记的残留容器并 docker rm -f 兜底回收。"""
        scan_command = (
            "docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}'"
        )
        scan = self.run_command(scan_command, timeout_s=60)
        cleaned: list[str] = []
        warnings: list[str] = []
        scanned = 0
        if not scan.ok:
            return CleanupResult(
                scanned=0,
                cleaned=cleaned,
                warnings=[f"docker ps 扫描失败: {scan.stderr.strip()}"],
            )
        lines = [line.strip() for line in scan.stdout.splitlines() if line.strip()]
        matched: list[str] = []
        for line in lines:
            parts = line.split("|")
            if len(parts) < 3:
                continue
            container_id, container_name, image = parts[0], parts[1], parts[2]
            scanned += 1
            if self._container_matches_workspace(container_name, image, workspace_roots):
                matched.append(container_id)
        for container_id in matched:
            remove = self.run_command(f"docker rm -f {container_id}", timeout_s=60)
            if remove.ok:
                cleaned.append(container_id)
            else:
                warnings.append(
                    f"清理容器失败 {container_id}: {remove.stderr.strip()}"
                )
        return CleanupResult(scanned=scanned, cleaned=cleaned, warnings=warnings)

    @staticmethod
    def _container_matches_workspace(
        container_name: str, image: str, workspace_roots: list[str]
    ) -> bool:
        haystack = f"{container_name} {image}"
        if "isaac" not in haystack.lower():
            return False
        for root in workspace_roots or []:
            marker = root.rstrip("/").rsplit("/", 1)[-1]
            if marker and marker in haystack:
                return True
        return False