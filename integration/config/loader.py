"""环境配置加载器与后端工厂。

- ``load_profile(name)``：按 ``local`` / ``sim`` / ``real`` 读取 TOML 配置，
  叠加环境变量覆盖，返回 ``ExecutorProfile``。
- ``build_backend(profile, perception)``：把 profile 映射为具体执行后端。

依赖规则：只在标准库上运行（``tomllib`` 为 Python 3.11+ 标准库）。
TOML 缺失或不可解析时回退到内置默认值，保证 ``local``（Mock）永远可用。
"""

from __future__ import annotations

import os
from pathlib import Path

from integration.config.local_env import load_local_env
from integration.config.models import ExecutorProfile, PROFILE_NAMES
from modules.executor.safety import MotionLimits, SafetyPolicy, WorkspaceLimits

_PROFILES_DIR = Path(__file__).resolve().parent / "profiles"

# Pydantic Settings owns .env for A, while this loader reads the same RIA_
# safety overrides from os.environ. Load the validated RIA-only file here so
# local/sim/real profiles receive the values shown in .env.example.
load_local_env(".env")

# 内置默认（与 profiles/*.toml 一致，作为离线兜底）。
_DEFAULTS = {
    "local": {
        "backend": "mock",
        "workspace": {"x_min": -0.5, "x_max": 0.5, "y_min": -0.5, "y_max": 0.5,
                      "z_min": 0.0, "z_max": 0.6},
        "motion": {"max_linear_velocity_m_s": 0.30, "max_angular_velocity_rad_s": 1.0,
                   "max_force_n": 10.0, "action_timeout_s": 30.0,
                   "default_linear_speed_m_s": 0.05, "grasp_verify_force_n": 0.5},
        "safety": {"require_human_confirmation": False, "e_stop_enabled": True,
                   "collision_check": True, "fail_closed_on_error": True},
    },
    "sim": {
        "backend": "isaac",
        "workspace": {"x_min": -0.5, "x_max": 0.7, "y_min": -0.5, "y_max": 0.5,
                      "z_min": 0.0, "z_max": 0.6},
        "motion": {"max_linear_velocity_m_s": 0.30, "max_angular_velocity_rad_s": 1.0,
                   "max_force_n": 10.0, "action_timeout_s": 180.0,
                   "default_linear_speed_m_s": 0.20, "grasp_verify_force_n": 0.5},
        "safety": {"require_human_confirmation": False, "e_stop_enabled": True,
                   "collision_check": True, "fail_closed_on_error": True},
    },
    "real": {
        "backend": "real",
        "workspace": {"x_min": -0.3, "x_max": 0.3, "y_min": -0.3, "y_max": 0.3,
                      "z_min": 0.02, "z_max": 0.45},
        "motion": {"max_linear_velocity_m_s": 0.05, "max_angular_velocity_rad_s": 0.5,
                   "max_force_n": 8.0, "action_timeout_s": 60.0,
                   "default_linear_speed_m_s": 0.02, "grasp_verify_force_n": 0.5},
        "safety": {"require_human_confirmation": True, "e_stop_enabled": True,
                   "collision_check": True, "fail_closed_on_error": True},
    },
}


def list_profiles() -> list[str]:
    return list(PROFILE_NAMES)


def _load_toml(name: str) -> dict:
    path = _PROFILES_DIR / f"{name}.toml"
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        return {}
    try:
        with open(path, "rb") as handle:
            return tomllib.load(handle)
    except (OSError, ValueError):
        return {}


def _apply_env_overrides(profile_data: dict) -> None:
    """叠加环境变量覆盖。优先读取项目已有的 RIA_* 安全变量。"""
    motion = profile_data.setdefault("motion", {})

    domain = os.environ.get("RIA_DEPLOYMENT_DOMAIN", "daily").lower()
    if domain not in ("daily", "industrial"):
        domain = "daily"
    velocity_key = f"RIA_{domain.upper()}_MAX_VELOCITY_MS"
    force_key = f"RIA_{domain.upper()}_MAX_FORCE_N"

    velocity = os.environ.get(velocity_key)
    if velocity is not None:
        try:
            motion["max_linear_velocity_m_s"] = float(velocity)
        except ValueError:
            pass
    force = os.environ.get(force_key)
    if force is not None:
        try:
            motion["max_force_n"] = float(force)
        except ValueError:
            pass

    backend = os.environ.get("EXECUTOR_BACKEND")
    if backend in ("mock", "isaac", "real"):
        profile_data["backend"] = backend


def _coerce_float(data: dict, key: str, default: float) -> float:
    value = data.get(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_safety(data: dict) -> SafetyPolicy:
    workspace_data = data.get("workspace", {})
    workspace = WorkspaceLimits(
        x_min=_coerce_float(workspace_data, "x_min", -0.5),
        x_max=_coerce_float(workspace_data, "x_max", 0.5),
        y_min=_coerce_float(workspace_data, "y_min", -0.5),
        y_max=_coerce_float(workspace_data, "y_max", 0.5),
        z_min=_coerce_float(workspace_data, "z_min", 0.0),
        z_max=_coerce_float(workspace_data, "z_max", 0.6),
    )
    motion_data = data.get("motion", {})
    motion = MotionLimits(
        max_linear_velocity_m_s=_coerce_float(
            motion_data, "max_linear_velocity_m_s", 0.30),
        max_angular_velocity_rad_s=_coerce_float(
            motion_data, "max_angular_velocity_rad_s", 1.0),
        max_force_n=_coerce_float(motion_data, "max_force_n", 10.0),
        action_timeout_s=_coerce_float(motion_data, "action_timeout_s", 30.0),
        default_linear_speed_m_s=_coerce_float(
            motion_data, "default_linear_speed_m_s", 0.05),
        grasp_verify_force_n=_coerce_float(
            motion_data, "grasp_verify_force_n", 0.5),
    )
    safety_data = data.get("safety", {})
    return SafetyPolicy(
        workspace=workspace,
        motion=motion,
        require_human_confirmation=bool(
            safety_data.get("require_human_confirmation", False)),
        e_stop_enabled=bool(safety_data.get("e_stop_enabled", True)),
        collision_check=bool(safety_data.get("collision_check", True)),
        fail_closed_on_error=bool(safety_data.get("fail_closed_on_error", True)),
    )


def load_profile(name: str) -> ExecutorProfile:
    if name not in PROFILE_NAMES:
        raise ValueError(
            f"unknown profile {name!r}; expected one of {list(PROFILE_NAMES)}"
        )
    data = dict(_DEFAULTS.get(name, {}))
    for key, value in _load_toml(name).items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            merged = dict(data[key])
            merged.update(value)
            data[key] = merged
        else:
            data[key] = value
    _apply_env_overrides(data)
    return ExecutorProfile(
        name=name,
        backend=data.get("backend", "mock"),
        safety=_build_safety(data),
    )


def build_backend(profile: ExecutorProfile, perception: dict, driver=None):
    """把 profile 映射为具体执行后端，供 executor 适配器使用。"""
    if profile.backend == "mock":
        from modules.executor.mock_backend import MockBackend

        return MockBackend.from_perception(perception)
    if profile.backend == "isaac":
        from modules.executor.isaac_backend import IsaacSimBackend

        return IsaacSimBackend.from_perception(
            perception, safety=profile.safety, driver=driver
        )
    if profile.backend == "real":
        from modules.executor.real_backend import RealRobotBackend

        return RealRobotBackend.from_perception(
            perception, safety=profile.safety, driver=driver
        )
    raise ValueError(f"unknown backend: {profile.backend}")
