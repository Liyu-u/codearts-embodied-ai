"""Offline policy matrix for planner/critic CodeArts routing."""

import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from integration.adapters import strategy


ROOT = Path(__file__).resolve().parents[2]
TASK = json.loads(
    (ROOT / "testdata" / "daily" / "strategy_normal_pick.json").read_text(
        encoding="utf-8"
    )
)


def _provider_result():
    with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "off"}):
        candidate = strategy.run(deepcopy(TASK))
    candidate.update(
        {
            "mode": "codearts_agent",
            "provenance": {
                "provider": "huaweicloud-codearts-agent",
                "transport": "codearts-cli",
            },
        }
    )
    return {
        "success": True,
        "strategy": candidate,
        "error": None,
        "trace": candidate["provenance"],
    }


def _review_result(round_no=1):
    return {
        "success": True,
        "review": {"status": "PASS", "issues": [], "risk_level": "LOW"},
        "error": None,
        "trace": {
            "provider": "huaweicloud-codearts-agent",
            "transport": "codearts-cli",
            "role": "critic",
            "round": round_no,
        },
    }


class CodeArtsStrategyPolicyTests(unittest.TestCase):
    def test_planner_is_low_latency_and_does_not_call_critic(self):
        with patch.dict(
            os.environ,
            {"CODEARTS_STRATEGY_MODE": "required", "CODEARTS_STRATEGY_POLICY": "planner"},
        ):
            with patch("integration.adapters.strategy.CodeArtsStrategyClient") as client:
                client.return_value.generate.return_value = _provider_result()
                output = strategy.run(deepcopy(TASK))
        self.assertEqual(output["strategy_policy"], "planner")
        client.return_value.generate.assert_called_once_with(TASK)
        client.return_value.review.assert_not_called()

    def test_quality_adds_one_independent_critic(self):
        with patch.dict(
            os.environ,
            {"CODEARTS_STRATEGY_MODE": "required", "CODEARTS_STRATEGY_POLICY": "quality"},
        ):
            with patch("integration.adapters.strategy.CodeArtsStrategyClient") as client:
                client.return_value.generate.return_value = _provider_result()
                client.return_value.review.return_value = _review_result()
                output = strategy.run(deepcopy(TASK))
        self.assertEqual(output["strategy_policy"], "planner_critic")
        self.assertEqual(len(output["critics"]), 1)
        client.return_value.review.assert_called_once_with(TASK, output, round_no=1)

    def test_max_requires_two_passes(self):
        with patch.dict(
            os.environ,
            {"CODEARTS_STRATEGY_MODE": "required", "CODEARTS_STRATEGY_POLICY": "max"},
        ):
            with patch("integration.adapters.strategy.CodeArtsStrategyClient") as client:
                client.return_value.generate.return_value = _provider_result()
                client.return_value.review.side_effect = [_review_result(1), _review_result(2)]
                output = strategy.run(deepcopy(TASK))
        self.assertEqual(output["strategy_policy"], "planner_critic_double")
        self.assertEqual([item["round"] for item in output["critics"]], [1, 2])
        self.assertEqual(client.return_value.review.call_count, 2)

    def test_required_mode_blocks_when_critic_rejects(self):
        failure = {
            "success": False,
            "review": None,
            "error": "CODEARTS_REVIEW_REJECTED:BLOCK",
            "trace": {"provider": "huaweicloud-codearts-agent", "role": "critic"},
        }
        with patch.dict(
            os.environ,
            {"CODEARTS_STRATEGY_MODE": "required", "CODEARTS_STRATEGY_POLICY": "quality"},
        ):
            with patch("integration.adapters.strategy.CodeArtsStrategyClient") as client:
                client.return_value.generate.return_value = _provider_result()
                client.return_value.review.return_value = failure
                output = strategy.run(deepcopy(TASK))
        self.assertTrue(output["blocked"])
        self.assertIn("CODEARTS_REVIEW_REJECTED:BLOCK", output["blocking_reasons"])

    def test_auto_mode_falls_back_when_critic_unavailable(self):
        failure = {
            "success": False,
            "review": None,
            "error": "CODEARTS_CLI_TIMEOUT",
            "trace": {"provider": "huaweicloud-codearts-agent", "role": "critic"},
        }
        with patch.dict(
            os.environ,
            {"CODEARTS_STRATEGY_MODE": "auto", "CODEARTS_STRATEGY_POLICY": "quality"},
        ):
            with patch("integration.adapters.strategy.CodeArtsStrategyClient") as client:
                client.return_value.generate.return_value = _provider_result()
                client.return_value.review.return_value = failure
                output = strategy.run(deepcopy(TASK))
        self.assertTrue(output["success"])
        self.assertEqual(output["mode"], "primitive_plan_fallback")
        self.assertEqual(output["provider_error"], "CODEARTS_CLI_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
