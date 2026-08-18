"""策略生成模块的 task.v1 -> strategy.v1 契约与冒烟测试。"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

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
        self.assertEqual(
            [step["action"] for step in output["steps"]],
            [
                "detect_object",
                "move_to_object",
                "grasp",
                "move_to_target",
                "release",
            ],
        )
        self.assertEqual(
            output["steps"][0],
            {
                "step_id": "task-001-detect",
                "action": "detect_object",
                "arguments": {"object_name": "obj-001"},
            },
        )
        self.assertEqual(
            output["steps"][1]["arguments"],
            {"object_id": "$task-001-detect.object_id"},
        )
        self.assertEqual(
            output["steps"][2]["on_failure"],
            {
                "max_attempts": 1,
                "steps": [
                    {
                        "step_id": "task-001-retry-grasp",
                        "action": "grasp",
                        "arguments": {
                            "object_id": "$task-001-detect.object_id"
                        },
                    }
                ],
                "on_exhausted": "stop",
            },
        )
        self.assertEqual(
            output["steps"][3]["arguments"],
            {"target": "zone-001"},
        )
        self.assertEqual(output["steps"][4]["arguments"], {})
        self.assertIsNone(output["code"])

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

    def test_unsupported_ready_actions_are_blocked(self):
        for action in (
            "push",
            "dynamic_grasp",
            "wait",
            "handover",
            "pour",
            "sort_by_color",
            "sort_by_size",
            "filter_by_attribute",
        ):
            with self.subTest(action=action):
                output = strategy.run({
                    "schema_version": "task.v1",
                    "task_id": f"unsupported-{action}",
                    "action": action,
                    "target_ids": ["obj-001"],
                    "destination_id": "zone-001",
                    "status": "READY",
                })
                self.assert_strategy_schema(output)
                self.assertFalse(output["success"])
                self.assertTrue(output["blocked"])
                self.assertEqual(output["steps"], [])
                self.assertIsNone(output["code"])
                self.assertIn(
                    f"UNSUPPORTED_ACTION:{action}",
                    output["blocking_reasons"],
                )

    def test_open_actions_lower_to_existing_primitive_source(self):
        cases = {
            "pick": ["detect_object", "move_to_object", "grasp"],
            "grasp": ["detect_object", "move_to_object", "grasp"],
            "transfer": [
                "detect_object", "move_to_object", "grasp",
                "move_to_target", "release",
            ],
            "fetch": [
                "detect_object", "move_to_object", "grasp",
                "move_to_target", "release",
            ],
            "stack": [
                "detect_object", "move_to_object", "grasp",
                "move_to_target", "release",
            ],
        }
        for action, expected_actions in cases.items():
            with self.subTest(action=action):
                output = strategy.run({
                    "schema_version": "task.v1",
                    "task_id": f"open-{action}",
                    "action": action,
                    "target_ids": ["obj-001"],
                    "destination_id": None if action in {"pick", "grasp"} else "zone-001",
                    "status": "READY",
                })
                self.assert_strategy_schema(output)
                self.assertTrue(output["success"])
                self.assertFalse(output["blocked"])
                self.assertEqual(
                    [step["action"] for step in output["steps"]],
                    expected_actions,
                )
                if action == "stack":
                    self.assertEqual(
                        output["steps"][3]["arguments"]["placement_mode"],
                        "stack_on",
                    )

    def test_pick_and_place_requires_one_target_id_and_destination_id(self):
        cases = (
            ([], "zone-001"),
            (["obj-001", "obj-002"], "zone-001"),
            (["obj-001"], None),
        )
        for target_ids, destination_id in cases:
            with self.subTest(
                target_ids=target_ids,
                destination_id=destination_id,
            ):
                output = strategy.run({
                    "schema_version": "task.v1",
                    "task_id": "unresolved-pick",
                    "action": "pick_and_place",
                    "target_ids": target_ids,
                    "target_object": "红色方块",
                    "destination_id": destination_id,
                    "status": "READY",
                })
                self.assert_strategy_schema(output)
                self.assertFalse(output["success"])
                self.assertTrue(output["blocked"])
                self.assertEqual(output["steps"], [])
                self.assertIsNone(output["code"])

    def test_required_mode_blocks_when_codearts_is_unavailable(self):
        task = load_task("strategy_normal_pick.json")
        failure = {
            "success": False,
            "strategy": None,
            "error": "CODEARTS_CLI_NOT_FOUND",
            "trace": {
                "provider": "huaweicloud-codearts-agent",
                "transport": "codearts-cli",
            },
        }
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "required"}):
            with patch(
                "integration.adapters.strategy.CodeArtsStrategyClient"
            ) as client_class:
                client_class.return_value.generate.return_value = failure
                output = strategy.run(task)

        self.assert_strategy_schema(output)
        self.assertTrue(output["blocked"])
        self.assertEqual(output["mode"], "codearts_blocked")
        self.assertIn("CODEARTS_CLI_NOT_FOUND", output["blocking_reasons"])

    def test_auto_mode_records_safe_local_fallback(self):
        task = load_task("strategy_normal_pick.json")
        failure = {
            "success": False,
            "strategy": None,
            "error": "CODEARTS_CLI_NOT_FOUND",
            "trace": {
                "provider": "huaweicloud-codearts-agent",
                "transport": "codearts-cli",
            },
        }
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "auto"}):
            with patch(
                "integration.adapters.strategy.CodeArtsStrategyClient"
            ) as client_class:
                client_class.return_value.generate.return_value = failure
                output = strategy.run(task)

        self.assert_strategy_schema(output)
        self.assertTrue(output["success"])
        self.assertEqual(output["mode"], "primitive_plan_fallback")
        self.assertEqual(output["provider_error"], "CODEARTS_CLI_NOT_FOUND")

    def test_adapter_returns_successful_codearts_strategy(self):
        task = load_task("strategy_normal_pick.json")
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "off"}):
            generated = strategy.run(task)
        generated["mode"] = "codearts_agent"
        generated["provenance"] = {
            "provider": "huaweicloud-codearts-agent",
            "transport": "codearts-cli",
        }
        success = {
            "success": True,
            "strategy": generated,
            "error": None,
            "trace": generated["provenance"],
        }

        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "required"}):
            with patch(
                "integration.adapters.strategy.CodeArtsStrategyClient"
            ) as client_class:
                client_class.return_value.generate.return_value = success
                output = strategy.run(task)

        self.assert_strategy_schema(output)
        self.assertEqual(output["mode"], "codearts_agent")
        self.assertEqual(
            output["provenance"]["provider"],
            "huaweicloud-codearts-agent",
        )


if __name__ == "__main__":
    unittest.main()
