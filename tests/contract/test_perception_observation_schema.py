import json
import unittest
from copy import deepcopy
from pathlib import Path

from integration.contract_validation import validate_contract


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "testdata" / "integration" / "a_perception_observation_v1.json"
CONTRACT = "perception_observation.1.0.0"


def load_observation() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class PerceptionObservationContractTests(unittest.TestCase):
    def test_exact_a_observation_is_accepted(self):
        self.assertEqual(validate_contract(load_observation(), CONTRACT), [])

    def test_wrong_message_type_is_rejected(self):
        value = load_observation()
        value["message_type"] = "scene_update"
        self.assertTrue(any("message_type" in item for item in validate_contract(value, CONTRACT)))

    def test_missing_object_id_is_rejected(self):
        value = load_observation()
        del value["objects"][0]["object_id"]
        self.assertTrue(any("object_id" in item for item in validate_contract(value, CONTRACT)))

    def test_missing_quaternion_w_is_rejected(self):
        value = deepcopy(load_observation())
        del value["objects"][0]["pose"]["orientation"]["w"]
        self.assertTrue(any("orientation.w" in item for item in validate_contract(value, CONTRACT)))


if __name__ == "__main__":
    unittest.main()
