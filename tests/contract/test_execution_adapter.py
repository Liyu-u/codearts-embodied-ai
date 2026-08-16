import json
import unittest
from pathlib import Path

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from integration.contract_validation import validate_contract
from modules.executor.mock_backend import MockBackend


ROOT = Path(__file__).resolve().parents[2]


class ExecutionAdapterContractTests(unittest.TestCase):
    def setUp(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        self.adapter = ExecutorAdapter(MockBackend.from_perception(scene))
        self.strategy = json.loads(
            (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
                encoding="utf-8"
            )
        )

    def test_execution_validates_against_execution_v1(self):
        output = self.adapter.run(self.strategy)
        self.assertEqual(validate_contract(output, "execution.v1"), [])
        self.assertEqual(output["task_id"], self.strategy["task_id"])

    def test_health_reports_bound_mock_backend(self):
        health = self.adapter.health()
        self.assertEqual(health["status"], "ok")
        self.assertEqual(health["backend"], "mock")
        self.assertEqual(
            health["supported_actions"],
            [
                "detect_object",
                "grasp",
                "move_to_object",
                "move_to_target",
                "release",
            ],
        )


if __name__ == "__main__":
    unittest.main()
