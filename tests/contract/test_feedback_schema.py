"""契约测试：TraceCoder 适配器输出必须符合 contracts/v1 协议。

覆盖三条协议链：
  1. tracecoder.run() 输出 → feedback.schema.json
  2. patch 字段 → strategy.schema.json
  3. Mock 执行器输出 → execution.schema.json
任何字段漂移（改名/漏字段/类型不符）都会在这里暴露，
从而保证模块对外接口与 Schema 冻结版本一致。
"""

import copy
import json
import unittest

from integration.adapters.tracecoder import (
    _strategy_v1_to_native,
    reset_experience_store,
    resolve_task_data,
    run,
)
from tests.helpers.schema_validate import load_schema, validate
from tests.helpers.tracecoder_fixtures import (
    DEMO_STRATEGY_V1,
    DEMO_TASK_V1,
    mock_executor_run,
)


class TestFeedbackContract(unittest.TestCase):
    """tracecoder.run() 输出符合 feedback.v1。"""

    def setUp(self):
        reset_experience_store()

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
        self.assertEqual(output["patch"]["task_id"], "demo_place_cup")

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
        # 初始执行失败，虽然仿真生成了修复 patch，但尚未重新执行 → 可重试
        output = run(self._input())
        diagnosis = json.loads(output["diagnosis"])
        self.assertTrue(diagnosis["simulation_final_passed"])
        self.assertFalse(diagnosis["final_passed"])
        self.assertTrue(output["retryable"])

    def test_safety_event_forces_non_retryable_null_patch(self):
        execution = mock_executor_run(DEMO_STRATEGY_V1, DEMO_TASK_V1)
        execution["status"] = "FAILED"
        execution["safety_events"] = [{
            "type": "COLLISION_DETECTED",
            "severity": "critical",
            "triggered": True,
        }]
        output = run(self._input(execution))
        self.assertFalse(output["retryable"])
        self.assertIsNone(output["patch"])
        self.assertIn("COLLISION_DETECTED", output["provenance"]["safety_events"])

    def test_non_idempotent_prefix_is_not_retried(self):
        execution = mock_executor_run(DEMO_STRATEGY_V1, DEMO_TASK_V1)
        execution["status"] = "FAILED"
        execution["steps"] = [
            {"step_id": "grasp_cup", "action": "grasp", "status": "SUCCESS"},
            {"step_id": "release_cup", "action": "release", "status": "FAILED"},
        ]
        output = run(self._input(execution))
        self.assertFalse(output["retryable"])
        self.assertIsNone(output["patch"])
        self.assertIn("NON_IDEMPOTENT_PREFIX", output["diagnosis"])

    def test_execution_task_id_must_match_task(self):
        execution = mock_executor_run(DEMO_STRATEGY_V1, DEMO_TASK_V1)
        execution["task_id"] = "another_task"
        with self.assertRaises(ValueError):
            run(self._input(execution))

    def test_execution_failure_drives_candidate_simulation(self):
        task = copy.deepcopy(DEMO_TASK_V1)
        task.pop("tracecoder")
        execution = {
            "schema_version": "execution.v1",
            "task_id": task["task_id"],
            "status": "FAILED",
            "steps": [{
                "step_id": "grasp_cup",
                "action": "grasp",
                "status": "FAILED",
                "reason": "OBJECT_NOT_REACHABLE_FROM_CURRENT_POSE",
            }],
        }
        task_data = resolve_task_data(
            task,
            _strategy_v1_to_native(DEMO_STRATEGY_V1),
            execution,
        )
        self.assertEqual(task_data["scenarios"][0]["failures"], {"grasp": 1})

    def test_perception_objects_are_preferred_when_task_has_no_tracecoder_fixture(self):
        task = copy.deepcopy(DEMO_TASK_V1)
        task.pop("tracecoder")
        perception = {
            "schema_version": "perception.v1",
            "scene_id": "scene-grounded",
            "objects": [
                {
                    "id": "red_cup",
                    "category": "cup",
                    "pose": {"x": 1.2, "y": -0.4, "z": 0.3},
                },
                {
                    "id": "left_bin",
                    "category": "bin",
                    "pose": {"x": 2.0, "y": 0.8, "z": 0.0},
                },
            ],
        }
        task_data = resolve_task_data(
            task,
            _strategy_v1_to_native(DEMO_STRATEGY_V1),
            {"status": "RUNNING"},
            perception=perception,
        )
        objects = {item["id"]: item for item in task_data["initial_state"]["objects"]}
        self.assertEqual(objects["red_cup"]["position"], [1.2, -0.4, 0.3])
        self.assertEqual(objects["left_bin"]["position"], [2.0, 0.8, 0.0])

    def test_successful_reexecution_is_the_final_pass_signal(self):
        first = run(self._input())
        patched_strategy = first["patch"]
        execution = mock_executor_run(patched_strategy, DEMO_TASK_V1)
        self.assertEqual(execution["status"], "SUCCEEDED")

        output = run({
            "task": DEMO_TASK_V1,
            "strategy": patched_strategy,
            "execution": execution,
        })
        diagnosis = json.loads(output["diagnosis"])
        self.assertTrue(diagnosis["execution_passed"])
        self.assertTrue(diagnosis["final_passed"])
        self.assertFalse(output["retryable"])


if __name__ == "__main__":
    unittest.main()
