"""策略生成模块的 task.v1 -> strategy.v1 契约与冒烟测试。"""

import json
import unittest
from pathlib import Path

from integration.adapters import strategy
from tests.helpers.schema_validate import load_schema, validate


ROOT = Path(__file__).resolve().parents[2]
TESTDATA = ROOT / "testdata" / "daily"


def load_task(name: str) -> dict:
    return json.loads((TESTDATA / name).read_text(encoding="utf-8"))


class TestStrategyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema("strategy.schema.json")

    def assert_strategy_schema(self, output: dict):
        errors = validate(output, self.schema)
        self.assertEqual(errors, [], "strategy.v1 校验失败:\n" + "\n".join(errors))

    def test_normal_task_generates_strategy(self):
        output = strategy.run(load_task("strategy_normal_pick.json"))

        self.assert_strategy_schema(output)
        self.assertTrue(output["success"])
        self.assertFalse(output["blocked"])
        self.assertEqual(output["task_id"], "task-001")
        self.assertEqual(output["steps"][0]["arguments"]["target_id"], "obj-001")
        self.assertEqual(output["steps"][0]["arguments"]["destination"]["z"], 0.03)
        self.assertIsInstance(output["code"], str)

    def test_non_ready_tasks_are_blocked(self):
        for filename in ("strategy_target_not_found.json", "strategy_blocked_dangerous.json"):
            with self.subTest(filename=filename):
                output = strategy.run(load_task(filename))
                self.assert_strategy_schema(output)
                self.assertFalse(output["success"])
                self.assertTrue(output["blocked"])
                self.assertEqual(output["steps"], [])
                self.assertIsNone(output["code"])

    def test_incomplete_ready_task_is_blocked(self):
        output = strategy.run({
            "schema_version": "task.v1",
            "task_id": "missing-target",
            "action": "pick_and_place",
            "status": "READY",
            "destination": {"x": 0.2, "y": 0.0, "z": 0.03},
        })

        self.assert_strategy_schema(output)
        self.assertTrue(output["blocked"])
        self.assertIsNone(output["code"])

    def test_all_builtin_actions_generate(self):
        tasks = [
            {
                "action": "pick_and_place",
                "target_object": "red",
                "destination": {"x": 0.2, "y": 0.0, "z": 0.03},
            },
            {
                "action": "push",
                "target_object": "green",
                "destination": {"x": 0.4, "y": -0.2, "z": 0.04},
            },
            {
                "action": "stack",
                "target_object": "red",
                "reference_object": "blue",
            },
            {
                "action": "sort_by_color",
                "target_objects": ["red", "blue"],
                "attributes": ["red", "blue"],
                "num_piles": 2,
            },
            {"action": "sort_by_size", "target_objects": ["red", "blue"]},
            {
                "action": "filter_by_attribute",
                "target_objects": ["red", "blue"],
                "attributes": ["red"],
                "destination": {"x": 0.2, "y": 0.0, "z": 0.03},
            },
        ]

        for index, details in enumerate(tasks):
            with self.subTest(action=details["action"]):
                task = {
                    "schema_version": "task.v1",
                    "task_id": f"builtin-{index}",
                    "status": "READY",
                    **details,
                }
                output = strategy.run(task)
                self.assert_strategy_schema(output)
                self.assertTrue(output["success"], output.get("message"))
                self.assertIsInstance(output["code"], str)


if __name__ == "__main__":
    unittest.main()
