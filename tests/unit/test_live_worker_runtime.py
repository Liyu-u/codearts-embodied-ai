from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.live_intelligent_e2e import strategy_digest
from tools.live_worker import runtime as runtime_module
from tools.live_worker.runtime import LiveRuntimeWorker
from tools.relay.runtime_protocol import RuntimeLayout, atomic_write_json
from tests.unit.test_cloud_relay_isaac_job import execute_job
from tests.unit.test_cloud_orchestrator import perception_document


def prepare_job(run_id: str = "run-worker-001") -> dict:
    return {
        "schema_version": "cloud-job.v1",
        "job_type": "ISAAC_PREPARE_AND_PERCEIVE",
        "run_id": run_id,
        "case_id": "multi-red-001",
        "scene_id": "multi_object_stacking",
    }


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class LiveRuntimeWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "runtime"
        self.layout = RuntimeLayout(self.root)
        self.calls: list[tuple] = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def build_worker(self, *, prepare=None, execute=None):
        def default_prepare(job):
            self.calls.append(("prepare", job["run_id"]))
            return {"perception.json": perception_document(job["run_id"])}

        def default_execute(job):
            self.calls.append(("execute", job["run_id"]))
            return {
                "execution.json": {
                    "schema_version": "execution.v1",
                    "task_id": job["run_id"],
                    "status": "SUCCEEDED",
                    "steps": [],
                    "input_strategy_sha256": job["strategy_sha256"],
                },
                "final_pose.json": {
                    "run_id": job["run_id"],
                    "task_id": job["run_id"],
                    "goal_reached": True,
                },
            }

        def reset(job):
            self.calls.append(("reset", job["run_id"]))

        return LiveRuntimeWorker(
            self.layout,
            execute=execute or default_execute,
            prepare=prepare or default_prepare,
            reset=reset,
            worker_instance_id="kit-instance-test",
            world_id="world-session-test",
        )

    def enqueue(self, job: dict) -> Path:
        path = self.layout.for_run(job["run_id"])["inbox"]
        atomic_write_json(path, job)
        return path

    def test_atomic_claim_allows_only_one_active_job(self) -> None:
        first_inbox = self.enqueue(prepare_job("run-worker-001"))
        second_inbox = self.enqueue(prepare_job("run-worker-002"))
        worker = self.build_worker()

        first = worker.claim_next_job()
        second = worker.claim_next_job()

        self.assertEqual(first["run_id"], "run-worker-001")
        self.assertIsNone(second)
        self.assertFalse(first_inbox.exists())
        self.assertTrue(self.layout.for_run("run-worker-001")["active"].is_file())
        self.assertTrue(second_inbox.is_file())

    def test_prepare_persists_atomic_provenance_events_and_completion(self) -> None:
        self.enqueue(prepare_job())
        worker = self.build_worker()

        outcome = worker.process_once()

        paths = self.layout.for_run("run-worker-001")
        perception = json.loads((paths["result_dir"] / "perception.json").read_text(encoding="utf-8"))
        completion = json.loads((paths["result_dir"] / "complete.json").read_text(encoding="utf-8"))
        events = read_events(paths["events"])
        self.assertEqual(outcome["status"], "SUCCEEDED")
        self.assertEqual(self.calls, [("reset", "run-worker-001"), ("prepare", "run-worker-001")])
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual(perception["provenance"]["backend"], "isaac_ground_truth")
        self.assertEqual(perception["provenance"]["kit_instance_id"], "kit-instance-test")
        self.assertEqual(completion["provenance"]["world_id"], "world-session-test")
        self.assertTrue(all(event["provenance"]["world_id"] == "world-session-test" for event in events))
        self.assertFalse(paths["active"].exists())
        self.assertEqual(list(paths["result_dir"].glob("*.tmp")), [])

    def test_duplicate_completed_job_returns_receipt_without_reexecution(self) -> None:
        self.enqueue(prepare_job())
        worker = self.build_worker()
        first = worker.process_once()
        self.enqueue(prepare_job())

        duplicate = worker.process_once()

        self.assertEqual(first["status"], "SUCCEEDED")
        self.assertEqual(duplicate["status"], "DUPLICATE")
        self.assertEqual(self.calls.count(("prepare", "run-worker-001")), 1)
        self.assertFalse(self.layout.for_run("run-worker-001")["inbox"].exists())

    def test_terminal_event_is_durable_before_complete_marker_is_published(self) -> None:
        actions = []
        real_append = runtime_module.atomic_append_event
        real_write = runtime_module.atomic_write_json

        def tracked_append(path, event):
            actions.append(("event", event["type"]))
            return real_append(path, event)

        def tracked_write(path, value):
            if Path(path).name == "complete.json":
                actions.append(("marker", value["status"]))
            return real_write(path, value)

        self.enqueue(prepare_job())
        with patch.object(runtime_module, "atomic_append_event", side_effect=tracked_append), patch.object(
            runtime_module, "atomic_write_json", side_effect=tracked_write
        ):
            self.build_worker().process_once()

        self.assertLess(actions.index(("event", "JOB_COMPLETED")), actions.index(("marker", "SUCCEEDED")))

    def test_invalid_digest_unknown_kind_and_non_null_code_never_call_executor(self) -> None:
        invalid_digest = execute_job("run-worker-digest")
        invalid_digest["strategy_sha256"] = "drift"
        unknown = prepare_job("run-worker-unknown")
        unknown["job_type"] = "SHELL"
        code_job = execute_job("run-worker-code")
        code_job["strategy"]["code"] = "print('unsafe')"
        code_job["strategy_sha256"] = strategy_digest(code_job["strategy"])
        worker = self.build_worker()

        for job in (invalid_digest, unknown, code_job):
            self.enqueue(job)
            with self.assertRaises(ValueError):
                worker.process_once()

        self.assertFalse(any(call[0] == "execute" for call in self.calls))

    def test_crash_leaves_active_job_and_restart_recovers_with_continuous_sequence(self) -> None:
        attempts = 0

        def crashing_prepare(job):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise KeyboardInterrupt("simulated process crash")
            self.calls.append(("prepare", job["run_id"]))
            return {"perception.json": perception_document(job["run_id"])}

        self.enqueue(prepare_job())
        first_worker = self.build_worker(prepare=crashing_prepare)
        with self.assertRaises(KeyboardInterrupt):
            first_worker.process_once()
        self.assertTrue(self.layout.for_run("run-worker-001")["active"].is_file())

        recovered = self.build_worker(prepare=crashing_prepare).process_once()

        events = read_events(self.layout.for_run("run-worker-001")["events"])
        self.assertEqual(recovered["status"], "SUCCEEDED")
        self.assertEqual([event["sequence"] for event in events], [1, 2, 3, 4])
        self.assertEqual(events[1]["type"], "JOB_RECOVERED")

    def test_unknown_inbox_files_and_symlinks_are_rejected(self) -> None:
        inbox = self.root / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "notes.txt").write_text("not a job", encoding="utf-8")
        worker = self.build_worker()
        with self.assertRaises(ValueError):
            worker.claim_next_job()
        (inbox / "notes.txt").unlink()

        target = self.root / "outside.json"
        atomic_write_json(target, prepare_job())
        link = inbox / "run-worker-001.json"
        try:
            os.symlink(target, link)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        with self.assertRaises(ValueError):
            worker.claim_next_job()


if __name__ == "__main__":
    unittest.main()
