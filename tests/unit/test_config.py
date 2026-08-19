import os
import unittest
from unittest.mock import patch

from integration.adapters import perception
from integration.config.loader import build_backend, list_profiles, load_profile
from modules.executor.isaac_backend import IsaacSimBackend
from modules.executor.mock_backend import MockBackend
from modules.executor.real_backend import RealRobotBackend


class ConfigLoaderTests(unittest.TestCase):
    def setUp(self):
        self.scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})

    def test_lists_three_profiles(self):
        self.assertEqual(list_profiles(), ["local", "sim", "real"])

    def test_local_maps_to_mock_backend(self):
        profile = load_profile("local")
        self.assertEqual(profile.name, "local")
        self.assertEqual(profile.backend, "mock")
        self.assertIsInstance(build_backend(profile, self.scene), MockBackend)

    def test_sim_maps_to_isaac_backend(self):
        profile = load_profile("sim")
        self.assertEqual(profile.backend, "isaac")
        backend = build_backend(profile, self.scene)
        self.assertIsInstance(backend, IsaacSimBackend)
        self.assertEqual(backend.mode, "isaac")

    def test_real_maps_to_real_backend_and_requires_confirmation(self):
        profile = load_profile("real")
        self.assertEqual(profile.backend, "real")
        self.assertTrue(profile.safety.require_human_confirmation)
        self.assertLess(profile.safety.motion.max_linear_velocity_m_s, 0.1)
        backend = build_backend(profile, self.scene)
        self.assertIsInstance(backend, RealRobotBackend)
        self.assertFalse(backend.is_confirmed())

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown profile"):
            load_profile("production")

    def test_env_velocity_override_is_applied(self):
        with patch.dict(os.environ, {"RIA_DAILY_MAX_VELOCITY_MS": "0.11"}):
            profile = load_profile("sim")
        self.assertEqual(profile.safety.motion.max_linear_velocity_m_s, 0.11)

    def test_env_backend_override_is_applied(self):
        with patch.dict(os.environ, {"EXECUTOR_BACKEND": "mock"}):
            profile = load_profile("sim")
        self.assertEqual(profile.backend, "mock")


if __name__ == "__main__":
    unittest.main()
