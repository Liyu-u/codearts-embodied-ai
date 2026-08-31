import unittest

from integration.adapters import perception
from integration.adapters.executor import ExecutorAdapter
from modules.executor.isaac_backend import IsaacSimBackend
from modules.executor.isaac_driver import FrankaPickPlaceDriver
from modules.executor.safety import SafetyPolicy
from tools.real_isaac_experiment import SafetyInjectingDriver
from tools.run_ground_truth_executor_acceptance import build_ground_truth_manifest
from tools.run_ground_truth_executor_acceptance_v4 import _corrected_manifest
from tests.unit.fake_driver import FakeDriver


class _Driver:
    def __init__(self):
        self.estopped = False
        self.moves = []

    def move_to(self, pose, linear_speed, timeout_s):
        self.moves.append((pose, linear_speed, timeout_s))
        return {"status": "SUCCESS", "duration_ms": 1, "pose": pose}

    def collision_free(self, pose, radius, excluded_paths=()):
        return True

    def gripper_close(self, force, timeout_s):
        return {"status": "SUCCESS", "duration_ms": 1, "grasp_force_n": force}

    def gripper_open(self, width, timeout_s):
        return {"status": "SUCCESS", "duration_ms": 1}

    def e_stop(self):
        self.estopped = True


class RealIsaacSupplementContractTests(unittest.TestCase):
    def test_manifest_contains_selected_dynamic_and_decoy_objects(self):
        manifest = build_ground_truth_manifest(
            object_ids=("red_cube_left", "red_cube_right", "green_cube"),
            destination_ids=("zone_unstack_target",),
        )
        ids = [item["id"] for item in manifest]
        self.assertEqual(
            ids,
            ["red_cube_left", "red_cube_right", "green_cube", "zone_unstack_target"],
        )
        self.assertTrue(all(item["execution"].get("graspable") for item in manifest[:3]))
        self.assertTrue(manifest[-1]["execution"]["valid_destination"])

    def test_v4_manifest_wrapper_preserves_multi_object_arguments(self):
        manifest = _corrected_manifest(
            object_ids=("red_cube_left", "green_cube"),
            destination_ids=("zone_unstack_target",),
        )
        self.assertEqual(
            [item["id"] for item in manifest],
            ["red_cube_left", "green_cube", "zone_unstack_target"],
        )
        self.assertEqual(manifest[-1]["category"], "桌子")

    def test_franka_driver_keeps_dynamic_object_id(self):
        driver = FrankaPickPlaceDriver(object(), dynamic_object_id="red_cube")
        self.assertEqual(driver.dynamic_object_id, "red_cube")

    def test_transport_feedback_has_room_for_a_second_object_layout(self):
        correction = FrankaPickPlaceDriver.bounded_transport_correction((0.04, 0.06))
        self.assertGreaterEqual(correction[0], 0.028)
        self.assertGreaterEqual(correction[1], 0.042)

    def test_transport_phase_is_bounded_for_dynamic_grasp_stability(self):
        """The long carry phase must not expose a grasp to unnecessary ticks."""
        self.assertLessEqual(FrankaPickPlaceDriver.CONTROLLER_EVENTS_DT[4], 60)
        self.assertEqual(len(FrankaPickPlaceDriver.CONTROLLER_EVENTS_DT), 7)

    def test_safety_injector_reports_each_physical_guard(self):
        for mode, expected in (
            ("speed_exceed", "SPEED_LIMIT_EXCEEDED"),
            ("collision", "COLLISION_DETECTED"),
            ("timeout", "ACTION_TIMEOUT"),
            ("force_exceed", "GRIPPER_FORCE_LIMIT_EXCEEDED"),
            ("e_stop", "E_STOP_TRIGGERED"),
        ):
            raw = _Driver()
            injected = SafetyInjectingDriver(raw, {mode: 1})
            if mode == "force_exceed":
                result = injected.gripper_close(10.0, 1.0)
            elif mode == "collision":
                result = {
                    "reason": "COLLISION_DETECTED"
                    if not injected.collision_free(
                        {"x": 0.4, "y": 0.0, "z": 0.2}, 0.05
                    )
                    else "NO_INJECTION"
                }
            elif mode == "e_stop":
                result = injected.move_to({"x": 0.4, "y": 0.0, "z": 0.2}, 0.05, 1.0)
            else:
                result = injected.move_to({"x": 0.4, "y": 0.0, "z": 0.2}, 0.05, 1.0)
            self.assertEqual(result["reason"], expected)
            self.assertTrue(injected.injection_log[-1]["applied"])
            if mode == "e_stop":
                self.assertTrue(raw.estopped)

    def test_injected_force_rejection_is_fail_closed(self):
        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        objects = {item["id"]: item for item in scene["objects"]}
        driver = SafetyInjectingDriver(FakeDriver(objects=objects), {"force_exceed": 1})
        backend = IsaacSimBackend.from_perception(scene, safety=SafetyPolicy(), driver=driver)
        execution = ExecutorAdapter(backend).run({
            "schema_version": "strategy.v1",
            "task_id": "force-safety",
            "code": None,
            "steps": [
                {"step_id": "detect", "action": "detect_object", "arguments": {"object_id": "green_cube"}},
                {"step_id": "approach", "action": "move_to_object", "arguments": {"object_id": "green_cube"}},
                {"step_id": "grasp", "action": "grasp", "arguments": {"object_id": "green_cube"}},
            ],
        })
        self.assertEqual(execution["status"], "SAFE_STOP")
        self.assertTrue(backend.snapshot()["safe_stopped"])
        self.assertIn(
            "GRIPPER_FORCE_LIMIT_EXCEEDED",
            {event["type"] for event in execution["safety_events"]},
        )


if __name__ == "__main__":
    unittest.main()
