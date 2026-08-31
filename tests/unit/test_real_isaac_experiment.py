import json
import tempfile
import unittest
from pathlib import Path

from tools.real_isaac_experiment import (
    FailureInjectingDriver,
    build_variant_strategy,
    load_experiment_config,
    select_execution_strategy,
    wait_for_strategy_file,
    select_case,
    variant_runtime,
)
from tools.live_intelligent_e2e import document_digest


class RealIsaacExperimentConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "schema_version": "real-isaac-experiment.v1",
            "seed": 20260830,
            "global": {
                "scene_id": "stacking_cubes",
                "device": "cuda",
                "gpu_index": "1",
                "action_timeout_s": 300,
                "prewarm_forward_steps": 1,
            },
            "tasks": [
                {
                    "id": "grasp-recover",
                    "category": "recoverable_failure",
                    "instruction": "把绿色方块放到桌子上",
                    "object_id": "green_cube",
                    "destination_id": "zone_unstack_target",
                    "failure_injection": {"grasp": 1},
                    "expected_status": "SUCCEEDED",
                },
                {
                    "id": "grasp-stop",
                    "category": "safe_stop",
                    "instruction": "把绿色方块放到桌子上",
                    "object_id": "green_cube",
                    "destination_id": "zone_unstack_target",
                    "failure_injection": {"grasp": 2},
                    "expected_status": "SAFE_STOP",
                },
            ],
        }

    def test_load_config_keeps_fixed_seed_and_task_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(self.config), encoding="utf-8")
            loaded = load_experiment_config(path)

        self.assertEqual(loaded["seed"], 20260830)
        self.assertEqual(loaded["global"]["gpu_index"], "1")
        self.assertEqual(select_case(loaded, "grasp-recover")["failure_injection"], {"grasp": 1})

    def test_v0_and_v2_have_no_recovery_but_v4_has_bounded_recovery(self):
        case = select_case(self.config, "grasp-recover")
        v0 = build_variant_strategy(case, "V0_RULE_BASELINE", task_id="t-v0")
        v2 = build_variant_strategy(case, "V2_FULL_NO_D", task_id="t-v2")
        v4 = build_variant_strategy(case, "V4_FULL", task_id="t-v4")

        self.assertNotIn("on_failure", v0["steps"][2])
        self.assertNotIn("on_failure", v2["steps"][2])
        self.assertEqual(v4["steps"][2]["on_failure"]["max_attempts"], 1)
        self.assertEqual(v4["task_id"], "t-v4")

    def test_v1_is_real_codearts_without_d_recovery(self):
        runtime = variant_runtime("V1_CODEARTS_POLICY")
        self.assertEqual(runtime["modules"], ["A", "B", "C"])
        self.assertTrue(runtime["external_strategy_required"])
        self.assertFalse(runtime["repair_enabled"])

    def test_v0_can_execute_a_same_scene_external_rule_strategy(self):
        case = select_case(self.config, "grasp-recover")
        perception = {"schema_version": "perception.v1", "objects": []}
        external = build_variant_strategy(case, "V0_RULE_BASELINE", task_id="rule-task")
        external["input_perception_sha256"] = document_digest(perception)
        selected = select_execution_strategy(
            case,
            "V0_RULE_BASELINE",
            external_strategy=external,
            live_perception=perception,
        )
        self.assertEqual(selected["task_id"], "rule-task")

    def test_external_strategy_is_preserved_for_v1_v2_and_v4(self):
        case = select_case(self.config, "grasp-recover")
        external = {
            "schema_version": "strategy.v1",
            "task_id": "provider-task",
            "steps": [
                {"step_id": "provider-detect", "action": "detect_object", "arguments": {"object_id": "green_cube"}},
                {"step_id": "provider-release", "action": "release", "arguments": {}},
            ],
            "provenance": {"provider": "codearts", "request_id": "req-1", "fallback": False},
        }
        live_perception = {"schema_version": "perception.v1", "objects": []}
        external["input_perception_sha256"] = document_digest(live_perception)
        for variant in ("V1_CODEARTS_POLICY", "V2_FULL_NO_D", "V4_FULL"):
            selected = select_execution_strategy(
                case,
                variant,
                external_strategy=external,
                live_perception=live_perception,
                task_id="execution-task",
            )
            self.assertEqual([step["step_id"] for step in selected["steps"]], ["provider-detect", "provider-release"])
            self.assertEqual(selected["provenance"]["request_id"], "req-1")
            self.assertEqual(selected["task_id"], "provider-task")

    def test_external_strategy_is_required_for_intelligent_variants(self):
        case = select_case(self.config, "grasp-recover")
        with self.assertRaisesRegex(ValueError, "external CodeArts strategy"):
            select_execution_strategy(case, "V2_FULL_NO_D", external_strategy=None, live_perception={})

    def test_external_strategy_must_match_live_isaac_perception(self):
        case = select_case(self.config, "grasp-recover")
        external = {
            "schema_version": "strategy.v1",
            "task_id": "provider-task",
            "steps": [{"step_id": "s1", "action": "release", "arguments": {}}],
            "input_perception_sha256": "stale",
        }
        with self.assertRaisesRegex(ValueError, "same live Isaac perception"):
            select_execution_strategy(
                case,
                "V1_CODEARTS_POLICY",
                external_strategy=external,
                live_perception={"schema_version": "perception.v1"},
            )

    def test_legacy_external_strategy_is_bound_to_current_live_perception(self):
        case = select_case(self.config, "grasp-recover")
        external = {
            "schema_version": "strategy.v1",
            "task_id": "provider-task",
            "steps": [{"step_id": "s1", "action": "release", "arguments": {}}],
        }
        live_perception = {"schema_version": "perception.v1", "objects": []}
        selected = select_execution_strategy(
            case,
            "V4_FULL",
            external_strategy=external,
            live_perception=live_perception,
        )
        self.assertEqual(
            selected["input_perception_sha256"], document_digest(live_perception)
        )
        self.assertEqual(selected["provenance"]["legacy_binding"], "live_perception")

    def test_legacy_strategy_arguments_follow_case_object_and_destination(self):
        case = select_case(self.config, "grasp-recover")
        external = {
            "schema_version": "strategy.v1",
            "task_id": "provider-task",
            "steps": [
                {"step_id": "detect", "action": "detect_object", "arguments": {"object_id": "red_cube"}},
                {"step_id": "move", "action": "move_to_object", "arguments": {"object_id": "red_cube"}},
                {"step_id": "grasp", "action": "grasp", "arguments": {"object_id": "red_cube"}},
                {"step_id": "target", "action": "move_to_target", "arguments": {"destination_id": "old_target"}},
            ],
        }
        selected = select_execution_strategy(
            case,
            "V4_FULL",
            external_strategy=external,
            live_perception={"schema_version": "perception.v1", "objects": []},
        )
        self.assertEqual(selected["steps"][0]["arguments"]["object_id"], "green_cube")
        self.assertEqual(selected["steps"][2]["arguments"]["object_id"], "green_cube")
        self.assertEqual(selected["steps"][3]["arguments"]["destination_id"], "zone_unstack_target")

    def test_wait_for_strategy_file_reads_bridge_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy.json"
            path.write_text(json.dumps({"schema_version": "strategy.v1", "steps": [{}]}), encoding="utf-8")
            value = wait_for_strategy_file(path, timeout_s=0.1, poll_s=0.01)
        self.assertEqual(value["schema_version"], "strategy.v1")

    def test_release_recovery_retries_release_without_recalibrating_target(self):
        case = dict(self.config["tasks"][0])
        case["failure_injection"] = {"release": 1}
        strategy = build_variant_strategy(case, "V4_FULL", task_id="t-release")
        release = strategy["steps"][4]
        self.assertEqual(
            [step["action"] for step in release["on_failure"]["steps"]],
            ["release"],
        )

    def test_persistent_failure_is_recorded_as_a_case_condition(self):
        case = select_case(self.config, "grasp-stop")
        self.assertEqual(case["failure_injection"], {"grasp": 2})
        self.assertEqual(case["expected_status"], "SAFE_STOP")

    def test_no_gate_runtime_is_simulation_only(self):
        runtime = variant_runtime("V3_FULL_NO_GATE")
        self.assertFalse(runtime["safety_gate_enabled"])
        self.assertTrue(runtime["simulation_only"])
        self.assertTrue(variant_runtime("V4_FULL")["safety_gate_enabled"])

    def test_failure_injector_fails_only_the_configured_number_of_grasps(self):
        class FakeDriver:
            def __init__(self):
                self.reset_count = 0

            def reset_for_control(self):
                self.reset_count += 1

            def gripper_close(self, force, timeout_s):
                return {"status": "SUCCESS", "duration_ms": 1}

            def gripper_open(self, width, timeout_s):
                return {"status": "SUCCESS", "duration_ms": 1}

        raw_driver = FakeDriver()
        driver = FailureInjectingDriver(raw_driver, {"grasp": 1})
        first = driver.gripper_close(1, 2)
        second = driver.gripper_close(1, 2)
        self.assertEqual(first["status"], "FAILED")
        self.assertEqual(first["reason"], "INJECTED_FAILURE:grasp")
        self.assertEqual(second["status"], "SUCCESS")
        self.assertEqual(raw_driver.reset_count, 1)
        self.assertTrue(driver.injection_log[0]["reset_for_retry"])

        release_driver = FailureInjectingDriver(raw_driver, {"release": 1})
        release = release_driver.gripper_open(0.08, 2)
        self.assertEqual(release["status"], "FAILED")
        self.assertFalse(release_driver.injection_log[0]["reset_for_retry"])
        self.assertEqual(raw_driver.reset_count, 1)


if __name__ == "__main__":
    unittest.main()
