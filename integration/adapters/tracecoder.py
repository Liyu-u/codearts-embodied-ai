"""TraceCoder 模块适配器 —— 统一联调仓库的 feedback 环节接入点。

仓库原则要求每个模块对外实现统一适配器：
    run(input_json: dict) -> output_json: dict
    health() -> dict

本适配器把『Codearts-Tracecoder 的机器人策略修复引擎』包装成闭环中的
feedback.v1 服务：接收上游执行结果，做失败归因，返回修复建议。

输入输出协议
------------
输入（run 的 input_json，来自 integration/pipeline.py）：
    {
        "task":      <task.v1>,       # 语义任务描述（intent 模块输出）
        "strategy":  <strategy.v1>,   # 当前待修复策略（strategy_generation 输出）
        "execution": <execution.v1>,  # 执行日志（executor 输出）
        "perception": <perception.v1>,# 可选：真实感知对象/位姿，用于修复仿真 grounding
    }
输出（feedback.v1）：
    {
        "schema_version": "feedback.v1",
        "task_id":        str,
        "diagnosis":      str,   # 结构化诊断 JSON 序列化（schema 限定为 string）
        "retryable":      bool,  # 修复未完全通过时 True，闭环可带 patch 再试
        "patch":          object|null,  # 修复后的完整策略（strategy.v1）
    }

关于『轻量仿真 → Isaac Sim』的演进（重要）
----------------------------------------
当前 TraceCoder 引擎内置轻量确定性仿真（modules/evaluator/tracecoder/
simulator.py），仅用于 Mock 联调阶段生成候选修复策略；最终是否通过，
必须以输入的 execution.v1 真实执行证据为准。

后续接入 Isaac Sim 后，真实执行日志（execution.v1）会承载执行证据，
TraceCoder 消费它做归因——本适配器已经把 execution.v1 作为必备输入，
届时只需把 execute 证据的『来源』从轻量仿真替换为 Isaac Sim，诊断/修复
逻辑（tracecoder 引擎）不变。scenarios/goals 等仿真几何信息届时由
perception.v1 与真实执行日志提供，见 resolve_task_data() 的注释。
"""

from __future__ import annotations

import json
from typing import Any

from integration.contract_validation import validate_contract

# TraceCoder 引擎：统一联调仓库内以相对导入引用本仓库 modules 包。
from modules.evaluator.tracecoder import process_policy
from modules.evaluator.tracecoder.experience import ExperienceStore
from modules.evaluator.tracecoder.models import normalize_strategy

MODULE_NAME = "tracecoder"
MODULE_VERSION = "1.0.0"  # 与 Codearts-Tracecoder 上游 src/robot_policy 对齐

# 经验库持久化路径（gitignore 已排除，见仓库 .env/.gitignore 约定）。
# 在进程内存活即可让 HLLM『记事本』跨任务生效；落盘留作后续增强。
_EXPERIENCE_STORE: ExperienceStore | None = None


# ---------------------------------------------------------------------------
# 协议映射：strategy.v1 <-> TraceCoder 原生策略
# ---------------------------------------------------------------------------

def _step_v1_to_native(step: dict) -> dict:
    """Convert one strategy.v1 step, including nested recovery steps."""

    native = {
        "id": step.get("step_id", step.get("id")),
        "action": step.get("action"),
        "arguments": step.get("arguments") or {},
    }
    if step.get("on_failure") is not None:
        recovery = step["on_failure"]
        native["on_failure"] = {
            **recovery,
            "steps": [
                _step_v1_to_native(item) for item in recovery.get("steps", [])
            ],
        }
    return native


def _strategy_v1_to_native(strategy_v1: dict) -> dict:
    """strategy.v1 → 引擎原生策略（step_id 改名为 id）。

    TraceCoder 的原生恢复步骤也使用 ``id``，所以嵌套的 ``on_failure``
    步骤必须和顶层步骤一起转换，避免 D 生成的 patch 回到 C 时丢失 ID。
    """

    steps = [_step_v1_to_native(step) for step in strategy_v1.get("steps", [])]
    # strategy.v1 没有独立的 strategy_id，task_id 就是策略与任务的关联键。
    # 必须保留下来，否则 normalize_strategy() 会生成 generated_strategy，
    # 返回的 patch 就无法再关联到原任务。
    return {"strategy_id": strategy_v1.get("task_id"), "steps": steps}


