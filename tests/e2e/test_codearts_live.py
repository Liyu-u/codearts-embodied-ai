"""Opt-in live stability check; skipped during ordinary offline test runs."""

import os
import unittest

from tools.benchmark_codearts import run_benchmark


@unittest.skipUnless(
    os.environ.get("CODEARTS_LIVE_TEST") == "1",
    "set CODEARTS_LIVE_TEST=1 to call the real CodeArts CLI",
)
class TestCodeArtsLive(unittest.TestCase):
    def test_repeated_codearts_runs_are_stable_and_provider_backed(self):
        report = run_benchmark(
            repeats=int(os.environ.get("CODEARTS_LIVE_REPEATS", "2")),
            case_count=int(os.environ.get("CODEARTS_LIVE_CASES", "3")),
            live=True,
            model=os.environ.get("CODEARTS_STRATEGY_MODEL")
            or "huaweicloud-maas/openpangu-2.0-flash",
            timeout_s=int(os.environ.get("CODEARTS_STRATEGY_TIMEOUT_S", "90")),
            pure=os.environ.get("CODEARTS_CLI_PURE") == "1",
        )
        self.assertTrue(report["comparison"]["provider_calls_prove_codearts_intervened"])
        self.assertTrue(report["comparison"]["stable"], report)


if __name__ == "__main__":
    unittest.main()
