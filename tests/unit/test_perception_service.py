import unittest

from modules.perception.service import observe_scene


class PerceptionServiceTests(unittest.TestCase):
    def test_stacking_scene_uses_stable_ids_and_world_frame(self):
        result = observe_scene({"scene_id": "stacking_cubes", "backend": "mock"})
        self.assertEqual(result["schema_version"], "perception.v1")
        self.assertEqual(result["scene_id"], "stacking_cubes")
        self.assertEqual(result["coordinate_frame"], "world")
        by_id = {item["id"]: item for item in result["objects"]}
        self.assertEqual(by_id["green_cube"]["attributes"]["color"], "green")
        self.assertTrue(by_id["green_cube"]["execution"]["graspable"])
        self.assertTrue(
            by_id["zone_unstack_target"]["execution"]["valid_destination"]
        )

    def test_unknown_scene_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported mock scene"):
            observe_scene({"scene_id": "missing", "backend": "mock"})

    def test_non_mock_backend_is_rejected_in_phase_one(self):
        with self.assertRaisesRegex(ValueError, "backend must be mock"):
            observe_scene({"scene_id": "stacking_cubes", "backend": "isaac"})


if __name__ == "__main__":
    unittest.main()
