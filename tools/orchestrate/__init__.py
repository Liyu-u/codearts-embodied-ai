"""tools/orchestrate —— 一键远程编排控制面。

将本地 A/B 意图与策略生成、远程 Isaac C 执行、本地 D 反馈修复
打包为单命令闭环，并产出 `remote-isaac-run.v1` 制品与完整证据目录。
"""

__all__ = [
    "orchestrate",
    "OrchestrationConfig",
    "OrchestrationResult",
    "StageReport",
    "RemoteIsaacRunArtifact",
]

from tools.orchestrate.orchestrator import orchestrate
from tools.orchestrate.types import (
    OrchestrationConfig,
    OrchestrationResult,
    RemoteIsaacRunArtifact,
    StageReport,
)