import json
import math
import unittest
from copy import deepcopy
from pathlib import Path

from modules.perception.observation_normalizer import normalize_observation


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "testdata" / "integration" / "a_perception_observation_v1.json"


def load_observation() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class PerceptionObservationNormalizerTests(unittest.TestCase):
    def test_normalizes_a_observation_without_inventing_execution_capabilities(self):
        result = normalize_observation(load_observation())

        self.assertEqual(result["schema_version"], "perception.v1")
        self.assertEqual(result["scene_id"], "table_scene_001")
        self.assertEqual(result["coordinate_frame"], "robot_base")

        obj = result["objects"][0]
        self.assertEqual(obj["id"], "obj_001")
        self.assertEqual(obj["category"], "cup")
        self.assertEqual(obj["pose"], {"x": 0.35, "y": 0.12, "z": 0.75})
        self.assertEqual(
            obj["orientation"], {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
        )
        self.assertEqual(
            obj["dimensions"], {"width": 0.06, "height": 0.12, "depth": 0.06}
        )
        self.assertEqual(obj["attributes"]["color"], "transparent")
        self.assertEqual(obj["attributes"]["shape"], "cylindrical")
        self.assertEqual(obj["attributes"]["texture"], "smooth")
        self.assertNotIn("execution", obj)

        context = result["execution_context"]
        self.assertEqual(context["backend"], "external_observation")
        self.assertEqual(context["observation_id"], "obs_1723456789123_0001")
        self.assertEqual(context["orientation_order"], "xyzw")
        self.assertTrue(context["simulation_metadata"]["evaluation_only"])

    def test_selects_highest_scoring_candidate_instead_of_first_candidate(self):
        value = load_observation()
        value["objects"][0]["category_candidates"] = [
            {"name": "bottle", "score": 0.4},
            {"name": "cup", "score": 0.9},
        ]

        result = normalize_observation(value)

        self.assertEqual(result["objects"][0]["category"], "cup")

    def test_rejects_duplicate_object_ids(self):
        value = load_observation()
        value["objects"].append(deepcopy(value["objects"][0]))

        with self.assertRaisesRegex(ValueError, "duplicate object_id: obj_001"):
            normalize_observation(value)

    def test_rejects_non_finite_numeric_values(self):
        value = load_observation()
        value["objects"][0]["pose"]["position"]["x"] = math.nan

        with self.assertRaisesRegex(ValueError, "finite"):
            normalize_observation(value)


if __name__ == "__main__":
    unittest.main()
