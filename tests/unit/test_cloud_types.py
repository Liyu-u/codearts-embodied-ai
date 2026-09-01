from __future__ import annotations

import unittest

from demo.cloud.scenario_registry import get_verified_scenario, list_verified_scenarios
from demo.cloud.types import (
    JobState,
    RunState,
    TERMINAL_RUN_STATES,
    assert_transition,
    public_run_snapshot,
)


class CloudRunTypeTests(unittest.TestCase):
    def test_happy_path_accepts_every_required_transition(self) -> None:
        path = [
            RunState.CREATED,
            RunState.PREPARING_SCENE,
            RunState.PERCEIVING,
            RunState.UNDERSTANDING,
            RunState.PLANNING,
            RunState.QUEUED_C,
            RunState.EXECUTING,
            RunState.VERIFYING,
            RunState.SUCCEEDED,
        ]

        for current, target in zip(path, path[1:]):
            with self.subTest(current=current, target=target):
                self.assertEqual(assert_transition(current, target), target)

    def test_terminal_states_reject_every_late_transition(self) -> None:
        self.assertEqual(
            TERMINAL_RUN_STATES,
            {
                RunState.SUCCEEDED,
                RunState.BLOCKED,
                RunState.FAILED,
                RunState.SAFE_STOPPED,
                RunState.CANCELLED,
            },
        )

        for current in TERMINAL_RUN_STATES:
            for target in RunState:
                with self.subTest(current=current, target=target):
                    with self.assertRaises(ValueError):
                        assert_transition(current, target)

    def test_unknown_or_skipped_transitions_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            assert_transition(RunState.CREATED, RunState.EXECUTING)
        with self.assertRaises(ValueError):
            assert_transition("CREATED", "NOT_A_STATE")

    def test_job_states_cover_lease_lifecycle(self) -> None:
        self.assertEqual(
            {state.value for state in JobState},
            {"QUEUED", "LEASED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"},
        )

    def test_public_snapshot_omits_secret_and_internal_fields(self) -> None:
        source = {
            "run_id": "run-001",
            "state": "EXECUTING",
            "instruction": "把红色方块放到桌面区域",
            "relay_token": "secret",
            "credential_ciphertext": "encrypted-secret",
            "database_path": "C:/private/cloud.sqlite3",
            "remote_path": "/data/stu_01/private",
            "ssh_key_path": "C:/private/id_ed25519",
        }

        snapshot = public_run_snapshot(source)

        self.assertEqual(snapshot["run_id"], "run-001")
        self.assertEqual(snapshot["state"], "EXECUTING")
        self.assertEqual(snapshot["instruction"], source["instruction"])
        self.assertFalse(
            {
                "relay_token",
                "credential_ciphertext",
                "database_path",
                "remote_path",
                "ssh_key_path",
            }
            & snapshot.keys()
        )


class VerifiedScenarioRegistryTests(unittest.TestCase):
    def test_registry_exposes_only_the_three_first_phase_isaac_scenes(self) -> None:
        scenarios = list_verified_scenarios()

        self.assertEqual(
            [scenario["id"] for scenario in scenarios],
            ["multi-red-001", "multi-green-001", "multi-red-003"],
        )
        for scenario in scenarios:
            self.assertEqual(scenario["backend"], "isaac")
            self.assertEqual(scenario["scene_version"], "v1.1-supplement")
            self.assertEqual(scenario["livestream_url"], "/live/isaac/index.m3u8")
            self.assertIn("prepare_and_perceive", scenario["capabilities"])
            self.assertIn("execute_strategy", scenario["capabilities"])
            self.assertTrue(scenario["instruction"])
            self.assertTrue(scenario["object_id"])
            self.assertEqual(scenario["destination_id"], "zone_unstack_target")

    def test_get_returns_a_copy_and_unknown_scene_is_rejected(self) -> None:
        scenario = get_verified_scenario("multi-red-001")
        scenario["instruction"] = "tampered"

        self.assertEqual(
            get_verified_scenario("multi-red-001")["instruction"],
            "把红色方块放到桌面区域",
        )
        with self.assertRaises(KeyError):
            get_verified_scenario("not-verified")


if __name__ == "__main__":
    unittest.main()
