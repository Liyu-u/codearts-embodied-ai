import unittest

from integration.adapters import perception
from modules.executor.mock_backend import MockBackend


class MockExecutorBackendTests(unittest.TestCase):
    def setUp(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        self.backend = MockBackend.from_perception(scene)

    def test_complete_pick_and_place_updates_object_position(self):
        detected = self.backend.execute("detect_object", {"object_id": "green_cube"})
        self.assertEqual(detected["object_id"], "green_cube")
        self.assertEqual(
            self.backend.execute(
                "move_to_object", {"object_id": "green_cube"}
            )["status"],
            "SUCCESS",
        )
        self.assertEqual(
            self.backend.execute("grasp", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            self.backend.execute(
                "move_to_target", {"destination_id": "zone_unstack_target"}
            )["status"],
            "SUCCESS",
        )
        self.assertEqual(self.backend.execute("release", {})["status"], "SUCCESS")
        state = self.backend.snapshot()
        self.assertEqual(
            state["objects"]["green_cube"]["pose"],
            state["objects"]["zone_unstack_target"]["pose"],
        )

    def test_grasp_without_approach_fails(self):
        result = self.backend.execute("grasp", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "OBJECT_NOT_APPROACHED")

    def test_target_must_be_declared_safe_destination(self):
        result = self.backend.execute(
            "move_to_target", {"destination_id": "red_cube"}
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "INVALID_DESTINATION:red_cube")

    def test_stack_placement_uses_explicit_mode_and_dimensions(self):
        self.backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(
            self.backend.execute("grasp", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            self.backend.execute(
                "move_to_target",
                {"target": "red_cube", "placement_mode": "stack_on"},
            )["status"],
            "SUCCESS",
        )
        self.assertEqual(self.backend.execute("release", {})["status"], "SUCCESS")
        state = self.backend.snapshot()
        self.assertEqual(
            state["objects"]["green_cube"]["pose"],
            {"x": 0.25, "y": 0.0, "z": 0.08},
        )

    def test_failure_injection_is_counted(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        backend = MockBackend.from_perception(scene, failures={"grasp": 1})
        backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(
            backend.execute("grasp", {"object_id": "green_cube"})["status"],
            "FAILED",
        )
        self.assertEqual(
            backend.execute("grasp", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )

    def test_safe_stop_prevents_further_actions(self):
        self.backend.safe_stop("test")
        result = self.backend.execute("detect_object", {"object_id": "green_cube"})
        self.assertEqual(result["reason"], "BACKEND_SAFE_STOPPED")


if __name__ == "__main__":
    unittest.main()
