"""EvidenceValidator 单元测试：契约缺字段、版本不匹配、状态非 SUCCEEDED。"""

import json
import tempfile
import unittest
from pathlib import Path

from tools.orchestrate.validate import EvidenceValidator


def _write(tmp: Path, name: str, payload: dict) -> Path:
    path = tmp / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _execution(**overrides) -> dict:
    value = {
        "schema_version": "execution.v1",
        "task_id": "t1",
        "status": "SUCCEEDED",
        "steps": [{"step_id": "s1", "status": "SUCCEEDED"}],
    }
    value.update(overrides)
    return value


def _perception(**overrides) -> dict:
    value = {
        "schema_version": "perception.v1",
        "scene_id": "stacking_cubes",
        "objects": [
            {"id": "green_cube", "category": "block",
             "pose": {"x": 0.0, "y": 0.0, "z": 0.1}}
        ],
    }
    value.update(overrides)
    return value


class EvidenceValidatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.validator = EvidenceValidator()

    def tearDown(self):
        self.tmp.cleanup()

    def test_successful_execution_passes(self):
        path = _write(self.root, "execution.json", _execution())
        verdict = self.validator.validate_execution(path)
        self.assertTrue(verdict.passed)
        self.assertEqual(verdict.execution_status, "SUCCEEDED")

    def test_missing_required_field_contract_error(self):
        path = _write(self.root, "execution.json", {"task_id": "t1"})
        verdict = self.validator.validate_execution(path)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failure_class, "contract")

    def test_version_mismatch_contract_error(self):
        payload = _execution(schema_version="execution.v2")
        path = _write(self.root, "execution.json", payload)
        verdict = self.validator.validate_execution(path)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failure_class, "contract")

    def test_safe_stop_classified_safety_or_execution(self):
        payload = _execution(status="SAFE_STOP")
        path = _write(self.root, "execution.json", payload)
        verdict = self.validator.validate_execution(path)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failure_class, "safety_or_execution")
        self.assertEqual(verdict.execution_status, "SAFE_STOP")

    def test_unparseable_json_contract_error(self):
        path = self.root / "execution.json"
        path.write_text("{not json", encoding="utf-8")
        verdict = self.validator.validate_execution(path)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failure_class, "contract")

    def test_perception_contract_check(self):
        path = _write(self.root, "perception.json", _perception())
        verdict = self.validator.validate_perception(path)
        self.assertTrue(verdict.passed)

    def test_perception_invalid(self):
        path = _write(self.root, "perception.json", {"schema_version": "perception.v1"})
        verdict = self.validator.validate_perception(path)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.failure_class, "contract")


if __name__ == "__main__":
    unittest.main()