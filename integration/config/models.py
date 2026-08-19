"""执行环境配置的数据模型。

``ExecutorProfile`` 描述一个执行环境（local / sim / real）：
- ``backend``：使用哪个执行后端（mock / isaac / real）；
- ``safety``：该环境的安全策略（工作空间、限速、超时、人工确认、碰撞、急停）。

数据类本身是纯 Python，不依赖 Isaac Sim，可在 CI 中导入与测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from modules.executor.safety import SafetyPolicy

BACKENDS = ("mock", "isaac", "real")
PROFILE_NAMES = ("local", "sim", "real")


@dataclass(frozen=True)
class ExecutorProfile:
    name: str
    backend: str
    safety: SafetyPolicy = field(default_factory=SafetyPolicy)

    def __post_init__(self) -> None:
        if self.backend not in BACKENDS:
            raise ValueError(
                f"unknown backend {self.backend!r}; expected one of {BACKENDS}"
            )
