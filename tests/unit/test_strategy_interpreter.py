import json
import unittest
from pathlib import Path

from integration.adapters import perception
from modules.executor.mock_backend import MockBackend
from modules.executor.strategy_interpreter import StrategyInterpreter


ROOT = Path(__file__).resolve().parents[2]


def load_strategy():
    return json.loads(
        (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
            encoding="utf-8"
        )
    )


def make_interpreter(failures=None):
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    return StrategyInterpreter(MockBackend.from_perception(scene, failures=failures))


class StrategyInterpreterTests(unittest.TestCase):
    def test_successful_strategy_resolves_object_reference(self):
        output = make_interpreter().run(load_strategy())
        self.assertEqual(output["status"], "SUCCEEDED")
        self.assertEqual(output["task_id"], "stacking-demo-001")
        grasp = next(
            item for item in output["steps"] if item["step_id"] == "grasp_green"
        )
        self.assertEqual(grasp["arguments"]["object_id"], "green_cube")

    def test_unknown_action_is_rejected_before_backend_execution(self):
        strategy = load_strategy()
        strategy["steps"][0]["action"] = "run_shell"
        with self.assertRaisesRegex(ValueError, "UNKNOWN_ACTION:run_shell"):
            make_interpreter().run(strategy)

    def test_non_empty_code_is_rejected(self):
        strategy = load_strategy()
        strategy["code"] = "import os"
        with self.assertRaisesRegex(ValueError, "strategy.code must be empty"):
            make_interpreter().run(strategy)

    def test_unresolved_reference_fails_and_skips_remaining_steps(self):
        strategy = load_strategy()
        strategy["steps"][1]["arguments"]["object_id"] = "$missing.object_id"
        output = make_interpreter().run(strategy)
        self.assertEqual(output["status"], "FAILED")
        self.assertEqual(output["steps"][1]["status"], "FAILED")
        self.assertTrue(
            output["steps"][1]["reason"].startswith("UNRESOLVED_REFERENCE")
        )
        self.assertTrue(
            all(item["status"] == "SKIPPED" for item in output["steps"][2:])
        )

    def test_duplicate_step_id_is_rejected(self):
        strategy = load_strategy()
        strategy["steps"][1]["step_id"] = strategy["steps"][0]["step_id"]
        with self.assertRaisesRegex(ValueError, "DUPLICATE_STEP_ID"):
            make_interpreter().run(strategy)

    def test_legacy_detect_object_name_is_rejected(self):
        strategy = load_strategy()
        strategy["steps"][0]["arguments"] = {"object_name": "green_cube"}

        with self.assertRaisesRegex(
            ValueError, "INVALID_ARGUMENT:detect_object:object_id is required"
        ):
            make_interpreter().run(strategy)

    def test_legacy_move_target_field_is_rejected(self):
        strategy = load_strategy()
        strategy["steps"][3]["arguments"] = {"target": "zone_unstack_target"}

        with self.assertRaisesRegex(
            ValueError, "INVALID_ARGUMENT:move_to_target:destination_id is required"
        ):
            make_interpreter().run(strategy)


if __name__ == "__main__":
    unittest.main()
