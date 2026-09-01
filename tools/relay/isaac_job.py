from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from tools.relay.runtime_protocol import atomic_write_json, validate_job


class RuntimeRemote(Protocol):
    def upload_job(self, remote_path: str, job: dict[str, Any]) -> None: ...

    def read_text(self, remote_path: str) -> str | None: ...

    def read_json(self, remote_path: str) -> object: ...

    def exists(self, remote_path: str) -> bool: ...

    def cleanup_run(self, run_id: str) -> None: ...


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
            while True:
                for event in self._new_events(
                    self.config.remote.read_text(paths["events"]), last_sequence
                ):
                    emit(event)
                    last_sequence = event["sequence"]
                if self.config.remote.exists(paths["complete"]):
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"persistent Isaac worker timed out for {run_id}")
                time.sleep(self.config.poll_interval_s)

            completion = self.config.remote.read_json(paths["complete"])
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
