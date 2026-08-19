"""供单元测试使用的假运动驱动（不依赖 Isaac Sim）。"""

from __future__ import annotations

from copy import deepcopy

from modules.executor.isaac_driver import DriverError, motion_result


class FakeDriver:
    """可配置的假驱动，用于测试后端的安全守卫与状态机。"""

    def __init__(
        self,
        objects=None,
        *,
        collision_free=True,
        collision_error=False,
        move_collisions=None,
        move_timeout=False,
        move_fail=None,
        grasp_force_n=2.0,
        grasp_verified=True,
        gripper_close_timeout=False,
        gripper_open_timeout=False,
    ):
        self.objects = deepcopy(objects or {})
        self.collision_free_flag = collision_free
        self.collision_error = collision_error
        self.move_collisions = list(move_collisions or [])
        self.move_timeout = move_timeout
        self.move_fail = move_fail
        self.grasp_force_n = grasp_force_n
        self.grasp_verified = grasp_verified
        self.gripper_close_timeout = gripper_close_timeout
        self.gripper_open_timeout = gripper_open_timeout
        self.connected = False
        self.stopped = False
        self.move_calls = []

    def connect(self):
        self.connected = True

    def move_to(self, pose, linear_speed, timeout_s):
        self.move_calls.append(deepcopy(pose))
        if self.move_fail:
            return motion_result("FAILED", self.move_fail, 10)
        if self.move_timeout:
            return motion_result("FAILED", "ACTION_TIMEOUT", 10, timed_out=True)
        return motion_result(
            "SUCCESS", "", 50, pose=deepcopy(pose),
            collisions=self.move_collisions,
            trajectory=[{
                "timestamp_ms": 0,
                "coordinate_frame": "world",
                "position": deepcopy(pose),
            }],
        )

    def gripper_open(self, width, timeout_s):
        if self.gripper_open_timeout:
            return motion_result("FAILED", "ACTION_TIMEOUT", 20, timed_out=True)
        return motion_result("SUCCESS", "", 20, width=width)

    def gripper_close(self, force, timeout_s):
        if self.gripper_close_timeout:
            return motion_result("FAILED", "ACTION_TIMEOUT", 30, timed_out=True)
        return motion_result(
            "SUCCESS", "", 30, width=0.001, grasp_force_n=self.grasp_force_n
        )

    def read_object_pose(self, object_id):
        item = self.objects.get(object_id)
        if item is None:
            raise DriverError(f"object not found: {object_id}")
        return deepcopy(item["pose"])

    def collision_free(self, pose, radius, excluded_paths=()):
        if self.collision_error:
            raise DriverError("collision query failed")
        return self.collision_free_flag

    def verify_grasp(self, object_id, initial_pose=None, lift_z=0.20):
        item = self.objects.get(object_id)
        return {
            "verified": bool(self.grasp_verified),
            "object_pose": deepcopy(item["pose"]) if item else None,
            "grasp_force_n": self.grasp_force_n,
            "reason": "" if self.grasp_verified else "OBJECT_DID_NOT_LIFT",
        }

    def e_stop(self):
        self.stopped = True

    def shutdown(self):
        self.stopped = True
