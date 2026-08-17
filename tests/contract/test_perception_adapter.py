import json
import unittest
from pathlib import Path

from integration.adapters import perception
from integration.contract_validation import ContractValidationError, validate_contract


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "testdata" / "integration" / "a_perception_observation_v1.json"


def load_observation() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class PerceptionAdapterContractTests(unittest.TestCase):
    def test_run_returns_valid_perception_v1(self):
        output = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        self.assertEqual(validate_contract(output, "perception.v1"), [])

    def test_health_reports_mock_mode(self):
        self.assertEqual(
            perception.health(),
            {
                "status": "ok",
                "module": "perception",
                "version": "1.0.0",
                "backend": "mock",
            },
        )

    def test_run_accepts_formal_a_perception_observation(self):
        output = perception.run(load_observation())

        self.assertEqual(validate_contract(output, "perception.v1"), [])
        self.assertEqual(output["objects"][0]["id"], "obj_001")
        self.assertEqual(output["objects"][0]["category"], "cup")

    def test_formal_version_with_wrong_message_type_is_contract_error(self):
        observation = load_observation()
        observation["message_type"] = "scene_update"

        with self.assertRaisesRegex(ContractValidationError, "message_type"):
            perception.run(observation)


if __name__ == "__main__":
    unittest.main()
