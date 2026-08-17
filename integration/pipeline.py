"""统一联调总线。

总线只负责模块之间的编排，不实现 A/B/C/D 的业务逻辑：

    感知 → 意图 → 策略 → C 执行 → D 检查/修复 → C 重试

重试是有上限且受安全条件约束的，避免错误 patch 导致无限执行循环。
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Optional

from integration.contract_validation import validate_contract


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_RETRIES = 2


def run_pipeline(
    perception: dict,
    instruction: str,
    adapters: dict,
    *,
    engine: Optional[str] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    request_id: Optional[str] = None,
) -> dict:
    """运行 A→B→C→D 的一次或多次受控联调。

    Args:
        perception: C/感知模块输出的 ``perception.v1`` 场景。
        instruction: 用户自然语言指令。
        adapters: 注入 ``intent``/``strategy``/``executor``，以及可选的
            ``tracecoder`` 适配器。
        engine: 可选的 A 引擎选择（``rule``/``llm``/``hybrid``）。不传时
            保留 A 自己的 ``RIA_PLANNER_ENGINE`` 环境配置。
        max_retries: D 产生 patch 后允许重新交给 C 的最大次数；默认 2。
        request_id: 可选的本次请求 ID。提供后会写入 task.v1，避免同一场景
            的多条指令共用 scene_id 作为 task_id。

    返回值继续保留原有的 task/strategy/execution/feedback 字段，并增加：
        ``initial_strategy``：B 最初生成的策略；
        ``attempts``：每次交给 C 的策略、执行结果和 D 反馈；
        ``retry_count``：实际重试次数；
        ``stop_reason``：总线停止循环的原因。
    """

    _validate_retry_limit(max_retries)
    intent_input = {"instruction": instruction, "perception": perception}
    if engine is not None:
        intent_input["engine"] = engine
    if request_id is not None:
        intent_input["task_id"] = str(request_id)

    task = adapters["intent"].run(intent_input)
    if task.get("status") != "READY":
        return {"status": "BLOCKED", "task": task}

    initial_strategy = adapters["strategy"].run(task)
    if initial_strategy.get("blocked") or initial_strategy.get("success") is False:
        return {
            "status": "BLOCKED",
            "task": task,
            "strategy": initial_strategy,
        }

    executor = adapters["executor"]
    feedback_adapter = adapters.get("tracecoder")
    current_strategy = initial_strategy
    execution: Optional[dict] = None
    feedback_out: Optional[dict] = None
    attempts: list[dict] = []
    retry_count = 0
    stop_reason = "NO_TRACE_CODER"

    while True:
        execution = executor.run(current_strategy)
        feedback_out = None
        if feedback_adapter is not None:
            feedback_out = feedback_adapter.run(
                {
                    "task": task,
                    "strategy": current_strategy,
                    "execution": execution,
                    "perception": perception,
                }
            )

        attempts.append(
            {
                "attempt": len(attempts) + 1,
                "retry_index": retry_count,
                "strategy": deepcopy(current_strategy),
                "execution": deepcopy(execution),
                "feedback": deepcopy(feedback_out),
            }
        )

        if feedback_adapter is None:
            stop_reason = "NO_TRACE_CODER"
            break
        if execution.get("status") == "SUCCEEDED":
            stop_reason = "EXECUTION_SUCCEEDED"
            break
        if execution.get("status") == "SAFE_STOP":
            stop_reason = "SAFETY_STOP"
            break
        if not isinstance(feedback_out, dict) or not feedback_out.get("retryable"):
            stop_reason = "FEEDBACK_NOT_RETRYABLE"
            break
        if retry_count >= max_retries:
            stop_reason = "MAX_RETRIES_EXCEEDED"
            break

        patch = feedback_out.get("patch")
        patch_error = _validate_patch(patch, task_id=task.get("task_id"))
        if patch_error:
            stop_reason = patch_error
            break
        if _same_strategy(patch, current_strategy):
            stop_reason = "PATCH_UNCHANGED"
            break

        current_strategy = patch
        retry_count += 1

    # ``execution`` is guaranteed to be populated by the loop.  Keeping the
    # guard makes the boundary explicit for static type checkers and protects
    # callers if a custom executor violates the adapter contract.
    if execution is None:  # pragma: no cover - defensive boundary
        raise RuntimeError("executor did not return an execution result")

    return {
        "status": execution.get("status"),
        "task": task,
        # This is the strategy that produced the final execution evidence.
        "strategy": current_strategy,
        "initial_strategy": initial_strategy,
        "execution": execution,
        "feedback": feedback_out,
        "attempts": attempts,
        "retry_count": retry_count,
        "stop_reason": stop_reason,
    }


def _validate_retry_limit(max_retries: int) -> None:
    if isinstance(max_retries, bool) or not isinstance(max_retries, int):
        raise ValueError("max_retries must be an integer")
    if max_retries < 0 or max_retries > 10:
        raise ValueError("max_retries must be between 0 and 10")


def _validate_patch(patch: Any, *, task_id: Any) -> Optional[str]:
    """Validate D's patch before allowing it to reach C."""

    if not isinstance(patch, dict):
        return "PATCH_MISSING"
    errors = validate_contract(patch, "strategy.v1")
    if errors:
        return "PATCH_INVALID:" + "; ".join(errors)
    if task_id and patch.get("task_id") != task_id:
        return "PATCH_TASK_ID_MISMATCH"
    if patch.get("code") not in (None, ""):
        return "PATCH_CODE_NOT_ALLOWED"
    return None


def _same_strategy(left: dict, right: dict) -> bool:
    """Compare execution-relevant strategy fields, ignoring adapter metadata."""

    def execution_shape(strategy: dict) -> dict:
        return {
            "schema_version": strategy.get("schema_version"),
            "task_id": strategy.get("task_id"),
            "steps": strategy.get("steps"),
            "code": strategy.get("code") or None,
        }

    return execution_shape(left) == execution_shape(right)
