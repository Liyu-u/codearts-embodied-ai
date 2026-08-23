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
import time
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
REVIEW_BEGIN = "STRATEGY_REVIEW_BEGIN"
REVIEW_END = "STRATEGY_REVIEW_END"
REPO_ROOT = Path(__file__).resolve().parents[2]
# Local CODEARTS_* values are loaded explicitly by application entrypoints.
# Importing this client must remain side-effect free for offline tests.
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
        self.timeout_s = timeout_s or _positive_int_env("CODEARTS_STRATEGY_TIMEOUT_S", 60)
        self.max_retries = _bounded_int_env(
            "CODEARTS_STRATEGY_MAX_RETRIES", default=1, maximum=2
        )
        self.retry_backoff_s = _nonnegative_float_env(
            "CODEARTS_STRATEGY_RETRY_BACKOFF_S", default=0.2
        )
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
            "source": "codearts_agent",
            "transport": "codearts-cli",
            "agent": self.agent or None,
            "model": self.model or None,
            "request_id": str(uuid4()),
            "run_id": task.get("task_id"),
            "fallback": False,
            "validation": {"passed": False, "errors": []},
            "_started_monotonic": time.monotonic(),
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

        # Demo HTTP uses a threaded server; serialize provider calls so two
        # requests cannot contend for one local CodeArts session. Transport
        # failures get at most one bounded retry; validation failures never
        # trigger a second remote plan.
        with _CLI_LOCK:
            completed, transport_error = self._run_cli_with_retries(command, trace)
        if transport_error:
            return _failure(transport_error, trace)

        trace["exit_code"] = completed.returncode
        if completed.returncode != 0:
            detail = _compact_error(completed.stderr or completed.stdout)
            return _failure(f"CODEARTS_CLI_FAILED:{detail}", trace)

        candidate = extract_strategy(completed.stdout)
        if candidate is None:
            provider_error = extract_provider_error(completed.stdout)
            if provider_error:
                return _failure(f"CODEARTS_PROVIDER_ERROR:{provider_error}", trace)
            return _failure("CODEARTS_OUTPUT_MISSING_STRATEGY", trace)

        errors = validate_strategy(candidate, task)
        if errors == ["task_id does not match input task"] and _has_explicit_task_id(candidate):
            candidate = dict(candidate)
            candidate["task_id"] = task["task_id"]
            trace["task_id_bound_locally"] = True
            trace["binding_reason"] = "provider_task_id_mismatch_only"
            errors = validate_strategy(candidate, task)

        # A zero-step envelope is a transient provider formatting failure in
        # practice.  Spend only the bounded B retry budget on that shape;
        # semantic/action validation errors are never retried.
        if errors and _is_retryable_strategy_validation(errors) and self.max_retries:
            trace["validation_retry_count"] = 1
            time.sleep(min(2.0, self.retry_backoff_s))
            with _CLI_LOCK:
                retry_completed, retry_error = self._run_cli_with_retries(command, trace)
            if retry_error:
                return _failure(retry_error, trace)
            trace["exit_code"] = retry_completed.returncode
            if retry_completed.returncode != 0:
                detail = _compact_error(retry_completed.stderr or retry_completed.stdout)
                return _failure(f"CODEARTS_CLI_FAILED:{detail}", trace)
            candidate = extract_strategy(retry_completed.stdout)
            if candidate is None:
                return _failure("CODEARTS_OUTPUT_MISSING_STRATEGY", trace)
            errors = validate_strategy(candidate, task)
            if errors == ["task_id does not match input task"] and _has_explicit_task_id(candidate):
                candidate = dict(candidate)
                candidate["task_id"] = task["task_id"]
                trace["task_id_bound_locally"] = True
                trace["binding_reason"] = "provider_task_id_mismatch_only"
                errors = validate_strategy(candidate, task)

        if errors:
            trace["validation"] = {"passed": False, "errors": list(errors)}
            return _failure("CODEARTS_STRATEGY_REJECTED:" + errors[0], trace)

        strategy = _ensure_bounded_grasp_recovery(candidate)
        strategy["code"] = None
        # Revalidate the locally normalized recovery branch.  This is the
        # shared C/D contract: one grasp retry is allowed, exhaustion enters
        # C SAFE_STOP and D must not propose another automatic retry.
        normalized_errors = validate_strategy(strategy, task)
        if normalized_errors:
            trace["validation"] = {
                "passed": False,
                "errors": list(normalized_errors),
            }
            return _failure(
                "CODEARTS_STRATEGY_REJECTED:" + normalized_errors[0], trace
            )
        trace["validation"] = {"passed": True, "errors": []}
        trace["recovery_normalized"] = strategy != candidate
        _finalize_trace(trace)
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

    def review(
        self,
        task: dict[str, Any],
        strategy: dict[str, Any],
        *,
        round_no: int = 1,
    ) -> dict[str, Any]:
        """Ask an independent CodeArts turn to review a candidate strategy.

        The reviewer is deliberately a separate call from ``generate``.  It
        cannot mutate the candidate and its answer is accepted only when it
        is a small, explicit PASS document.  The local contract validator
        remains the final authority even after a PASS.
        """
        resolved = self._resolve_executable()
        trace = {
            "provider": "huaweicloud-codearts-agent",
            "source": "codearts_agent",
            "transport": "codearts-cli",
            "agent": self.agent or None,
            "model": self.model or None,
            "role": "critic",
            "round": round_no,
            "request_id": str(uuid4()),
            "run_id": task.get("task_id"),
            "fallback": False,
            "validation": {"passed": False, "errors": []},
            "_started_monotonic": time.monotonic(),
        }
        if resolved is None:
            return _review_failure("CODEARTS_CLI_NOT_FOUND", trace)

        command = [resolved, "run", "--format", "json"]
        if _truthy_env("CODEARTS_CLI_PURE"):
            command.insert(2, "--pure")
        if self.agent:
            command.extend(["--agent", self.agent])
        if self.model:
            command.extend(["--model", self.model])
        command.extend(
            [
                "--title",
                f"robot-strategy-critic-{task.get('task_id', 'unknown')}-r{round_no}-{uuid4().hex[:10]}",
                _build_review_prompt(task, strategy, round_no),
            ]
        )
        with _CLI_LOCK:
            completed, transport_error = self._run_cli_with_retries(command, trace)
        if transport_error:
            return _review_failure(transport_error, trace)

        trace["exit_code"] = completed.returncode
        if completed.returncode != 0:
            detail = _compact_error(completed.stderr or completed.stdout)
            return _review_failure(f"CODEARTS_CLI_FAILED:{detail}", trace)

        review = extract_review(completed.stdout)
        if review is None:
            return _review_failure("CODEARTS_REVIEW_OUTPUT_MISSING", trace)
        errors = validate_review(review)
        if errors:
            return _review_failure("CODEARTS_REVIEW_REJECTED:" + errors[0], trace)
        if review["status"] != "PASS":
            return _review_failure(
                "CODEARTS_REVIEW_REJECTED:" + review["status"], trace
            )
        trace["validation"] = {"passed": True, "errors": []}
        trace["status"] = "PASS"
        _finalize_trace(trace)
        return {"success": True, "review": review, "error": None, "trace": trace}

    def _run_cli_with_retries(
        self, command: list[str], trace: dict[str, Any]
    ) -> tuple[Any | None, str | None]:
        """Run one bounded transport retry loop for generate/review.

        Only launch, timeout and transient non-zero exit failures are retried.
        A malformed or semantically unsafe response is returned to the caller
        immediately so the provider cannot amplify a bad answer.
        """
        attempts = self.max_retries + 1
        last_error = "CODEARTS_CLI_FAILED:unknown error"
        for attempt in range(1, attempts + 1):
            trace["attempt_count"] = attempt
            try:
                completed = self._run_cli(command)
            except subprocess.TimeoutExpired:
                last_error = "CODEARTS_CLI_TIMEOUT"
                retryable = True
            except OSError as exc:
                last_error = f"CODEARTS_CLI_START_FAILED:{exc}"
                retryable = True
            else:
                trace["exit_code"] = completed.returncode
                if completed.returncode == 0:
                    return completed, None
                detail = _compact_error(completed.stderr or completed.stdout)
                last_error = f"CODEARTS_CLI_FAILED:{detail}"
                retryable = _is_transient_cli_error(detail)

            if not retryable or attempt >= attempts:
                break
            trace["retry_count"] = attempt
            time.sleep(min(2.0, self.retry_backoff_s * attempt))
        return None, last_error

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
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _bounded_int_env(name: str, *, default: int, maximum: int) -> int:
    return min(maximum, _positive_int_env(name, default))


