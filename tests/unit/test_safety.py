import unittest

from modules.executor.safety import (
    Deadline,
    WorkspaceLimits,
    clamp_speed,
    in_workspace,
    speed_violation,
    workspace_violations,
)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.workspace = WorkspaceLimits(
            x_min=-0.5, x_max=0.5, y_min=-0.5, y_max=0.5, z_min=0.0, z_max=0.6
        )

    def test_pose_inside_workspace_has_no_violations(self):
        self.assertEqual(
            workspace_violations({"x": 0.1, "y": 0.0, "z": 0.2}, self.workspace),
            [],
        )

    def test_pose_outside_workspace_reports_axis(self):
        violations = workspace_violations(
            {"x": 1.2, "y": 0.0, "z": 0.2}, self.workspace
        )
        self.assertTrue(any("x=1.2" in item for item in violations))

    def test_invalid_pose_is_a_violation_not_silent_pass(self):
        violations = workspace_violations({"x": "bad", "y": 0.0, "z": 0.2},
                                          self.workspace)
        self.assertTrue(any("invalid pose" in item for item in violations))

    def test_in_workspace(self):
        self.assertTrue(in_workspace({"x": 0, "y": 0, "z": 0.1}, self.workspace))
        self.assertFalse(in_workspace({"x": 0, "y": 0, "z": 0.9}, self.workspace))


class SpeedTests(unittest.TestCase):
    def test_clamp_speed_caps_above_limit(self):
        self.assertEqual(clamp_speed(1.0, 0.3), 0.3)

    def test_clamp_speed_keeps_below_limit(self):
        self.assertEqual(clamp_speed(0.05, 0.3), 0.05)

    def test_negative_speed_is_zero(self):
        self.assertEqual(clamp_speed(-0.1, 0.3), 0.0)

    def test_speed_violation(self):
        self.assertTrue(speed_violation(0.5, 0.3))
        self.assertFalse(speed_violation(0.3, 0.3))


class DeadlineTests(unittest.TestCase):
    def test_deadline_starts_unexpired(self):
        self.assertFalse(Deadline(1.0).expired)

    def test_deadline_rejects_non_positive_timeout(self):
        with self.assertRaises(ValueError):
            Deadline(0.0)

    def test_deadline_result_shape(self):
        result = Deadline(2.0).result()
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["timeout_s"], 2.0)
        self.assertGreater(result["remaining_s"], 0.0)


if __name__ == "__main__":
    unittest.main()
