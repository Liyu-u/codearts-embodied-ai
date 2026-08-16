import json
import unittest
from pathlib import Path

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from integration.contract_validation import assert_contract
from integration.pipeline import run_pipeline
from modules.executor.mock_backend import MockBackend


ROOT = Path(__file__).resolve().parents[2]


class StaticAdapter:
    def __init__(self, value):
        self.value = value

    def run(self, input_json):
        return json.loads(json.dumps(self.value))

    def health(self):
        return {"status": "ok"}


class MockStackingEndToEndTests(unittest.TestCase):
    def test_green_cube_reaches_declared_safe_destination(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        strategy = json.loads(
            (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
                encoding="utf-8"
            )
        )
        task = {
            "schema_version": "task.v1",
            "task_id": "stacking-demo-001",
            "action": "pick_and_place",
            "target_ids": ["green_cube"],
            "destination_id": "zone_unstack_target",
            "constraints": [],
            "status": "READY",
            "blocking_reasons": [],
        }
        assert_contract(task, "task.v1")
        assert_contract(strategy, "strategy.v1")

        backend = MockBackend.from_perception(scene)
        output = run_pipeline(
            scene,
            "把绿色方块移到安全区",
            {
                "intent": StaticAdapter(task),
                "strategy": StaticAdapter(strategy),
                "executor": ExecutorAdapter(backend),
            },
        )

        self.assertEqual(output["status"], "SUCCEEDED")
        assert_contract(output["execution"], "execution.v1")
        state = backend.snapshot()
        self.assertEqual(
            state["objects"]["green_cube"]["pose"],
            state["objects"]["zone_unstack_target"]["pose"],
        )


if __name__ == "__main__":
    unittest.main()