def _nonnegative_float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _is_transient_cli_error(detail: str) -> bool:
    text = str(detail or "").lower()
    return any(token in text for token in (
        "timeout", "timed out", "temporarily unavailable", "connection",
        "econn", "429", "500", "502", "503", "504", "rate limit", "busy",
    ))


def _is_retryable_strategy_validation(errors: list[str]) -> bool:
    allowed = {"steps must be a non-empty array", "task_id does not match input task"}
    return bool(errors) and set(errors).issubset(allowed) and "steps must be a non-empty array" in errors


def _truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _ensure_bounded_grasp_recovery(candidate: dict[str, Any]) -> dict[str, Any]:
    """Add the canonical one-retry grasp branch when CodeArts omits it.

    CodeArts is authoritative for the plan shape, but recovery semantics are
    owned by the local B/C/D contract.  The normalization is deliberately
    narrow: it touches only a missing grasp recovery branch and never adds
    actions or changes IDs.
    """
    strategy = json.loads(json.dumps(candidate, ensure_ascii=False))
    changed = False
    for step in strategy.get("steps") or []:
        if not isinstance(step, dict) or step.get("action") != "grasp":
            continue
        if step.get("on_failure") is not None:
            continue
        arguments = dict(step.get("arguments") or {})
        if not arguments.get("object_id"):
            continue
        step_id = str(step.get("step_id") or "grasp")
        step["on_failure"] = {
            "max_attempts": 1,
            "steps": [{
                "step_id": f"{step_id}-retry",
                "action": "grasp",
                "arguments": arguments,
            }],
            "on_exhausted": "stop",
        }
        changed = True
        break
    return strategy


