from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.live_intelligent_e2e import document_digest, strategy_digest
from tools.relay.runtime_protocol import (
    RuntimeLayout,
    atomic_append_event,
    atomic_write_json,
    validate_job,
    validate_run_id,
)
from tests.unit.test_cloud_orchestrator import perception_document, strategy_document, task_document


class LiveRuntimeProtocolTests(unittest.TestCase):
    def test_run_id_accepts_safe_ids_and_rejects_path_or_shell_tokens(self) -> None:
        self.assertEqual(validate_run_id("run-20260901_ab.cd-01"), "run-20260901_ab.cd-01")
        for value in ("", ".", "..", "../escape", "a/b", "a\\b", "a b", "$(whoami)", "a;rm"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_run_id(value)

    def test_prepare_job_accepts_only_known_kind_and_identity(self) -> None:
        job = {
            "schema_version": "cloud-job.v1",
            "job_type": "ISAAC_PREPARE_AND_PERCEIVE",
            "run_id": "run-001",
            "case_id": "multi-red-001",
            "scene_id": "multi_object_stacking",
        }
        self.assertEqual(validate_job(job)["job_type"], "ISAAC_PREPARE_AND_PERCEIVE")
        for mutation in (
            {"job_type": "RUN_SHELL"},
            {"schema_version": "unknown"},
            {"run_id": "../escape"},
            {"case_id": "../../case"},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    validate_job({**job, **mutation})

    def test_execute_job_requires_digest_task_continuity_and_primitive_allowlist(self) -> None:
        run_id = "run-001"
        perception = perception_document(run_id)
        task = task_document(run_id)
        strategy = strategy_document(run_id, perception)
        job = {
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
        self.assertEqual(validate_job(job)["strategy_sha256"], strategy_digest(strategy))

        invalid = dict(job)
        invalid["strategy_sha256"] = "drift"
        with self.assertRaises(ValueError):
            validate_job(invalid)
        invalid = {**job, "task": {**task, "task_id": "other"}}
        with self.assertRaises(ValueError):
            validate_job(invalid)
        unsafe_strategy = {**strategy, "steps": [{"step_id": "x", "action": "run_shell", "arguments": {}}]}
        invalid = {**job, "strategy": unsafe_strategy, "strategy_sha256": strategy_digest(unsafe_strategy)}
        with self.assertRaises(ValueError):
            validate_job(invalid)

    def test_atomic_json_and_jsonl_writes_leave_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document_path = root / "results" / "execution.json"
            event_path = root / "events" / "run-001.jsonl"

            atomic_write_json(document_path, {"status": "SUCCEEDED", "中文": "正常"})
            atomic_append_event(event_path, {"sequence": 1, "type": "STARTED"})
            atomic_append_event(event_path, {"sequence": 2, "type": "DONE"})

            self.assertEqual(json.loads(document_path.read_text(encoding="utf-8"))["中文"], "正常")
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["sequence"] for event in events], [1, 2])
            self.assertFalse(any(path.suffix == ".tmp" for path in root.rglob("*")))

    def test_layout_keeps_all_paths_under_one_runtime_root(self) -> None:
        layout = RuntimeLayout(Path("/data/stu_01/workspace/live-runtime"))
        paths = layout.for_run("run-001")
        self.assertEqual(paths["inbox"].as_posix(), "/data/stu_01/workspace/live-runtime/inbox/run-001.json")
        self.assertEqual(paths["result_dir"].as_posix(), "/data/stu_01/workspace/live-runtime/results/run-001")


if __name__ == "__main__":
    unittest.main()
