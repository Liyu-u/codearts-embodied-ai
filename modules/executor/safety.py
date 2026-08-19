"""Executor 安全守卫层。

本模块只包含纯 Python 的数据结构和函数，不导入 Isaac Sim / Omniverse，
因此可以在 `huawei` 环境和 CI（标准库）中直接运行与测试。

它定义三类安全约束，供 Mock / IsaacSim / 真机后端共同使用：

- 工作空间限制（workspace）：目标位姿必须在立方体工作空间内，越界即拒绝；
- 运动限制（motion）：线速度、角速度、末端力上限，以及每个动作的墙钟超时；
- 安全策略（policy）：是否需要人工确认、是否启用急停、是否做碰撞检查、
  以及碰撞/驱动异常时是 fail-closed（拒绝动作）还是 fail-open（放行）。

所有函数都是无副作用、可单元测试的纯函数。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkspaceLimits:
    """世界坐标系下的立方体工作空间（单位：米）。

    z_min 通常取桌面以上（例如 0.0），z_max 取机械臂可安全到达的上限。
    """

    x_min: float = -0.5
    x_max: float = 0.5
    y_min: float = -0.5
    y_max: float = 0.5
    z_min: float = 0.0
    z_max: float = 0.6


@dataclass(frozen=True)
class MotionLimits:
    """运动安全上限。线速度单位为米/秒，角速度为弧度/秒，力为牛顿。

    - ``max_linear_velocity_m_s``：末端线速度安全上限（“限速”天花板）。
    - ``default_linear_speed_m_s``：无显式速度时后端采用的指令速度，永远不大于上限。
    - ``grasp_verify_force_n``：抓取后夹持力低于该值判定为“抓空”。
    """

    max_linear_velocity_m_s: float = 0.30
    max_angular_velocity_rad_s: float = 1.0
    max_force_n: float = 10.0
    action_timeout_s: float = 30.0
    default_linear_speed_m_s: float = 0.05
    grasp_verify_force_n: float = 0.5


@dataclass(frozen=True)
class SafetyPolicy:
    """一个执行环境的完整安全策略。

    fail_closed_on_error=True 表示：碰撞检查抛异常、驱动不可用等“未知”情况
    一律按不安全处理（拒绝/停止），而不是假设安全继续执行。
    """

    workspace: WorkspaceLimits = field(default_factory=WorkspaceLimits)
    motion: MotionLimits = field(default_factory=MotionLimits)
    require_human_confirmation: bool = False
    e_stop_enabled: bool = True
    collision_check: bool = True
    fail_closed_on_error: bool = True


def _coordinate(pose: Any, axis: str) -> float:
    if not isinstance(pose, dict):
        raise TypeError(f"pose must be an object, got {type(pose).__name__}")
    value = pose.get(axis)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"pose.{axis} must be a number, got {value!r}")
    return float(value)


def workspace_violations(pose: dict, workspace: WorkspaceLimits) -> list[str]:
    """返回目标位姿越界的原因列表；为空表示在工作空间内。

    位姿字段缺失或类型错误被视为越界（fail-closed），而不是静默放行。
    """
    try:
        x, y, z = _coordinate(pose, "x"), _coordinate(pose, "y"), _coordinate(pose, "z")
    except (TypeError, ValueError) as exc:
        return [f"WORKSPACE_VIOLATION:invalid pose: {exc}"]

    violations: list[str] = []
    if not workspace.x_min <= x <= workspace.x_max:
        violations.append(f"WORKSPACE_VIOLATION:x={x} outside [{workspace.x_min},{workspace.x_max}]")
    if not workspace.y_min <= y <= workspace.y_max:
        violations.append(f"WORKSPACE_VIOLATION:y={y} outside [{workspace.y_min},{workspace.y_max}]")
    if not workspace.z_min <= z <= workspace.z_max:
        violations.append(f"WORKSPACE_VIOLATION:z={z} outside [{workspace.z_min},{workspace.z_max}]")
    return violations


def in_workspace(pose: dict, workspace: WorkspaceLimits) -> bool:
    """位姿是否完全落在工作空间内。"""
    return not workspace_violations(pose, workspace)


def clamp_speed(commanded_m_s: float, limit_m_s: float) -> float:
    """把指令线速度限制到安全上限，返回实际采用的线速度。"""
    if commanded_m_s < 0:
        # 负速度在语义上不合法，按 0 处理（停止），而不是反向放大。
        return 0.0
    return min(commanded_m_s, limit_m_s)


def speed_violation(commanded_m_s: float, limit_m_s: float) -> bool:
    """指令线速度是否超过安全上限。"""
    return commanded_m_s > limit_m_s


class Deadline:
    """墙钟截止时间。用于动作内部循环的实时超时判断，不依赖 sleep。"""

    def __init__(self, timeout_s: float) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self._timeout_s = float(timeout_s)
        self._started = time.monotonic()

    @property
    def remaining_s(self) -> float:
        return max(0.0, self._timeout_s - (time.monotonic() - self._started))

    @property
    def expired(self) -> bool:
        return self.remaining_s <= 0.0

    def result(self) -> dict:
        """返回超时证据，便于后端统一写入动作结果。"""
        return {
            "timed_out": self.expired,
            "timeout_s": self._timeout_s,
            "remaining_s": self.remaining_s,
        }
