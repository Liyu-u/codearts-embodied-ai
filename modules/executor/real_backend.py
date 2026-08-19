"""RealRobotBackend：真机执行后端骨架。

与 ``IsaacSimBackend`` 共享同一套安全守卫，但默认强制人工确认：任何运动动作在
操作者调用 ``confirm(operator_id)`` 之前都会被拒绝。真机必须先经过小范围、低速、
限工作空间、带急停的验证，再逐步放开速度与范围（见 real.toml 与交接文档）。
"""

from __future__ import annotations

from modules.executor.robot_backend import BaseRobotBackend
from modules.executor.safety import SafetyPolicy


class RealRobotBackend(BaseRobotBackend):
    mode = "real"

    def __init__(self, objects, safety=None, driver=None):
        if safety is None:
            safety = SafetyPolicy(require_human_confirmation=True)
        super().__init__(objects, safety=safety, driver=driver)
