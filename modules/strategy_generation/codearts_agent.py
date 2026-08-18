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
import tempfile
import threading
from uuid import uuid4
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
_CLI_LOCK = threading.Lock()


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
        if _truthy_env("CODEARTS_CLI_PURE"):
            command.insert(2, "--pure")
        if self.agent:
            command.extend(["--agent", self.agent])
        if self.model:
            command.extend(["--model", self.model])
        # CodeArts derives a default session title from the beginning of the
        # prompt.  Our prompts intentionally share that prefix; without an
        # explicit unique title, consecutive non-interactive calls can attach
        # to the same local session and stall on Windows.
        command.extend(
            [
                "--title",
                f"robot-strategy-{task.get('task_id', 'unknown')}-{uuid4().hex[:10]}",
            ]
        )
        command.append(_build_prompt(task))

        try:
            # Demo HTTP uses a threaded server; serialize provider calls so
            # two requests cannot contend for one local CodeArts session.
            with _CLI_LOCK:
                completed = self._run_cli(command)
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
        if errors == ["task_id does not match input task"] and _has_explicit_task_id(candidate):
            # ``task_id`` is transport metadata, not a planning decision.  A
            # general-purpose agent can still copy it from a Skill example;
            # spending a second remote request to repair that single field
            # makes the real path unnecessarily slow and prone to timeouts.
            # Bind the already validated candidate to the original task in an
            # explicit local compilation step.  No other field is normalized:
            # target/destination IDs, action order, references and recovery
            # policy are validated again after the binding.
            candidate = dict(candidate)
            candidate["task_id"] = task["task_id"]
            trace["task_id_bound_locally"] = True
            trace["binding_reason"] = "provider_task_id_mismatch_only"
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

    def _run_cli(self, command: list[str]) -> Any:
        """Run the CLI without pipe back-pressure on Windows.

        CodeArts can emit a multi-event JSON stream and may leave a helper
        process attached to stdout for a short period.  Capturing that stream
        with ``PIPE`` can deadlock the Python parent on Windows.  The real
        runner therefore captures into temporary files; injected test runners
        keep the lightweight in-memory path.
        """
        kwargs = {
            "cwd": str(REPO_ROOT),
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": self.timeout_s,
            "check": False,
        }
        if self._runner is not subprocess.run:
            return self._runner(command, capture_output=True, **kwargs)

        with (
            tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file,
            tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stderr_file,
        ):
            completed = self._runner(
                command,
                stdout=stdout_file,
                stderr=stderr_file,
                **kwargs,
            )
            stdout_file.seek(0)
            stderr_file.seek(0)
            completed.stdout = stdout_file.read()
            completed.stderr = stderr_file.read()
            return completed

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


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _failure(error: str, trace: dict[str, Any]) -> dict[str, Any]:
    return {"success": False, "strategy": None, "error": error, "trace": trace}


def _has_explicit_task_id(strategy: dict[str, Any]) -> bool:
    """Return whether a provider supplied a concrete ID that can be bound.

    Missing metadata is not eligible for binding: the provider must still
    return a complete strategy object.  Only a concrete, mismatching ID is
    treated as transport metadata; all semantic fields remain untrusted.
    """
    value = strategy.get("task_id")
    return isinstance(value, str) and bool(value.strip())


def _compact_error(value: str) -> str:
    compact = " ".join((value or "unknown error").split())
    return compact[:300]


def _build_prompt(task: dict[str, Any]) -> str:
    task_id = task.get("task_id")
    target_ids = task.get("target_ids") or []
    destination_id = task.get("destination_id")
    target_id = target_ids[0] if target_ids else None
    # Keep this prompt deliberately compact.  The CLI is non-interactive and
    # long planning instructions make some CodeArts models enter tool/reasoning
    # loops instead of returning the required JSON event.
    return f"""只返回一个策略 JSON，不要调用工具、读取文件或输出解释。
任务：task_id={task_id}; target_id={target_id}; destination_id={destination_id}
必须放在 {OUTPUT_BEGIN} 和 {OUTPUT_END} 之间，且严格使用此结构：
{{"schema_version":"strategy.v1","task_id":"{task_id}","steps":[
{{"step_id":"detect","action":"detect_object","arguments":{{"object_id":"{target_id}"}}}},
{{"step_id":"approach","action":"move_to_object","arguments":{{"object_id":"$detect.object_id"}}}},
{{"step_id":"grasp","action":"grasp","arguments":{{"object_id":"$detect.object_id"}}}},
{{"step_id":"target","action":"move_to_target","arguments":{{"destination_id":"{destination_id}"}}}},
{{"step_id":"release","action":"release","arguments":{{}}}}],"code":null}}
只允许这五个 action；字段名必须是 step_id/action/arguments；不得输出 target_id、target_ids 或 object_id 顶层字段；不得改变三个 ID。
"""


def _build_repair_prompt(task: dict[str, Any], candidate: dict[str, Any]) -> str:
    """Ask the provider to repair one rejected strategy without broadening it."""
    task_json = json.dumps(task, ensure_ascii=False, indent=2)
    candidate_json = json.dumps(candidate, ensure_ascii=False, indent=2)
    return f"""请修复下面已经生成但未通过本地契约校验的 strategy.v1。

只允许修复 task_id：必须把 strategy.task_id 设置为输入任务的精确值 `{task.get('task_id')}`。
保留所有步骤、动作、参数、稳定目标 ID 和目的地 ID，不得引入新动作或新 ID。
code 必须为 null。不要输出解释、Markdown 或可执行代码。
仍然必须使用 {OUTPUT_BEGIN} 与 {OUTPUT_END} 标记，标记之间只能有一个 JSON 对象。

输入任务：
{task_json}

待修复策略：
{candidate_json}
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