def _step_native_to_v1(step: dict) -> dict:
    """Convert one native step, including nested recovery steps."""

    v1_step = {
        "step_id": step.get("id", step.get("step_id")),
        "action": step.get("action"),
        "arguments": step.get("arguments") or {},
    }
    if step.get("on_failure") is not None:
        recovery = step["on_failure"]
        v1_step["on_failure"] = {
            **recovery,
            "steps": [
                _step_native_to_v1(item) for item in recovery.get("steps", [])
            ],
        }
    return v1_step


def _strategy_native_to_v1(strategy_native: dict) -> dict:
    """引擎原生策略 → strategy.v1（id 改名为 step_id）。

    注意：strategy.v1 的 on_failure 是 object（非空），无恢复的步骤要
    省略该字段而不是输出 null，否则过不了契约校验。
    """
    steps = [_step_native_to_v1(step) for step in strategy_native.get("steps", [])]
    return {
        "schema_version": "strategy.v1",
        "task_id": strategy_native.get("strategy_id"),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# task_data 解析：v1 语义 → TraceCoder 引擎任务描述
# ---------------------------------------------------------------------------

def _derive_objects(native_strategy: dict) -> list[dict]:
    """从策略中推导轻量仿真的物体清单（轻量仿真阶段的位置占位）。

    收集 detect_object 引用的物体名和 move_to_target 引用的容器名；
    位置用占位坐标（策略里 grasp 前必有 approach/move 步骤，所以绝对坐标
    不影响执行正确性）。接入 Isaac Sim 后，物体位姿改由 perception.v1 提供。
    """
    objects: list[dict] = []
    seen: set[str] = set()
    counter = [0]

    def add(name: str, is_container: bool) -> None:
        if not name or name in seen:
            return
        seen.add(name)
        counter[0] += 1
        # 占位位置：物体在一条弧线上铺开，容器在目标区，保证 move→grasp→move 可达。
        if is_container:
            position = [0.6, 0.5 + 0.1 * counter[0], 0.0]
        else:
            position = [0.5 + 0.1 * counter[0], 0.0, 0.0]
        objects.append({
            "id": f"obj_{counter[0]}",
            "name": name,
            "position": position,
            "visible": True,
            "reachable": True,
            "orientation": 0.0,
        })

    for step in native_strategy.get("steps", []):
        args = step.get("arguments") or {}
        if step.get("action") == "detect_object":
            add(args.get("object_name"), is_container=False)
        elif step.get("action") == "move_to_target":
            add(args.get("target"), is_container=True)
    return objects


def _perception_objects(perception: dict | None) -> list[dict]:
    """把 perception.v1 对象转换为 TraceCoder 轻量状态对象。

    D 仍使用自己的轻量仿真，但优先使用上游感知的稳定 ID、位姿和能力，
    避免复杂场景完全依赖策略字段推导占位几何。
    """
    if not isinstance(perception, dict):
        return []
    objects: list[dict] = []
    for item in perception.get("objects") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        pose = item.get("pose") or {}
        if isinstance(pose, dict):
            position = [
                float(pose.get("x", 0.0) or 0.0),
                float(pose.get("y", 0.0) or 0.0),
                float(pose.get("z", 0.0) or 0.0),
            ]
        elif isinstance(pose, list) and len(pose) >= 3:
            position = [float(pose[0]), float(pose[1]), float(pose[2])]
        else:
            position = [0.0, 0.0, 0.0]
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        execution = item.get("execution") if isinstance(item.get("execution"), dict) else {}
        objects.append({
            "id": item["id"],
            "name": item.get("category") or attributes.get("display_name") or item["id"],
            "position": position,
            "visible": bool(item.get("visible", True)),
            "reachable": bool(execution.get("reachable", True)),
            "orientation": float(item.get("orientation", 0.0) or 0.0),
        })
    return objects


def _derive_goals(task: dict, native_strategy: dict) -> list[dict]:
    """从 task.v1 语义推导任务目标（轻量仿真阶段的尽力而为映射）。

    覆盖当前已开放动作：
      - place/put/move/transfer/fetch：目标物放入目标容器 → object_inside
      - stack：目标物位于稳定底座上 → object_on
      - pick/grasp：夹爪应占用目标物 → gripper_empty=False
      - rotate：目标物应旋转到指定角度 → object_oriented
    其它动作推导不出明确目标时返回空列表（仅做安全检查），并在 diagnosis
    中如实说明——不静默猜测。接入 Isaac Sim 后由真实执行日志佐证目标达成。
    """
    action = (task.get("action") or "").lower()
    targets = task.get("target_ids") or []
    destination = task.get("destination_id")

    if action == "stack":
        if targets and destination:
            return [{
                "type": "object_on",
                "object": targets[0],
                "base": destination,
            }]
    if (
        "place" in action
        or "put" in action
        or "move" in action
        or action in {"transfer", "fetch"}
    ):
        if targets and destination:
            return [{
                "type": "object_inside",
                "object": targets[0],
                "container": destination,
            }]
    if action in {"pick", "grasp"}:
        if targets:
            return [{"type": "gripper_empty", "expected": False}]
    if action == "rotate":
        if targets:
            return [{"type": "object_oriented", "object": targets[0], "angle": 0.0}]

    del native_strategy  # 目前推导不依赖策略内部结构，保留参数以对齐签名
    return []


def _derive_max_api_calls(task: dict) -> int | None:
    """从 constraints 里解析 max_api_calls=N；没有则返回 None（不限制）。"""
    for constraint in task.get("constraints") or []:
        if isinstance(constraint, str) and constraint.startswith("max_api_calls="):
            try:
                return int(constraint.split("=", 1)[1])
            except ValueError:
                return None
    return None


def _execution_failure_counts(execution: dict) -> dict[str, int]:
    """把执行日志中的失败动作转换成轻量仿真的失败注入。"""
    failures: dict[str, int] = {}
    for item in _execution_summary(execution)["failed_steps"]:
        action = item.get("action")
        if action:
            failures[action] = failures.get(action, 0) + 1
    return failures


def resolve_task_data(
    task: dict,
    native_strategy: dict,
    execution: dict,
    perception: dict | None = None,
) -> dict:
    """把 v1 协议解析为 TraceCoder 引擎的 task_data。

    task.v1 是语义级描述（动作/目标物/目标容器），TraceCoder 引擎还需要
    轻量仿真的几何与场景信息。解析优先级：
      1. task["tracecoder"]：调用方显式提供的完整任务描述（推荐，schema
         允许 additionalProperties），可含 objects/goals/scenarios/
         reference_duration_ms，精确且确定；
      2. 否则优先使用上游 perception.v1 的对象信息；
      3. 感知也缺失时才按 v1 语义尽力推导（物体/目标/API 上限）。
    每个推导结果都标注为『轻量仿真代理』，避免被误当成真实环境数据。
    """
    explicit = task.get("tracecoder") or {}

    objects = (
        explicit.get("objects")
        or _perception_objects(perception)
        or _derive_objects(native_strategy)
    )
    goals = explicit.get("goals") or _derive_goals(task, native_strategy)
    scenarios = explicit.get("scenarios")
    if not scenarios:
        # 没有专门的 Mock 场景时，用真实 execution.v1 中记录的失败动作
        # 驱动候选修复仿真，而不是无条件假设 normal 场景。
        scenario = {"name": "execution_evidence", "required": True}
        failures = _execution_failure_counts(execution)
        if failures:
            scenario["failures"] = failures
        scenarios = [scenario]

    # 参考时长：只有执行已成功时，本次执行时长才能作为理想时长的有效下界；
    # 执行失败时它包含失败/恢复路径，不作为参考基准（efficiency 回退通用口径）。
    reference_duration_ms = explicit.get("reference_duration_ms")
    if not reference_duration_ms and execution.get("status") == "SUCCEEDED":
        reference_duration_ms = execution.get("total_duration_ms")

    return {
        "task_id": task.get("task_id"),
        "initial_state": {
            "robot": {"position": [0.0, 0.0, 0.0], "gripper_empty": True},
            "objects": objects,
        },
        "goals": goals,
        "scenarios": scenarios,
        "safety_rules": {"max_api_calls": _derive_max_api_calls(task)},
        "reference_duration_ms": reference_duration_ms,
        # 评估器目前用轻量仿真生成候选 patch；把真实执行证据一并带入任务数据，
        # 供诊断层记录，避免把仿真结果误当成真实执行结果。
        "execution_evidence": _execution_summary(execution),
    }


# ---------------------------------------------------------------------------
# feedback.v1 输出组装
# ---------------------------------------------------------------------------

def _execution_summary(execution: dict) -> dict:
    """提取 execution.v1 中与反馈直接相关的事实，避免复制完整日志。"""
    failed_steps = []
    for step in execution.get("steps") or []:
        status = str(step.get("status", "")).upper()
        if status in {"FAILED", "FAIL", "ERROR", "SAFE_STOP"}:
            failed_steps.append({
                "step_id": step.get("step_id"),
                "action": step.get("action"),
                "status": step.get("status"),
                "reason": step.get("reason") or step.get("error"),
            })
    return {
        "task_id": execution.get("task_id"),
        "status": execution.get("status"),
        "failed_steps": failed_steps,
        "safety_events": execution.get("safety_events") or [],
    }


def _has_safety_violation(execution: dict) -> bool:
    """判断执行日志是否记录了明确的安全违规。"""
    for event in execution.get("safety_events") or []:
        count = event.get("count")
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
            return True
        if event.get("triggered") is True or str(event.get("status", "")).upper() in {
            "VIOLATION", "TRIGGERED", "FAILED"
        }:
            return True
    return False


def _execution_passed(execution: dict) -> bool:
    """execution.v1 是最终事实来源，轻量仿真不能覆盖真实失败。"""
    # steps 中可能保留可恢复的中间失败；最终 status 才表示整次执行结果。
    return execution.get("status") == "SUCCEEDED" and not _has_safety_violation(execution)


def _build_diagnosis(
    result: dict,
    execution: dict,
    final_passed: bool,
    *,
    patch_valid: bool,
    patch_changed: bool,
    retry_reason: str,
) -> str:
    """把引擎的结构化结果序列化为 feedback.v1 的 diagnosis 字符串。

    schema 限定 diagnosis 为 string，因此这里把结构化内容 JSON 序列化；
    读取方用 json.loads 还原。包含：最终状态、停止原因、每轮修复来源与
    教训、失败签名、三维质量分——足够闭环/人工判断『为什么失败、改了什么』。
    """
    history = result.get("repair_history") or []
    final_eval = result.get("final_evaluation") or {}
    score = final_eval.get("score") or {}
    structured = {
        # final_passed 必须反映真实 execution.v1，而不是内部仿真结果。
        "final_passed": final_passed,
        "execution_status": execution.get("status"),
        "execution_passed": _execution_passed(execution),
        "simulation_final_passed": bool(result.get("final_passed")),
        "execution_evidence": _execution_summary(execution),
        "status": result.get("status"),
        "stopped_reason": result.get("stopped_reason"),
        "patch_valid": patch_valid,
        "patch_changed": patch_changed,
        "retry_reason": retry_reason,
        "repair_rounds": len(history),
        "repair_log": [
            {
                "attempt": item.get("attempt"),
                "source": item.get("source"),  # "rules" | "hllm"
                "result": (item.get("result") or {}).get("result"),
                "lesson": item.get("lesson"),
            }
            for item in history
        ],
        "hllm_stats": result.get("hllm_stats"),
        "quality": {
            "score": score.get("quality_score"),
            "dimensions": score.get("dimensions"),
        },
    }
    return json.dumps(structured, ensure_ascii=False)


def _strategy_execution_shape(strategy: dict) -> dict:
    """比较会影响 C 执行的字段，忽略适配器元数据。"""
    return {
        "schema_version": strategy.get("schema_version"),
        "task_id": strategy.get("task_id"),
        "steps": strategy.get("steps") or [],
        "code": strategy.get("code") or None,
    }


def _build_feedback(
    result: dict,
    execution: dict,
    current_strategy: dict,
) -> dict:
    """把引擎结果映射为 feedback.v1。"""
    final_passed = _execution_passed(execution)
    safety_violation = _has_safety_violation(execution)
    patch = _strategy_native_to_v1(result.get("best_strategy") or {})
    patch_errors = validate_contract(patch, "strategy.v1")
    patch_valid = not patch_errors
    patch_changed = _strategy_execution_shape(patch) != _strategy_execution_shape(current_strategy)
    if execution.get("status") != "FAILED":
        retry_reason = "EXECUTION_NOT_FAILED"
    elif safety_violation:
        retry_reason = "SAFETY_EVENT"
    elif not patch_valid:
        retry_reason = "PATCH_INVALID"
    elif not patch_changed:
        retry_reason = "PATCH_UNCHANGED"
    else:
        retry_reason = "PATCH_READY"
    # 只有存在合法且发生变化的 patch，FAILED 才允许进入总线重试。
    retryable = (
        execution.get("status") == "FAILED"
        and not safety_violation
        and patch_valid
        and patch_changed
    )
    return {
        "schema_version": "feedback.v1",
        "task_id": result.get("task_id"),
        "diagnosis": _build_diagnosis(
            result,
            execution,
            final_passed,
            patch_valid=patch_valid,
            patch_changed=patch_changed,
            retry_reason=retry_reason,
        ),
        # retryable：真实执行 FAILED、无安全事件、且 patch 合法并发生变化。
        "retryable": retryable,
        # patch：修复后的完整策略（strategy.v1）。无论通过与否都返回——
        # 通过=质量优化后的最终版；未通过=当前最优可用版。
        "patch": patch,
    }


# ---------------------------------------------------------------------------
# 统一适配器接口
# ---------------------------------------------------------------------------

def health() -> dict:
    """模块健康检查：报告引擎可用性与运行模式。"""
    global _EXPERIENCE_STORE
    if _EXPERIENCE_STORE is None:
        _EXPERIENCE_STORE = ExperienceStore()
    return {
        "status": "ok",
        "module": MODULE_NAME,
        "version": MODULE_VERSION,
        # 离线规则模式（三角色纯规则 + HLLM 经验库）即可运行完整修复闭环，
        # 不依赖 LLM API / Isaac Sim。
        "offline_capable": True,
        "llm_optional": True,
        "engine_ready": True,
        "experience_entries": len(_EXPERIENCE_STORE.entries),
    }


def run(
    input_json: dict,
    *,
    experience_store: ExperienceStore | None = None,
) -> dict:
    """执行 TraceCoder 修复闭环，返回 feedback.v1。

    input_json = {
        task: task.v1,
        strategy: strategy.v1,
        execution: execution.v1,
        perception: perception.v1,  # optional grounding context
    }

    流程：
      1. 解析 v1 输入 → 引擎原生格式；
      2. resolve_task_data 构造引擎任务描述（轻量仿真阶段）；
      3. process_policy 跑 初始分→修复→最终分 闭环（含 HLLM 经验库）；
      4. 结果映射为 feedback.v1 返回。
    """
    global _EXPERIENCE_STORE
    if experience_store is None:
        if _EXPERIENCE_STORE is None:
            _EXPERIENCE_STORE = ExperienceStore()
        experience_store = _EXPERIENCE_STORE

    task = input_json.get("task") or {}
    strategy_v1 = input_json.get("strategy") or {}
    execution = input_json.get("execution") or {}
    perception = input_json.get("perception")

    # 缺少核心输入时如实报错（符合仓库『危险/缺字段必须阻断』原则），
    # 不静默返回空 patch。
    task_id = task.get("task_id")
    strategy_task_id = strategy_v1.get("task_id")
    execution_task_id = execution.get("task_id")
    if not task_id or not strategy_v1.get("steps") or not execution:
        raise ValueError(
            "tracecoder.run 需要完整的 {task, strategy, execution} 输入，"
            f"实际收到 task_id={task.get('task_id')!r}, "
            f"steps={len(strategy_v1.get('steps') or [])}, execution={'有' if execution else '无'}"
        )
    if strategy_task_id != task_id:
        raise ValueError(
            f"task_id 不一致：task={task_id!r}, strategy={strategy_task_id!r}"
        )
    if execution_task_id != task_id:
        raise ValueError(
            f"task_id 不一致：task={task_id!r}, execution={execution_task_id!r}"
        )
    if execution.get("schema_version") != "execution.v1":
        raise ValueError("execution 必须是 execution.v1")
    if execution.get("status") not in {"SUCCEEDED", "FAILED", "SAFE_STOP"}:
        raise ValueError(f"execution.status 无效：{execution.get('status')!r}")
    if not isinstance(execution.get("steps"), list) or not execution["steps"]:
        raise ValueError("execution.steps 必须是非空数组")

    native_strategy = normalize_strategy(_strategy_v1_to_native(strategy_v1))
    task_data = resolve_task_data(
        task,
        native_strategy,
        execution,
        perception=perception,
    )

    result = process_policy(
        task_data,
        initial_strategy=native_strategy,
        max_repair_attempts=5,
        optimize_quality=True,
        # HLLM 经验库：进程内记忆，跨 run() 调用复用成功修复组合。
        experience_store=experience_store,
    )
    return _build_feedback(result, execution, strategy_v1)


def reset_experience_store() -> None:
    """清空进程级经验库，供测试和可重复演示使用。"""
    global _EXPERIENCE_STORE
    _EXPERIENCE_STORE = ExperienceStore()
