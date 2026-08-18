import json
import unittest
from uuid import UUID

from integration.adapters import intent, perception, strategy, tracecoder
from integration.adapters.executor import ExecutorAdapter
from integration.pipeline import run_pipeline
from modules.executor.mock_backend import MockBackend


class AbcdPickAndPlaceEndToEndTests(unittest.TestCase):
    def test_real_adapters_complete_ready_pick_and_place(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        backend = MockBackend.from_perception(scene)

        output = run_pipeline(
            scene,
            "把绿色方块放到桌子上",
            {
                "intent": intent,
                "strategy": strategy,
                "executor": ExecutorAdapter(backend),
                "tracecoder": tracecoder,
            },
        )

        self.assertEqual(output["task"]["status"], "READY", output["task"])
        self.assertEqual(output["task"]["target_ids"], ["green_cube"])
        self.assertEqual(output["task"]["destination_id"], "zone_unstack_target")
        self.assertEqual(
            [step["action"] for step in output["strategy"]["steps"]],
            [
                "detect_object",
                "move_to_object",
                "grasp",
                "move_to_target",
                "release",
            ],
        )
        self.assertIsNone(output["strategy"]["code"])
        self.assertEqual(output["execution"]["status"], "SUCCEEDED")
        self.assertEqual(output["feedback"]["schema_version"], "feedback.v1")
        UUID(output["task"]["task_id"])
        self.assertEqual(output["feedback"]["task_id"], output["task"]["task_id"])
        diagnosis = json.loads(output["feedback"]["diagnosis"])
        self.assertTrue(diagnosis["final_passed"])
        self.assertTrue(diagnosis["execution_passed"])

        state = backend.snapshot()
        self.assertEqual(
            state["objects"]["green_cube"]["pose"],
            state["objects"]["zone_unstack_target"]["pose"],
        )


if __name__ == "__main__":
    unittest.main()
