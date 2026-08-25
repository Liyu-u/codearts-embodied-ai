import unittest

from integration.adapters import perception
from modules.executor.isaac_backend import IsaacSimBackend
from modules.executor.safety import MotionLimits, SafetyPolicy, WorkspaceLimits
from tests.unit.fake_driver import FakeDriver


def scene_objects():
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    return scene, {item["id"]: item for item in scene["objects"]}


def make_backend(safety=None, **driver_kwargs):
    scene, objects = scene_objects()
    driver = FakeDriver(objects=objects, **driver_kwargs)
    backend = IsaacSimBackend.from_perception(scene, safety=safety, driver=driver)
    return backend, driver


def tight_z_safety(z_max=0.1):
    workspace = WorkspaceLimits(
        x_min=-0.5, x_max=0.5, y_min=-0.5, y_max=0.5, z_min=0.0, z_max=z_max
    )
    return SafetyPolicy(workspace=workspace)


def zero_speed_safety():
    return SafetyPolicy(motion=MotionLimits(max_linear_velocity_m_s=0.0))


class ReleaseVerifyingDriver(FakeDriver):
    def __init__(self, *args, release_verified=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.release_verified = release_verified

    def verify_release(self, object_id, target_pose, tolerance_m=0.06):
        pose = target_pose if self.release_verified else self.objects[object_id]["pose"]
        return {
            "verified": self.release_verified,
            "object_pose": pose,
            "reason": "" if self.release_verified else "OBJECT_NOT_AT_TARGET",
        }


class TargetSettingDriver(ReleaseVerifyingDriver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_poses = []

    def set_target_pose(self, target_pose):
        self.target_poses.append(target_pose)


class IsaacSimBackendTests(unittest.TestCase):
    def test_complete_pick_and_place_updates_object_position(self):
        backend, _ = make_backend()
        self.assertEqual(
            backend.execute("detect_object", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            backend.execute("move_to_object", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            backend.execute("grasp", {"object_id": "green_cube"})["status"],
            "SUCCESS",
        )
        self.assertEqual(
            backend.execute(
                "move_to_target", {"destination_id": "zone_unstack_target"}
            )["status"],
            "SUCCESS",
        )
        self.assertEqual(backend.execute("release", {})["status"], "SUCCESS")
        state = backend.snapshot()
        self.assertEqual(
            state["objects"]["green_cube"]["pose"],
            state["objects"]["zone_unstack_target"]["pose"],
        )

    def test_grasp_without_approach_fails(self):
        backend, _ = make_backend()
        result = backend.execute("grasp", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "OBJECT_NOT_APPROACHED")

    def test_workspace_violation_is_fail_closed(self):
        backend, _ = make_backend(safety=tight_z_safety(0.1))
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "WORKSPACE_VIOLATION")
        self.assertEqual(result["safety_events"][0]["type"], "WORKSPACE_VIOLATION")
        # 失败安全停止：安全门触发后后端进入停止状态。
        self.assertTrue(backend.snapshot()["safe_stopped"])
        self.assertEqual(
            backend.execute("detect_object", {"object_id": "green_cube"})["reason"],
            "BACKEND_SAFE_STOPPED",
        )

    def test_collision_detected_is_fail_closed(self):
        backend, _ = make_backend(collision_free=False)
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "COLLISION_DETECTED")
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_collision_query_error_is_fail_closed_not_fail_open(self):
        # 关键回归：查询异常绝不能“假设安全”。
        backend, _ = make_backend(collision_error=True)
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["reason"].startswith("COLLISION_CHECK_ERROR"))
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_collision_reported_after_motion_is_fail_closed(self):
        backend, _ = make_backend(move_collisions=["/World/obstacle"])
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "COLLISION_DETECTED")
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_zero_speed_limit_rejects_motion(self):
        backend, _ = make_backend(safety=zero_speed_safety())
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "SPEED_LIMIT_EXCEEDED")

    def test_move_timeout_is_fail_closed(self):
        backend, _ = make_backend(move_timeout=True)
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "ACTION_TIMEOUT")
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_weak_grasp_is_rejected(self):
        backend, _ = make_backend(grasp_force_n=0.1)
        backend.execute("move_to_object", {"object_id": "green_cube"})
        result = backend.execute("grasp", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "GRASP_WEAK")

    def test_grasp_requires_lift_verification(self):
        backend, _ = make_backend(grasp_verified=False)
        backend.execute("move_to_object", {"object_id": "green_cube"})
        result = backend.execute("grasp", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "GRASP_UNVERIFIED")
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_gripper_timeout_is_fail_closed(self):
        backend, _ = make_backend(gripper_close_timeout=True)
        backend.execute("move_to_object", {"object_id": "green_cube"})
        result = backend.execute("grasp", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "ACTION_TIMEOUT")
        self.assertEqual(result["safety_events"][0]["type"], "ACTION_TIMEOUT")
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_real_driver_release_is_verified_before_success(self):
        scene, objects = scene_objects()
        driver = ReleaseVerifyingDriver(objects=objects, release_verified=True)
        backend = IsaacSimBackend.from_perception(scene, safety=SafetyPolicy(), driver=driver)
        for action, args in [
            ("detect_object", {"object_id": "green_cube"}),
            ("move_to_object", {"object_id": "green_cube"}),
            ("grasp", {"object_id": "green_cube"}),
            ("move_to_target", {"destination_id": "zone_unstack_target"}),
        ]:
            self.assertEqual(backend.execute(action, args)["status"], "SUCCESS")
        self.assertEqual(backend.execute("release", {})["status"], "SUCCESS")

    def test_real_driver_receives_measured_target_before_motion(self):
        scene, objects = scene_objects()
        driver = TargetSettingDriver(objects=objects, release_verified=True)
        backend = IsaacSimBackend.from_perception(scene, safety=SafetyPolicy(), driver=driver)
        for action, args in [
            ("detect_object", {"object_id": "green_cube"}),
            ("move_to_object", {"object_id": "green_cube"}),
            ("grasp", {"object_id": "green_cube"}),
        ]:
            self.assertEqual(backend.execute(action, args)["status"], "SUCCESS")
        result = backend.execute(
            "move_to_target", {"destination_id": "zone_unstack_target"}
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(len(driver.target_poses), 1)
        self.assertEqual(
            driver.target_poses[0], objects["zone_unstack_target"]["pose"]
        )

    def test_real_driver_release_mismatch_is_fail_closed(self):
        scene, objects = scene_objects()
        driver = ReleaseVerifyingDriver(objects=objects, release_verified=False)
        backend = IsaacSimBackend.from_perception(scene, safety=SafetyPolicy(), driver=driver)
        for action, args in [
            ("detect_object", {"object_id": "green_cube"}),
            ("move_to_object", {"object_id": "green_cube"}),
            ("grasp", {"object_id": "green_cube"}),
            ("move_to_target", {"destination_id": "zone_unstack_target"}),
        ]:
            self.assertEqual(backend.execute(action, args)["status"], "SUCCESS")
        result = backend.execute("release", {})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "RELEASE_UNVERIFIED")
        self.assertTrue(backend.snapshot()["safe_stopped"])

    def test_safe_stop_prevents_further_actions(self):
        backend, _ = make_backend()
        backend.safe_stop("test")
        result = backend.execute("detect_object", {"object_id": "green_cube"})
        self.assertEqual(result["reason"], "BACKEND_SAFE_STOPPED")

    def test_emergency_stop_engages_e_stop(self):
        backend, driver = make_backend()
        result = backend.emergency_stop()
        self.assertEqual(result["status"], "SAFE_STOP")
        self.assertEqual(result["reason"], "E_STOP_TRIGGERED")
        self.assertTrue(driver.stopped)

    def test_stack_on_places_held_object_above_base(self):
        backend, _ = make_backend()
        for action, args in [
            ("detect_object", {"object_id": "green_cube"}),
            ("move_to_object", {"object_id": "green_cube"}),
            ("grasp", {"object_id": "green_cube"}),
        ]:
            self.assertEqual(backend.execute(action, args)["status"], "SUCCESS")
        result = backend.execute(
            "move_to_target",
            {"destination_id": "red_cube", "placement_mode": "stack_on"},
        )
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result.get("placement_mode"), "stack_on")
        self.assertEqual(backend.execute("release", {})["status"], "SUCCESS")
        state = backend.snapshot()
        green_pose = state["objects"]["green_cube"]["pose"]
        red_pose = state["objects"]["red_cube"]["pose"]
        # 堆叠：绿色方块中心在红色底座正上方（底座半高 0.02 + 方块半高 0.02 → z=0.08）。
        self.assertEqual(green_pose["x"], red_pose["x"])
        self.assertEqual(green_pose["y"], red_pose["y"])
        self.assertAlmostEqual(green_pose["z"], 0.08, places=6)
        self.assertGreater(green_pose["z"], red_pose["z"])

    def test_stack_on_rejects_non_stackable_destination(self):
        backend, _ = make_backend()
        for action, args in [
            ("detect_object", {"object_id": "green_cube"}),
            ("move_to_object", {"object_id": "green_cube"}),
            ("grasp", {"object_id": "green_cube"}),
        ]:
            self.assertEqual(backend.execute(action, args)["status"], "SUCCESS")
        result = backend.execute(
            "move_to_target",
            {"destination_id": "zone_unstack_target", "placement_mode": "stack_on"},
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(
            result["reason"], "INVALID_STACK_DESTINATION:zone_unstack_target"
        )

    def test_direct_rejects_stackable_destination(self):
        # direct 放置到可堆叠底座应被拒绝（与 MockBackend 语义一致）。
        backend, _ = make_backend()
        for action, args in [
            ("detect_object", {"object_id": "green_cube"}),
            ("move_to_object", {"object_id": "green_cube"}),
            ("grasp", {"object_id": "green_cube"}),
        ]:
            self.assertEqual(backend.execute(action, args)["status"], "SUCCESS")
        result = backend.execute(
            "move_to_target", {"destination_id": "red_cube"}
        )
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "INVALID_DESTINATION:red_cube")


if __name__ == "__main__":
    unittest.main()
