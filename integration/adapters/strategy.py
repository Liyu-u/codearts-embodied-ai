"""
策略生成适配器 — B 角色 (冯海)

职责：
1. 接收标准 task.v1 JSON
2. 通过 CodeArts CLI 调用代码智能体生成结构化原子策略
3. 严格校验智能体输出，并按配置安全回退或阻断
4. 输出不含可执行 Python 代码的标准 strategy.v1 JSON

联调仓库统一接口：
    run(input_json: dict) -> dict
    health() -> dict
"""

import os
from typing import Any, List

from modules.strategy_generation.codearts_agent import CodeArtsStrategyClient
from integration.strategy_policy import (
    normalize_capabilities,
    validate_strategy,
)


# Configuration is supplied by the application entrypoint or process environment.
# Do not mutate os.environ while importing the adapter; this keeps CI deterministic.


# These are the task-level actions for which A's grounded constraints can be
# lowered to the primitive source that C actually exposes.  This is a
# deliberately small allow-list: understanding an action in A is not enough
# to make it executable.
SUPPORTED_PRIMITIVE_TASK_ACTIONS = {
    "pick",
    "grasp",
    "pick_and_place",
    "place",
    "transfer",
    "fetch",
    "stack",
}

# CodeArts receives the same open action set as the local primitive planner.
# Each action is still constrained by the action-specific prompt and validator.
CODEARTS_TASK_ACTIONS = frozenset(SUPPORTED_PRIMITIVE_TASK_ACTIONS)


def run(input_json: dict) -> dict:
    """
    接收标准 task.v1，输出标准 strategy.v1。

    输入 task.v1 示例：
        {
            "schema_version": "task.v1",
            "task_id": "task-001",
            "action": "pick_and_place",
            "target_ids": ["obj-001"],
            "destination_id": null,
            "status": "READY",
            ...
        }

    输出 strategy.v1 示例：
        {
            "schema_version": "strategy.v1",
            "task_id": "task-001",
            "steps": [...],
            "code": null
        }
    """
    if not isinstance(input_json, dict):
        return _blocked_result(
            "unknown",
            ["task.v1 输入必须是 JSON 对象"],
            "输入格式错误，未生成策略",
        )

    # 先校验任务外壳，避免空输入或错误版本被静默当成默认抓取任务。
    envelope_errors = _validate_task_envelope(input_json)
    if envelope_errors:
        return _blocked_result(
            input_json.get("task_id", "unknown"),
            envelope_errors,
            "task.v1 输入校验失败，未生成策略",
        )

    # 如果 task 状态不是 READY，直接阻断，不生成策略。
    status = input_json["status"]
    if status != "READY":
        reasons = _normalize_reasons(
            input_json.get("blocking_reasons"),
            f"task status is {status}",
        )
        return _blocked_result(
            input_json["task_id"],
            reasons,
            "任务状态未就绪，未生成策略",
        )

    action = input_json["action"]
    capabilities = normalize_capabilities(input_json.get("capabilities"))
    if action not in SUPPORTED_PRIMITIVE_TASK_ACTIONS:
        return _blocked_result(
            input_json["task_id"],
            [f"UNSUPPORTED_ACTION:{action}"],
            "当前动作没有 A 约束、B 策略和 C 执行源共同覆盖",
        )

    ready_errors = _validate_ready_task(input_json, action)
    if ready_errors:
        return _blocked_result(
            input_json["task_id"],
            ready_errors,
            "READY 任务缺少生成策略所需信息",
        )

    task_id = input_json["task_id"]
    target_id = input_json["target_ids"][0]
    destination_id = input_json.get("destination_id")
    codearts_mode = _codearts_mode()
    codearts_policy = _codearts_policy()
    if action not in CODEARTS_TASK_ACTIONS and codearts_mode == "required":
        return _blocked_result(
            task_id,
            [f"CODEARTS_ACTION_TEMPLATE_NOT_SUPPORTED:{action}"],
            "当前 CodeArts 策略模板尚未覆盖该动作，未降级执行",
        )
    if action in CODEARTS_TASK_ACTIONS and codearts_mode != "off":
        client = CodeArtsStrategyClient()
        result = client.generate(input_json)
        if result["success"]:
            quality_result = _run_codearts_critics(
                client,
                input_json,
                result["strategy"],
                codearts_policy,
            )
            if quality_result["success"]:
                strategy = result["strategy"]
                strategy["strategy_policy"] = quality_result["policy"]
                if quality_result["critics"]:
                    strategy["critics"] = quality_result["critics"]
                    strategy.setdefault("provenance", {})["critics"] = quality_result[
                        "critics"
                    ]
                policy_errors = validate_strategy(
                    strategy,
                    task=input_json,
                    capabilities=capabilities,
                )["errors"]
                if policy_errors:
                    return _blocked_result(
                        task_id,
                        policy_errors,
                        "CodeArts 策略未通过共享安全校验",
                    )
                return strategy
            result = {
                "success": False,
                "strategy": None,
                "error": quality_result["error"],
                "trace": quality_result["trace"],
            }
        if codearts_mode == "required":
            blocked = _blocked_result(
                task_id,
                [result["error"]],
                "CodeArts 智能体未能生成通过安全校验的策略",
            )
            blocked.update(
                {
                    "mode": "codearts_blocked",
                    "validation": {"passed": False, "errors": [result["error"]]},
                    "provenance": result["trace"],
                }
            )
            return blocked
        return _local_pick_and_place_strategy(
            task_id,
            target_id,
            destination_id,
            action=action,
            mode="primitive_plan_fallback",
            provider_error=result["error"],
            provenance=result["trace"],
            capabilities=capabilities,
        )

    return _local_pick_and_place_strategy(
        task_id,
        target_id,
        destination_id,
        action=action,
        mode=("primitive_plan" if action == "pick_and_place" else "primitive_plan_extended"),
        capabilities=capabilities,
    )


