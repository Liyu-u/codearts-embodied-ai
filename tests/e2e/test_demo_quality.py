"""Quality-oriented assertions for every closed-loop Demo scenario.

The existing scenario test checks the declared final status.  These tests go
further: they verify the stage at which a scenario stops, the execution
evidence, the feedback decision, and the final scene state.
"""

from __future__ import annotations

import json
import unittest

from demo.scenarios import get_scenario, list_scenarios
from demo.server import _run_demo


def _run(scene_id: str, instruction: str | None = None, request_id: str | None = None) -> dict:
    scenario = get_scenario(scene_id)
    payload = {
        "scene_id": scene_id,
        "instruction": instruction or scenario["instruction"],
        "engine": "rule",
    }
    if request_id:
        payload["request_id"] = request_id
    return _run_demo(payload)


def _diagnosis(result: dict) -> dict:
    feedback = result.get("feedback")
    if not feedback:
        return {}
    return json.loads(feedback["diagnosis"])


class DemoQualityTests(unittest.TestCase):
    def test_catalog_is_unique_and_has_complete_demo_metadata(self):
        scenarios = list_scenarios()
        ids = [item["id"] for item in scenarios]
        self.assertGreaterEqual(len(scenarios), 10)
        self.assertEqual(len(ids), len(set(ids)))
        for item in scenarios:
            with self.subTest(scene_id=item["id"]):
                self.assertTrue(item["name"])
                self.assertTrue(item["description"])
                self.assertTrue(item["instruction"])
                self.assertIn(item["expected"], {"SUCCEEDED", "BLOCKED", "FAILED", "SAFE_STOP"})

    def test_success_scenarios_have_complete_execution_evidence(self):
        for item in list_scenarios():
            if item["expected"] != "SUCCEEDED":
                continue
            with self.subTest(scene_id=item["id"]):
                response = _run(item["id"])
                result = response["result"]
                task = result["task"]
                strategy = result["strategy"]
                execution = result["execution"]
                feedback = result["feedback"]
                diagnosis = _diagnosis(result)

                self.assertEqual(result["status"], "SUCCEEDED")
                self.assertEqual(task["status"], "READY")
                self.assertEqual(task["action"], "pick_and_place")
                self.assertFalse(strategy.get("blocked", False))
                self.assertEqual(
                    [step["action"] for step in strategy["steps"]],
                    ["detect_object", "move_to_object", "grasp", "move_to_target", "release"],
                )
                self.assertEqual(execution["status"], "SUCCEEDED")
                self.assertTrue(execution["steps"])
                self.assertEqual(execution["steps"][-1]["status"], "SUCCESS")
                self.assertIsNotNone(feedback)
                self.assertFalse(feedback["retryable"])
                self.assertTrue(diagnosis["final_passed"])
                self.assertTrue(diagnosis["execution_passed"])

                snapshot = response["backend_snapshot"]["objects"]
                self.assertEqual(
                    snapshot[task["target_ids"][0]]["pose"],
                    snapshot[task["destination_id"]]["pose"],
                )

                if item["id"] == "tracecoder_repair":
                    self.assertEqual(result["retry_count"], 1)
                    self.assertEqual(len(result["attempts"]), 2)
                    patch = result["attempts"][0]["feedback"]["patch"]
                    grasp = next(step for step in patch["steps"] if step["action"] == "grasp")
                    recovery_steps = grasp["on_failure"]["steps"]
                    self.assertTrue(all(step.get("step_id") for step in recovery_steps))
                    self.assertEqual(result["attempts"][0]["execution"]["status"], "FAILED")
                    self.assertEqual(result["attempts"][1]["execution"]["status"], "SUCCEEDED")
                elif item["id"] == "grasp_retry_success":
                    self.assertEqual(result["retry_count"], 0)
                    self.assertIn(
                        "recovery_1",
                        {step.get("phase") for step in execution["steps"]},
                    )
                else:
                    self.assertEqual(result["retry_count"], 0)

    def test_blocked_scenarios_stop_at_the_correct_boundary(self):
        a_blocked = {"ambiguous_red_cubes", "no_destination", "target_not_found"}
        b_blocked = {"unsupported_push"}
        for scene_id in a_blocked | b_blocked:
            with self.subTest(scene_id=scene_id):
                result = _run(scene_id)["result"]
                self.assertEqual(result["status"], "BLOCKED")
                self.assertNotIn("execution", result)
                self.assertNotIn("feedback", result)
                if scene_id in a_blocked:
                    self.assertNotIn("strategy", result)
                    self.assertNotEqual(result["task"]["status"], "READY")
                else:
                    self.assertTrue(result["strategy"]["blocked"])
                    self.assertIn(
                        "UNSUPPORTED_ACTION:push",
                        result["strategy"]["blocking_reasons"],
                    )

    def test_failure_and_safe_stop_keep_diagnostic_evidence(self):
        safe_stop_response = _run("grasp_safe_stop")
        safe_stop = safe_stop_response["result"]
        self.assertEqual(safe_stop["status"], "SAFE_STOP")
        self.assertEqual(safe_stop["execution"]["status"], "SAFE_STOP")
        self.assertIn(
            "RECOVERY_EXHAUSTED",
            {event["type"] for event in safe_stop["execution"]["safety_events"]},
        )
        self.assertFalse(safe_stop["feedback"]["retryable"])
        self.assertFalse(_diagnosis(safe_stop)["execution_passed"])
        self.assertTrue(safe_stop_response["backend_snapshot"]["safe_stopped"])

        failed = _run("invalid_destination")["result"]
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["execution"]["status"], "FAILED")
        failed_steps = [step for step in failed["execution"]["steps"] if step["status"] == "FAILED"]
        self.assertTrue(failed_steps)
        self.assertIn("INVALID_DESTINATION", failed_steps[0]["reason"])
        self.assertIsNotNone(failed["feedback"])
        self.assertFalse(failed["feedback"]["retryable"])
        diagnosis = _diagnosis(failed)
        self.assertFalse(diagnosis["execution_passed"])
        self.assertIn(diagnosis["retry_reason"], {"PATCH_UNCHANGED", "FEEDBACK_NOT_RETRYABLE"})

    def test_same_scenario_is_deterministic_with_distinct_request_ids(self):
        first = _run("tracecoder_repair", request_id="quality-run-1")
        second = _run("tracecoder_repair", request_id="quality-run-2")
        for response in (first, second):
            self.assertTrue(response["request_id"].startswith("quality-run-"))
        self.assertNotEqual(first["request_id"], second["request_id"])

        def stable(response: dict) -> dict:
            result = response["result"]
            return {
                "status": result["status"],
                "task_status": result["task"]["status"],
                "action": result["task"]["action"],
                "retry_count": result["retry_count"],
                "stop_reason": result["stop_reason"],
                "attempt_statuses": [item["execution"]["status"] for item in result["attempts"]],
                "snapshot": response["backend_snapshot"],
            }

        self.assertEqual(stable(first), stable(second))


if __name__ == "__main__":
    unittest.main()
