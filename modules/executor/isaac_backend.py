"""IsaacSimBackend：与 ``MockBackend`` 同接口的真实 Isaac Sim 执行后端。

在 Kit 运行时内通过 ``OmniDriver`` 驱动 Franka Panda。构造方式与 Mock 一致，
从而保证“同一条指令在 Mock 和 Isaac Sim 中产生可对照的执行结果”：

    from modules.executor.isaac_backend import IsaacSimBackend
    from modules.executor.isaac_driver import OmniDriver

    backend = IsaacSimBackend.from_perception(
        perception_v1,
        safety=profile.safety,
        driver=OmniDriver(world=world),
    )
    backend.connect()
"""

from __future__ import annotations

from modules.executor.robot_backend import BaseRobotBackend


class IsaacSimBackend(BaseRobotBackend):
    mode = "isaac"
