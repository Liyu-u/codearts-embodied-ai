import unittest

import numpy as np

from integration.adapters.isaac_camera_perception import run
from integration.contract_validation import validate_contract
from modules.perception.isaac_camera import IsaacCameraObservationProvider


class _FakeCameraSensor:
    def __init__(self):
        self.rgb = np.zeros((4, 4, 3), dtype=np.uint8)
        self.segmentation = np.zeros((4, 4), dtype=np.uint32)
        self.depth = np.ones((4, 4), dtype=np.float32)
        self.segmentation[1:3, 1:3] = 7
        self.rgb[1:3, 1:3] = [20, 220, 20]

    def get_data(self, name):
        if name == "rgb":
            return self.rgb, {}
        if name == "distance_to_image_plane":
            return self.depth, {}
        if name == "instance_id_segmentation":
            return self.segmentation, {"idToLabels": {"7": {"class": "green_cube"}}}
        raise KeyError(name)


def _provider():
    transform = np.eye(4, dtype=float)
    transform[:3, 3] = [0.5, 0.0, 1.0]
    return IsaacCameraObservationProvider(
        _FakeCameraSensor(),
        {"fx": 100.0, "fy": 100.0, "cx": 1.5, "cy": 1.5, "world_from_camera": transform},
        manifest=(
            {
                "object_id": "green_cube",
                "category": "绿色方块",
                "color": "green",
                "shape": "cube",
                "geometry_prior": {"width": 0.0515, "height": 0.0515, "depth": 0.0515},
                "segmentation_labels": ("green_cube",),
            },
        ),
        clock=lambda: 1_000_000_000,
    )


class IsaacCameraPerceptionTests(unittest.TestCase):
    def test_camera_frame_is_normalized_into_internal_perception(self):
        provider = _provider()
        observation = provider.observe()

        self.assertEqual(validate_contract(observation, "perception_observation.1.0.0"), [])
        self.assertEqual(observation["source"]["module"], "isaac_sim_camera")
        self.assertEqual(observation["objects"][0]["object_id"], "green_cube")
        self.assertAlmostEqual(observation["objects"][0]["pose"]["position"]["x"], 0.5)
        self.assertAlmostEqual(observation["objects"][0]["pose"]["position"]["y"], 0.0)
        self.assertAlmostEqual(observation["objects"][0]["pose"]["position"]["z"], 0.0)

        scene = run(_provider())
        self.assertEqual(validate_contract(scene, "perception.v1"), [])
        self.assertEqual(scene["objects"][0]["id"], "green_cube")
        self.assertEqual(scene["objects"][0]["category"], "绿色方块")
        self.assertEqual(scene["objects"][0]["attributes"]["color"], "green")
        self.assertEqual(scene["execution_context"]["backend"], "external_observation")

    def test_unknown_segmentation_label_is_not_invented(self):
        provider = _provider()
        provider.sensor.segmentation[:, :] = 99
        observation = provider.observe()
        self.assertEqual(observation["objects"], [])
        self.assertEqual(provider.last_metrics["visible_objects"], 0)

    def test_missing_camera_stream_is_rejected(self):
        provider = _provider()
        provider.sensor.depth = None
        with self.assertRaisesRegex(RuntimeError, "camera frame is incomplete"):
            provider.observe()

    def test_last_observation_and_unreliable_geometry_are_recorded(self):
        provider = _provider()
        provider.camera_model["fx"] = 1.0
        observation = provider.observe()
        self.assertEqual(provider.last_observation["objects"], observation["objects"])
        self.assertIsNot(provider.last_observation, observation)
        self.assertEqual(
            observation["objects"][0]["geometry"]["size"],
            {"width": 0.0515, "height": 0.0515, "depth": 0.0515},
        )
        debug = provider.last_metrics["objects"]["green_cube"]
        self.assertEqual(debug["geometry_source"], "geometry_prior")
        self.assertIn("width", debug["rejected_spans"])


if __name__ == "__main__":
    unittest.main()
