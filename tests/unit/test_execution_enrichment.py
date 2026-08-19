import json
import unittest
from pathlib import Path

from integration.adapters import perception
from modules.executor.isaac_backend import IsaacSimBackend
from modules.executor.safety import SafetyPolicy, WorkspaceLimits
from modules.executor.strategy_interpreter import StrategyInterpreter
from tests.unit.fake_driver import FakeDriver

ROOT = Path(__file__).resolve().parents[2]


def strategy():
    return json.loads(
        (ROOT / "testdata" / "daily" / "stacking_strategy.json").read_text(
            encoding="utf-8"
        )
    )


def make_interpreter(safety=None, **driver_kwargs):
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    objects = {item["id"]: item for item in scene["objects"]}
    driver = FakeDriver(objects=objects, **driver_kwargs)
    backend = IsaacSimBackend.from_perception(scene, safety=safety, driver=driver)
    return StrategyInterpreter(backend)


def tight_z_safety(z_max=0.1):
    workspace = WorkspaceLimits(
        x_min=-0.5, x_max=0.5, y_min=-0.5, y_max=0.5, z_min=0.0, z_max=z_max
    )
    return SafetyPolicy(workspace=workspace)


class ExecutionEnrichmentTests(unittest.TestCase):
    def test_successful_run_records_motion_evidence_in_steps(self):
        output = make_interpreter().run(strategy())
        self.assertEqual(output["status"], "SUCCEEDED")
        approach = next(
            item for item in output["steps"] if item["step_id"] == "approach_green"
        )
        self.assertIn("pose", approach)

    def test_backend_safety_event_flows_to_execution_and_safe_stops(self):
        output = make_interpreter(safety=tight_z_safety(0.1)).run(strategy())
        self.assertEqual(output["status"], "SAFE_STOP")
        # 后端（工作空间越界）事件按时间顺序排在最前。
        self.assertEqual(output["safety_events"][0]["type"], "WORKSPACE_VIOLATION")
        # 失败步骤被记录，后续主步骤被跳过。
        self.assertTrue(any(item["step_id"] == "approach_green"
                            and item["status"] == "FAILED"
                            for item in output["steps"]))
        self.assertTrue(all(item["status"] == "SKIPPED"
                            for item in output["steps"]
                            if item["step_id"] in ("grasp_green", "move_target",
                                                   "release_green")))


if __name__ == "__main__":
    unittest.main()
