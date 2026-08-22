import unittest

from integration.adapters.isaac_perception import run
from integration.contract_validation import validate_contract
from modules.perception.isaac_ground_truth import IsaacGroundTruthProvider


class _FakeDriver:
    poses = {
        "red_cube": {"x": 0.65, "y": -0.20, "z": 0.0258},
        "green_cube": {"x": 0.50, "y": 0.0, "z": 0.0258},
        "zone_unstack_target": {"x": 0.45, "y": 0.10, "z": 0.02575},
    }

    def read_object_pose(self, object_id):
        return dict(self.poses[object_id])


class IsaacGroundTruthPerceptionTests(unittest.TestCase):
    def test_live_driver_poses_are_emitted_as_perception_v1(self):
        output = run(IsaacGroundTruthProvider(_FakeDriver()))
        self.assertEqual(validate_contract(output, "perception.v1"), [])
        self.assertEqual(output["execution_context"]["backend"], "isaac_ground_truth")
        self.assertEqual(output["execution_context"]["pose_source"], "live_usd_physx_driver")
        self.assertEqual(output["objects"][1]["pose"], {"x": 0.5, "y": 0.0, "z": 0.0258})
        self.assertTrue(output["spatial_messages"])

    def test_non_finite_driver_pose_is_rejected(self):
        driver = _FakeDriver()
        driver.poses["green_cube"] = {"x": float("nan"), "y": 0.0, "z": 0.0}
        with self.assertRaisesRegex(ValueError, "finite"):
            run(IsaacGroundTruthProvider(driver))


if __name__ == "__main__":
    unittest.main()
