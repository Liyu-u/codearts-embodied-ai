"""Offline proof that the B adapter distinguishes local and CodeArts paths."""

import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from integration.adapters import strategy
from modules.strategy_generation.codearts_agent import validate_strategy


ROOT = Path(__file__).resolve().parents[2]
TASK = json.loads(
    (ROOT / "testdata" / "daily" / "strategy_normal_pick.json").read_text(
        encoding="utf-8"
    )
)


class TestCodeArtsComparison(unittest.TestCase):
    def test_off_and_required_paths_are_observably_different(self):
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "off"}):
            baseline = strategy.run(deepcopy(TASK))

        provider_strategy = deepcopy(baseline)
        provider_strategy.update(
            {
                "mode": "codearts_agent",
                "message": "CodeArts 智能体已生成并通过本地安全校验",
                "provenance": {
                    "provider": "huaweicloud-codearts-agent",
                    "transport": "codearts-cli",
                },
            }
        )
        provider_result = {
            "success": True,
            "strategy": provider_strategy,
            "error": None,
            "trace": provider_strategy["provenance"],
        }
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "required"}):
            with patch(
                "integration.adapters.strategy.CodeArtsStrategyClient"
            ) as client_class:
                client_class.return_value.generate.return_value = provider_result
                codearts = strategy.run(deepcopy(TASK))
                client_class.return_value.generate.assert_called_once_with(TASK)

        self.assertTrue(baseline["success"])
        self.assertEqual(baseline["mode"], "primitive_plan")
        self.assertNotIn("provenance", baseline)
        self.assertTrue(codearts["success"])
        self.assertEqual(codearts["mode"], "codearts_agent")
        self.assertEqual(
            codearts["provenance"]["provider"], "huaweicloud-codearts-agent"
        )
        self.assertEqual(validate_strategy(codearts, TASK), [])

    def test_repeated_provider_results_keep_execution_contract(self):
        baseline = strategy.run(deepcopy(TASK))
        provider_strategy = deepcopy(baseline)
        provider_strategy.update(
            {
                "mode": "codearts_agent",
                "provenance": {
                    "provider": "huaweicloud-codearts-agent",
                    "transport": "codearts-cli",
                },
            }
        )
        result = {
            "success": True,
            "strategy": provider_strategy,
            "error": None,
            "trace": provider_strategy["provenance"],
        }
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "required"}):
            with patch(
                "integration.adapters.strategy.CodeArtsStrategyClient"
            ) as client_class:
                client_class.return_value.generate.return_value = result
                outputs = [strategy.run(deepcopy(TASK)) for _ in range(5)]

        self.assertTrue(all(output["success"] for output in outputs))
        self.assertTrue(all(output["mode"] == "codearts_agent" for output in outputs))
        self.assertTrue(
            all(output["provenance"]["provider"] == "huaweicloud-codearts-agent" for output in outputs)
        )
        self.assertTrue(all(output["code"] is None for output in outputs))
        self.assertEqual(client_class.return_value.generate.call_count, 5)


if __name__ == "__main__":
    unittest.main()
