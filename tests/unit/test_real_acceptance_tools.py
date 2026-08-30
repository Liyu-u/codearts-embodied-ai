from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.run_camera_executor_acceptance import _attach_execution_capabilities
from tools.summarize_real_acceptance import classify, load_records, summarize


class RealAcceptanceToolTests(unittest.TestCase):
    def test_camera_adapter_adds_static_capabilities_without_touching_pose(self):
        scene = {
            "objects": [
                {"id": "green_cube", "pose": {"x": 0.5, "y": 0.0, "z": 0.05}},
                {"id": "zone_unstack_target", "pose": {"x": 0.45, "y": 0.1, "z": 0.03}},
            ]
        }

        result = _attach_execution_capabilities(scene)

        self.assertIs(result, scene)
        self.assertEqual(result["objects"][0]["pose"]["x"], 0.5)
        self.assertTrue(result["objects"][0]["execution"]["graspable"])
        self.assertFalse(result["objects"][1]["execution"]["graspable"])
        self.assertTrue(result["objects"][1]["execution"]["valid_destination"])

    def test_failure_classification_separates_transport_and_business(self):
        self.assertEqual(classify({"status": "SUCCEEDED"}), "success")
        self.assertEqual(classify({"status": "FAILED", "message": "Permission denied (publickey)"}), "transport_auth")
        self.assertEqual(classify({"status": "FAILED", "stages": {"C": {"status": "SAFE_STOP"}}}), "safety_stop")
        self.assertEqual(classify({"status": "FAILED", "contract_checks": {"task_id_match": False}}), "contract")
        self.assertEqual(classify({"status": "FAILED", "stages": {"C": {"status": "FAILED"}}}), "execution")

    def test_safe_stop_wins_over_stale_contract_flag(self):
        self.assertEqual(
            classify({
                "status": "FAILED",
                "failure_class": "contract",
                "contract_checks": {"all_execution_steps_success": False},
                "stages": {"C": {"status": "SAFE_STOP"}},
            }),
            "safety_stop",
        )

    def test_transport_failure_wins_over_missing_execution_contract_flag(self):
        self.assertEqual(
            classify({
                "status": "FAILED",
                "failure_class": "runner",
                "contract_checks": {"task_strategy_execution_feedback_task_id_match": False},
                "remote_run": {"message": "ssh connection timed out"},
            }),
            "transport_auth",
        )

    def test_missing_execution_is_runner_not_contract(self):
        self.assertEqual(
            classify({
                "status": "FAILED",
                "expected_status": "SUCCEEDED",
                "failure_class": "contract",
                "stages": {"A": {"status": "READY"}, "B": {"status": "SUCCEEDED"}, "C": {"status": None}},
                "contract_checks": {"task_strategy_execution_feedback_task_id_match": False},
            }),
            "runner",
        )

    def test_safe_stop_rate_uses_case_expected_status(self):
        result = summarize([
            {
                "status": "FAILED",
                "expected_status": "SAFE_STOP",
                "stages": {"C": {"status": "SAFE_STOP"}},
            },
        ])
        self.assertEqual(result["safe_stop_expected_count"], 1)
        self.assertEqual(result["safe_stop_correct_rate"], 1.0)

    def test_summary_is_deterministic_and_keeps_transport_outcome_visible(self):
        records = [
            {
                "run_id": "ok",
                "status": "SUCCEEDED",
                "contract_checks": {"task_id_match": True},
                "stages": {"C": {"status": "SUCCEEDED", "wall_ms": 100.0}},
            },
            {
                "run_id": "auth",
                "status": "FAILED",
                "message": "ssh Permission denied",
            },
        ]
        result = summarize(records)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["passed_count"], 1)
        self.assertEqual(result["pass_rate"], 0.5)
        self.assertEqual(result["contract_pass_rate"], 1.0)
        self.assertEqual(result["failure_classes"], {"success": 1, "transport_auth": 1})
        self.assertEqual(result["isaac_wall_ms_p95"], 100.0)

    def test_load_records_ignores_incomplete_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            good = root / "real-acceptance-good"
            good.mkdir()
            (good / "full_test_status.json").write_text(
                json.dumps({"status": "SUCCEEDED"}), encoding="utf-8"
            )
            (root / "real-acceptance-incomplete").mkdir()
            records = load_records(root, "real-acceptance-*")
            self.assertEqual([row["run_id"] for row in records], ["real-acceptance-good"])


if __name__ == "__main__":
    unittest.main()
