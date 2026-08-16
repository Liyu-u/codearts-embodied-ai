"""Static checks for CodeArts-generated robot policies."""

from __future__ import annotations

import re
from typing import Any

VARIABLE_PATTERN = re.compile(r"^\$([^.]+)\.(.+)$")


def _type_matches(value: Any, expected: str) -> bool:
    if isinstance(value, str) and value.startswith("$"):
        return True
    mapping = {
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    expected_type = mapping.get(expected)
    return expected_type is None or isinstance(value, expected_type)


def check_strategy(strategy: dict, api_catalog: dict) -> dict:
    issues: list[dict] = []
    steps = strategy.get("steps")
    if not isinstance(steps, list) or not steps:
        return {
            "passed": False,
            "issues": [{
                "step_id": None,
                "type": "EMPTY_STRATEGY",
                "message": "策略中没有可执行步骤。",
            }],
        }

    seen_ids: set[str] = set()
    for index, step in enumerate(steps):
        step_id = step.get("id")
        if not isinstance(step_id, str) or not step_id:
            issues.append({
                "step_id": step_id,
                "type": "MISSING_STEP_ID",
                "message": f"第 {index + 1} 个步骤缺少编号。",
            })
            continue
        if step_id in seen_ids:
            issues.append({
                "step_id": step_id,
                "type": "DUPLICATE_STEP_ID",
                "message": f"步骤编号 {step_id} 重复。",
            })

        if step.get("type", "action") != "action":
            issues.append({
                "step_id": step_id,
                "type": "UNSUPPORTED_STEP_TYPE",
                "message": f"暂不支持步骤类型 {step.get('type')}。",
            })
            seen_ids.add(step_id)
            continue

        action = step.get("action")
        if action not in api_catalog:
            issues.append({
                "step_id": step_id,
                "type": "UNKNOWN_ACTION",
                "message": f"动作 {action!r} 不在机器人动作清单中。",
            })
            seen_ids.add(step_id)
            continue

        arguments = step.get("arguments", {})
        if not isinstance(arguments, dict):
            issues.append({
                "step_id": step_id,
                "type": "INVALID_ARGUMENTS",
                "message": "动作参数必须是对象。",
            })
            continue

        required = api_catalog[action].get("required_arguments", {})
        for name, expected_type in required.items():
            if name not in arguments:
                issues.append({
                    "step_id": step_id,
                    "type": "MISSING_ARGUMENT",
                    "argument": name,
                    "message": f"动作 {action} 缺少参数 {name}。",
                })
            elif not _type_matches(arguments[name], expected_type):
                issues.append({
                    "step_id": step_id,
                    "type": "INVALID_ARGUMENT_TYPE",
                    "argument": name,
                    "message": f"参数 {name} 的类型不符合 {expected_type}。",
                })

        for name, value in arguments.items():
            if not isinstance(value, str):
                continue
            match = VARIABLE_PATTERN.match(value)
            if match and match.group(1) not in seen_ids:
                issues.append({
                    "step_id": step_id,
                    "type": "UNKNOWN_REFERENCE",
                    "argument": name,
                    "message": f"参数 {name} 引用了尚未执行的步骤 {match.group(1)}。",
                })

        recovery = step.get("on_failure")
        if recovery:
            attempts = recovery.get("max_attempts", 1)
            if not isinstance(attempts, int) or attempts < 1 or attempts > 5:
                issues.append({
                    "step_id": step_id,
                    "type": "INVALID_RETRY_COUNT",
                    "message": "失败重试次数必须在 1 到 5 之间。",
                })
        seen_ids.add(step_id)

    return {"passed": not issues, "issues": issues}
