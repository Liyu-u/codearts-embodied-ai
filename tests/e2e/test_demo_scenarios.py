"""Backend contract checks for every scenario exposed by the frontend Demo."""

from __future__ import annotations

import unittest

from demo.scenarios import get_scenario, list_scenarios
from demo.server import _run_demo


class DemoScenarioPipelineTests(unittest.TestCase):
    def test_every_frontend_scenario_reaches_its_declared_outcome(self):
        scenarios = list_scenarios()
        self.assertGreaterEqual(len(scenarios), 8)

        for scenario in scenarios:
            with self.subTest(scene_id=scenario["id"]):
                response = _run_demo(
                    {
                        "scene_id": scenario["id"],
                        "instruction": scenario["instruction"],
                        "engine": "rule",
                    }
                )
                self.assertTrue(response["ok"])
                self.assertEqual(response["scenario"]["id"], scenario["id"])
                self.assertEqual(response["result"]["status"], scenario["expected"])
                self.assertEqual(
                    response["acceptance"],
                    {
                        "expected_status": scenario["expected"],
                        "actual_status": scenario["expected"],
                        "passed": True,
                        "message": "实际结果符合场景预期",
                    },
                )
                self.assertIn("scene", response)
                self.assertIn("result", response)
                self.assertIn("backend_snapshot", response)

    def test_tracecoder_repair_scenario_exposes_real_patch_retry(self):
        response = _run_demo(
            {
                "scene_id": "tracecoder_repair",
                "instruction": get_scenario("tracecoder_repair")["instruction"],
                "engine": "rule",
                "request_id": "demo-tracecoder-repair-test",
            }
        )
        result = response["result"]
        self.assertEqual(result["status"], "SUCCEEDED")
        self.assertEqual(result["retry_count"], 1)
        self.assertEqual(len(result["attempts"]), 2)
        first, second = result["attempts"]
        self.assertEqual(first["execution"]["status"], "FAILED")
        self.assertTrue(first["feedback"]["retryable"])
        self.assertEqual(second["execution"]["status"], "SUCCEEDED")
        self.assertTrue(
            any(step.get("on_failure") for step in first["feedback"]["patch"]["steps"])
        )
        self.assertEqual(result["stop_reason"], "EXECUTION_SUCCEEDED")

    def test_demo_request_id_prevents_same_scene_task_collision(self):
        payload = {
            "scene_id": "stacking_cubes",
            "instruction": "把红色方块放到桌子上",
            "engine": "rule",
        }
        first = _run_demo(payload)
        second = _run_demo(payload)
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertNotEqual(first["result"]["task"]["task_id"], second["result"]["task"]["task_id"])

    def test_sorting_workcell_supports_multiple_commands(self):
        scenario = get_scenario("sorting_workcell")
        self.assertEqual(len(scenario["commands"]), 6)

        for command in scenario["commands"]:
            with self.subTest(instruction=command["instruction"]):
                response = _run_demo(
                    {
                        "scene_id": "sorting_workcell",
                        "instruction": command["instruction"],
                        "engine": "rule",
                    }
                )
                result = response["result"]
                self.assertEqual(result["status"], command["expected"], result)

                if command["expected"] != "SUCCEEDED":
                    self.assertNotIn("execution", result)
                    continue

                self.assertEqual(result["task"]["action"], "pick_and_place")
                self.assertEqual(result["task"]["target_ids"], [command["target_id"]])
                self.assertEqual(
                    result["task"]["destination_id"], command["destination_id"]
                )
                self.assertEqual(result["execution"]["status"], "SUCCEEDED")
                snapshot = response["backend_snapshot"]
                self.assertEqual(
                    snapshot["objects"][command["target_id"]]["pose"],
                    snapshot["objects"][command["destination_id"]]["pose"],
                )


if __name__ == "__main__":
    unittest.main()
