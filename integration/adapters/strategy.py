"""
策略生成适配器 — B 角色 (冯海)

职责：
1. 接收标准 task.v1 JSON
2. 把第一阶段 READY pick_and_place 任务降解为可信原子动作
3. 输出不含可执行 Python 代码的标准 strategy.v1 JSON

联调仓库统一接口：
    run(input_json: dict) -> dict
    health() -> dict
"""

import sys
from pathlib import Path
from typing import Any, List

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
    if action != "pick_and_place":
        return _blocked_result(
            input_json["task_id"],
            [f"UNSUPPORTED_ACTION:{action}"],
            "第一阶段只允许执行 pick_and_place",
        )

    ready_errors = _validate_ready_task(input_json)
    if ready_errors:
        return _blocked_result(
            input_json["task_id"],
            ready_errors,
            "READY 任务缺少生成策略所需信息",
        )

    task_id = input_json["task_id"]
    target_id = input_json["target_ids"][0]
    destination_id = input_json["destination_id"]
    return {
        "schema_version": "strategy.v1",
        "task_id": task_id,
        "steps": _build_pick_and_place_steps(
            task_id,
            target_id,
            destination_id,
        ),
        # B 内部仍可保留模板/LLM 代码生成能力，但公开联调边界禁止
        # 把代码交给 C；C 也会对非空 code 做第二次拒绝。
        "code": None,
        "success": True,
        "blocked": False,
        "message": "pick_and_place 已转换为五步原子策略",
        "mode": "primitive_plan",
        "validation": {},
    }


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


def _validate_ready_task(task: dict) -> List[str]:
    """校验第一阶段 READY pick_and_place 的稳定实体绑定。"""
    errors = []
    target_ids = task.get("target_ids")
    if not _is_string_list(target_ids) or len(target_ids) != 1:
        errors.append("第一阶段 pick_and_place 必须且只能提供一个 target_id")
    if not _is_non_empty_string(task.get("destination_id")):
        errors.append("第一阶段 pick_and_place 必须提供 destination_id")

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

def _build_pick_and_place_steps(
    task_id: str,
    target_id: str,
    destination_id: str,
) -> list[dict]:
    """构造 C 和 D 共同支持的五步原子策略。"""
    detect_step_id = f"{task_id}-detect"
    object_reference = f"${detect_step_id}.object_id"
    return [
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
        {
            "step_id": f"{task_id}-move-target",
            "action": "move_to_target",
            "arguments": {"destination_id": destination_id},
        },
        {
            "step_id": f"{task_id}-release",
            "action": "release",
            "arguments": {},
        },
    ]


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
