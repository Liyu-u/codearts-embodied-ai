"""Offline checks for the reusable CodeArts test sets."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.run_codearts_testsets import (
    _is_transient_provider_error,
    _local_provider_result,
    list_testsets,
    load_testset,
    run_testsets,
)


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

    def test_live_batch_retries_transient_provider_failure(self):
        case = load_testset("normal_scale_functional")["cases"][0]
        valid = _local_provider_result(case["task"])["strategy"]
        valid["critics"] = [{"passed": True}]
        transient = {
            "success": False,
            "blocked": True,
            "code": None,
            "mode": "codearts_blocked",
            "steps": [],
            "critics": [],
            "provider_error": "CODEARTS_CLI_TIMEOUT",
            "blocking_reasons": ["provider unavailable"],
            "provenance": {"provider": "huaweicloud-codearts-agent"},
        }
        with patch(
            "tools.run_codearts_testsets.strategy_adapter.run",
            side_effect=[transient, valid],
        ) as mocked:
            report = run_testsets(
                ["normal_scale_functional"],
                live=True,
                policy="quality",
                repeats=1,
                limit=1,
                transport_retries=1,
                retry_backoff_s=0,
            )
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(report["summary"]["all_passed"], report)
        self.assertEqual(report["summary"]["transport_retries"], 1)
        self.assertEqual(report["summary"]["provider_attempts"], 2)
        self.assertTrue(_is_transient_provider_error(transient))

    def test_live_batch_resume_skips_completed_cases(self):
        case = load_testset("normal_scale_functional")["cases"][0]
        valid = _local_provider_result(case["task"])["strategy"]
        valid["critics"] = [{"passed": True}]
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "batch.json"
            with patch("tools.run_codearts_testsets.strategy_adapter.run", return_value=valid) as mocked:
                first = run_testsets(
                    ["normal_scale_functional"],
                    live=True,
                    policy="quality",
                    repeats=1,
                    limit=1,
                    output=output,
                    transport_retries=0,
                )
                self.assertEqual(mocked.call_count, 1)
            with patch("tools.run_codearts_testsets.strategy_adapter.run", side_effect=AssertionError("must resume")):
                resumed = run_testsets(
                    ["normal_scale_functional"],
                    live=True,
                    policy="quality",
                    repeats=1,
                    limit=1,
                    output=output,
                    resume=True,
                    transport_retries=0,
                )
        self.assertEqual(first["summary"], resumed["summary"])


if __name__ == "__main__":
    unittest.main()
