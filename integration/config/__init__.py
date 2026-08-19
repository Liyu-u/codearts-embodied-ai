"""local / sim / real 执行环境配置包。

用法：

    from integration.config.loader import load_profile, build_backend

    profile = load_profile("sim")                 # local | sim | real
    backend = build_backend(profile, perception_v1)
"""

from integration.config.loader import build_backend, list_profiles, load_profile
from integration.config.models import ExecutorProfile

__all__ = [
    "ExecutorProfile",
    "build_backend",
    "list_profiles",
    "load_profile",
]
