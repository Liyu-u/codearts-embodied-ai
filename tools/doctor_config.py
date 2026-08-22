"""Check the local A/B/D configuration without exposing secrets.

Usage from the repository root::

    python tools/doctor_config.py
    python tools/doctor_config.py --live-codearts

The default check is offline and safe for CI. ``--live-codearts`` additionally
invokes the locally installed CodeArts CLI to check account/model visibility.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ERRORS = 0
WARNINGS = 0


def report(level: str, message: str) -> None:
    global ERRORS, WARNINGS
    print(f"[{level}] {message}")
    if level == "ERROR":
        ERRORS += 1
    elif level == "WARN":
        WARNINGS += 1


def env_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    keys: list[str] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.append(key)
    return keys


def check_namespace(filename: str, prefix: str, *, required: bool = False) -> None:
    path = ROOT / filename
    if not path.is_file():
        if required:
            report("ERROR", f"缺少本地配置文件: {filename}")
        else:
            report("WARN", f"尚未生成本地配置文件: {filename}")
        return
    keys = env_keys(path)
    invalid = [key for key in keys if not key.startswith(prefix)]
    if invalid:
        report(
            "ERROR",
            f"{filename} 混入了其他模块字段: {', '.join(invalid)}；请重新运行 tools/setup.ps1",
        )
    else:
        report("OK", f"{filename} 字段命名空间正确（{len(keys)} 个字段）")


def check_python() -> None:
    if sys.version_info < (3, 11):
        report("ERROR", f"Python 需要 3.11+，当前为 {sys.version.split()[0]}")
    else:
        report("OK", f"Python {sys.version.split()[0]}")


def check_a() -> None:
    try:
        from modules.intent_understanding.robot_intent_agent.config.settings import Settings

        settings = Settings()
    except Exception as exc:  # pydantic validation errors are configuration errors
        report("ERROR", f"A 配置无法解析: {type(exc).__name__}: {exc}")
        return

    mode = settings.planner_engine.strip().lower()
    has_key = settings.has_deepseek_key()
    if mode not in {"rule", "llm", "hybrid"}:
        report("ERROR", f"A 的 RIA_PLANNER_ENGINE 无效: {mode}")
    elif mode == "llm" and not has_key:
        report("ERROR", "A 设置为 llm，但 RIA_DEEPSEEK_API_KEY 未配置")
    elif mode == "hybrid" and not has_key:
        report("WARN", "A 设置为 hybrid，但未配置 Key，将只使用规则回退")
    else:
        report("OK", f"A 配置可用：engine={mode}, model={settings.deepseek_model}")


def check_d() -> None:
    try:
        from modules.evaluator.tracecoder.llm_provider import LLMConfig

        config = LLMConfig.from_env()
    except Exception as exc:
        report("ERROR", f"D 配置无法解析: {type(exc).__name__}: {exc}")
        return

    if config.mode == "required" and (not config.api_key or not config.model):
        report("ERROR", "D 设置为 required，但模型或 API Key 未配置")
    elif config.mode in {"optional", "required"} and not config.api_key:
        report("WARN", f"D 为 {config.mode}，但未配置 Key，将无法进行真实 LLM 调用")
    else:
        report(
            "OK",
            f"D 配置可用：mode={config.mode}, model={config.model or '未配置'}, "
            f"key={'已配置' if config.api_key else '未配置'}",
        )


def check_b(live: bool) -> None:
    try:
        from integration.config.local_env import load_codearts_env

        load_codearts_env()
    except Exception as exc:
        report("ERROR", f"B 本地配置加载失败: {type(exc).__name__}: {exc}")
        return

    codearts_file = ROOT / "codearts.env"
    if codearts_file.is_file():
        invalid = [key for key in env_keys(codearts_file) if not key.startswith("CODEARTS_")]
        if invalid:
            report("ERROR", f"codearts.env 混入了其他模块字段: {', '.join(invalid)}")
        else:
            report("OK", f"codearts.env 字段命名空间正确（{len(env_keys(codearts_file))} 个字段）")
    elif any(key in os.environ for key in ("CODEARTS_CLI", "CODEARTS_STRATEGY_MODE", "CODEARTS_STRATEGY_MODEL")):
        report("OK", "B 配置来自当前用户/进程环境（未生成 codearts.env 也可以运行）")
    else:
        report("WARN", "未生成 codearts.env；请运行 tools/setup.ps1 固化 B 的本地配置")

    executable = os.environ.get("CODEARTS_CLI", "codearts").strip() or "codearts"
    resolved = shutil.which(executable)
    mode = os.environ.get("CODEARTS_STRATEGY_MODE", "auto").strip().lower()
    policy = os.environ.get("CODEARTS_STRATEGY_POLICY", "planner").strip().lower()
    if mode not in {"off", "auto", "required"}:
        report("ERROR", f"B 的 CODEARTS_STRATEGY_MODE 无效: {mode}")
    if policy not in {"planner", "quality", "max"}:
        report("ERROR", f"B 的 CODEARTS_STRATEGY_POLICY 无效: {policy}")
    if resolved is None:
        if mode == "required":
            report("ERROR", f"B 为 required，但找不到 CodeArts CLI: {executable}")
        else:
            report("WARN", f"找不到 CodeArts CLI: {executable}（B 将按模式回退或关闭）")
        return

    report(
        "OK",
        f"B CLI 可用：{resolved}, mode={mode}, policy={policy}, "
        f"model={os.environ.get('CODEARTS_STRATEGY_MODEL') or 'CLI 默认'}",
    )
    if not live or mode == "off":
        return

    for command in ((resolved, "models"), (resolved, "agent", "list")):
        try:
            completed = subprocess.run(
                list(command),
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            report("ERROR", f"CodeArts {' '.join(command[1:])} 检查失败: {exc}")
            continue
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "无输出").strip()
            report("ERROR", f"CodeArts {' '.join(command[1:])} 返回 {completed.returncode}: {detail[:300]}")
        else:
            report("OK", f"CodeArts {' '.join(command[1:])} 检查通过")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-codearts",
        action="store_true",
        help="真实调用 CodeArts models/agent list 检查账号与可见性",
    )
    args = parser.parse_args()

    print(f"配置体检：{ROOT}")
    check_python()
    check_namespace(".env", "RIA_", required=True)
    check_namespace("tracecoder_llm.env", "TRACECODER_LLM_")
    check_a()
    check_d()
    check_b(args.live_codearts)
    print(f"完成：{ERRORS} 个错误，{WARNINGS} 个警告")
    return 1 if ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