def _failure(error: str, trace: dict[str, Any]) -> dict[str, Any]:
    validation = trace.get("validation")
    if not isinstance(validation, dict) or not validation.get("errors"):
        trace["validation"] = {"passed": False, "errors": [error]}
    _finalize_trace(trace)
    return {"success": False, "strategy": None, "error": error, "trace": trace}


def _review_failure(error: str, trace: dict[str, Any]) -> dict[str, Any]:
    validation = trace.get("validation")
    if not isinstance(validation, dict) or not validation.get("errors"):
        trace["validation"] = {"passed": False, "errors": [error]}
    _finalize_trace(trace)
    return {"success": False, "review": None, "error": error, "trace": trace}


def _finalize_trace(trace: dict[str, Any]) -> None:
    started = trace.pop("_started_monotonic", None)
    if isinstance(started, (int, float)):
        trace["latency_ms"] = round(max(0.0, (time.monotonic() - started) * 1000), 3)


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
    """Build an action-specific, bounded prompt for the CodeArts agent.

    A already normalizes user intent to the open task actions.  The prompt
    must preserve that action instead of silently asking every task for a
    five-step pick-and-place plan; the local validator remains authoritative.
    """
    task_id = task.get("task_id")
    target_ids = task.get("target_ids") or []
    destination_id = task.get("destination_id")
    target_id = target_ids[0] if target_ids else None
    action = str(task.get("action") or "pick_and_place")
    detect_id = f"{task_id}-detect"
    object_reference = f"${detect_id}.object_id"
    steps = [
        {
            "step_id": detect_id,
            "action": "detect_object",
            "arguments": {"object_id": target_id},
        },
        {
            "step_id": f"{task_id}-approach",
            "action": "move_to_object",
            "arguments": {"object_id": object_reference},
        },
        {
            "step_id": f"{task_id}-grasp",
            "action": "grasp",
            "arguments": {"object_id": object_reference},
        },
    ]
    if action not in {"pick", "grasp"}:
        move_arguments: dict[str, Any] = {"destination_id": destination_id}
        if action == "stack":
            move_arguments["placement_mode"] = "stack_on"
        steps.extend(
            [
                {
                    "step_id": f"{task_id}-target",
                    "action": "move_to_target",
                    "arguments": move_arguments,
                },
                {
                    "step_id": f"{task_id}-release",
                    "action": "release",
                    "arguments": {},
                },
            ]
        )

    contract = {
        "schema_version": "strategy.v1",
        "task_id": task_id,
        "steps": steps,
        "code": None,
    }
    expected_actions = [step["action"] for step in steps]
    stack_rule = (
        "stack 必须在 move_to_target.arguments 中保留 placement_mode=stack_on。"
        if action == "stack"
        else ""
    )
    prompt = f"""只返回一个策略 JSON，不要调用工具、读取文件或输出解释。
任务动作={action}; task_id={task_id}; target_id={target_id}; destination_id={destination_id}
必须放在 {OUTPUT_BEGIN} 和 {OUTPUT_END} 之间，且严格使用此结构：
{json.dumps(contract, ensure_ascii=False, separators=(",", ":"))}
本任务只允许动作序列：{expected_actions}。{stack_rule}
字段名必须是 step_id/action/arguments；不得输出 target_id、target_ids 或 object_id 顶层字段；不得改变任务中的稳定 ID；pick/grasp 不得添加搬运、放置或释放步骤。
"""
    # The Windows CLI treats embedded newlines in a positional message as
    # argument boundaries, so send the exact contract as one argument.
    return " ".join(line.strip() for line in prompt.splitlines() if line.strip())


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


