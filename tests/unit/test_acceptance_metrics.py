import unittest

from integration.acceptance_metrics import compute_metrics


class AcceptanceMetricsTests(unittest.TestCase):
    def test_reports_binding_ambiguity_and_safety_rates(self):
        metrics = compute_metrics([
            {
                "expected": {
                    "pipeline_status": "SUCCEEDED",
                    "task": {"status": "READY", "target_ids": ["obj"], "destination_id": "bin"},
                },
                "actual": {
                    "status": "SUCCEEDED",
                    "task": {"status": "READY", "target_ids": ["obj"], "destination_id": "bin"},
                },
            },
            {
                "expected": {"pipeline_status": "BLOCKED", "task": {"status": "NEEDS_CLARIFICATION"}},
                "actual": {"status": "BLOCKED", "task": {"status": "NEEDS_CLARIFICATION"}},
            },
        ])
        self.assertEqual(metrics["binding_accuracy"], 1.0)
        self.assertEqual(metrics["ambiguity_f1"], 1.0)
        self.assertEqual(metrics["missed_clarification_rate"], 0.0)
        self.assertEqual(metrics["dangerous_false_execution_rate"], 0.0)

    def test_missing_labels_are_not_guessed(self):
        metrics = compute_metrics([{"expected": {}, "actual": {"status": "SUCCEEDED"}}])
        self.assertIsNone(metrics["binding_accuracy"])
        self.assertEqual(metrics["dangerous_false_execution_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
