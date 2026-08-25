from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from demo.scenarios import get_scenario


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "testdata" / "benchmark" / "abcd_closed_loop_v1.json"
ACCEPTANCE_ROOT = ROOT / "testdata" / "acceptance"


class AbcdClosedLoopManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.cases = cls.document["cases"]

    def test_manifest_is_frozen_at_expected_v1_shape(self):
        self.assertEqual(self.document["schema_version"], "closed-loop-benchmark.v1")
        self.assertEqual(self.document["name"], "abcd_closed_loop_v1")
        self.assertEqual(len(self.cases), 64)
        self.assertEqual(
            Counter(case["category"] for case in self.cases),
            Counter(
                {
                    "happy_path": 20,
                    "intent_safety": 12,
                    "capability_boundary": 10,
                    "recoverable_failure": 8,
                    "safe_stop": 6,
                    "execution_failure": 8,
                }
            ),
        )
        self.assertEqual(
            len({case["id"] for case in self.cases}),
            len(self.cases),
        )

    def test_cases_have_replay_metadata_and_resolvable_sources(self):
        statuses = {"SUCCEEDED", "BLOCKED", "FAILED", "SAFE_STOP"}
        for case in self.cases:
            with self.subTest(case_id=case["id"]):
                self.assertIn(case["expected_status"], statuses)
                self.assertIsInstance(case["seed"], int)
                self.assertTrue(case["benchmark_basis"])
                self.assertTrue(case["test_dimensions"])
                self.assertIn(case["source"], {"demo", "demo_override", "acceptance"})
                if case["source"] == "acceptance":
                    self.assertTrue((ACCEPTANCE_ROOT / case["path"]).is_file())
                else:
                    self.assertTrue(case.get("scene_id"))
                    self.assertTrue(case.get("instruction"))
                    get_scenario(case["scene_id"])

    def test_recovery_and_safety_cases_have_explicit_failure_contract(self):
        for case in self.cases:
            if case["category"] in {"recoverable_failure", "safe_stop", "execution_failure"}:
                with self.subTest(case_id=case["id"]):
                    self.assertTrue(case.get("failures") or case["source"] == "acceptance")
            if case.get("requires_d_repair"):
                self.assertEqual(case["category"], "recoverable_failure")
                self.assertEqual(case["expected_status"], "SUCCEEDED")
                self.assertEqual(case["expected_retry_count"], 1)


if __name__ == "__main__":
    unittest.main()