def _build_review_prompt(
    task: dict[str, Any], strategy: dict[str, Any], round_no: int
) -> str:
    task_json = json.dumps(task, ensure_ascii=False, separators=(",", ":"))
    strategy_json = json.dumps(strategy, ensure_ascii=False, separators=(",", ":"))
    return f"""只返回一个审查 JSON，不调用工具、读取文件或修改候选策略。
这是第 {round_no} 轮独立安全审查。检查动作白名单、稳定 ID、步骤顺序、引用、恢复限制和 code=null。
任务：{task_json}
候选策略：{strategy_json}
必须把唯一 JSON 放在 {REVIEW_BEGIN} 和 {REVIEW_END} 之间，严格使用：
{{"status":"PASS","issues":[],"risk_level":"LOW"}}
如果有问题，status 只能是 REPAIR_REQUIRED 或 BLOCK，issues 必须给出简短原因；不要输出解释或 Markdown。
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


def extract_review(stdout: str) -> dict[str, Any] | None:
    """Extract the small review object from CodeArts event/JSON output."""
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
        for candidate in _walk_objects(document):
            if "status" in candidate and "issues" in candidate:
                return candidate
    return None


def extract_provider_error(stdout: str) -> str | None:
    """Extract a structured CodeArts error even when the process exits 0.

    CodeArts emits provider/network failures as JSON events with a normal
    process exit code.  Treating those events as an empty model response hides
    the actionable cause and makes auto-mode diagnostics misleading.
    """
    stripped = (stdout or "").strip()
    if not stripped:
        return None
    documents: list[Any] = []
    try:
        documents.append(json.loads(stripped))
    except json.JSONDecodeError:
        for line in stripped.splitlines():
            try:
                documents.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    for document in documents:
        for candidate in _walk_objects(document):
            if candidate.get("type") != "error":
                continue
            error = candidate.get("error")
            if isinstance(error, dict):
                data = error.get("data")
                if isinstance(data, dict) and data.get("message"):
                    return " ".join(str(data["message"]).split())[:300]
                if error.get("message"):
                    return " ".join(str(error["message"]).split())[:300]
            if isinstance(error, str) and error.strip():
                return " ".join(error.split())[:300]
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


def _walk_objects(value: Any) -> Iterator[dict[str, Any]]:
    """Yield every nested object, including objects wrapped in text events."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_objects(child)
        return
    if isinstance(value, list):
        for child in value:
            yield from _walk_objects(child)
        return
    if not isinstance(value, str):
        return
    for payload in _json_payloads_with_markers(value, REVIEW_BEGIN, REVIEW_END):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        yield from _walk_objects(parsed)


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


def _json_payloads_with_markers(text: str, begin: str, end: str) -> Iterator[str]:
    marker = re.search(
        re.escape(begin) + r"\s*(.*?)\s*" + re.escape(end),
        text,
        re.DOTALL,
    )
    if marker:
        yield marker.group(1)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        yield stripped


def validate_review(review: Any) -> list[str]:
    """Validate the critic contract; the critic cannot approve mutations."""
    if not isinstance(review, dict):
        return ["review must be an object"]
    if review.get("status") not in {"PASS", "REPAIR_REQUIRED", "BLOCK"}:
        return ["status must be PASS, REPAIR_REQUIRED or BLOCK"]
    issues = review.get("issues")
    if not isinstance(issues, list) or not all(
        isinstance(item, str) and item.strip() for item in issues
    ):
        return ["issues must be a string array"]
    if review.get("risk_level") not in {"LOW", "MEDIUM", "HIGH"}:
        return ["risk_level must be LOW, MEDIUM or HIGH"]
    if review["status"] == "PASS" and issues:
        return ["PASS review cannot contain issues"]
    return []


def validate_strategy(strategy: Any, task: dict[str, Any]) -> list[str]:
    """Validate untrusted agent output against the requested task action."""
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

    task_action = str(task.get("action") or "pick_and_place")
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

    actual_actions = [step.get("action") for step in steps]
    if task_action in {"pick", "grasp"} and any(
        action in {"move_to_target", "release"} for action in actual_actions
    ):
        errors.append(f"{task_action} strategy must not include destination actions")
    if task_action == "stack":
        stack_moves = [
            step for step in steps if step.get("action") == "move_to_target"
        ]
        if not any(
            (step.get("arguments") or {}).get("placement_mode") == "stack_on"
            for step in stack_moves
        ):
            errors.append("stack move_to_target must use placement_mode=stack_on")

    required_actions = _required_actions_for_task(task_action)
    cursor = 0
    for step in steps:
        if cursor < len(required_actions) and step.get("action") == required_actions[cursor]:
            cursor += 1
    if cursor != len(required_actions):
        errors.append(f"{task_action} actions are missing or out of safe order")
    return errors


def _required_actions_for_task(task_action: str) -> list[str]:
    """Return the safe C primitive sequence for an A task action."""
    if task_action in {"pick", "grasp"}:
        return ["detect_object", "move_to_object", "grasp"]
    return [
        "detect_object",
        "move_to_object",
        "grasp",
        "move_to_target",
        "release",
    ]

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
