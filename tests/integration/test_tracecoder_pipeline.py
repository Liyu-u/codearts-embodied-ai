"""集成测试：TraceCoder 接入后跑通统一闭环 pipeline。

用 Mock 的 intent / strategy / executor 适配器 + 真实 tracecoder 适配器，
走 pipeline.run_pipeline 的『意图 → 策略 → 执行 → 反馈』完整链路：
  - 贪心基线无恢复 → 执行失败
  - TraceCoder 给失败抓取补 on_failure 恢复 → 生成待重执行的 patch
  - feedback.v1 输出可供上层回归测试与策略修正消费
"""

import json
import unittest

from integration.adapters.tracecoder import health, run
from integration.pipeline import run_pipeline
from tests.helpers.tracecoder_fixtures import (
    DEMO_STRATEGY_V1,
    DEMO_TASK_V1,
    mock_executor_run,
)


def _mock_adapters():
    """构造五段闭环所需的适配器集合（C 模块用 Mock 执行器替身）。

    每段都是带 run()/health() 的对象，与仓库『统一适配器』约定一致；
    正常联调时由各成员模块的真实适配器替换。
    """

    class MockAdapter:
        def __init__(self, fn):
            self._fn = fn

        def run(self, *args, **kwargs):
            return self._fn(*args, **kwargs)

        def health(self):
            return {"status": "ok"}

    def intent_run(input_json):
        # A 模块替身：直接返回语义任务（正常联调时由意图理解模块产出）。
        del input_json
        return DEMO_TASK_V1

    def strategy_run(task):
        # B 模块替身：返回贪心基线策略（正常联调时由 CodeArts 策略生成产出）。
        del task
        return DEMO_STRATEGY_V1

    return {
        "intent": MockAdapter(intent_run),
        "strategy": MockAdapter(strategy_run),
        "executor": MockAdapter(lambda strategy: mock_executor_run(strategy, DEMO_TASK_V1)),
        "tracecoder": MockAdapter(lambda input_json: run(input_json)),
    }


class TestTraceCoderPipeline(unittest.TestCase):
    def test_health_ok(self):
        h = health()
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["offline_capable"])
        self.assertTrue(h["engine_ready"])

    def test_full_pipeline_repairs_strategy(self):
        perception = {
            "schema_version": "perception.v1",
            "scene_id": "scene_demo",
            "objects": [],
        }
        out = run_pipeline(perception, "把红杯放进左托盘", _mock_adapters())

        # 闭环各段都有产出
        self.assertEqual(out["status"], "FAILED")          # 初始执行失败（无恢复）
        self.assertIn("task", out)
        self.assertIn("strategy", out)
        self.assertIn("execution", out)
        feedback = out["feedback"]
        self.assertIsNotNone(feedback)
        self.assertEqual(feedback["schema_version"], "feedback.v1")

        # 仿真修复应成功，但真实执行尚未重跑，不能冒充已通过
        diag = json.loads(feedback["diagnosis"])
        self.assertTrue(diag["simulation_final_passed"], diag.get("stopped_reason"))
        self.assertFalse(diag["final_passed"])
        self.assertTrue(feedback["retryable"])

        # patch 是修复后的完整策略：抓取步骤已带 on_failure 恢复
        patch_steps = feedback["patch"]["steps"]
        grasp = next(s for s in patch_steps if s["action"] == "grasp")
        self.assertIsNotNone(grasp.get("on_failure"))
        self.assertEqual(
            grasp["on_failure"]["on_exhausted"], "stop",
            "恢复耗尽后应安全停止，不能带病继续",
        )

    def test_pipeline_without_tracecoder_keeps_feedback_none(self):
        # 不注册 tracecoder 适配器时，pipeline 应优雅跳过（向后兼容）
        adapters = _mock_adapters()
        del adapters["tracecoder"]
        perception = {"schema_version": "perception.v1", "scene_id": "s", "objects": []}
        out = run_pipeline(perception, "指令", adapters)
        self.assertIsNone(out["feedback"])


if __name__ == "__main__":
    unittest.main()
