"""
策略生成适配器 — B 角色 (冯海)

职责：
1. 接收标准 task.v1 JSON，转换成本模块的 intent 格式
2. 调用 strategy_generator 生成策略代码
3. 把结果转换回标准 strategy.v1 JSON

联调仓库统一接口：
    run(input_json: dict) -> dict
    health() -> dict
"""

import math
import sys
from pathlib import Path
from typing import Any, Dict, List

# 把 modules/strategy_generation 加入搜索路径，方便导入冯海的代码
_MOD_DIR = str(Path(__file__).resolve().parent.parent.parent / "modules" / "strategy_generation")
if _MOD_DIR not in sys.path:
    sys.path.insert(0, _MOD_DIR)

from strategy_generator import generate_strategy


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
            "code": "def task_main(): ..."
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

    ready_errors = _validate_ready_task(input_json)
    if ready_errors:
        return _blocked_result(
            input_json["task_id"],
            ready_errors,
            "READY 任务缺少生成策略所需信息",
        )

    # 把 task.v1 转成冯海模块需要的 intent 格式
    intent = _task_v1_to_intent(input_json)

    # 调用策略生成器
    result = generate_strategy(intent)

    # 把生成结果转回标准 strategy.v1
    return _result_to_strategy_v1(input_json, intent, result)


def health() -> dict:
    """返回模块健康状态。"""
    try:
        # 尝试生成一个最简单的策略，验证模块可用
        test_intent = {
            "intent_id": "health-check",
            "action": "pick_and_place",
            "target_object": "test_cube",
            "destination": {"x": 0.2, "y": 0.0, "z": 0.03},
        }
        result = generate_strategy(test_intent)
        ok = bool(result.get("success"))
        return {
            "module": "strategy_generation",
            "role": "B",
            "owner": "冯海",
            "healthy": ok,
            "message": result.get("message", ""),
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


def _validate_destination(destination: Any) -> List[str]:
    """校验显式坐标；不在这里限制工作空间 XY 范围，交由场景配置决定。"""
    if not isinstance(destination, dict):
        return ["destination 必须包含 x、y、z 坐标"]

    errors = []
    for axis in ("x", "y", "z"):
        value = destination.get(axis)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"destination.{axis} 必须是数值")
        elif not math.isfinite(float(value)):
            errors.append(f"destination.{axis} 必须是有限数值")

    if not errors and float(destination["z"]) < 0.02:
        errors.append("destination.z 不得低于安全高度 0.02m")
    return errors


