import unittest

from modules.executor.isaac_driver import _physx_hit_path, _raycast_overlap_fallback


class _Hit:
    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class _Carb:
    @staticmethod
    def Float3(*values):
        return tuple(values)


class _RaycastQuery:
    def __init__(self):
        self.calls = 0

    def raycast_closest(self, origin, direction, distance):
        self.calls += 1
        if self.calls == 1:
            return {"hit": True, "rigidBody": "/World/robot/panda_hand"}
        return {"hit": False}


class PhysXHitPathTests(unittest.TestCase):
    def test_prefers_rigid_body_path(self):
        self.assertEqual(
            _physx_hit_path({"rigid_body": "/World/robot", "collision": "/World/robot/link"}),
            "/World/robot",
        )

    def test_uses_collision_when_rigid_body_is_empty(self):
        self.assertEqual(
            _physx_hit_path(_Hit(rigid_body="", collision="/World/ground_plane/Collision")),
            "/World/ground_plane/Collision",
        )

    def test_supports_camel_case_collision_fields(self):
        self.assertEqual(
            _physx_hit_path({"rigidBody": "", "collision": "/World/green_cube"}),
            "/World/green_cube",
        )

    def test_rejects_non_path_scalar_as_unknown(self):
        self.assertEqual(_physx_hit_path({"rigid_body": 17, "collision": None}), "")

    def test_raycast_fallback_resolves_count_only_overlap(self):
        query = _RaycastQuery()
        paths, unresolved = _raycast_overlap_fallback(query, (0.0, 0.0, 0.0), 0.05, _Carb)
        self.assertIn("/World/robot/panda_hand", paths)
        self.assertFalse(unresolved)
        self.assertEqual(query.calls, 26)


if __name__ == "__main__":
    unittest.main()
