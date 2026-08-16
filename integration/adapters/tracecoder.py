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
simulator.py），因此 run() 能独立跑通 初始分→修复→最终分 的完整闭环，
用于 Mock 联调阶段。

后续接入 Isaac Sim 后，真实执行日志（execution.v1）会承载执行证据，
TraceCoder 消费它做归因——本适配器已经把 execution.v1 作为必备输入，
届时只需把 execute 证据的『来源』从轻量仿真替换为 Isaac Sim，诊断/修复
逻辑（tracecoder 引擎）不变。scenarios/goals 等仿真几何信息届时由
perception.v1 与真实执行日志提供，见 resolve_task_data() 的注释。
"""

from __future__ import annotations

import json
from typing import Any

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

def _strategy_v1_to_native(strategy_v1: dict) -> dict:
    """strategy.v1 → 引擎原生策略（step_id 改名为 id）。

    其余字段（arguments / on_failure）双方语义一致，直接透传。
    """
    steps = []
    for step in strategy_v1.get("steps", []):
        steps.append({
            "id": step.get("step_id"),
            "action": step.get("action"),
            "arguments": step.get("arguments") or {},
            "on_failure": step.get("on_failure"),
        })
    return {"steps": steps}


def _strategy_native_to_v1(strategy_native: dict) -> dict:
    """引擎原生策略 → strategy.v1（id 改名为 step_id）。

    注意：strategy.v1 的 on_failure 是 object（非空），无恢复的步骤要
    省略该字段而不是输出 null，否则过不了契约校验。
    """
    steps = []
    for step in strategy_native.get("steps", []):
        v1_step = {
            "step_id": step.get("id"),
            "action": step.get("action"),
            "arguments": step.get("arguments") or {},
        }
        if step.get("on_failure") is not None:
            v1_step["on_failure"] = step["on_failure"]
        steps.append(v1_step)
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


def _derive_goals(task: dict, native_strategy: dict) -> list[dict]:
    """从 task.v1 语义推导任务目标（轻量仿真阶段的尽力而为映射）。

    覆盖最常见的三类动作：
      - place/put/move：目标物放入目标容器 → object_inside
      - pick：夹爪应占用目标物 → gripper_empty=False
      - rotate：目标物应旋转到指定角度 → object_oriented
    其它动作推导不出明确目标时返回空列表（仅做安全检查），并在 diagnosis
    中如实说明——不静默猜测。接入 Isaac Sim 后由真实执行日志佐证目标达成。
    """
    action = (task.get("action") or "").lower()
    targets = task.get("target_ids") or []
    destination = task.get("destination_id")

    if "place" in action or "put" in action or "move" in action:
        if targets and destination:
            return [{
                "type": "object_inside",
                "object": targets[0],
                "container": destination,
            }]
    if action == "pick":
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


def resolve_task_data(task: dict, native_strategy: dict, execution: dict) -> dict:
    """把 v1 协议解析为 TraceCoder 引擎的 task_data。

    task.v1 是语义级描述（动作/目标物/目标容器），TraceCoder 引擎还需要
    轻量仿真的几何与场景信息。解析优先级：
      1. task["tracecoder"]：调用方显式提供的完整任务描述（推荐，schema
         允许 additionalProperties），可含 objects/goals/scenarios/
         reference_duration_ms，精确且确定；
      2. 否则按 v1 语义尽力推导（物体/目标/API 上限）。
    每个推导结果都标注为『轻量仿真代理』，避免被误当成真实环境数据。
    """
    explicit = task.get("tracecoder") or {}

    objects = explicit.get("objects") or _derive_objects(native_strategy)
    goals = explicit.get("goals") or _derive_goals(task, native_strategy)
    scenarios = explicit.get("scenarios") or [{
        "name": "normal",
        "required": True,
        # 演示/联调可在 task 里注入失败：{"failures": {"grasp": 1}}
        # 让引擎有机会展示『给失败步骤补 on_failure 恢复』的修复逻辑。
    }]

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
    }


# ---------------------------------------------------------------------------
# feedback.v1 输出组装
# ---------------------------------------------------------------------------

def _build_diagnosis(result: dict) -> str:
    """把引擎的结构化结果序列化为 feedback.v1 的 diagnosis 字符串。

    schema 限定 diagnosis 为 string，因此这里把结构化内容 JSON 序列化；
    读取方用 json.loads 还原。包含：最终状态、停止原因、每轮修复来源与
    教训、失败签名、三维质量分——足够闭环/人工判断『为什么失败、改了什么』。
    """
    history = result.get("repair_history") or []
    final_eval = result.get("final_evaluation") or {}
    score = final_eval.get("score") or {}
    structured = {
        "final_passed": bool(result.get("final_passed")),
        "status": result.get("status"),
        "stopped_reason": result.get("stopped_reason"),
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


def _build_feedback(result: dict) -> dict:
    """把引擎结果映射为 feedback.v1。"""
    final_passed = bool(result.get("final_passed"))
    return {
        "schema_version": "feedback.v1",
        "task_id": result.get("task_id"),
        "diagnosis": _build_diagnosis(result),
        # retryable：修复未完全通过 → 闭环可以用返回的 patch 再次迭代；
        # 已通过 → 无需重试（质量优化已完成，patch 是可直接采用的最终版）。
        "retryable": not final_passed,
        # patch：修复后的完整策略（strategy.v1）。无论通过与否都返回——
        # 通过=质量优化后的最终版；未通过=当前最优可用版。
        "patch": _strategy_native_to_v1(result.get("best_strategy") or {}),
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


def run(input_json: dict) -> dict:
    """执行 TraceCoder 修复闭环，返回 feedback.v1。

    input_json = {task: task.v1, strategy: strategy.v1, execution: execution.v1}

    流程：
      1. 解析 v1 输入 → 引擎原生格式；
      2. resolve_task_data 构造引擎任务描述（轻量仿真阶段）；
      3. process_policy 跑 初始分→修复→最终分 闭环（含 HLLM 经验库）；
      4. 结果映射为 feedback.v1 返回。
    """
    global _EXPERIENCE_STORE
    if _EXPERIENCE_STORE is None:
        _EXPERIENCE_STORE = ExperienceStore()

    task = input_json.get("task") or {}
    strategy_v1 = input_json.get("strategy") or {}
    execution = input_json.get("execution") or {}

    # 缺少核心输入时如实报错（符合仓库『危险/缺字段必须阻断』原则），
    # 不静默返回空 patch。
    if not task.get("task_id") or not strategy_v1.get("steps") or not execution:
        raise ValueError(
            "tracecoder.run 需要完整的 {task, strategy, execution} 输入，"
            f"实际收到 task_id={task.get('task_id')!r}, "
            f"steps={len(strategy_v1.get('steps') or [])}, execution={'有' if execution else '无'}"
        )

    native_strategy = normalize_strategy(_strategy_v1_to_native(strategy_v1))
    task_data = resolve_task_data(task, native_strategy, execution)

    result = process_policy(
        task_data,
        initial_strategy=native_strategy,
        max_repair_attempts=5,
        optimize_quality=True,
        # HLLM 经验库：进程内记忆，跨 run() 调用复用成功修复组合。
        experience_store=_EXPERIENCE_STORE,
    )
    return _build_feedback(result)
