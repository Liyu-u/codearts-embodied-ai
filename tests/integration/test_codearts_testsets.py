"""Offline checks for the reusable CodeArts test sets."""

import unittest

from tools.run_codearts_testsets import list_testsets, run_testsets


class CodeArtsTestsetTests(unittest.TestCase):
    def test_all_testsets_are_valid_and_pass_offline(self):
        names = list_testsets()
        for required in (
            "normal_quality",
            "safety_boundary",
            "stability_repeat",
            "normal_scale_functional",
            "normal_scale_semantic",
            "normal_scale_safety",
            "normal_scale_stability",
            "normal_scale_resilience",
        ):
            self.assertIn(required, names)
        report = run_testsets(names, live=False, repeats=2)
        self.assertTrue(report["summary"]["all_passed"], report)
        self.assertTrue(report["summary"]["all_stable"], report)
        self.assertEqual(report["summary"]["provider_calls"], 0)
        self.assertEqual(report["summary"]["contract_failures"], 0)


if __name__ == "__main__":
    unittest.main()
