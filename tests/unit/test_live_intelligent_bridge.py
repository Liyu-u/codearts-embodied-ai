import unittest

from tools.run_live_intelligent_bridge import build_container_command


class LiveIntelligentBridgeTests(unittest.TestCase):
    def test_container_waits_for_strategy_after_live_perception(self):
        command = build_container_command(
            remote_root="/data/stu_01/workspace/live-run",
            container_name="live-run",
            task_config="testdata/benchmark/real_isaac_supplement_v2.json",
            case_id="multi-red-003",
            variant_id="V4_FULL",
            seed=20260831,
            gpu_index="0",
            strategy_wait_s=600,
        )
        self.assertIn("--strategy-wait-s 600", command)
        self.assertIn("/workspace/results", command)
        self.assertIn("/workspace/live_strategy.json", command)
        self.assertIn("--no-healthcheck", command)
        self.assertNotIn("API_KEY", command)
        self.assertNotIn("CODEARTS_CLI_AK", command)


if __name__ == "__main__":
    unittest.main()
