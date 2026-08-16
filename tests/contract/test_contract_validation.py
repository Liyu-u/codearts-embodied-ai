import unittest

from integration.contract_validation import (
    ContractValidationError,
    assert_contract,
    load_contract,
    validate_contract,
)


class ContractValidationTests(unittest.TestCase):
    def test_loads_strategy_contract_by_schema_version(self):
        schema = load_contract("strategy.v1")
        self.assertEqual(schema["$id"], "robot-system/strategy/v1")

    def test_valid_strategy_has_no_errors(self):
        value = {
            "schema_version": "strategy.v1",
            "task_id": "task-001",
            "steps": [],
            "code": None,
        }
        self.assertEqual(validate_contract(value, "strategy.v1"), [])

    def test_missing_required_field_reports_json_path(self):
        value = {"schema_version": "strategy.v1", "steps": []}
        self.assertEqual(
            validate_contract(value, "strategy.v1"),
            ["$.task_id: required property is missing"],
        )

    def test_wrong_const_and_nested_type_are_reported(self):
        value = {
            "schema_version": "strategy.v2",
            "task_id": "task-001",
            "steps": [{"step_id": "s1", "action": "grasp", "arguments": []}],
        }
        errors = validate_contract(value, "strategy.v1")
        self.assertIn("$.schema_version: expected constant 'strategy.v1'", errors)
        self.assertIn("$.steps[0].arguments: expected type object", errors)

    def test_assert_contract_raises_one_stable_error(self):
        with self.assertRaisesRegex(
            ContractValidationError,
            "strategy.v1 validation failed",
        ):
            assert_contract({}, "strategy.v1")

    def test_unknown_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported schema version"):
            load_contract("unknown.v1")


if __name__ == "__main__":
    unittest.main()
