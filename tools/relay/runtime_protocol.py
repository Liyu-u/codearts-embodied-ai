from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from integration.contract_validation import assert_contract
from integration.strategy_policy import DEFAULT_CAPABILITIES, validate_strategy
from tools.live_intelligent_e2e import document_digest, strategy_digest


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
KNOWN_JOB_TYPES = frozenset({"ISAAC_PREPARE_AND_PERCEIVE", "ISAAC_EXECUTE"})


def validate_run_id(value: object) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"unsafe run_id: {value!r}")
    if value in {".", ".."} or ".." in value:
        raise ValueError(f"unsafe run_id: {value!r}")
    return value


def _validate_id(value: object, label: str) -> str:
    try:
        return validate_run_id(value)
    except ValueError as exc:
        raise ValueError(f"unsafe {label}: {value!r}") from exc


def validate_job(job: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(job, Mapping):
        raise ValueError("job must be an object")
    value = deepcopy(dict(job))
    if value.get("schema_version") != "cloud-job.v1":
        raise ValueError("job schema_version must be cloud-job.v1")
    job_type = value.get("job_type")
    if job_type not in KNOWN_JOB_TYPES:
        raise ValueError(f"unsupported job_type: {job_type!r}")
    run_id = validate_run_id(value.get("run_id"))
    _validate_id(value.get("case_id"), "case_id")

    if job_type == "ISAAC_PREPARE_AND_PERCEIVE":
        if not isinstance(value.get("scene_id"), str) or not value["scene_id"]:
            raise ValueError("prepare job requires scene_id")
        return value

    task = value.get("task")
    perception = value.get("perception")
    strategy = value.get("strategy")
    assert_contract(task, "task.v1")
    assert_contract(perception, "perception.v1")
    if task.get("task_id") != run_id or strategy.get("task_id") != run_id:
        raise ValueError("task/strategy identity does not match run_id")
    if value.get("perception_sha256") != document_digest(perception):
        raise ValueError("perception digest drift")
    if value.get("strategy_sha256") != strategy_digest(strategy):
        raise ValueError("strategy digest drift")
    validation = validate_strategy(
        strategy,
        task=task,
        capabilities=value.get("capabilities") or DEFAULT_CAPABILITIES,
    )
    if not validation["passed"]:
        raise ValueError("strategy is not executable: " + "; ".join(validation["errors"]))
    if strategy.get("code") is not None:
        raise ValueError("strategy.code must be null")
    return value


def atomic_write_json(path: str | Path, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_append_event(path: str | Path, event: Mapping[str, Any]) -> None:
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
        raise ValueError("event sequence must be a positive integer")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(dict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    descriptor = os.open(target, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    root: Path

    def for_run(self, run_id: str) -> dict[str, Path]:
        safe = validate_run_id(run_id)
        return {
            "inbox": self.root / "inbox" / f"{safe}.json",
            "active": self.root / "active" / f"{safe}.json",
            "events": self.root / "events" / f"{safe}.jsonl",
            "result_dir": self.root / "results" / safe,
            "control": self.root / "control" / f"{safe}.json",
        }
