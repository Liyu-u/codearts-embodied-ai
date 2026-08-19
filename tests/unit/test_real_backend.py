import unittest

from integration.adapters import perception
from modules.executor.real_backend import RealRobotBackend
from tests.unit.fake_driver import FakeDriver


def make_real_backend():
    scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
    objects = {item["id"]: item for item in scene["objects"]}
    driver = FakeDriver(objects=objects)
    backend = RealRobotBackend.from_perception(scene, driver=driver)
    return backend, driver


class RealRobotBackendTests(unittest.TestCase):
    def test_motion_requires_human_confirmation(self):
        backend, _ = make_real_backend()
        self.assertFalse(backend.is_confirmed())
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "HUMAN_CONFIRMATION_REQUIRED")

    def test_read_only_detect_does_not_require_confirmation(self):
        backend, _ = make_real_backend()
        result = backend.execute("detect_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "SUCCESS")

    def test_after_confirmation_motion_proceeds(self):
        backend, _ = make_real_backend()
        backend.confirm("operator-wu")
        self.assertTrue(backend.is_confirmed())
        result = backend.execute("move_to_object", {"object_id": "green_cube"})
        self.assertEqual(result["status"], "SUCCESS")

    def test_default_policy_requires_confirmation(self):
        backend, _ = make_real_backend()
        self.assertTrue(backend.require_confirmation())


if __name__ == "__main__":
    unittest.main()
