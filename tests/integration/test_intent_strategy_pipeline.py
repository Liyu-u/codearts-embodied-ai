"""Real A -> B smoke test using the unified integration pipeline."""

import unittest

from integration.adapters import intent, strategy
from integration.pipeline import run_pipeline


class _MockExecutor:
    def run(self, strategy_v1):
        return {
            "schema_version": "execution.v1",
            "task_id": strategy_v1["task_id"],
            "status": "SUCCEEDED",
            "steps": [{"step_id": "mock-1", "status": "SUCCEEDED"}],
        }


class TestIntentStrategyPipeline(unittest.TestCase):
    def test_real_intent_output_can_enter_strategy_adapter(self):
        perception = {
            "schema_version": "perception.v1",
            "scene_id": "scene-intent-strategy-001",
            "objects": [
                {
                    "id": "obj-red",
                    "category": "红色方块",
                    "pose": {"x": 0.10, "y": 0.00, "z": 0.03},
                    "attributes": {"color": "red"},
                },
                {
                    "id": "surface-table",
                    "category": "桌子",
                    "pose": {"x": 0.30, "y": 0.00, "z": 0.03},
                },
            ],
        }
        out = run_pipeline(
            perception,
            "把红色方块放到桌子上",
            {
                "intent": intent,
                "strategy": strategy,
                "executor": _MockExecutor(),
            },
        )

        self.assertEqual(out["task"]["schema_version"], "task.v1")
        self.assertEqual(out["task"]["status"], "READY", out["task"])
        self.assertEqual(out["task"]["target_ids"], ["obj-red"])
        self.assertEqual(out["task"]["destination_id"], "surface-table")
        self.assertEqual(out["strategy"]["schema_version"], "strategy.v1")
        self.assertTrue(out["strategy"]["success"], out["strategy"])
        self.assertEqual(out["status"], "SUCCEEDED")

    def test_blocked_strategy_never_reaches_executor(self):
        task = {
            "schema_version": "task.v1",
            "task_id": "blocked-codearts",
            "action": "pick_and_place",
            "target_ids": ["obj-red"],
            "destination_id": "surface-table",
            "status": "READY",
        }
        blocked = {
            "schema_version": "strategy.v1",
            "task_id": "blocked-codearts",
            "steps": [],
            "code": None,
            "success": False,
            "blocked": True,
            "blocking_reasons": ["CODEARTS_CLI_NOT_FOUND"],
        }

        class StaticAdapter:
            def __init__(self, value):
                self.value = value

            def run(self, _):
                return self.value

        class RejectUnexpectedExecution:
            def run(self, _):
                raise AssertionError("blocked strategy reached executor")

        output = run_pipeline(
            {},
            "instruction",
            {
                "intent": StaticAdapter(task),
                "strategy": StaticAdapter(blocked),
                "executor": RejectUnexpectedExecution(),
            },
        )

        self.assertEqual(output["status"], "BLOCKED")
        self.assertEqual(output["strategy"], blocked)
        self.assertNotIn("execution", output)


if __name__ == "__main__":
    unittest.main()
