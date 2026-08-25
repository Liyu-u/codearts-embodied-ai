"""Validate and apply small, reviewable strategy patches."""

from __future__ import annotations

from copy import deepcopy

ALLOWED_OPERATIONS = {
    "update_argument",
    "update_step",
    "insert_before",
    "insert_after",
    "append_step",
    "delete_step",
    "replace_action",
}


def validate_patch(patch: dict) -> dict:
    issues = []
    if not isinstance(patch, dict):
        return {
            "passed": False,
            "issues": ["修改结果必须是 JSON 对象。"],
        }
    changes = patch.get("changes")
    if not isinstance(changes, list) or not changes:
        return {
            "passed": False,
            "issues": ["修改结果中没有 changes 列表。"],
        }
    for index, change in enumerate(changes):
        if not isinstance(change, dict):
            issues.append(
                f"第 {index + 1} 项必须是包含 operation 的 JSON 对象，"
                f"实际是 {type(change).__name__}。"
            )
            continue
        operation = change.get("operation")
        if operation not in ALLOWED_OPERATIONS:
            issues.append(f"第 {index + 1} 项使用了不支持的修改操作 {operation!r}。")
        if operation not in {"append_step"} and not change.get("target_step"):
            issues.append(f"第 {index + 1} 项缺少 target_step。")
        if operation in {"insert_before", "insert_after", "append_step"}:
            if not isinstance(change.get("content"), dict):
                issues.append(f"第 {index + 1} 项缺少要插入的步骤。")
        if operation == "update_argument" and not change.get("argument"):
            issues.append(f"第 {index + 1} 项缺少 argument。")
    return {"passed": not issues, "issues": issues}


def apply_patch(strategy: dict, patch: dict) -> dict:
    validation = validate_patch(patch)
    if not validation["passed"]:
        raise ValueError("; ".join(validation["issues"]))

    updated = deepcopy(strategy)
    steps = updated.setdefault("steps", [])

    def locate(step_id: str) -> int:
        for index, step in enumerate(steps):
            if step.get("id") == step_id:
                return index
        raise ValueError(f"找不到要修改的步骤 {step_id}。")

    for change in patch["changes"]:
        operation = change["operation"]
        if operation == "append_step":
            steps.append(deepcopy(change["content"]))
            continue

        index = locate(change["target_step"])
        if operation == "update_argument":
            steps[index].setdefault("arguments", {})[change["argument"]] = deepcopy(
                change.get("value")
            )
        elif operation == "update_step":
            content = deepcopy(change.get("content", {}))
            steps[index].update(content)
        elif operation == "insert_before":
            steps.insert(index, deepcopy(change["content"]))
        elif operation == "insert_after":
            steps.insert(index + 1, deepcopy(change["content"]))
        elif operation == "delete_step":
            steps.pop(index)
        elif operation == "replace_action":
            steps[index]["action"] = change["action"]
            if "arguments" in change:
                steps[index]["arguments"] = deepcopy(change["arguments"])
    return updated
