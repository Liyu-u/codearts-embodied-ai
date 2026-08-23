"""Regression tests for TraceCoder's risk-based invocation budgets."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch
from pathlib import Path

import integration.adapters.tracecoder as adapter_mod
from integration.adapters.tracecoder import configure_llm, run
from modules.evaluator.tracecoder.processor import process_policy
from tests.helpers.fake_llm_provider import FakeLLMProvider, smart_handler
from tests.helpers.tracecoder_fixtures import (
    DEMO_STRATEGY_V1,
    DEMO_TASK_V1,
    demo_task_data,
)


class TestTraceCoderBudget(unittest.TestCase):
    def setUp(self):
        adapter_mod._EXPERIENCE_STORE = None
        configure_llm(mode=None, provider=None)

    def tearDown(self):
        configure_llm(mode=None, provider=None)

    def test_healthy_success_skips_tracecoder_without_provider_call(self):
        fake = FakeLLMProvider(handler=smart_handler())
        configure_llm(mode="optional", provider=fake)
        execution = {
            "schema_version": "execution.v1",
            "task_id": DEMO_TASK_V1["task_id"],
            "status": "SUCCEEDED",
            "steps": [{"step_id": "done", "action": "noop", "status": "SUCCEEDED"}],
            "safety_events": [],
        }
        perception = {
            "schema_version": "perception.v1",
            "scene_id": "scene-budget",
            "objects": [{"id": "red_cup"}, {"id": "left_bin"}],
        }
        result = run({
            "task": DEMO_TASK_V1,
            "strategy": DEMO_STRATEGY_V1,
            "execution": execution,
            "perception": perception,
        })
        diagnosis = json.loads(result["diagnosis"])
        self.assertFalse(diagnosis["tracecoder_invoked"])
        self.assertEqual(result["provenance"]["source"], "tracecoder_skipped")
        self.assertEqual(fake.calls, [])

    def test_budget_tiers_are_bounded_and_distinct(self):
        failed = {
            "task": {"task_id": "t", "target_ids": [], "status": "READY"},
            "strategy": {"confidence": 0.95},
            "execution": {
                "status": "FAILED",
                "steps": [{"status": "FAILED"}],
            },
        }
        normal, reasons = adapter_mod._select_tracecoder_budget(failed, "optional")
        self.assertEqual(normal.tier, "normal")
        self.assertEqual(normal.max_tokens, 3072)
        self.assertEqual(normal.thinking, "disabled")
        self.assertEqual(normal.max_retries, 1)
        self.assertEqual(normal.max_repair_attempts, 1)
        self.assertFalse(normal.optimize_quality)
        self.assertEqual(normal.call_style, "compact")
        self.assertTrue(reasons)

        failed["retry_count"] = 1
        hard, _ = adapter_mod._select_tracecoder_budget(failed, "optional")
        self.assertEqual(hard.tier, "hard")
        self.assertEqual(hard.max_tokens, 6144)
        self.assertEqual(hard.thinking, "enabled")
        self.assertEqual(hard.max_retries, 1)
        self.assertEqual(hard.max_repair_attempts, 1)
        self.assertEqual(hard.call_style, "roles")

        failed["tracecoder_profile"] = "expert"
        expert, _ = adapter_mod._select_tracecoder_budget(failed, "optional")
        self.assertEqual(expert.tier, "expert")
        self.assertEqual(expert.max_tokens, 8192)
        self.assertTrue(expert.optimize_quality)

    def test_legacy_routing_is_explicit_rollback_for_healthy_success(self):
        healthy = {
            "task": {"task_id": "t"},
            "strategy": {"confidence": 0.95},
            "execution": {
                "status": "SUCCEEDED",
                "steps": [{"status": "SUCCEEDED"}],
            },
        }
        with patch.dict(os.environ, {
            "TRACECODER_ADAPTIVE_ROUTING": "legacy",
            "TRACECODER_LEGACY_MAX_TOKENS": "8192",
            "TRACECODER_LEGACY_THINKING": "enabled",
            "TRACECODER_LEGACY_MAX_RETRIES": "2",
            "TRACECODER_LEGACY_MAX_REPAIR_ATTEMPTS": "2",
        }, clear=False):
            budget, reasons = adapter_mod._select_tracecoder_budget(healthy, "optional")
        self.assertIsNotNone(budget)
        self.assertEqual(budget.tier, "legacy")
        self.assertEqual(budget.max_tokens, 8192)
        self.assertEqual(budget.thinking, "enabled")
        self.assertEqual(budget.max_retries, 2)
        self.assertEqual(budget.max_repair_attempts, 2)
        self.assertTrue(budget.optimize_quality)
        self.assertEqual(budget.call_style, "roles")
        self.assertIn("legacy_compatibility", reasons)

    def test_compact_repair_uses_one_model_request(self):
        fake = FakeLLMProvider(handler=smart_handler())
        native = adapter_mod._strategy_v1_to_native(DEMO_STRATEGY_V1)
        result = process_policy(
            demo_task_data(),
            initial_strategy=native,
            max_repair_attempts=1,
            optimize_quality=False,
            call_style="compact",
            llm_mode="optional",
            llm_provider=fake,
        )
        self.assertTrue(result["final_passed"])
        self.assertEqual(result["llm_stats"]["calls"], 1)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["role"], "compact")


if __name__ == "__main__":
    unittest.main()
