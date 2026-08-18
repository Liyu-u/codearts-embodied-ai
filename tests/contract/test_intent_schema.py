"""Intent-understanding adapter -> task.v1 contract smoke tests."""

import unittest

from integration.adapters import intent
from tests.helpers.schema_validate import load_schema, validate


class TestIntentContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = load_schema("task.schema.json")

    def assert_task_schema(self, output):
        errors = validate(output, self.schema)
        self.assertEqual(errors, [], "task.v1 校验失败:\n" + "\n".join(errors))

    def test_ready_pick_and_place(self):
        output = intent.run(
            {
                "instruction": "把红色方块放到桌子上",
                "perception": {
                    "schema_version": "perception.v1",
                    "scene_id": "scene-contract-001",
                    "objects": [
                        {
                            "id": "obj-red",
                            "category": "红色方块",
                            "pose": {"x": 0.10, "y": 0.00, "z": 0.03},
                            "dimensions": {"width": 0.05, "height": 0.05, "depth": 0.05},
                            "attributes": {"color": "red"},
                            "execution": {"graspable": True},
                        },
                        {
                            "id": "surface-table",
                            "category": "桌子",
                            "pose": {"x": 0.30, "y": 0.00, "z": 0.03},
                            "attributes": {"support_surface": True},
                            "dimensions": {"width": 0.50, "height": 0.05, "depth": 0.50},
                            "execution": {"valid_destination": True},
                        },
                    ],
                },
            }
        )
        self.assert_task_schema(output)
        self.assertEqual(output["schema_version"], "task.v1")
        self.assertEqual(output["target_ids"], ["obj-red"])
        self.assertEqual(output["destination_id"], "surface-table")
        self.assertIn(output["status"], {"READY", "NEEDS_CLARIFICATION", "BLOCKED"})
        self.assertIsInstance(output["blocking_reasons"], list)

    def test_invalid_input_is_blocked(self):
        output = intent.run({"instruction": ""})
        self.assert_task_schema(output)
        self.assertEqual(output["status"], "BLOCKED")
        self.assertFalse(output["execution_allowed"])

    def test_health(self):
        result = intent.health()
        self.assertTrue(result["healthy"], result)


if __name__ == "__main__":
    unittest.main()
