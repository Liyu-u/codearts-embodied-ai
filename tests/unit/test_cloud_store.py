from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from demo.cloud.store import CloudStore
from demo.cloud.types import RunState


class CloudStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "cloud.sqlite3"
        self.store = CloudStore(self.db_path)
        self.store.create_run(
            "run-001",
            "multi-red-001",
            "把红色方块放到桌面区域",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_database_enables_wal_and_foreign_keys(self) -> None:
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            connection.execute("PRAGMA foreign_keys=ON")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO jobs(job_id, run_id, job_type, state, payload_json, "
                    "attempts, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    ("bad", "missing", "ISAAC_EXECUTE", "QUEUED", "{}", 0, 1, 1),
                )
        finally:
            connection.close()

    def test_duplicate_event_id_is_idempotent(self) -> None:
        event = {
            "event_id": "evt-001",
            "type": "RUN_CREATED",
            "stage": "CREATED",
            "payload": {"visible": True},
            "created_at": 1000,
        }

        self.assertEqual(self.store.append_events("run-001", [event]), 1)
        self.assertEqual(self.store.append_events("run-001", [event]), 0)

        events = self.store.list_events("run-001")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sequence"], 1)
        self.assertEqual(events[0]["payload"], {"visible": True})

    def test_duplicate_artifact_name_is_idempotent_but_drift_is_rejected(self) -> None:
        job = self.store.enqueue_job(
            "run-001", "ISAAC_PREPARE_AND_PERCEIVE", {"case_id": "multi-red-001"}
        )
        claimed = self.store.claim_job("relay-a", lease_ms=1000, now_ms=100)
        self.assertEqual(claimed["job_id"], job["job_id"])
        artifact = {"schema_version": "perception.v1", "task_id": "run-001"}

        self.store.save_artifact(
            "run-001", "perception.json", artifact, job_id=job["job_id"], relay_id="relay-a", now_ms=101
        )
        self.store.save_artifact(
            "run-001", "perception.json", artifact, job_id=job["job_id"], relay_id="relay-a", now_ms=102
        )

        self.assertEqual(len(self.store.list_artifacts("run-001")), 1)
        with self.assertRaises(ValueError):
            self.store.save_artifact(
                "run-001",
                "perception.json",
                {"schema_version": "perception.v1", "task_id": "different"},
                job_id=job["job_id"],
                relay_id="relay-a",
                now_ms=103,
            )

    def test_terminal_run_rejects_late_state_changes(self) -> None:
        self.store.transition_run("run-001", RunState.CANCELLED, now_ms=100)

        with self.assertRaises(ValueError):
            self.store.transition_run("run-001", RunState.FAILED, now_ms=101)
        self.assertEqual(self.store.get_run("run-001")["state"], "CANCELLED")

    def test_only_one_relay_claims_a_job(self) -> None:
        job = self.store.enqueue_job(
            "run-001", "ISAAC_PREPARE_AND_PERCEIVE", {"case_id": "multi-red-001"}
        )

        first = self.store.claim_job("relay-a", lease_ms=500, now_ms=1000)
        second = self.store.claim_job("relay-b", lease_ms=500, now_ms=1000)

        self.assertEqual(first["job_id"], job["job_id"])
        self.assertEqual(first["lease_owner"], "relay-a")
        self.assertIsNone(second)

    def test_lease_owner_is_required_for_renew_events_artifacts_and_completion(self) -> None:
        job = self.store.enqueue_job(
            "run-001", "ISAAC_EXECUTE", {"strategy_digest": "abc"}
        )
        self.store.claim_job("relay-a", lease_ms=500, now_ms=1000)

        with self.assertRaises(PermissionError):
            self.store.renew_lease(job["job_id"], "relay-b", lease_ms=500, now_ms=1001)
        with self.assertRaises(PermissionError):
            self.store.append_events(
                "run-001",
                [{"event_id": "evt-wrong", "type": "ACTION", "payload": {}}],
                job_id=job["job_id"],
                relay_id="relay-b",
                now_ms=1001,
            )
        with self.assertRaises(PermissionError):
            self.store.save_artifact(
                "run-001",
                "execution.json",
                {},
                job_id=job["job_id"],
                relay_id="relay-b",
                now_ms=1001,
            )
        with self.assertRaises(PermissionError):
            self.store.complete_job(job["job_id"], "relay-b", succeeded=True, now_ms=1001)

    def test_expired_lease_is_requeued_and_can_be_claimed_after_restart(self) -> None:
        job = self.store.enqueue_job(
            "run-001", "ISAAC_EXECUTE", {"strategy_digest": "abc"}
        )
        self.store.claim_job("relay-a", lease_ms=10, now_ms=100)

        restarted = CloudStore(self.db_path)
        self.assertEqual(restarted.recover_expired_jobs(now_ms=111), 1)
        recovered = restarted.claim_job("relay-b", lease_ms=100, now_ms=112)

        self.assertEqual(recovered["job_id"], job["job_id"])
        self.assertEqual(recovered["lease_owner"], "relay-b")
        self.assertEqual(recovered["attempts"], 2)

    def test_process_restart_preserves_runs_jobs_events_and_relay_session(self) -> None:
        job = self.store.enqueue_job(
            "run-001", "ISAAC_PREPARE_AND_PERCEIVE", {"case_id": "multi-red-001"}
        )
        self.store.append_events(
            "run-001", [{"event_id": "evt-persist", "type": "QUEUED", "payload": {}}]
        )
        self.store.update_relay_session("relay-a", {"ssh": "ok"}, now_ms=500)

        restarted = CloudStore(self.db_path)

        self.assertEqual(restarted.get_run("run-001")["scene_id"], "multi-red-001")
        self.assertEqual(restarted.get_job(job["job_id"])["payload"]["case_id"], "multi-red-001")
        self.assertEqual(restarted.list_events("run-001")[0]["event_id"], "evt-persist")
        self.assertEqual(restarted.get_relay_session("relay-a")["status"], {"ssh": "ok"})


if __name__ == "__main__":
    unittest.main()
