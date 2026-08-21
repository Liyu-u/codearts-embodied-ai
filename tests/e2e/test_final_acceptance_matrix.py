"""Final offline acceptance matrix for the A/B/C/D delivery gate.

The normal and adversarial cases in ``test_closed_loop_acceptance`` exercise
the complete bus with the deterministic Mock backend.  This matrix adds the
two C-side fail-closed cases that cannot be represented by MockBackend:
an action timeout and a collision reported by the motion driver.  Both use
the same ``strategy.v1`` contract that B sends to the real Isaac backend.
"""

from __future__ import annotations

import unittest

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from modules.executor.isaac_backend import IsaacSimBackend
from modules.executor.mock_backend import MockBackend
from tests.unit.fake_driver import FakeDriver


def _strategy(task_id: str = "final-acceptance-task") -> dict:
    return {
        "schema_version": "strategy.v1",
        "task_id": task_id,
        "code": None,
        "steps": [
            {"step_id": "detect", "action": "detect_object", "arguments": {"object_id": "green_cube"}},
            {"step_id": "approach", "action": "move_to_object", "arguments": {"object_id": "green_cube"}},
            {"step_id": "grasp", "action": "grasp", "arguments": {"object_id": "green_cube"}},
            {
                "step_id": "transfer",
                "action": "move_to_target",
                "arguments": {"destination_id": "zone_unstack_target"},
            },
            {"step_id": "release", "action": "release", "arguments": {}},
        ],
    }


def _scene_and_objects() -> tuple[dict, dict]:
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    return scene, {item["id"]: item for item in scene["objects"]}


class FinalAcceptanceMatrixTests(unittest.TestCase):
    def test_different_object_stack_is_successful(self):
        """A second object/destination combination remains executable."""
        scene, _ = _scene_and_objects()
        backend = MockBackend.from_perception(scene)
        execution = ExecutorAdapter(backend).run(_strategy())
        self.assertEqual(execution["status"], "SUCCEEDED")
        self.assertEqual(backend.snapshot()["held_id"], None)
        self.assertEqual(
            backend.snapshot()["objects"]["green_cube"]["pose"],
            backend.snapshot()["objects"]["zone_unstack_target"]["pose"],
        )

    def test_motion_timeout_enters_safe_stop(self):
        """The real-backend contract turns a motion timeout into SAFE_STOP."""
        scene, objects = _scene_and_objects()
        driver = FakeDriver(objects=objects, move_timeout=True)
        backend = IsaacSimBackend.from_perception(scene, driver=driver)
        backend.connect()
        execution = ExecutorAdapter(backend).run(_strategy("timeout-task"))
        self.assertEqual(execution["status"], "SAFE_STOP")
        self.assertIn(
            "ACTION_TIMEOUT",
            {event["type"] for event in execution["safety_events"]},
        )
        self.assertTrue(backend.snapshot()["safe_stopped"])
        self.assertEqual(len(execution["steps"]), 6)  # detect, failed, three skipped, stop

    def test_collision_enters_safe_stop_without_running_later_steps(self):
        """A collision report is fail-closed and prevents grasp/transfer/release."""
        scene, objects = _scene_and_objects()
        driver = FakeDriver(objects=objects, collision_free=False)
        backend = IsaacSimBackend.from_perception(scene, driver=driver)
        backend.connect()
        execution = ExecutorAdapter(backend).run(_strategy("collision-task"))
        self.assertEqual(execution["status"], "SAFE_STOP")
        self.assertIn(
            "COLLISION_DETECTED",
            {event["type"] for event in execution["safety_events"]},
        )
        executed = {
            step["action"]
            for step in execution["steps"]
            if step["status"] == "SUCCESS"
        }
        self.assertNotIn("grasp", executed)
        self.assertNotIn("release", executed)
        self.assertTrue(backend.snapshot()["safe_stopped"])


if __name__ == "__main__":
    unittest.main()