def _local_pick_and_place_strategy(
    task_id: str,
    target_id: str,
    destination_id: str | None,
    *,
    action: str = "pick_and_place",
    mode: str,
    provider_error: str | None = None,
    provenance: dict | None = None,
    capabilities: dict | None = None,
) -> dict:
    output = {
        "schema_version": "strategy.v1",
        "task_id": task_id,
        "steps": _build_primitive_steps(
            task_id,
            action,
            target_id,
            destination_id,
        ),
        # B 内部仍可保留模板/LLM 代码生成能力，但公开联调边界禁止
        # 把代码交给 C；C 也会对非空 code 做第二次拒绝。
        "code": None,
        "success": True,
        "blocked": False,
        "message": (
            "CodeArts 不可用，已安全回退到本地五步原子策略"
            if provider_error
            else _strategy_message(action)
        ),
        "mode": mode,
        "validation": {"passed": True, "errors": []},
    }
    if provider_error:
        output["provider_error"] = provider_error
    output["provenance"] = dict(
        provenance
        or {
            "source": "local_rules",
            "agent": "primitive_planner",
            "model": None,
            "request_id": task_id,
            "run_id": task_id,
            "latency_ms": 0,
            "fallback": bool(provider_error),
            "validation": {"passed": True, "errors": []},
        }
    )
    output["provenance"].setdefault("source", "codearts_agent")
    if provider_error:
        output["provenance"]["provider_attempted"] = output["provenance"].get("provider")
        output["provenance"]["source"] = "local_rules"
        output["provenance"]["fallback"] = True
    policy = validate_strategy(
        output,
        task={"task_id": task_id},
        capabilities=normalize_capabilities(capabilities),
    )
    if not policy["passed"]:
        return _blocked_result(
            task_id,
            policy["errors"],
            "本地策略未通过共享安全校验",
        )
    return output


