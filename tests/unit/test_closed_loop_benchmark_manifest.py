from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "testdata" / "benchmark" / "closed_loop_cases.json"


class ClosedLoopBenchmarkManifestTests(unittest.TestCase):
    def test_manifest_has_required_scale_and_categories(self):
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "closed-loop-benchmark.v1")
        cases = document["cases"]
        self.assertGreaterEqual(len(cases), 30)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        categories = {case["category"] for case in cases}
        self.assertEqual(
            categories,
            {
                "happy_path",
                "intent_safety",
                "capability_boundary",
                "recoverable_failure",
                "safe_stop",
                "execution_failure",
            },
        )

    def test_sources_and_expectations_are_resolvable(self):
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        acceptance_root = ROOT / "testdata" / "acceptance"
        for case in document["cases"]:
            with self.subTest(case_id=case["id"]):
                self.assertIn(case["source"], {"demo", "demo_override", "acceptance"})
                self.assertIn(case["expected_status"], {"SUCCEEDED", "BLOCKED", "FAILED", "SAFE_STOP"})
                if case["source"] == "acceptance":
                    self.assertTrue((acceptance_root / case["path"]).is_file())
                if case["source"] in {"demo", "demo_override"}:
                    self.assertTrue(case.get("scene_id"))
                    self.assertTrue(case.get("instruction"))


if __name__ == "__main__":
    unittest.main()
