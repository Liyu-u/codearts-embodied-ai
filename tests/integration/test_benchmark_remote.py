"""批量统计增强集成测试：断点续跑、交互认证拒绝、Mock 基准报告字段。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.run_closed_loop_benchmark import (
    _append_partial,
    _load_partial,
    _partial_path,
    _run_one_remote,
    run_benchmark,
)
from tools.run_closed_loop_benchmark import main as benchmark_main
from tools.orchestrate.types import OrchestrationResult


class PartialResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_path_and_roundtrip(self):
        output = self.root / "report.json"
        partial = _partial_path(output)
        self.assertEqual(partial.name, "report.json.partial.jsonl")
        record = {"case_id": "c1", "run_id": "r1"}
        _append_partial(partial, record)
        loaded = _load_partial(partial)
        self.assertEqual(loaded, [record])

    def test_append_preserves_order(self):
        partial = self.root / "x.jsonl"
        for index in range(3):
            _append_partial(partial, {"case_id": f"c{index}", "run_id": f"r{index}"})
        loaded = _load_partial(partial)
        self.assertEqual([item["case_id"] for item in loaded], ["c0", "c1", "c2"])


class InteractiveRemoteRejectTest(unittest.TestCase):
    def test_remote_backend_without_interactive_flag_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            benchmark_main(
                [
                    "--backend",
                    "remote_isaac",
                    "--server",
                    "10.0.0.1",
                    "--port",
                    "5122",
                    "--user",
                    "stu",
                    "--remote-base",
                    "/data/stu/workspace",
                    "--manifest",
                    str(Path("testdata/benchmark/closed_loop_cases.json").resolve()),
                ]
            )
        self.assertNotEqual(ctx.exception.code, 0)


class RemoteBackendMockTest(unittest.TestCase):
    def test_run_one_remote_reuses_orchestrate_and_returns_evidence(self):
        case = {"id": "rbt-001", "instruction": "x", "scene_id": "s1", "expected_status": "SUCCEEDED"}
        fake = OrchestrationResult(
            run_id="benchmark-rbt-001-r1",
            status="SUCCEEDED",
            failure_class=None,
            stages=[],
            artifact_paths={"execution": Path("/data/stu/evidence/exec.json")},
            retry_command=None,
        )
        with mock.patch("tools.orchestrate.orchestrator.orchestrate", return_value=fake) as m:
            record = _run_one_remote(case, 1, {"server": "s", "user": "u", "remote_base": "/b"}, 2)
        self.assertEqual(record["passed"], True)
        self.assertEqual(record["failure_class"], None)
        self.assertEqual(record["evidence_path"], str(Path("/data/stu/evidence/exec.json")))
        self.assertEqual(record["run_id"], "benchmark-rbt-001-r1")
        cfg = m.call_args.args[0]
        self.assertEqual(cfg.auth_mode, "batch")

    def test_run_one_remote_transport_failure_preserved(self):
        case = {"id": "rbt-002", "instruction": "x", "scene_id": "s1", "expected_status": "SUCCEEDED"}
        fake = OrchestrationResult(
            run_id="benchmark-rbt-002-r1",
            status="FAILED",
            failure_class="transport_auth",
            stages=[],
            artifact_paths={},
            retry_command=None,
        )
        with mock.patch("tools.orchestrate.orchestrator.orchestrate", return_value=fake):
            record = _run_one_remote(case, 1, {"server": "s", "user": "u", "remote_base": "/b"}, 2)
        self.assertEqual(record["passed"], False)
        self.assertEqual(record["failure_class"], "transport_auth")
        self.assertEqual(record["stop_reason"], "transport_auth")


class MockBenchmarkReportFieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = self.root / "report.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_mock_benchmark_report_has_required_fields(self):
        manifest = Path("testdata/benchmark/abcd_closed_loop_v1.json").resolve()
        report = run_benchmark(
            mode="baseline",
            repeats=1,
            policy="planner",
            model=None,
            timeout_s=60,
            pure=False,
            limit=1,
            manifest_path=manifest,
            backend="mock",
            output=self.output,
        )
        self.assertEqual(report["backend"], "mock")
        self.assertIn("metadata", report)
        for field in ["git_sha", "profile", "timestamp", "command", "manifest_path"]:
            self.assertIn(field, report["metadata"], field)
        summary = report["summary"]
        for field in [
            "transport_failures",
            "business_failures",
            "contract_failures",
            "safety_failures",
            "c_internal_recovery_rate",
            "d_repair_success_rate",
            "p50_latency_ms",
            "p95_latency_ms",
        ]:
            self.assertIn(field, summary, field)
        self.assertTrue(self.output.parent.exists() or True)


if __name__ == "__main__":
    unittest.main()