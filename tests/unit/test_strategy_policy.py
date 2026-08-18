"""Shared B/C/D strategy safety policy tests."""

import unittest

from integration.strategy_policy import (
    DEFAULT_CAPABILITIES,
    validate_patch,
    validate_strategy,
)


def _strategy():
    return {
        "schema_version": "strategy.v1",
        "task_id": "task-policy-001",
        "steps": [
            {
                "step_id": "detect",
                "action": "detect_object",
                "arguments": {"object_id": "obj-red"},
            },
            {
                "step_id": "approach",
                "action": "move_to_object",
                "arguments": {"object_id": "$detect.object_id"},
            },
            {
                "step_id": "grasp",
                "action": "grasp",
                "arguments": {"object_id": "$detect.object_id"},
                "on_failure": {
                    "max_attempts": 3,
                    "steps": [
                        {
                            "step_id": "retry-grasp",
                            "action": "grasp",
                            "arguments": {"object_id": "$detect.object_id"},
                        }
                    ],
                    "on_exhausted": "stop",
                },
            },
            {
                "step_id": "move",
                "action": "move_to_target",
                "arguments": {"destination_id": "zone-001"},
            },
            {"step_id": "release", "action": "release", "arguments": {}},
        ],
        "code": None,
    }


class StrategyPolicyTests(unittest.TestCase):
    def test_valid_strategy_passes_with_capabilities(self):
        result = validate_strategy(
            _strategy(),
            task={"task_id": "task-policy-001"},
            capabilities=DEFAULT_CAPABILITIES,
        )
        self.assertTrue(result["passed"], result)

    def test_unknown_action_and_wrong_arguments_are_rejected(self):
        value = _strategy()
        value["steps"][0]["action"] = "run_python"
        value["steps"][1]["arguments"] = {"target": "obj-red"}
        result = validate_strategy(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("UNKNOWN_ACTION" in item for item in result["errors"]))
        self.assertTrue(any("object_id is required" in item for item in result["errors"]))

    def test_recovery_over_capability_limit_is_rejected(self):
        value = _strategy()
        value["steps"][2]["on_failure"]["max_attempts"] = 4
        result = validate_strategy(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("RECOVERY_LIMIT_EXCEEDED" in item for item in result["errors"]))

    def test_patch_must_change_execution_shape(self):
        result = validate_patch(
            _strategy(),
            current_strategy=_strategy(),
            task={"task_id": "task-policy-001"},
        )
        self.assertFalse(result["passed"])
        self.assertTrue(any(item.startswith("PATCH_UNCHANGED") for item in result["errors"]))

    def test_self_reference_is_not_available_before_step_completion(self):
        value = _strategy()
        value["steps"][1]["arguments"] = {"object_id": "$approach.object_id"}
        result = validate_strategy(value)
        self.assertFalse(result["passed"])
        self.assertTrue(any("UNRESOLVED_REFERENCE" in item for item in result["errors"]))


if __name__ == "__main__":
    unittest.main()
