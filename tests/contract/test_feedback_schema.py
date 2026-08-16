"""契约测试：TraceCoder 适配器输出必须符合 contracts/v1 协议。

覆盖三条协议链：
  1. tracecoder.run() 输出 → feedback.schema.json
  2. patch 字段 → strategy.schema.json
  3. Mock 执行器输出 → execution.schema.json
任何字段漂移（改名/漏字段/类型不符）都会在这里暴露，
从而保证模块对外接口与 Schema 冻结版本一致。
"""

import unittest

from integration.adapters.tracecoder import run
from tests.helpers.schema_validate import load_schema, validate
from tests.helpers.tracecoder_fixtures import (
    DEMO_STRATEGY_V1,
    DEMO_TASK_V1,
    mock_executor_run,
)


class TestFeedbackContract(unittest.TestCase):
    """tracecoder.run() 输出符合 feedback.v1。"""

    def _input(self, execution=None):
        return {
            "task": DEMO_TASK_V1,
            "strategy": DEMO_STRATEGY_V1,
            "execution": execution or mock_executor_run(DEMO_STRATEGY_V1, DEMO_TASK_V1),
        }

    def test_feedback_validates_against_feedback_schema(self):
        output = run(self._input())
        errors = validate(output, load_schema("feedback.schema.json"))
        self.assertEqual(errors, [], "feedback 输出不符合 feedback.v1：\n" + "\n".join(errors))

    def test_feedback_required_fields_present(self):
        output = run(self._input())
        self.assertEqual(output["schema_version"], "feedback.v1")
        self.assertEqual(output["task_id"], "demo_place_cup")
        self.assertIsInstance(output["diagnosis"], str)
        self.assertIn("final_passed", output["diagnosis"])  # 序列化诊断可被解析

    def test_patch_validates_against_strategy_schema(self):
        output = run(self._input())
        patch = output["patch"]
        self.assertIsNotNone(patch)
        errors = validate(patch, load_schema("strategy.schema.json"))
        self.assertEqual(errors, [], "patch 不符合 strategy.v1：\n" + "\n".join(errors))

    def test_mock_executor_validates_against_execution_schema(self):
        execution = mock_executor_run(DEMO_STRATEGY_V1, DEMO_TASK_V1)
        errors = validate(execution, load_schema("execution.schema.json"))
        self.assertEqual(errors, [], "执行日志不符合 execution.v1：\n" + "\n".join(errors))

    def test_retryable_flag_semantics(self):
        # 修复后应通过 → retryable=False
        output = run(self._input())
        self.assertFalse(output["retryable"])


if __name__ == "__main__":
    unittest.main()