def _validate_ready_task(task: dict) -> List[str]:
    """校验 READY 任务是否具备生成可执行策略的必要信息。"""
    errors = []
    action = task["action"]
    target_ids = task.get("target_ids") or []
    target_objects = task.get("target_objects") or []
    has_target = bool(target_ids or target_objects or _is_non_empty_string(task.get("target_object")))

    if target_ids and not _is_string_list(target_ids):
        errors.append("target_ids 必须是非空字符串数组")
    if target_objects and not _is_string_list(target_objects):
        errors.append("target_objects 必须是非空字符串数组")
    if task.get("attributes") is not None and not _is_string_list(task.get("attributes")):
        errors.append("attributes 必须是字符串数组")
    if task.get("constraints") is not None and not _is_string_list(task.get("constraints")):
        errors.append("constraints 必须是字符串数组")

    if action in {"pick_and_place", "push", "stack", "sort_by_size"} and not has_target:
        errors.append("未指定目标物体（target_object、target_objects 或 target_ids）")
    if action == "sort_by_color" and not has_target and not task.get("attributes"):
        errors.append("颜色分类至少需要目标物体或 attributes")
    if action == "filter_by_attribute" and not has_target and not task.get("attributes"):
        errors.append("属性筛选至少需要目标物体或 attributes")
    if action == "stack" and not (
        _is_non_empty_string(task.get("reference_object"))
        or _is_non_empty_string(task.get("reference_id"))
    ):
        errors.append("堆叠任务缺少参照物体（reference_object 或 reference_id）")

    # 当前仓库没有 destination_id 到场景坐标的解析器，不能再静默使用默认坐标。
    position_actions = {"pick_and_place", "push", "filter_by_attribute"}
    if action in position_actions:
        if task.get("destination") is None:
            if task.get("destination_id"):
                errors.append("destination_id 尚未接入场景位置解析，需提供显式 destination 坐标")
            else:
                errors.append("该动作缺少 destination 坐标")
        else:
            errors.extend(_validate_destination(task.get("destination")))

    if task.get("num_piles") is not None:
        value = task["num_piles"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            errors.append("num_piles 必须是正整数")

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

def _task_v1_to_intent(task: dict) -> dict:
    """把标准 task.v1 转成冯海模块的 intent 格式。"""
    target_ids = task.get("target_ids") or []
    # 保留现有 target_object 优先级，同时把 ID 单独传递，供下游后续做正式解析。
    target_object = task.get("target_object") or (target_ids[0] if target_ids else "")

    intent = {
        "intent_id": task.get("task_id", "task-unknown"),
        "action": task.get("action", "pick_and_place"),
        "target_object": target_object,
        "target_ids": target_ids,
    }

    # 多目标
    if task.get("target_objects"):
        intent["target_objects"] = task["target_objects"]
    elif len(target_ids) > 1:
        intent["target_objects"] = target_ids

    # 参照物体
    if task.get("reference_object"):
        intent["reference_object"] = task["reference_object"]
    elif task.get("reference_id"):
        intent["reference_object"] = task["reference_id"]

    # 目标位置（A 角色可能在 task 里附带坐标）
    if task.get("destination") is not None:
        intent["destination"] = task["destination"]
    if task.get("destination_id"):
        intent["destination_id"] = task["destination_id"]

    # 空间关系
    if task.get("spatial_relation"):
        intent["spatial_relation"] = task["spatial_relation"]

    # 属性
    if task.get("attributes"):
        intent["attributes"] = task["attributes"]

    # 约束
    if task.get("constraints"):
        intent["constraints"] = task["constraints"]

    # 排序依据
    if task.get("sort_criterion"):
        intent["sort_criterion"] = task["sort_criterion"]

    # 分类堆数
    if task.get("num_piles"):
        intent["num_piles"] = task["num_piles"]

    # 原始文本
    if task.get("raw_text"):
        intent["raw_text"] = task["raw_text"]

    return intent


def _result_to_strategy_v1(task: dict, intent: dict, result: dict) -> dict:
    """把策略生成结果转成标准 strategy.v1。"""
    task_id = task.get("task_id", "task-unknown")
    action = task.get("action", intent.get("action", "unknown"))
    target_ids = task.get("target_ids") or []

    success = bool(result.get("success"))
    # 生成失败时不把未通过校验的代码交给下游。
    code = result.get("code") if success else None

    # 构造步骤列表
    if success and code:
        arguments = {
            "target_id": target_ids[0] if target_ids else intent.get("target_object"),
        }
        if target_ids:
            arguments["target_ids"] = list(target_ids)
        for field in (
            "target_objects", "destination", "destination_id", "reference_object",
            "reference_id", "spatial_relation", "attributes", "constraints",
            "sort_criterion", "num_piles",
        ):
            if field in task and task[field] is not None:
                arguments[field] = task[field]

        step = {
            "step_id": f"{task_id}-step-001",
            "action": action,
            "arguments": arguments,
            "on_failure": {"retryable": True},
        }
        steps = [step]
    else:
        steps = []

    return {
        "schema_version": "strategy.v1",
        "task_id": task_id,
        "steps": steps,
        "code": code,
        "success": success,
        "blocked": False,
        "message": result.get("message", ""),
        "mode": result.get("mode", ""),
        "validation": result.get("validation", {}),
    }


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
        "status": "READY",
        "blocking_reasons": [],
    }
    out = run(sample_task)
    print(f"success: {out.get('success')}")
    print(f"steps: {len(out.get('steps', []))} 步")
    print(f"code 长度: {len(out.get('code') or '')} 字符")