def health() -> dict:
    """返回模块健康状态。"""
    try:
        mode = _codearts_mode()
        policy = _codearts_policy()
        availability = CodeArtsStrategyClient().availability()
        ok = availability["available"] or mode != "required"
        return {
            "module": "strategy_generation",
            "role": "B",
            "owner": "冯海",
            "healthy": ok,
            "message": (
                "CodeArts CLI 可用"
                if availability["available"]
                else (
                    "CodeArts CLI 不可用；required 模式将阻断策略生成"
                    if mode == "required"
                    else "CodeArts CLI 不可用；auto 模式将使用本地安全回退"
                )
            ),
            "codearts_mode": mode,
            "codearts_policy": policy,
            "codearts": availability,
        }
    except Exception as e:
        return {
            "module": "strategy_generation",
            "role": "B",
            "owner": "冯海",
            "healthy": False,
            "message": f"健康检查失败: {e}",
        }


# ============================================================
# 内部转换函数
# ============================================================


def _codearts_mode() -> str:
    """Return off/auto/required; invalid values fail safe to auto."""
    value = os.environ.get("CODEARTS_STRATEGY_MODE", "auto").strip().lower()
    return value if value in {"off", "auto", "required"} else "auto"


def _codearts_policy() -> str:
    """Return planner/planner_critic/planner_critic_double policy.

    The names are intentionally user-facing and stable: ``planner`` is the
    low-latency default, ``quality`` adds one independent critic, and ``max``
    requires two independent critic passes before execution.
    """
    value = os.environ.get("CODEARTS_STRATEGY_POLICY", "planner").strip().lower()
    return value if value in {"planner", "quality", "max"} else "planner"


def _run_codearts_critics(
    client: CodeArtsStrategyClient,
    task: dict,
    candidate: dict,
    policy: str,
) -> dict:
    if policy == "planner":
        return {
            "success": True,
            "policy": "planner",
            "critics": [],
            "error": None,
            "trace": candidate.get("provenance", {}),
        }
    rounds = 2 if policy == "max" else 1
    critics = []
    for round_no in range(1, rounds + 1):
        review = client.review(task, candidate, round_no=round_no)
        if not review.get("success"):
            trace = dict(candidate.get("provenance") or {})
            trace["critic"] = review.get("trace") or {}
            trace["critic_round"] = round_no
            return {
                "success": False,
                "policy": policy,
                "critics": critics,
                "error": review.get("error") or "CODEARTS_REVIEW_FAILED",
                "trace": trace,
            }
        critics.append({"round": round_no, **(review.get("review") or {}), "trace": review.get("trace")})
    return {
        "success": True,
        "policy": "planner_critic_double" if rounds == 2 else "planner_critic",
        "critics": critics,
        "error": None,
        "trace": candidate.get("provenance", {}),
    }


def _validate_task_envelope(task: dict) -> List[str]:
    """校验 task.v1 的最小外壳，保持对正常联调输入的兼容。"""
    errors = []
    if task.get("schema_version") != "task.v1":
        errors.append("schema_version 必须为 task.v1")
    if not isinstance(task.get("task_id"), str) or not task["task_id"].strip():
        errors.append("task_id 必须是非空字符串")
    if not isinstance(task.get("action"), str) or not task["action"].strip():
        errors.append("action 必须是非空字符串")
    if task.get("status") not in {"READY", "NEEDS_CLARIFICATION", "BLOCKED"}:
        errors.append("status 必须是 READY、NEEDS_CLARIFICATION 或 BLOCKED")
    if task.get("destination_id") is not None and not _is_non_empty_string(task.get("destination_id")):
        errors.append("destination_id 必须是字符串或 null")
    if task.get("blocking_reasons") is not None and not _is_string_list(task.get("blocking_reasons")):
        errors.append("blocking_reasons 必须是字符串数组")
    return errors


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_non_empty_string(item) for item in value)


