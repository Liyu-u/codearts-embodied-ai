from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.live_intelligent_e2e import document_digest, strategy_digest
from tools.relay.isaac_job import IsaacJobConfig, IsaacJobRunner
from tests.unit.test_cloud_orchestrator import perception_document, strategy_document, task_document


class FakeRuntimeRemote:
    def __init__(self, *, complete: bool = True) -> None:
        self.complete = complete
        self.uploads: list[tuple[str, dict]] = []
        self.reads: list[str] = []
        self.cleaned: list[str] = []
        self.files: dict[str, object] = {}

    def upload_job(self, remote_path: str, job: dict) -> None:
        self.uploads.append((remote_path, job))
        run_id = job["run_id"]
        root = "/data/stu_01/workspace/live-runtime"
        self.files[f"{root}/events/{run_id}.jsonl"] = "\n".join(
            [
                json.dumps({"sequence": 1, "event_id": "evt-1", "type": "STARTED"}),
                json.dumps({"sequence": 2, "event_id": "evt-2", "type": "COMPLETED"}),
            ]
        )
        if job["job_type"] == "ISAAC_PREPARE_AND_PERCEIVE":
            self.files[f"{root}/results/{run_id}/perception.json"] = perception_document(run_id)
        else:
            self.files[f"{root}/results/{run_id}/execution.json"] = {
                "schema_version": "execution.v1",
                "task_id": run_id,
                "status": "SUCCEEDED",
                "steps": [],
                "input_strategy_sha256": job["strategy_sha256"],
                "provenance": {"backend": "isaac", "run_id": run_id},
            }
            self.files[f"{root}/results/{run_id}/final_pose.json"] = {
                "run_id": run_id,
                "task_id": run_id,
                "goal_reached": True,
                "provenance": {"backend": "isaac"},
            }
        self.files[f"{root}/results/{run_id}/complete.json"] = {"status": "SUCCEEDED"}

    def read_text(self, remote_path: str) -> str | None:
        self.reads.append(remote_path)
        value = self.files.get(remote_path)
        return value if isinstance(value, str) else None

    def read_json(self, remote_path: str):
        self.reads.append(remote_path)
        return self.files.get(remote_path)

    def exists(self, remote_path: str) -> bool:
        return self.complete and remote_path in self.files

    def cleanup_run(self, run_id: str) -> None:
        self.cleaned.append(run_id)


def execute_job(run_id: str = "run-001") -> dict:
    perception = perception_document(run_id)
    task = task_document(run_id)
    strategy = strategy_document(run_id, perception)
    return {
        "schema_version": "cloud-job.v1",
        "job_type": "ISAAC_EXECUTE",
        "run_id": run_id,
        "case_id": "multi-red-001",
        "task": task,
        "perception": perception,
        "perception_sha256": document_digest(perception),
        "strategy": strategy,
        "strategy_sha256": strategy_digest(strategy),
    }


class IsaacJobRunnerTests(unittest.TestCase):
    def build_runner(self, remote, directory, *, timeout_s=1.0):
        return IsaacJobRunner(
            IsaacJobConfig(
                remote=remote,
                remote_root="/data/stu_01/workspace/live-runtime",
                local_result_root=Path(directory),
                timeout_s=timeout_s,
                poll_interval_s=0.001,
            )
        )

    def test_prepare_job_uploads_atomic_inbox_emits_ordered_events_and_collects_allowlist(self) -> None:
        remote = FakeRuntimeRemote()
        events = []
        with tempfile.TemporaryDirectory() as directory:
            runner = self.build_runner(remote, directory)
            result = runner.run(
                {
                    "schema_version": "cloud-job.v1",
                    "job_type": "ISAAC_PREPARE_AND_PERCEIVE",
                    "run_id": "run-001",
                    "case_id": "multi-red-001",
                    "scene_id": "multi_object_stacking",
                },
                events.append,
            )

        self.assertEqual(remote.uploads[0][0], "/data/stu_01/workspace/live-runtime/inbox/run-001.json")
        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertEqual(set(result["artifacts"]), {"perception.json"})
        self.assertEqual(remote.cleaned, ["run-001"])

    def test_execute_collects_only_allowed_results_and_never_uses_broad_cleanup(self) -> None:
        remote = FakeRuntimeRemote()
        remote.files["/data/stu_01/workspace/live-runtime/results/run-001/secret.env"] = {"secret": True}
        with tempfile.TemporaryDirectory() as directory:
            result = self.build_runner(remote, directory).run(execute_job(), lambda _event: None)

        self.assertEqual(set(result["artifacts"]), {"execution.json", "final_pose.json"})
        self.assertEqual(remote.cleaned, ["run-001"])
        self.assertFalse(any("livestream" in path.lower() for path in remote.reads))
        self.assertFalse(any("secret.env" in path for path in remote.reads))

    def test_digest_drift_is_rejected_before_remote_upload(self) -> None:
        remote = FakeRuntimeRemote()
        job = execute_job()
        job["strategy_sha256"] = "drift"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.build_runner(remote, directory).run(job, lambda _event: None)
        self.assertEqual(remote.uploads, [])
        self.assertEqual(remote.cleaned, [])

    def test_timeout_cleans_only_the_current_run(self) -> None:
        remote = FakeRuntimeRemote(complete=False)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TimeoutError):
                self.build_runner(remote, directory, timeout_s=0.01).run(execute_job(), lambda _event: None)
        self.assertEqual(remote.cleaned, ["run-001"])


if __name__ == "__main__":
    unittest.main()
