from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from tools.relay.runtime_protocol import (
    RuntimeLayout,
    atomic_append_event,
    atomic_write_json,
    validate_job,
    validate_run_id,
)


JobCallback = Callable[[dict[str, Any]], Mapping[str, Any]]
ResetCallback = Callable[[dict[str, Any]], None]


class LiveRuntimeWorker:
    _ARTIFACTS = {
        "ISAAC_PREPARE_AND_PERCEIVE": frozenset({"perception.json"}),
        "ISAAC_EXECUTE": frozenset({"execution.json", "final_pose.json"}),
    }

    def __init__(
        self,
        layout: RuntimeLayout,
        *,
        execute: JobCallback,
        prepare: JobCallback,
        reset: ResetCallback,
        worker_instance_id: str,
        world_id: str,
    ) -> None:
        if not worker_instance_id or not world_id:
            raise ValueError("worker_instance_id and world_id are required")
        self.layout = layout
        self.execute = execute
        self.prepare = prepare
        self.reset = reset
        self.worker_instance_id = worker_instance_id
        self.world_id = world_id
        self._ensure_layout()

        # Cross-UID handoff:
        # Windows Relay uploads jobs as host user stu_01, while the
        # persistent Isaac container consumes them as isaac-sim.
        # The inbox is therefore intentionally shared writable.
        (self.layout.root / "inbox").chmod(0o777)

        atomic_write_json(
            self.layout.root / "worker.json",
            {
                "schema_version": "live-worker.v1",
                "kit_instance_id": self.worker_instance_id,
                "worker_instance_id": self.worker_instance_id,
                "world_id": self.world_id,
            },
        )

    @property
    def provenance(self) -> dict[str, str]:
        return {
            "backend": "isaac",
            "kit_instance_id": self.worker_instance_id,
            "worker_instance_id": self.worker_instance_id,
            "world_id": self.world_id,
        }

    def _ensure_layout(self) -> None:
        if self.layout.root.exists() and self.layout.root.is_symlink():
            raise ValueError("runtime root must not be a symlink")
        for name in ("inbox", "active", "events", "results", "control", "rejected"):
            path = self.layout.root / name
            if path.exists() and path.is_symlink():
                raise ValueError(f"runtime {name} must not be a symlink")
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_object(path: Path) -> dict[str, Any]:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"runtime job is not a regular file: {path.name}")
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError("runtime job must be a JSON object")
        return value

    def _quarantine(self, path: Path) -> None:
        rejected = self.layout.root / "rejected" / f"{path.name}.{uuid4().hex}.rejected"
        os.replace(path, rejected)

    def _validate_job_file(self, path: Path) -> dict[str, Any]:
        try:
            value = self._load_object(path)
            validated = validate_job(value)
            if path.stem != validated["run_id"]:
                raise ValueError("job filename does not match run_id")
            return validated
        except Exception:
            if path.exists() and not path.is_symlink():
                self._quarantine(path)
            raise

    def _active_files(self) -> list[Path]:
        active_dir = self.layout.root / "active"
        files = sorted(active_dir.iterdir(), key=lambda item: item.name)
        if any(item.is_symlink() or not item.is_file() or item.suffix != ".json" for item in files):
            raise ValueError("active directory contains an unknown or unsafe file")
        if len(files) > 1:
            raise RuntimeError("persistent worker has more than one active job")
        return files

    def _completion_path(self, run_id: str) -> Path:
        result_dir = self.layout.for_run(run_id)["result_dir"]
        if result_dir.exists() and result_dir.is_symlink():
            raise ValueError("result directory must not be a symlink")
        return result_dir / "complete.json"

    def _completion_matches(self, job: Mapping[str, Any]) -> bool:
        path = self._completion_path(str(job["run_id"]))
        if not path.is_file():
            return False

        completion = self._load_object(path)

        if completion.get("run_id") != job.get("run_id"):
            return False
        if completion.get("job_type") != job.get("job_type"):
            return False

        job_id = job.get("job_id")
        if job_id is not None and completion.get("job_id") != job_id:
            return False

        return True

    def claim_next_job(self) -> dict[str, Any] | None:
        self._ensure_layout()
        if self._active_files():
            return None
        inbox = self.layout.root / "inbox"
        entries = sorted(inbox.iterdir(), key=lambda item: item.name)
        if any(item.is_symlink() or not item.is_file() or item.suffix != ".json" for item in entries):
            raise ValueError("inbox contains an unknown or unsafe file")
        if not entries:
            return None
        source = entries[0]
        job = self._validate_job_file(source)
        run_id = job["run_id"]
        if self._completion_matches(job):
            source.unlink()
            return {**job, "_duplicate_receipt": True}
        active = self.layout.for_run(run_id)["active"]
        os.replace(source, active)
        return job

    def _next_sequence(self, run_id: str) -> int:
        path = self.layout.for_run(run_id)["events"]
        maximum = 0
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise ValueError("event log must be a regular file")
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                event = json.loads(line)
                sequence = event.get("sequence") if isinstance(event, dict) else None
                if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
                    raise ValueError("persisted event sequence is invalid")
                maximum = max(maximum, sequence)
        return maximum + 1

    def _event(
        self,
        run_id: str,
        event_type: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        sequence = self._next_sequence(run_id)
        event = {
            "sequence": sequence,
            "event_id": f"{run_id}-{sequence}-{event_type.lower()}",
            "type": event_type,
            "stage": "C",
            "payload": deepcopy(dict(payload or {})),
            "provenance": self.provenance,
        }
        atomic_append_event(self.layout.for_run(run_id)["events"], event)
        return event

    def recover_active_job(self) -> dict[str, Any] | None:
        files = self._active_files()
        if not files:
            return None
        path = files[0]
        job = self._validate_job_file(path)
        if self._completion_matches(job):
            path.unlink()
            return {**job, "_duplicate_receipt": True}
        self._event(job["run_id"], "JOB_RECOVERED")
        return job

    def _with_provenance(self, value: Mapping[str, Any]) -> dict[str, Any]:
        document = deepcopy(dict(value))
        provenance = document.get("provenance")
        merged = dict(provenance) if isinstance(provenance, Mapping) else {}
        merged.setdefault("backend", "isaac")
        merged.update(
            {
                "kit_instance_id": self.worker_instance_id,
                "worker_instance_id": self.worker_instance_id,
                "world_id": self.world_id,
            }
        )
        document["provenance"] = merged
        return document

    def _persist_success(
        self, job: Mapping[str, Any], artifacts: Mapping[str, Any]
    ) -> dict[str, Any]:
        run_id = str(job["run_id"])
        expected = self._ARTIFACTS[str(job["job_type"])]
        if set(artifacts) != expected:
            raise ValueError(
                f"runtime artifacts must be exactly {sorted(expected)}"
            )
        result_dir = self.layout.for_run(run_id)["result_dir"]
        if result_dir.exists() and result_dir.is_symlink():
            raise ValueError("result directory must not be a symlink")
        result_dir.mkdir(parents=True, exist_ok=True)
        for name in sorted(expected):
            value = artifacts[name]
            if not isinstance(value, Mapping):
                raise ValueError(f"runtime artifact is not an object: {name}")
            atomic_write_json(result_dir / name, self._with_provenance(value))
        completion = {
            "schema_version": "live-completion.v1",
            "run_id": run_id,
            "job_type": job["job_type"],
            "status": "SUCCEEDED",
            "artifacts": sorted(expected),
            "provenance": self.provenance,
        }
        if job.get("job_id") is not None:
            completion["job_id"] = job["job_id"]
        return completion

    def _persist_failure(self, job: Mapping[str, Any], error: str) -> dict[str, Any]:
        run_id = str(job["run_id"])
        result_dir = self.layout.for_run(run_id)["result_dir"]
        result_dir.mkdir(parents=True, exist_ok=True)
        completion = {
            "schema_version": "live-completion.v1",
            "run_id": run_id,
            "job_type": job["job_type"],
            "status": "FAILED",
            "error": error,
            "provenance": self.provenance,
        }
        if job.get("job_id") is not None:
            completion["job_id"] = job["job_id"]
        return completion

    def process_once(self) -> dict[str, Any]:
        job = self.recover_active_job()
        if job is None:
            job = self.claim_next_job()
        if job is None:
            return {"status": "IDLE"}
        run_id = str(job["run_id"])
        if job.get("_duplicate_receipt"):
            return {"status": "DUPLICATE", "run_id": run_id}
        active = self.layout.for_run(run_id)["active"]
        self._event(run_id, "JOB_STARTED", payload={"job_type": job["job_type"]})
        try:
            if job["job_type"] == "ISAAC_PREPARE_AND_PERCEIVE":
                self.reset(job)
                artifacts = self.prepare(job)
            else:
                self._event(
                    run_id,
                    "EXECUTION_STARTED",
                    payload={"job_type": job["job_type"]},
                )
                artifacts = self.execute(job)
            if not isinstance(artifacts, Mapping):
                raise ValueError("runtime callback must return an artifact object")
            completion = self._persist_success(job, artifacts)
            self._event(run_id, "JOB_COMPLETED", payload={"status": "SUCCEEDED"})
            atomic_write_json(
                self.layout.for_run(run_id)["result_dir"] / "complete.json", completion
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            completion = self._persist_failure(job, error)
            self._event(run_id, "JOB_FAILED", payload={"error": error})
            atomic_write_json(
                self.layout.for_run(run_id)["result_dir"] / "complete.json", completion
            )
        active.unlink(missing_ok=True)
        return {
            "status": completion["status"],
            "run_id": run_id,
            "job_type": job["job_type"],
            "provenance": self.provenance,
        }
