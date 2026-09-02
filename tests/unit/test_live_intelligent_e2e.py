import unittest

from tools.live_intelligent_e2e import api_call_evidence, audit_documents, build_normal_schedule, compute_metrics, document_digest, strategy_digest


class LiveIntelligentEvidenceTests(unittest.TestCase):
    def _documents(self):
        strategy = {
            "schema_version": "strategy.v1",
            "task_id": "task-1",
            "steps": [{"step_id": "s1", "action": "detect_object", "arguments": {"object_id": "green_cube"}}],
            "provenance": {"provider": "codearts", "request_id": "ca-1", "fallback": False, "validation": {"passed": True}},
        }
        perception = {"schema_version": "perception.v1", "provenance": {"backend": "isaac_ground_truth"}}
        strategy["input_perception_sha256"] = document_digest(perception)
        return {
            "input": {"instruction": "把绿色方块放到桌面上"},
            "api_calls": {
                "intent": {"provider": "deepseek", "network_calls": 1, "succeeded": True, "request_id": "ds-a-1", "fallback": False},
                "strategy": {"provider": "codearts", "calls": 1, "succeeded": True, "request_id": "ca-1", "fallback": False},
                "feedback": {"provider": "deepseek", "network_calls": 1, "succeeded": True, "request_id": "ds-d-1", "fallback": False},
            },
            "task": {"schema_version": "task.v1", "task_id": "task-1", "status": "READY", "target_ids": ["green_cube"]},
            "strategy": strategy,
            "perception": perception,
            "execution": {
                "schema_version": "execution.v1",
                "task_id": "task-1",
                "status": "SUCCEEDED",
                "cube_after": {"x": 0.45, "y": 0.1, "z": 0.03},
                "input_strategy_sha256": strategy_digest(strategy),
                "steps": [{"step_id": "s1", "status": "SUCCESS"}],
            },
            "progress": [{"step": "report", "status": "done"}],
            "container_log_present": True,
            "final_pose": {"object_id": "green_cube", "pose": {"x": 0.45, "y": 0.1, "z": 0.03}},
        }

    def test_intelligent_run_requires_fresh_provider_and_isaac_evidence(self):
        result = audit_documents(self._documents(), "V4_FULL")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["errors"], [])

    def test_missing_codearts_request_id_disqualifies_intelligent_run(self):
        docs = self._documents()
        docs["api_calls"]["strategy"]["request_id"] = None
        result = audit_documents(docs, "V2_FULL_NO_D")
        self.assertFalse(result["eligible"])
        self.assertIn("CODEARTS_REQUEST_ID_MISSING", result["errors"])

    def test_strategy_substitution_is_detected(self):
        docs = self._documents()
        docs["execution"]["input_strategy_sha256"] = "different"
        result = audit_documents(docs, "V1_CODEARTS_POLICY")
        self.assertFalse(result["eligible"])
        self.assertIn("EXECUTED_STRATEGY_MISMATCH", result["errors"])

    def test_strategy_must_be_generated_from_same_live_perception(self):
        docs = self._documents()
        docs["strategy"]["input_perception_sha256"] = "stale-scene"
        docs["execution"]["input_strategy_sha256"] = strategy_digest(docs["strategy"])
        result = audit_documents(docs, "V1_CODEARTS_POLICY")
        self.assertFalse(result["eligible"])
        self.assertIn("STRATEGY_PERCEPTION_MISMATCH", result["errors"])

    def test_v2_does_not_require_feedback_but_v4_does(self):
        docs = self._documents()
        del docs["api_calls"]["feedback"]
        self.assertTrue(audit_documents(docs, "V2_FULL_NO_D")["eligible"])
        self.assertFalse(audit_documents(docs, "V4_FULL")["eligible"])

    def test_safe_stop_rejects_successful_actions_after_stop(self):
        docs = self._documents()
        docs["execution"].update({
            "status": "SAFE_STOP",
            "steps": [
                {"step_id": "move", "status": "FAILED", "reason": "E_STOP_TRIGGERED"},
                {"step_id": "release", "status": "SUCCESS"},
            ],
        })
        result = audit_documents(docs, "V4_FULL")
        self.assertFalse(result["eligible"])
        self.assertIn("ACTION_AFTER_TERMINAL_STOP", result["errors"])

    def test_safe_stop_allows_explicit_terminal_stop_step(self):
        docs = self._documents()
        docs["execution"].update({
            "status": "SAFE_STOP",
            "steps": [
                {
                    "step_id": "grasp",
                    "action": "grasp",
                    "status": "FAILED",
                    "reason": "COLLISION_DETECTED",
                },
                {
                    "step_id": "safe_stop",
                    "action": "stop",
                    "phase": "safe_stop",
                    "status": "SUCCESS",
                    "reason": "COLLISION_DETECTED",
                },
            ],
        })

        result = audit_documents(docs, "V4_FULL")

        self.assertTrue(result["eligible"])
        self.assertNotIn(
            "ACTION_AFTER_TERMINAL_STOP",
            result["errors"],
        )

    def test_metrics_keep_physical_safety_and_api_failures_separate(self):
        metrics = compute_metrics([
            {"eligible": True, "population": "normal", "status": "SUCCEEDED", "api_ok": True, "contract_ok": True, "binding_ok": True, "duration_ms": 100},
            {"eligible": True, "population": "safety", "status": "SAFE_STOP", "api_ok": True, "dangerous_action_executed": False, "recovery_succeeded": True, "duration_ms": 50},
            {"eligible": False, "population": "api_failure", "status": "BLOCKED", "api_ok": False, "duration_ms": 10},
        ])
        self.assertEqual(metrics["normal"]["physical_task_success_rate"], 1.0)
        self.assertEqual(metrics["safety"]["safe_stop_correct_rate"], 1.0)
        self.assertEqual(metrics["safety"]["dangerous_action_execution_rate"], 0.0)
        self.assertEqual(metrics["api_failure"]["api_call_failure_rate"], 1.0)

    def test_api_call_evidence_uses_real_provider_metadata(self):
        task = {"diagnostics": {"engine_trace": {
            "llm_network_calls": 1,
            "llm_call_succeeded": True,
            "llm_request_id": "deepseek-1",
            "llm_request_id_source": "provider_response",
            "fallback_used": False,
        }}}
        strategy = {"success": True, "blocked": False, "provenance": {
            "provider": "codearts_cli", "request_id": "codearts-1", "fallback": False,
        }}
        calls = api_call_evidence(task, strategy)
        self.assertEqual(calls["intent"]["request_id"], "deepseek-1")
        self.assertEqual(calls["strategy"]["request_id"], "codearts-1")
        self.assertEqual(calls["strategy"]["calls"], 1)

    def test_normal_schedule_is_twenty_cases_four_versions_three_repeats(self):
        cases = [{"id": f"case-{index:02d}"} for index in range(20)]
        schedule = build_normal_schedule(cases, repeats=3, seed=20260831)
        self.assertEqual(len(schedule), 240)
        self.assertEqual({row["variant_id"] for row in schedule}, {"V0_RULE_BASELINE", "V1_CODEARTS_POLICY", "V2_FULL_NO_D", "V4_FULL"})
        self.assertEqual(len({(row["case_id"], row["repeat"], row["seed"]) for row in schedule}), 60)


if __name__ == "__main__":
    unittest.main()
