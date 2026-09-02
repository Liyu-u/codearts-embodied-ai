from __future__ import annotations

import base64
import json
import shlex
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol
from uuid import uuid4

from tools.relay.runtime_protocol import atomic_write_json, validate_job, validate_run_id


class RuntimeRemote(Protocol):
    def upload_job(self, remote_path: str, job: dict[str, Any]) -> None: ...

    def read_text(self, remote_path: str) -> str | None: ...

    def read_json(self, remote_path: str) -> object: ...

    def exists(self, remote_path: str) -> bool: ...

    def cleanup_run(self, run_id: str) -> None: ...


class OpenSSHRuntimeRemote:
    def __init__(
        self,
        *,
        server: str,
        port: int,
        user: str,
        ssh_key: str | Path,
        known_hosts: str | Path,
        remote_root: str = "/data/stu_01/workspace/live-runtime",
        connect_timeout_s: int = 12,
        run_command=subprocess.run,
    ) -> None:
        if not server or not user or port <= 0 or port > 65_535:
            raise ValueError("valid SSH server, user and port are required")
        root = PurePosixPath(remote_root)
        if not remote_root.startswith("/") or ".." in root.parts:
            raise ValueError("remote_root must be a safe absolute POSIX path")
        key = Path(ssh_key).expanduser().resolve()
        hosts = Path(known_hosts).expanduser().resolve()
        if not key.is_file():
            raise FileNotFoundError(f"SSH key not found: {key}")
        if not hosts.is_file():
            raise FileNotFoundError(f"known_hosts not found: {hosts}")
        self.spec = f"{user}@{server}"
        self.remote_root = str(root)
        self._run_command = run_command
        common = [
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={int(connect_timeout_s)}",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={hosts}",
            "-i",
            str(key),
        ]
        self._ssh = ["ssh", "-n", "-T", "-p", str(port), *common]
        self._scp = ["scp", "-P", str(port), *common]

    @staticmethod
    def encode_command(command: str) -> str:
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
        return f"printf '%s' '{encoded}' | base64 -d | bash"

    @staticmethod
    def decode_command(encoded_command: str) -> str:
        marker = "printf '%s' '"
        suffix = "' | base64 -d | bash"
        if not encoded_command.startswith(marker) or not encoded_command.endswith(suffix):
            raise ValueError("not an encoded relay command")
        value = encoded_command[len(marker) : -len(suffix)]
        return base64.b64decode(value).decode("utf-8")

    def _safe_path(self, remote_path: str) -> str:
        path = PurePosixPath(remote_path)
        root = PurePosixPath(self.remote_root)
        if not remote_path.startswith("/") or ".." in path.parts:
            raise ValueError("unsafe remote path")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("remote path is outside runtime root") from exc
        return str(path)

    def _run(self, command: str, *, check: bool = True):
        return self._run_command(
            [*self._ssh, self.spec, self.encode_command(command)],
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            check=check,
        )

    def upload_job(self, remote_path: str, job: dict[str, Any]) -> None:
        target = self._safe_path(remote_path)
        remote_temp = f"{target}.{uuid4().hex}.tmp"
        with tempfile.TemporaryDirectory(prefix="codearts-relay-") as directory:
            local = Path(directory) / "job.json"
            atomic_write_json(local, job)
            self._run(f"mkdir -p {shlex.quote(str(PurePosixPath(target).parent))}")
            self._run_command(
                [*self._scp, str(local), f"{self.spec}:{remote_temp}"],
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                check=True,
            )
            self._run(f"mv {shlex.quote(remote_temp)} {shlex.quote(target)}")

    def read_text(self, remote_path: str) -> str | None:
        target = self._safe_path(remote_path)

        existence = self._run(
            f"test -e {shlex.quote(target)}",
            check=False,
        )
        if existence.returncode == 1:
            return None
        if existence.returncode != 0:
            raise subprocess.CalledProcessError(
                existence.returncode,
                existence.args,
                output=existence.stdout,
                stderr=existence.stderr,
            )

        result = self._run(
            f"cat {shlex.quote(target)}",
            check=False,
        )
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )

        return result.stdout

    def read_json(self, remote_path: str) -> object:
        text = self.read_text(remote_path)
        return None if text is None else json.loads(text)

    def exists(self, remote_path: str) -> bool:
        target = self._safe_path(remote_path)
        result = self._run(f"test -s {shlex.quote(target)}", check=False)
        if result.returncode not in {0, 1}:
            raise subprocess.CalledProcessError(
                result.returncode,
                result.args,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result.returncode == 0

    def cleanup_run(self, run_id: str) -> None:
        safe = validate_run_id(run_id)
        inbox = self._safe_path(f"{self.remote_root}/inbox/{safe}.json")
        active = self._safe_path(f"{self.remote_root}/active/{safe}.json")
        self._run(f"rm -f -- {shlex.quote(inbox)} {shlex.quote(active)}")


@dataclass(frozen=True, slots=True)
class IsaacJobConfig:
    remote: RuntimeRemote
    remote_root: str
    local_result_root: Path
    timeout_s: float = 900.0
    poll_interval_s: float = 1.0

    def __post_init__(self) -> None:
        if not self.remote_root.startswith("/"):
            raise ValueError("remote_root must be an absolute POSIX path")
        if self.timeout_s <= 0 or self.poll_interval_s <= 0:
            raise ValueError("timeouts must be positive")


class IsaacJobRunner:
    _RESULTS = {
        "ISAAC_PREPARE_AND_PERCEIVE": ("perception.json",),
        "ISAAC_EXECUTE": ("execution.json", "final_pose.json"),
    }

    def __init__(self, config: IsaacJobConfig) -> None:
        self.config = config

    def _remote_paths(self, run_id: str) -> dict[str, str]:
        root = PurePosixPath(self.config.remote_root)
        return {
            "inbox": str(root / "inbox" / f"{run_id}.json"),
            "events": str(root / "events" / f"{run_id}.jsonl"),
            "result_dir": str(root / "results" / run_id),
            "complete": str(root / "results" / run_id / "complete.json"),
        }

    @staticmethod
    def _new_events(text: str | None, after_sequence: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for line in (text or "").splitlines():
            if not line.strip():
                continue
            value = json.loads(line)
            sequence = value.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError("runtime event sequence must be an integer")
            if sequence > after_sequence:
                events.append(value)
        events.sort(key=lambda item: item["sequence"])
        for previous, current in zip(events, events[1:]):
            if current["sequence"] <= previous["sequence"]:
                raise ValueError("runtime event sequences are not strictly ordered")
        return events

    @staticmethod
    def _completion_matches(
        completion: object,
        job: Mapping[str, Any],
    ) -> bool:
        if not isinstance(completion, dict):
            return False
        if completion.get("run_id") != job.get("run_id"):
            return False
        if completion.get("job_type") != job.get("job_type"):
            return False

        job_id = job.get("job_id")
        if job_id is not None and completion.get("job_id") != job_id:
            return False

        return True

    def run(
        self,
        job: dict[str, Any],
        emit: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        validated = validate_job(job)
        run_id = validated["run_id"]
        paths = self._remote_paths(run_id)
        uploaded = False
        last_sequence = 0
        deadline = time.monotonic() + self.config.timeout_s
        try:
            self.config.remote.upload_job(paths["inbox"], validated)
            uploaded = True
            completion: dict[str, Any] | None = None
            while True:
                for event in self._new_events(
                    self.config.remote.read_text(paths["events"]), last_sequence
                ):
                    emit(event)
                    last_sequence = event["sequence"]

                candidate = self.config.remote.read_json(paths["complete"])
                if self._completion_matches(candidate, validated):
                    completion = candidate
                    break

                if time.monotonic() >= deadline:
                    raise TimeoutError(f"persistent Isaac worker timed out for {run_id}")
                time.sleep(self.config.poll_interval_s)

            if not isinstance(completion, dict):
                raise ValueError("runtime complete marker is not a JSON object")
            artifacts: dict[str, Any] = {}
            local_dir = self.config.local_result_root / run_id
            for name in self._RESULTS[validated["job_type"]]:
                remote_path = f"{paths['result_dir']}/{name}"
                value = self.config.remote.read_json(remote_path)
                if not isinstance(value, dict):
                    raise ValueError(f"required runtime artifact is missing: {name}")
                artifacts[name] = value
                atomic_write_json(local_dir / name, value)
            return {
                "run_id": run_id,
                "job_type": validated["job_type"],
                "completion": completion,
                "artifacts": artifacts,
                "last_sequence": last_sequence,
            }
        finally:
            if uploaded:
                self.config.remote.cleanup_run(run_id)
