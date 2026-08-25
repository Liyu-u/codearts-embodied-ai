import unittest

import numpy as np

from modules.perception.isaac_camera_real import IsaacCameraRealObservationProvider


class _Sensor:
    def __init__(self):
        self.rgb = np.zeros((4, 4, 3), dtype=np.uint8)
        self.depth = np.ones((4, 4), dtype=np.float32)
        self.segmentation = np.zeros((4, 4), dtype=np.uint32)
        self.segmentation[1:3, 1:3] = 7

    def get_data(self, name):
        if name == "rgb":
            return self.rgb, {}
        if name == "distance_to_image_plane":
            return self.depth, {}
        if name == "instance_id_segmentation":
            return self.segmentation, {"idToLabels": {"7": {"class": "/World/green_cube"}}}
        raise KeyError(name)


class _SemanticFallbackSensor(_Sensor):
    def get_data(self, name):
        if name == "instance_id_segmentation":
            return self.segmentation, {}
        if name == "semantic_segmentation":
            return self.segmentation, {"idToLabels": {"7": {"class": "/World/green_cube"}}}
        return super().get_data(name)


class IsaacCameraRealTests(unittest.TestCase):
    def test_usd_path_label_and_standard_host_layout_are_supported(self):
        transform = np.eye(4, dtype=float)
        transform[:3, 3] = [0.5, 0.0, 1.0]
        provider = IsaacCameraRealObservationProvider(
            _Sensor(),
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
        )
        observation = provider.observe()
        self.assertEqual([item["object_id"] for item in observation["objects"]], ["green_cube"])

    def test_semantic_annotator_fallback_handles_late_instance_metadata(self):
        provider = IsaacCameraRealObservationProvider(
            _SemanticFallbackSensor(),
            {"fx": 100.0, "fy": 100.0, "cx": 1.5, "cy": 1.5, "world_from_camera": np.eye(4)},
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
        )
        observation = provider.observe()
        self.assertEqual([item["object_id"] for item in observation["objects"]], ["green_cube"])
        self.assertEqual(provider.last_metrics["segmentation_fallback"], "semantic_segmentation")


if __name__ == "__main__":
    unittest.main()
