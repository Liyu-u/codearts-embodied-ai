from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "testdata" / "acceptance" / "final_acceptance_matrix_v1.json"
GENERALIZATION = ROOT / "testdata" / "benchmark" / "llm_generalization_v1.json"


class FinalAcceptanceMatrixTests(unittest.TestCase):
    def test_matrix_covers_all_required_profiles(self):
        document = json.loads(MATRIX.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "final-acceptance-matrix.v1")
        ids = [item["id"] for item in document["profiles"]]
        self.assertEqual(
            ids,
            [
                "offline_regression",
                "codearts_online",
                "llm_generalization",
                "isaac_hil_ground_truth",
                "camera_perception_hil",
            ],
        )
        self.assertEqual(len(ids), len(set(ids)))

    def test_generalization_manifest_is_a_separate_holdout(self):
        document = json.loads(GENERALIZATION.read_text(encoding="utf-8"))
        self.assertEqual(document["split"], "held_out_language_and_composition")
        self.assertEqual(len(document["cases"]), 30)
        self.assertEqual(
            len({case["id"] for case in document["cases"]}),
            len(document["cases"]),
        )
        self.assertTrue(all(case.get("test_dimensions") for case in document["cases"]))


if __name__ == "__main__":
    unittest.main()