def _validate_ready_task(
    task: dict,
    action: str = "pick_and_place",
) -> List[str]:
    """Validate the stable entity bindings required by each open task action."""
    errors = []
    target_ids = task.get("target_ids")
    if not _is_string_list(target_ids) or len(target_ids) != 1:
        errors.append(f"{action} 必须且只能提供一个 target_id")

    needs_destination = action in {
        "pick_and_place",
        "place",
        "transfer",
        "fetch",
        "stack",
    }
    destination_id = task.get("destination_id")
    if needs_destination and not _is_non_empty_string(destination_id):
        errors.append(f"{action} 必须提供 destination_id")
    if not needs_destination and destination_id is not None:
        errors.append(f"{action} 不应忽略 destination_id")
    if action == "stack" and _is_string_list(target_ids) and len(target_ids) == 1:
        if target_ids[0] == destination_id:
            errors.append("stack 的 target_id 与 destination_id 不能相同")

    return errors


def _blocked_result(task_id: Any, reasons: List[str], message: str) -> dict:
    """构造字段稳定的阻断输出，方便下游统一处理。"""
    return {
        "schema_version": "strategy.v1",
        "task_id": task_id if isinstance(task_id, str) and task_id else "unknown",
        "steps": [],
        "code": None,
        "success": False,
        "blocked": True,
        "blocking_reasons": list(reasons),
        "message": message,
        "mode": "blocked",
        "validation": {},
    }


def _normalize_reasons(value: Any, fallback: str) -> List[str]:
    if isinstance(value, list):
        reasons = [item for item in value if isinstance(item, str) and item.strip()]
        if reasons:
            return reasons
    elif isinstance(value, str) and value.strip():
        return [value]
    return [fallback]

def _build_primitive_steps(
    task_id: str,
    action: str,
    target_id: str,
    destination_id: str | None,
) -> list[dict]:
    """Lower one grounded task into C's existing primitive action source."""
    detect_step_id = f"{task_id}-detect"
    object_reference = f"${detect_step_id}.object_id"
    steps = [
        {
            "step_id": detect_step_id,
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
            "on_failure": {
                "max_attempts": 1,
                "steps": [
                    {
                        "step_id": f"{task_id}-retry-grasp",
                        "action": "grasp",
                        "arguments": {"object_id": object_reference},
                    }
                ],
                "on_exhausted": "stop",
            },
        },
    ]
    if action in {"pick", "grasp"}:
        return steps

    move_arguments = {"destination_id": destination_id}
    if action == "stack":
        # This is still the same C primitive; the explicit mode prevents a
        # stack from silently becoming a direct placement at the base pose.
        move_arguments["placement_mode"] = "stack_on"
    steps.extend([
        {
            "step_id": f"{task_id}-move-target",
            "action": "move_to_target",
            # Formal B→C uses destination_id. Keep placement_mode explicit for stack.
            "arguments": move_arguments,
        },
        {
            "step_id": f"{task_id}-release",
            "action": "release",
            "arguments": {},
        },
    ])
    return steps


def _strategy_message(action: str) -> str:
    if action in {"pick", "grasp"}:
        return "单独抓取已转换为三步原子策略"
    if action == "stack":
        return "stack 已转换为带 stack_on 放置约束的五步原子策略"
    if action in {"transfer", "fetch"}:
        return f"{action} 已复用抓取、搬运、释放原子策略"
    return f"{action} 已转换为五步原子策略"


if __name__ == "__main__":
    # 直接运行时做一次自测
    print("=== 健康检查 ===")
    print(health())

    print("\n=== 筀单测试: 抓取放置 ===")
    sample_task = {
        "schema_version": "task.v1",
        "task_id": "task-001",
        "action": "pick_and_place",
        "target_ids": ["obj-001"],
        "target_object": "红色方块",
        "destination": {"x": 0.2, "y": 0.0, "z": 0.03},
        "destination_id": "zone-001",
        "status": "READY",
        "blocking_reasons": [],
    }
    out = run(sample_task)
    print(f"success: {out.get('success')}")
    print(f"steps: {len(out.get('steps', []))} 步")
    print(f"code 长度: {len(out.get('code') or '')} 字符")
