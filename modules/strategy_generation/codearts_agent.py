"""CodeArts CLI adapter for generating safe ``strategy.v1`` documents.

CodeArts is an agent runtime, not an OpenAI-compatible model endpoint.  This
module therefore invokes the official non-interactive CLI (``codearts run``)
and accepts only a validated, structured strategy from its output.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterator

from modules.executor.action_catalog import validate_action_arguments


ACTION_WHITELIST = {
    "detect_object",
    "move_to_object",
    "grasp",
    "move_to_target",
    "release",
}
OUTPUT_BEGIN = "STRATEGY_JSON_BEGIN"
OUTPUT_END = "STRATEGY_JSON_END"
REPO_ROOT = Path(__file__).resolve().parents[2]


class CodeArtsStrategyClient:
    """Invoke a configured CodeArts agent and validate its strategy output."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        agent: str | None = None,
        model: str | None = None,
        timeout_s: int | None = None,
        runner: Callable[..., Any] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.executable = executable or os.environ.get("CODEARTS_CLI", "codearts")
        self.agent = agent if agent is not None else os.environ.get("CODEARTS_STRATEGY_AGENT", "")
        self.model = model if model is not None else os.environ.get("CODEARTS_STRATEGY_MODEL", "")
        self.timeout_s = timeout_s or _positive_int_env("CODEARTS_STRATEGY_TIMEOUT_S", 120)
        self._runner = runner
        self._which = which

    def availability(self) -> dict[str, Any]:
        """Report whether the CodeArts executable can be launched."""
        resolved = self._resolve_executable()
        return {
            "available": resolved is not None,
            "executable": resolved or self.executable,
            "agent": self.agent or None,
            "model": self.model or None,
        }

    def generate(self, task: dict[str, Any]) -> dict[str, Any]:
        """Generate and validate a strategy for one ready ``task.v1`` object."""
        resolved = self._resolve_executable()
        trace = {
            "provider": "huaweicloud-codearts-agent",
            "transport": "codearts-cli",
            "agent": self.agent or None,
            "model": self.model or None,
        }
        if resolved is None:
            return _failure("CODEARTS_CLI_NOT_FOUND", trace)

        command = [resolved, "run", "--format", "json"]
        if self.agent:
            command.extend(["--agent", self.agent])
        if self.model:
            command.extend(["--model", self.model])
        command.append(_build_prompt(task))

        try:
            completed = self._runner(
                command,
                cwd=str(REPO_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return _failure("CODEARTS_CLI_TIMEOUT", trace)
        except OSError as exc:
            return _failure(f"CODEARTS_CLI_START_FAILED:{exc}", trace)

        trace["exit_code"] = completed.returncode
        if completed.returncode != 0:
            detail = _compact_error(completed.stderr or completed.stdout)
            return _failure(f"CODEARTS_CLI_FAILED:{detail}", trace)

        candidate = extract_strategy(completed.stdout)
        if candidate is None:
            return _failure("CODEARTS_OUTPUT_MISSING_STRATEGY", trace)

        errors = validate_strategy(candidate, task)
        if errors:
            return _failure("CODEARTS_STRATEGY_REJECTED:" + errors[0], trace)

        strategy = dict(candidate)
        strategy["code"] = None
        strategy.update(
            {
                "success": True,
                "blocked": False,
                "message": "CodeArts 智能体已生成并通过本地安全校验",
                "mode": "codearts_agent",
                "validation": {"passed": True, "errors": []},
                "provenance": trace,
            }
        )
        return {"success": True, "strategy": strategy, "error": None, "trace": trace}

    def _resolve_executable(self) -> str | None:
        path = Path(self.executable)
        if path.parent != Path(".") and path.is_file():
            return str(path)
        return self._which(self.executable)


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _failure(error: str, trace: dict[str, Any]) -> dict[str, Any]:
    return {"success": False, "strategy": None, "error": error, "trace": trace}


def _compact_error(value: str) -> str:
    compact = " ".join((value or "unknown error").split())
    return compact[:300]


def _build_prompt(task: dict[str, Any]) -> str:
    task_json = json.dumps(task, ensure_ascii=False, indent=2)
    return f"""请使用 robot-strategy Skill，为下面的 task.v1 生成一个安全的 strategy.v1。

硬性要求：
1. 只输出结构化策略，不生成 Python、Shell 或其他可执行代码，code 必须为 null。
2. action 只能使用 detect_object、move_to_object、grasp、move_to_target、release。
3. task_id 必须原样保留；目标和目的地必须使用输入中的稳定 ID。
4. 所有 step_id 唯一；引用格式为 $step_id.field。
5. 输出必须放在 {OUTPUT_BEGIN} 与 {OUTPUT_END} 之间，标记之间只能有一个 JSON 对象。
6. 不要修改仓库文件，不要运行命令；本次任务只返回策略 JSON。

输入任务：
{task_json}
"""


def extract_strategy(stdout: str) -> dict[str, Any] | None:
    """Extract a strategy from CodeArts JSON, JSONL, or text event output."""
    documents: list[Any] = []
    stripped = (stdout or "").strip()
    if not stripped:
        return None

    try:
        documents.append(json.loads(stripped))
    except json.JSONDecodeError:
        for line in stripped.splitlines():
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        documents.append(stripped)

    for document in documents:
        for candidate in _walk_candidates(document):
            if candidate.get("schema_version") == "strategy.v1":
                return candidate
    return None


def _walk_candidates(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("schema_version") == "strategy.v1":
            yield value
        for child in value.values():
            yield from _walk_candidates(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _walk_candidates(child)
        return
    if not isinstance(value, str):
        return

    for payload in _json_payloads(value):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        yield from _walk_candidates(parsed)


def _json_payloads(text: str) -> Iterator[str]:
    marker = re.search(
        re.escape(OUTPUT_BEGIN) + r"\s*(.*?)\s*" + re.escape(OUTPUT_END),
        text,
        re.DOTALL,
    )
    if marker:
        yield marker.group(1)
    for fenced in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        yield fenced.group(1)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        yield stripped


def validate_strategy(strategy: Any, task: dict[str, Any]) -> list[str]:
    """Validate the untrusted agent result before it reaches the executor."""
    errors: list[str] = []
    if not isinstance(strategy, dict):
        return ["strategy must be an object"]
    if strategy.get("schema_version") != "strategy.v1":
        errors.append("schema_version must be strategy.v1")
    if strategy.get("task_id") != task.get("task_id"):
        errors.append("task_id does not match input task")
    if strategy.get("code") not in (None, ""):
        errors.append("code must be null")

    steps = strategy.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append("steps must be a non-empty array")
        return errors
    if len(steps) > 50:
        errors.append("main step limit exceeded")
        return errors

    seen: set[str] = set()
    all_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            errors.append("each step must be an object")
            continue
        all_steps.append(step)
        recovery = step.get("on_failure")
        if recovery is not None:
            errors.extend(_validate_recovery(recovery, all_steps))

    for step in all_steps:
        prior_step_ids = set(seen)
        step_id = step.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            errors.append("step_id must be a non-empty string")
        elif step_id in seen:
            errors.append(f"duplicate step_id: {step_id}")
        else:
            seen.add(step_id)

        action = step.get("action")
        if action not in ACTION_WHITELIST:
            errors.append(f"action is not allowed: {action}")
            continue
        errors.extend(validate_action_arguments(action, step.get("arguments")))

        for reference in _reference_values(step.get("arguments")):
            source_id = reference[1:].split(".", 1)[0]
            if "." not in reference or source_id not in prior_step_ids:
                errors.append(f"unresolved or forward reference: {reference}")

    target_ids = task.get("target_ids") or []
    destination_id = task.get("destination_id")
    target_id = target_ids[0] if target_ids else None
    detects_target = any(
        step.get("action") == "detect_object"
        and target_id in (step.get("arguments") or {}).values()
        for step in steps
    )
    reaches_destination = any(
        step.get("action") == "move_to_target"
        and destination_id in (step.get("arguments") or {}).values()
        for step in steps
    )
    if target_id and not detects_target:
        errors.append("strategy lost the stable target_id")
    if destination_id and not reaches_destination:
        errors.append("strategy lost the stable destination_id")

    required_actions = [
        "detect_object",
        "move_to_object",
        "grasp",
        "move_to_target",
        "release",
    ]
    cursor = 0
    for step in steps:
        if cursor < len(required_actions) and step.get("action") == required_actions[cursor]:
            cursor += 1
    if cursor != len(required_actions):
        errors.append("pick_and_place actions are missing or out of safe order")
    return errors


def _validate_recovery(recovery: Any, all_steps: list[dict[str, Any]]) -> list[str]:
    if not isinstance(recovery, dict):
        return ["on_failure must be an object"]
    errors: list[str] = []
    attempts = recovery.get("max_attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 3:
        errors.append("recovery max_attempts must be between 1 and 3")
    recovery_steps = recovery.get("steps")
    if not isinstance(recovery_steps, list) or not recovery_steps:
        errors.append("recovery steps must be a non-empty array")
    elif len(recovery_steps) > 10:
        errors.append("recovery step limit exceeded")
    else:
        for step in recovery_steps:
            if isinstance(step, dict):
                all_steps.append(step)
            else:
                errors.append("each recovery step must be an object")
    if recovery.get("on_exhausted") != "stop":
        errors.append("recovery on_exhausted must be stop")
    return errors


def _reference_values(value: Any) -> Iterator[str]:
    if isinstance(value, str) and value.startswith("$"):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _reference_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _reference_values(child)
