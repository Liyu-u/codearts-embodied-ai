"""tools/orchestrate/__main__.py —— 一键远程编排 CLI 入口。

用法：
    python -m tools.orchestrate --instruction "..." --scene stacking_cubes \\
        --server <host> --port 5122 --user <user> --remote-base /data/<user>/workspace

输出：机器可读 JSON 到 stdout，阶段化日志到 stderr；失败返回非 0 退出码
并携带 ``failure_class`` 字段。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.orchestrate.orchestrator import orchestrate
from tools.orchestrate.types import OrchestrationConfig, exit_code_for


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.orchestrate",
        description="一键远程编排：本地 A/B → 远程 Isaac C → 本地 D → 证据目录",
    )
    parser.add_argument("--instruction", required=True, help="自然语言指令")
    parser.add_argument("--scene", required=True, help="场景 ID")
    parser.add_argument("--server", default="", help="远程服务器地址")
    parser.add_argument("--port", type=int, default=5122, help="SSH 端口")
    parser.add_argument("--user", default="", help="远程账号")
    parser.add_argument("--remote-base", default="", help="远程工作根目录")
    parser.add_argument(
        "--device", choices=["cpu", "cuda", "cuda:0"], default="cuda"
    )
    parser.add_argument(
        "--backend", choices=["mock", "remote_isaac"], default="remote_isaac"
    )
    parser.add_argument("--ssh-timeout", type=int, default=30)
    parser.add_argument("--container-timeout", type=int, default=180)
    parser.add_argument("--execution-timeout", type=int, default=900)
    parser.add_argument("--transport-retries", type=int, default=2)
    parser.add_argument(
        "--auth-mode", choices=["key", "interactive", "batch"], default="key"
    )
    parser.add_argument("--key-path", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    config = OrchestrationConfig(
        instruction=args.instruction,
        scene_id=args.scene,
        server=args.server,
        port=args.port,
        user=args.user,
        remote_base=args.remote_base,
        device=args.device,
        auth_mode=args.auth_mode,
        key_path=args.key_path,
        ssh_timeout_s=args.ssh_timeout,
        container_timeout_s=args.container_timeout,
        execution_timeout_s=args.execution_timeout,
        transport_retries=args.transport_retries,
        backend=args.backend,
        out_dir=args.out_dir,
    )

    result = orchestrate(config, logger=lambda text: print(text, file=sys.stderr))
    payload = {
        "run_id": result.run_id,
        "status": result.status,
        "failure_class": result.failure_class,
        "stages": [vars(stage) for stage in result.stages],
        "artifact_paths": {
            name: str(path) for name, path in sorted(result.artifact_paths.items())
        },
        "retry_command": result.retry_command,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code_for(result.failure_class)


if __name__ == "__main__":
    sys.exit(main())