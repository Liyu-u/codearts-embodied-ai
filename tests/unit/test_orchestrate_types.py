"""编排器类型模型单元测试：字段/类型/run_id 校验/退出码映射。"""

import unittest
from pathlib import Path

from tools.orchestrate.types import (
    OrchestrationConfig,
    StageReport,
    OrchestrationResult,
    exit_code_for,
    validate_config,
    validate_run_id,
)


class OrchestrationConfigTest(unittest.TestCase):
    def _config(self, **overrides) -> OrchestrationConfig:
        values = dict(
            instruction="把绿色方块放到桌子上",
            scene_id="stacking_cubes",
            server="10.0.0.1",
            port=5122,
            user="stu",
            remote_base="/data/stu/workspace",
            auth_mode="key",
            key_path=Path("id_rsa"),
        )
        values.update(overrides)
        return OrchestrationConfig(**values)

    def test_frozen_and_defaults(self):
        config = self._config()
        self.assertEqual(config.device, "cuda")
        self.assertEqual(config.ssh_timeout_s, 30)
        self.assertEqual(config.transport_retries, 2)
        with self.assertRaises(Exception):
            config.instruction = "changed"

    def test_validate_config_missing_remote_fields(self):
        config = self._config(server="", user="")
        errors = validate_config(config)
        self.assertIn("server 必填", errors)
        self.assertIn("user 必填", errors)

    def test_validate_config_key_mode_requires_key_path(self):
        config = self._config(key_path=None)
        errors = validate_config(config)
        self.assertTrue(any("key-path" in e for e in errors))

    def test_validate_config_empty_instruction(self):
        config = self._config(instruction="   ")
        errors = validate_config(config)
        self.assertTrue(any("instruction" in e for e in errors))

    def test_exit_code_mapping(self):
        self.assertEqual(exit_code_for(None), 0)
        self.assertEqual(exit_code_for("transport_auth"), 10)
        self.assertEqual(exit_code_for("contract"), 20)
        self.assertEqual(exit_code_for("safety_or_execution"), 30)
        self.assertEqual(exit_code_for("runner"), 40)

    def test_validate_run_id(self):
        validate_run_id("run-001_x.y")
        for bad in ["", "a b", "a/b", "a\nb", None]:
            with self.assertRaises(ValueError):
                validate_run_id(bad)


class StageReportTest(unittest.TestCase):
    def test_stage_report_fields(self):
        report = StageReport(
            stage="PREPARE", action="gen", duration_ms=10, outcome="SUCCEEDED"
        )
        self.assertEqual(report.stage, "PREPARE")
        self.assertIsNone(report.failure_class)

    def test_orchestration_result_serializable(self):
        result = OrchestrationResult(
            run_id="r1",
            status="FAILED",
            failure_class="contract",
            stages=[StageReport("DOWNLOAD", "fetch", 1, "FAILED", "contract")],
            artifact_paths={"execution": Path("execution.json")},
            retry_command=None,
        )
        self.assertEqual(result.status, "FAILED")


if __name__ == "__main__":
    unittest.main()