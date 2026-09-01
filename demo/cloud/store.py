from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4

from demo.cloud.types import JobState, RunState, assert_transition


def utc_ms() -> int:
    return time.time_ns() // 1_000_000


def _json_dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, default: object) -> object:
    if not value:
        return default
    return json.loads(value)


class CloudStore:
    def __init__(self, path: str | Path, busy_timeout_ms: int = 5000) -> None:
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = Path(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @contextmanager
    def _immediate(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs(
                    run_id TEXT PRIMARY KEY,
                    scene_id TEXT NOT NULL,
                    instruction TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT,
                    current_action TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    audit_eligible INTEGER NOT NULL DEFAULT 0,
                    repair_attempts INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS jobs(
                    job_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    job_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS jobs_claim_idx
                    ON jobs(state, available_at, created_at);

                CREATE TABLE IF NOT EXISTS events(
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    job_id TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT,
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(run_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS artifacts(
                    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
                    job_id TEXT REFERENCES jobs(job_id) ON DELETE SET NULL,
                    artifact_name TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(run_id, artifact_name)
                );

                CREATE TABLE IF NOT EXISTS relay_sessions(
                    relay_id TEXT PRIMARY KEY,
                    status_json TEXT NOT NULL,
                    last_seen_at INTEGER NOT NULL
                );
                """
            )

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["metadata"] = _json_load(value.pop("metadata_json"), {})
        value["result"] = _json_load(value.pop("result_json"), None)
        value["audit_eligible"] = bool(value["audit_eligible"])
        return value

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["payload"] = _json_load(value.pop("payload_json"), {})
        return value

    def create_run(
        self,
        run_id: str,
        scene_id: str,
        instruction: str,
        *,
        metadata: Mapping[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        if not run_id or not scene_id or not instruction.strip():
            raise ValueError("run_id, scene_id and instruction are required")
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, scene_id, instruction, state, stage, "
                "metadata_json, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    scene_id,
                    instruction.strip(),
                    RunState.CREATED.value,
                    RunState.CREATED.value,
                    _json_dump(dict(metadata or {})),
                    now,
                    now,
                ),
            )
        return self.get_run(run_id)

    def transition_run(
        self,
        run_id: str,
        target: RunState | str,
        *,
        now_ms: int | None = None,
        **updates: Any,
    ) -> dict[str, Any]:
        allowed_updates = {
            "stage",
            "current_action",
            "result",
            "audit_eligible",
            "repair_attempts",
            "error_code",
            "error_message",
            "started_at",
        }
        unknown = set(updates) - allowed_updates
        if unknown:
            raise ValueError(f"unsupported run fields: {sorted(unknown)}")
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            target_state = assert_transition(row["state"], target)
            assignments = ["state=?", "updated_at=?"]
            values: list[Any] = [target_state.value, now]
            for key, value in updates.items():
                column = "result_json" if key == "result" else key
                if key == "result":
                    value = _json_dump(value) if value is not None else None
                elif key == "audit_eligible":
                    value = int(bool(value))
                assignments.append(f"{column}=?")
                values.append(value)
            if "stage" not in updates:
                assignments.append("stage=?")
                values.append(target_state.value)
            if target_state in {
                RunState.SUCCEEDED,
                RunState.BLOCKED,
                RunState.FAILED,
                RunState.SAFE_STOPPED,
                RunState.CANCELLED,
            }:
                assignments.append("finished_at=?")
                values.append(now)
            values.append(run_id)
            connection.execute(
                f"UPDATE runs SET {', '.join(assignments)} WHERE run_id=?", values
            )
        return self.get_run(run_id)

    def enqueue_job(
        self,
        run_id: str,
        job_type: str,
        payload: Mapping[str, Any],
        *,
        job_id: str | None = None,
        available_at: int = 0,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        if not job_type:
            raise ValueError("job_type is required")
        now = utc_ms() if now_ms is None else int(now_ms)
        identifier = job_id or f"job-{uuid4().hex}"
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO jobs(job_id, run_id, job_type, state, payload_json, "
                "attempts, available_at, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    run_id,
                    job_type,
                    JobState.QUEUED.value,
                    _json_dump(dict(payload)),
                    0,
                    int(available_at),
                    now,
                    now,
                ),
            )
        return self.get_job(identifier)

    def claim_job(
        self,
        relay_id: str,
        *,
        lease_ms: int,
        now_ms: int | None = None,
    ) -> dict[str, Any] | None:
        if not relay_id or lease_ms <= 0:
            raise ValueError("relay_id and a positive lease_ms are required")
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._immediate() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE state=? AND available_at<=? "
                "ORDER BY created_at, job_id LIMIT 1",
                (JobState.QUEUED.value, now),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET state=?, lease_owner=?, lease_expires_at=?, "
                "attempts=attempts+1, updated_at=? WHERE job_id=? AND state=?",
                (
                    JobState.LEASED.value,
                    relay_id,
                    now + int(lease_ms),
                    now,
                    row["job_id"],
                    JobState.QUEUED.value,
                ),
            )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (row["job_id"],)
            ).fetchone()
            return self._job_from_row(claimed)

    @staticmethod
    def _require_lease(
        connection: sqlite3.Connection,
        job_id: str,
        relay_id: str,
        now_ms: int,
    ) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["lease_owner"] != relay_id:
            raise PermissionError("relay does not own this job lease")
        if row["state"] not in {JobState.LEASED.value, JobState.RUNNING.value}:
            raise ValueError(f"job is not active: {row['state']}")
        if row["lease_expires_at"] is None or int(row["lease_expires_at"]) < now_ms:
            raise PermissionError("job lease has expired")
        return row

    def renew_lease(
        self,
        job_id: str,
        relay_id: str,
        *,
        lease_ms: int,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        if lease_ms <= 0:
            raise ValueError("lease_ms must be positive")
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._immediate() as connection:
            row = self._require_lease(connection, job_id, relay_id, now)
            state = (
                JobState.RUNNING.value
                if row["state"] == JobState.LEASED.value
                else row["state"]
            )
            connection.execute(
                "UPDATE jobs SET state=?, lease_expires_at=?, updated_at=? WHERE job_id=?",
                (state, now + int(lease_ms), now, job_id),
            )
        return self.get_job(job_id)

    def append_events(
        self,
        run_id: str,
        events: Sequence[Mapping[str, Any]],
        *,
        job_id: str | None = None,
        relay_id: str | None = None,
        now_ms: int | None = None,
    ) -> int:
        now = utc_ms() if now_ms is None else int(now_ms)
        inserted = 0
        with self._immediate() as connection:
            if job_id is not None:
                if not relay_id:
                    raise PermissionError("relay_id is required for job events")
                lease = self._require_lease(connection, job_id, relay_id, now)
                if lease["run_id"] != run_id:
                    raise ValueError("job does not belong to run")
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM events WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            ) + 1
            for event in events:
                event_id = str(event.get("event_id") or "")
                event_type = str(event.get("type") or "")
                if not event_id or not event_type:
                    raise ValueError("event_id and type are required")
                existing = connection.execute(
                    "SELECT 1 FROM events WHERE event_id=?", (event_id,)
                ).fetchone()
                if existing is not None:
                    continue
                sequence = int(event.get("sequence") or next_sequence)
                connection.execute(
                    "INSERT INTO events(event_id, run_id, job_id, sequence, event_type, "
                    "stage, payload_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        run_id,
                        job_id,
                        sequence,
                        event_type,
                        event.get("stage"),
                        _json_dump(dict(event.get("payload") or {})),
                        int(event.get("created_at") or now),
                    ),
                )
                inserted += 1
                next_sequence = max(next_sequence, sequence + 1)
        return inserted

    def save_artifact(
        self,
        run_id: str,
        artifact_name: str,
        value: object,
        *,
        job_id: str,
        relay_id: str,
        now_ms: int | None = None,
    ) -> None:
        now = utc_ms() if now_ms is None else int(now_ms)
        encoded = _json_dump(value)
        with self._immediate() as connection:
            lease = self._require_lease(connection, job_id, relay_id, now)
            if lease["run_id"] != run_id:
                raise ValueError("job does not belong to run")
            existing = connection.execute(
                "SELECT value_json FROM artifacts WHERE run_id=? AND artifact_name=?",
                (run_id, artifact_name),
            ).fetchone()
            if existing is not None:
                if existing["value_json"] != encoded:
                    raise ValueError(f"artifact content drift: {artifact_name}")
                return
            connection.execute(
                "INSERT INTO artifacts(run_id, job_id, artifact_name, value_json, "
                "created_at) VALUES(?,?,?,?,?)",
                (run_id, job_id, artifact_name, encoded, now),
            )

    def complete_job(
        self,
        job_id: str,
        relay_id: str,
        *,
        succeeded: bool,
        error: str | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._immediate() as connection:
            self._require_lease(connection, job_id, relay_id, now)
            state = JobState.SUCCEEDED if succeeded else JobState.FAILED
            connection.execute(
                "UPDATE jobs SET state=?, error=?, updated_at=?, completed_at=?, "
                "lease_owner=NULL, lease_expires_at=NULL WHERE job_id=?",
                (state.value, error, now, now, job_id),
            )
        return self.get_job(job_id)

    def recover_expired_jobs(self, *, now_ms: int | None = None) -> int:
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._immediate() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state=?, lease_owner=NULL, lease_expires_at=NULL, "
                "updated_at=? WHERE state IN (?,?) AND lease_expires_at<?",
                (
                    JobState.QUEUED.value,
                    now,
                    JobState.LEASED.value,
                    JobState.RUNNING.value,
                    now,
                ),
            )
            return int(cursor.rowcount)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return self._run_from_row(row)

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._job_from_row(row)

    def list_jobs(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs WHERE run_id=? ORDER BY created_at, job_id",
                (run_id,),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def list_events(self, run_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence",
                (run_id, int(after_sequence)),
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["type"] = value.pop("event_type")
            value["payload"] = _json_load(value.pop("payload_json"), {})
            values.append(value)
        return values

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE run_id=? ORDER BY artifact_id", (run_id,)
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            value = dict(row)
            value["value"] = _json_load(value.pop("value_json"), None)
            values.append(value)
        return values

    def update_relay_session(
        self,
        relay_id: str,
        status: Mapping[str, Any],
        *,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        if not relay_id:
            raise ValueError("relay_id is required")
        now = utc_ms() if now_ms is None else int(now_ms)
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO relay_sessions(relay_id, status_json, last_seen_at) "
                "VALUES(?,?,?) ON CONFLICT(relay_id) DO UPDATE SET "
                "status_json=excluded.status_json, last_seen_at=excluded.last_seen_at",
                (relay_id, _json_dump(dict(status)), now),
            )
        return self.get_relay_session(relay_id)

    def get_relay_session(self, relay_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM relay_sessions WHERE relay_id=?", (relay_id,)
            ).fetchone()
        if row is None:
            raise KeyError(relay_id)
        value = dict(row)
        value["status"] = _json_load(value.pop("status_json"), {})
        return value
