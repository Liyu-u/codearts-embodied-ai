import json
import unittest
from pathlib import Path

from integration.adapters import perception
from modules.executor.mock_backend import MockBackend
from modules.executor.models import ExecutionLimits
from modules.executor.strategy_interpreter import StrategyInterpreter


ROOT = Path(__file__).resolve().parents[2]


def strategy():
    return json.loads(
        (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
            encoding="utf-8"
        )
    )


def interpreter(failures=None, limits=None):
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    backend = MockBackend.from_perception(scene, failures=failures)
    return StrategyInterpreter(backend, limits=limits)


class StrategyRecoveryTests(unittest.TestCase):
    def test_one_grasp_failure_is_recovered(self):
        output = interpreter(failures={"grasp": 1}).run(strategy())
        self.assertEqual(output["status"], "SUCCEEDED")
        recovery = [
            item for item in output["steps"] if item["phase"] == "recovery_1"
        ]
        self.assertEqual(len(recovery), 1)
        self.assertEqual(recovery[0]["action"], "grasp")
        self.assertEqual(recovery[0]["status"], "SUCCESS")

    def test_persistent_grasp_failure_safe_stops(self):
        output = interpreter(failures={"grasp": 10}).run(strategy())
        self.assertEqual(output["status"], "SAFE_STOP")
        self.assertEqual(output["steps"][-1]["phase"], "safe_stop")
        self.assertEqual(output["steps"][-1]["action"], "stop")
        self.assertEqual(output["safety_events"][0]["type"], "RECOVERY_EXHAUSTED")

    def test_invalid_recovery_limit_is_rejected_before_execution(self):
        value = strategy()
        value["steps"][2]["on_failure"]["max_attempts"] = 4
        with self.assertRaisesRegex(
            ValueError, "max_attempts must be between 1 and 3"
        ):
            interpreter().run(value)

    def test_action_call_limit_causes_safe_stop(self):
        limits = ExecutionLimits(max_action_calls=2)
        output = interpreter(limits=limits).run(strategy())
        self.assertEqual(output["status"], "SAFE_STOP")
        self.assertEqual(
            output["safety_events"][0]["type"], "ACTION_LIMIT_EXCEEDED"
        )

    def test_only_stop_is_allowed_on_exhausted(self):
        value = strategy()
        value["steps"][2]["on_failure"]["on_exhausted"] = "continue"
        with self.assertRaisesRegex(ValueError, "on_exhausted must be stop"):
            interpreter().run(value)


if __name__ == "__main__":
    unittest.main()
