import json
import unittest
from pathlib import Path

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
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


class MockIsaacPipelineTests(unittest.TestCase):
    def test_pipeline_reaches_executor_without_changing_public_signature(self):
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
        adapters = {
            "intent": StaticAdapter(task),
            "strategy": StaticAdapter(strategy),
            "executor": ExecutorAdapter(MockBackend.from_perception(scene)),
        }
        output = run_pipeline(scene, "把绿色方块移到安全区", adapters)
        self.assertEqual(output["status"], "SUCCEEDED")
        self.assertEqual(output["execution"]["task_id"], "stacking-demo-001")
        self.assertIsNone(output["feedback"])


if __name__ == "__main__":
    unittest.main()
