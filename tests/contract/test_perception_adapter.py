import unittest

from integration.adapters import perception
from integration.contract_validation import validate_contract


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


if __name__ == "__main__":
    unittest.main()
