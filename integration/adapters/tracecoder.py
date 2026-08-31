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
import os
from dataclasses import dataclass
from copy import deepcopy
from typing import Any

from integration.strategy_policy import normalize_capabilities, validate_strategy as validate_shared_strategy

# TraceCoder 引擎：统一联调仓库内以相对导入引用本仓库 modules 包。
from modules.evaluator.tracecoder import process_policy
from modules.evaluator.tracecoder.experience import ExperienceStore
from modules.evaluator.tracecoder.llm_provider import LLMConfig, LLMProvider
from modules.evaluator.tracecoder.models import normalize_strategy

MODULE_NAME = "tracecoder"
MODULE_VERSION = "1.0.0"  # 与 Codearts-Tracecoder 上游 src/robot_policy 对齐

# 经验库持久化路径（gitignore 已排除，见仓库 .env/.gitignore 约定）。
# 在进程内存活即可让 HLLM『记事本』跨任务生效；落盘留作后续增强。
_EXPERIENCE_STORE: ExperienceStore | None = None

# LLM 配置（TRACECODER_LLM_*，API Key 显式留空占位、用时再填）。
# 支持 configure_llm() 运行时注入（联调/测试用），未注入时用 env 默认。
_LLM_CONFIG = LLMConfig.from_env()
_LLM_PROVIDER = LLMProvider(_LLM_CONFIG)
_LLM_OVERRIDE: dict = {"mode": None, "provider": None}
@dataclass(frozen=True)
class TraceCoderBudget:
    """Per-invocation budget selected from execution risk, not globally fixed."""

    tier: str
    max_tokens: int
    thinking: str
    reasoning_effort: str
    max_retries: int
    max_repair_attempts: int
    optimize_quality: bool
    call_style: str


def _env_int(name: str, default: int, lower: int, upper: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(lower, min(value, upper))


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

def _adaptive_routing_mode() -> str:
    """Return the active routing mode, with an explicit legacy rollback path."""

    value = os.getenv("TRACECODER_ADAPTIVE_ROUTING", "adaptive").strip().lower()
    if value in {"legacy", "off", "disabled", "false", "0"}:
        return "legacy"
    return "adaptive"


def _tracecoder_budget(tier: str) -> TraceCoderBudget:
    """Build a bounded normal/hard/expert profile from environment overrides."""
    if tier == "expert":
        return TraceCoderBudget(
            tier="expert",
            max_tokens=_env_int("TRACECODER_LLM_EXPERT_MAX_TOKENS", 8192, 1024, 16384),
            thinking="enabled",
            reasoning_effort=os.getenv("TRACECODER_LLM_REASONING_EFFORT", "low").strip().lower(),
            max_retries=_env_int("TRACECODER_LLM_EXPERT_MAX_RETRIES", 1, 0, 1),
            max_repair_attempts=_env_int("TRACECODER_EXPERT_MAX_REPAIR_ATTEMPTS", 2, 1, 5),
            optimize_quality=_env_flag("TRACECODER_EXPERT_OPTIMIZE_QUALITY", True),
            call_style="roles",
        )
    if tier == "hard":
        return TraceCoderBudget(
            tier="hard",
            max_tokens=_env_int("TRACECODER_LLM_HARD_MAX_TOKENS", 6144, 1024, 16384),
            thinking=os.getenv("TRACECODER_LLM_HARD_THINKING", "enabled").strip().lower(),
            reasoning_effort=os.getenv("TRACECODER_LLM_HARD_REASONING_EFFORT", "low").strip().lower(),
            max_retries=_env_int("TRACECODER_LLM_HARD_MAX_RETRIES", 1, 0, 1),
            max_repair_attempts=_env_int("TRACECODER_HARD_MAX_REPAIR_ATTEMPTS", 1, 1, 2),
            optimize_quality=False,
            call_style="roles",
        )
    return TraceCoderBudget(
        tier="normal",
        max_tokens=_env_int("TRACECODER_LLM_MAX_TOKENS", 3072, 1024, 16384),
        thinking=os.getenv("TRACECODER_LLM_THINKING", "disabled").strip().lower(),
        reasoning_effort=os.getenv("TRACECODER_LLM_REASONING_EFFORT", "low").strip().lower(),
        max_retries=_env_int("TRACECODER_LLM_MAX_RETRIES", 1, 0, 1),
        max_repair_attempts=1,
        optimize_quality=False,
        call_style="compact",
    )


def _legacy_tracecoder_budget() -> TraceCoderBudget:
    """Reproduce the former fixed-budget, full-quality TraceCoder profile."""

    thinking = os.getenv("TRACECODER_LEGACY_THINKING", "enabled").strip().lower()
    if thinking not in {"enabled", "disabled"}:
        thinking = "enabled"
    reasoning_effort = os.getenv("TRACECODER_LEGACY_REASONING_EFFORT", "low").strip().lower()
    if reasoning_effort not in {"low", "high", "max"}:
        reasoning_effort = "low"
    return TraceCoderBudget(
        tier="legacy",
        max_tokens=_env_int("TRACECODER_LEGACY_MAX_TOKENS", 8192, 1024, 16384),
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        max_retries=_env_int("TRACECODER_LEGACY_MAX_RETRIES", 2, 0, 5),
        max_repair_attempts=_env_int("TRACECODER_LEGACY_MAX_REPAIR_ATTEMPTS", 2, 1, 5),
        optimize_quality=_env_flag("TRACECODER_LEGACY_OPTIMIZE_QUALITY", True),
        call_style="roles",
    )


def _low_confidence(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    keys = {
        "confidence", "overall_confidence", "parse_confidence",
        "grounding_confidence", "constraint_confidence",
        "plan_feasibility_confidence", "strategy_confidence",
        "planning_confidence",
    }
    for key in keys:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0.75:
            return True
    return False


def _perception_incomplete(perception: dict | None, task: dict) -> bool:
    """Only explicit/structural perception gaps force a feedback call."""
    if perception is None:
        # perception.v1 is optional at this adapter boundary; execution evidence
        # can still be authoritative when the caller deliberately omits it.
        return False
    if not isinstance(perception, dict):
        return True
    if perception.get("complete") is False or perception.get("status") in {
        "INCOMPLETE", "NEEDS_CLARIFICATION", "BLOCKED", "STALE",
    }:
        return True
    quality = perception.get("quality") or perception.get("assessment") or {}
    if isinstance(quality, dict) and quality.get("status") not in {None, "READY", "ok", "OK"}:
        return True
    objects = perception.get("objects")
    if not isinstance(objects, list):
        return True
    target_ids = set(task.get("target_ids") or [])
    if task.get("destination_id"):
        target_ids.add(task["destination_id"])
    object_ids = {
        item.get("id") or item.get("object_id") or item.get("track_id")
        for item in objects if isinstance(item, dict)
    }
    if target_ids and (not objects or not target_ids.issubset(object_ids)):
        return True
    for item in objects:
        if not isinstance(item, dict):
            return True
        value = item.get("confidence")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0.60:
            return True
    return False


def _failed_step_count(execution: dict) -> int:
    return sum(
        1 for step in execution.get("steps") or []
        if str(step.get("status", "")).upper() in {"FAILED", "FAIL", "ERROR", "SAFE_STOP"}
    )


def _select_tracecoder_budget(input_json: dict, llm_mode: str) -> tuple[TraceCoderBudget | None, list[str]]:
    """Return (None, reasons) for the cheap successful path."""
    task = input_json.get("task") or {}
    strategy = input_json.get("strategy") or {}
    execution = input_json.get("execution") or {}
    perception = input_json.get("perception")
    status = str(execution.get("status") or "").upper()
    reasons: list[str] = []
    safety_reasons = _safety_event_reasons(execution)
    if status != "SUCCEEDED":
        reasons.append("execution_" + status.lower())
    if safety_reasons:
        reasons.append("safety_event")
    if _failed_step_count(execution):
        reasons.append("abnormal_step_state")
    if _perception_incomplete(perception, task):
        reasons.append("perception_incomplete")
    if _low_confidence(task) or _low_confidence(strategy):
        reasons.append("low_confidence")
    if input_json.get("repair_required") or task.get("repair_required"):
        reasons.append("repair_required")
    # Live acceptance explicitly exercises the D provider even when C reports
    # a clean success, so the end-to-end evidence proves the configured
    # DeepSeek feedback path rather than taking the healthy-success bypass.
    if input_json.get("live_acceptance"):
        reasons.append("live_acceptance")

    requested = str(
        input_json.get("tracecoder_profile")
        or input_json.get("tracecoder_tier")
        or task.get("tracecoder_profile")
        or ""
    ).strip().lower()
    if requested in {"expert", "max", "full"}:
        reasons.append("explicit_expert_profile")
    elif requested in {"hard", "complex", "escalated"}:
        reasons.append("explicit_hard_profile")

    routing_mode = _adaptive_routing_mode()
    if not reasons and routing_mode == "adaptive":
        return None, []

    if routing_mode == "legacy":
        return _legacy_tracecoder_budget(), reasons or ["legacy_compatibility"]

    context = input_json.get("tracecoder_context") or {}
    retry_count = int(context.get("retry_count", input_json.get("retry_count", 0)) or 0)
    complexity = str(
        input_json.get("complexity")
        or context.get("complexity")
        or task.get("complexity")
        or ""
    ).strip().lower()
    expert = requested in {"expert", "max", "full"} or complexity in {"expert", "max"}
    hard = requested in {"hard", "complex", "escalated"} or complexity in {
        "hard", "complex", "escalated",
    }
    hard = hard or retry_count > 0 or _failed_step_count(execution) >= 2
    hard = hard or (bool(safety_reasons) and status not in {"SAFE_STOP", ""})
    budget = _tracecoder_budget("expert" if expert else "hard" if hard else "normal")

    return budget, reasons


def _provider_for_budget(provider, budget: TraceCoderBudget):
    """Clone the real provider so per-task budgets are concurrency-safe."""
    if not isinstance(provider, LLMProvider):
        return provider
    config = deepcopy(provider.config)
    config.max_tokens = budget.max_tokens
    config.max_retries = budget.max_retries
    config.thinking = budget.thinking if budget.thinking in {"enabled", "disabled"} else "disabled"
    config.reasoning_effort = budget.reasoning_effort
    return LLMProvider(config)


def configure_llm(mode=None, provider=None) -> None:
    """运行时注入 LLM 模式/Provider。传 None 的字段恢复 env 默认。

    测试/联调时传假 Provider 即可在 required 模式下验证『模型确实参与』，
    无需真实 API Key。
    """
    _LLM_OVERRIDE["mode"] = mode
    _LLM_OVERRIDE["provider"] = provider


def _configured_repair_attempts() -> int:
    """Read the bounded D repair budget without allowing runaway retries."""
    try:
        value = int(os.getenv("TRACECODER_MAX_REPAIR_ATTEMPTS", "1"))
    except (TypeError, ValueError):
        value = 1
    return max(1, min(value, 5))


def _active_llm() -> tuple:
    """当前生效的 (llm_mode, llm_provider)。"""
    mode = (
        _LLM_OVERRIDE["mode"]
        if _LLM_OVERRIDE["mode"] is not None else _LLM_CONFIG.mode
    )
    provider = (
        _LLM_OVERRIDE["provider"]
        if _LLM_OVERRIDE["provider"] is not None else _LLM_PROVIDER
    )
    return mode, provider


# ---------------------------------------------------------------------------
# 协议映射：strategy.v1 <-> TraceCoder 原生策略
# ---------------------------------------------------------------------------

def _step_v1_to_native(step: dict) -> dict:
    """Convert one strategy.v1 step, including nested recovery steps."""

    arguments = deepcopy(step.get("arguments") or {})
    # The evaluator's native catalog predates the formal B→C names. Translate
    # at the adapter boundary; the native engine remains backward-compatible,
    # while all external strategy.v1 messages stay canonical.
    if step.get("action") == "detect_object" and "object_name" not in arguments:
        if "object_id" in arguments:
            arguments["object_name"] = arguments.pop("object_id")
    if step.get("action") == "move_to_target" and "target" not in arguments:
        if "destination_id" in arguments:
            arguments["target"] = arguments.pop("destination_id")
    native = {
        "id": step.get("step_id", step.get("id")),
        "action": step.get("action"),
        "arguments": arguments,
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

    arguments = deepcopy(step.get("arguments") or {})
    # TraceCoder's historical native simulator still speaks object_name/target,
    # but feedback.v1 is the formal B→C contract. Normalize only at this
    # boundary so old internal repair rules remain executable while every patch
    # returned to the pipeline uses canonical IDs.
    if step.get("action") == "detect_object" and "object_id" not in arguments:
        if "object_name" in arguments:
            arguments["object_id"] = arguments.pop("object_name")
    if step.get("action") == "move_to_target" and "destination_id" not in arguments:
        if "target" in arguments:
            arguments["destination_id"] = arguments.pop("target")
    v1_step = {
        "step_id": step.get("id", step.get("step_id")),
        "action": step.get("action"),
        "arguments": arguments,
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
        "code": None,
    }


# ---------------------------------------------------------------------------
# task_data 解析：v1 语义 → TraceCoder 引擎任务描述
# ---------------------------------------------------------------------------

def _derive_objects(native_strategy: dict, task: dict | None = None) -> list[dict]:
    """从策略中推导轻量仿真的物体清单（轻量仿真阶段的位置占位）。

    收集 detect_object 引用的物体名和 move_to_target 引用的容器名；
    位置用占位坐标（策略里 grasp 前必有 approach/move 步骤，所以绝对坐标
    不影响执行正确性）。接入 Isaac Sim 后，物体位姿改由 perception.v1 提供。
    """
    objects: list[dict] = []
    seen: set[str] = set()
    counter = [0]
    task = task or {}
    stable_ids = {
        str(item) for item in (task.get("target_ids") or []) if item
    }
    if task.get("destination_id"):
        stable_ids.add(str(task["destination_id"]))

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
        # Goals and execution arguments use task.v1 IDs.  Synthetic obj_N
        # IDs made the final goal look failed after a successful repair
        # because the object was stored in obj_2 while the goal expected the
        # destination's stable ID.  Keep synthetic IDs only for legacy tasks
        # that provide no identity at the protocol boundary.
        object_id = name if name in stable_ids else f"obj_{counter[0]}"
        objects.append({
            "id": object_id,
            "name": name,
            "position": position,
            "visible": True,
            "reachable": True,
            "orientation": 0.0,
        })

    for step in native_strategy.get("steps", []):
        args = step.get("arguments") or {}
        if step.get("action") == "detect_object":
            add(args.get("object_id", args.get("object_name")), is_container=False)
        elif step.get("action") == "move_to_target":
            add(args.get("destination_id", args.get("target")), is_container=True)
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
        or _derive_objects(native_strategy, task=task)
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


SAFETY_EVENT_TYPES = {
    "WORKSPACE_VIOLATION",
    "COLLISION_DETECTED",
    "ACTION_TIMEOUT",
    "RECOVERY_EXHAUSTED",
}


def _safety_event_reasons(execution: dict) -> list[str]:
    """Return canonical safety reasons that prohibit automatic retry."""

    reasons: list[str] = []
    for event in execution.get("safety_events") or []:
        event_type = str(event.get("type", "")).upper()
        severity = str(event.get("severity", "")).lower()
        if event_type in SAFETY_EVENT_TYPES:
            reasons.append(event_type)
        elif severity in {"error", "critical"}:
            reasons.append(event_type or f"SEVERITY_{severity.upper()}")
        count = event.get("count")
        if isinstance(count, (int, float)) and not isinstance(count, bool) and count > 0:
            reasons.append(event_type or "SAFETY_EVENT")
        if event.get("triggered") is True or str(event.get("status", "")).upper() in {
            "VIOLATION", "TRIGGERED", "FAILED"
        }:
            reasons.append(event_type or "SAFETY_EVENT")
    return list(dict.fromkeys(reasons))


def _has_safety_violation(execution: dict) -> bool:
    """判断执行日志是否记录了明确的安全违规。"""

    return bool(_safety_event_reasons(execution))


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
        "verification_status": "EXECUTION_EVIDENCE_ANALYZED",
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
                "source": item.get("source"),  # "rules" | "llm" | "hllm" | "llm_required_failed"
                "result": (item.get("result") or {}).get("result"),
                "lesson": item.get("lesson"),
                # 修复前后证据：本轮 LLM/规则产出的诊断与 patch、完整执行结果
                # （含 safe_stop 等事件），供闭环/人工判断『为什么失败、改了什么』。
                "diagnosis": item.get("diagnosis"),
                "patch": item.get("patch"),
                "result_detail": item.get("result"),
            }
            for item in history
        ],
        "hllm_stats": result.get("hllm_stats"),
        "llm": {
            "stats": result.get("llm_stats"),
            "required_failed": result.get("llm_required_failed"),
            "calls": result.get("call_log") or [],
        },
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


def _empty_llm_stats(mode: str) -> dict:
    return {
        "mode": mode,
        "calls": 0,
        "ok_calls": 0,
        "fallback_calls": 0,
        "failed_calls": 0,
        "total_latency_ms": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }

def _build_skipped_feedback(
    task: dict,
    execution: dict,
    *,
    reasons: list[str],
    llm_mode: str,
    run_id: str | None,
) -> dict:
    """Return a contract-valid zero-call feedback record for healthy runs."""
    status = str(execution.get("status") or "").upper()
    final_passed = _execution_passed(execution)
    structured = {
        "status": "TRACE_CODER_SKIPPED",
        "verification_status": "SKIPPED_HEALTHY_SUCCESS",
        "routing_mode": _adaptive_routing_mode(),
        "skip_reason": reasons,
        "tracecoder_invoked": False,
        "execution_status": status,
        "execution_passed": final_passed,
        "final_passed": final_passed,
        "simulation_final_passed": final_passed,
        "repair_rounds": 0,
        "repair_log": [],
        "llm": {
            "stats": _empty_llm_stats(llm_mode),
            "required_failed": False,
            "calls": [],
        },
    }
    return {
        "schema_version": "feedback.v1",
        "task_id": task.get("task_id"),
        "execution_status": status,
        "final_passed": final_passed,
        "safety_stop": status == "SAFE_STOP",
        "stop_reason": execution.get("stop_reason"),
        "diagnosis": json.dumps(structured, ensure_ascii=False),
        "retryable": False,
        "patch": None,
        "provenance": {
            "source": "tracecoder_skipped",
            "agent": "TraceCoder",
            "mode": llm_mode,
            "profile": "bypass",
            "routing_mode": _adaptive_routing_mode(),
            "request_id": run_id,
            "run_id": run_id,
            "latency_ms": 0.0,
            "request_ids": [],
            "fallback": False,
            "llm_stats": _empty_llm_stats(llm_mode),
            "calls": [],
            "skip_reason": reasons,
            "validation": {"passed": True, "errors": []},
            "patch_validation": {"passed": True, "errors": []},
            "verification_status": "SKIPPED_HEALTHY_SUCCESS",
        },
    }

def _build_feedback(
    result: dict,
    execution: dict,
    current_strategy: dict,
    *,
    task: dict,
    capabilities: dict,
    llm_mode: str,
    budget: TraceCoderBudget | None = None,
    trigger_reasons: list[str] | None = None,
    run_id: str | None = None,
) -> dict:
    """把引擎结果映射为 feedback.v1。"""
    final_passed = _execution_passed(execution)
    safety_reasons = _safety_event_reasons(execution)
    safety_violation = bool(safety_reasons)
    patch = _strategy_native_to_v1(result.get("best_strategy") or {})
    patch_validation = validate_shared_strategy(
        patch,
        task=task,
        capabilities=capabilities,
    )
    patch_errors = patch_validation["errors"]
    patch_valid = patch_validation["passed"]
    patch_changed = _strategy_execution_shape(patch) != _strategy_execution_shape(current_strategy)
    execution_status = str(execution.get("status") or "").upper()
    failed_steps = [
        step for step in (execution.get("steps") or [])
        if isinstance(step, dict) and str(step.get("status") or "").upper() == "FAILED"
    ]
    failed_action = str((failed_steps[-1] if failed_steps else {}).get("action") or "")
    completed_actions = {
        str(step.get("action") or "")
        for step in (execution.get("steps") or [])
        if isinstance(step, dict)
        and str(step.get("status") or "").upper() == "SUCCESS"
    }
    # Replaying a whole plan after a successful grasp is unsafe: C may still
    # hold the object, so a release/move failure must remain FAILED until a
    # state-aware patch is available.  D may retry a failed grasp itself.
    non_idempotent_prefix = (
        failed_action in {"move_to_target", "release"}
        and "grasp" in completed_actions
    )
    if execution_status == "SAFE_STOP":
        retry_reason = "SAFE_STOP_NO_RETRY"
    elif execution_status != "FAILED":
        retry_reason = "EXECUTION_NOT_FAILED"
    elif safety_violation:
        retry_reason = "SAFETY_EVENT:" + ",".join(safety_reasons)
    elif non_idempotent_prefix:
        retry_reason = "NON_IDEMPOTENT_PREFIX"
    elif not patch_valid:
        retry_reason = "PATCH_INVALID"
    elif not patch_changed:
        retry_reason = "PATCH_UNCHANGED"
    else:
        retry_reason = "PATCH_READY"
    # 只有存在合法且发生变化的 patch，FAILED 才允许进入总线重试。
    retryable = (
        execution_status == "FAILED"
        and not safety_violation
        and not non_idempotent_prefix
        and patch_valid
        and patch_changed
    )
    if not retryable:
        patch = None
    llm_stats = result.get("llm_stats") or {}
    calls = result.get("call_log") or []
    call_models = [item.get("model") for item in calls if item.get("model")]
    request_ids = [item.get("request_id") for item in calls if item.get("request_id")]
    latency_values = [
        float(item.get("latency_ms", 0) or 0)
        for item in calls
        if isinstance(item.get("latency_ms", 0), (int, float))
    ]
    provenance = {
        "source": "tracecoder_llm" if llm_mode != "off" else "tracecoder_rules",
        "agent": "TraceCoder",
        "mode": llm_mode,
        "profile": budget.tier if budget else "unknown",
        "routing_mode": _adaptive_routing_mode(),
        "trigger_reasons": trigger_reasons or [],
        "model": call_models[0] if call_models else (_LLM_CONFIG.model or None),
        "request_id": run_id,
        "run_id": run_id,
        "latency_ms": round(sum(latency_values), 3),
        "request_ids": request_ids,
        "fallback": bool(llm_stats.get("fallback_calls")),
        "llm_stats": llm_stats,
        "calls": calls,
        "safety_events": safety_reasons,
        "validation": patch_validation,
        "patch_validation": patch_validation,
        "verification_status": "EXECUTION_EVIDENCE_ANALYZED",
    }
    return {
        "schema_version": "feedback.v1",
        "task_id": result.get("task_id"),
        # Expose canonical C/D status facts at the protocol boundary.  This
        # avoids treating an intermediate failed step or a safe stop as a
        # retryable failure.
        "execution_status": execution_status,
        "final_passed": final_passed,
        "safety_stop": execution_status == "SAFE_STOP",
        "stop_reason": execution.get("stop_reason"),
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
        # patch 仅在 FAILED 且存在安全、合法且有变化的修复时返回。
        "patch": patch,
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# 统一适配器接口
# ---------------------------------------------------------------------------

def health() -> dict:
    """模块健康检查：报告引擎可用性与运行模式。"""
    global _EXPERIENCE_STORE
    if _EXPERIENCE_STORE is None:
        _EXPERIENCE_STORE = ExperienceStore()
    mode, _ = _active_llm()
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
        # LLM 真实接入状态：当前模式 + 是否已配置 Key/模型。
        "llm": dict(_LLM_CONFIG.health_info(), **{"active_mode": mode}),
        "routing": {
            "mode": _adaptive_routing_mode(),
            "rollback_available": True,
            "legacy_profile": {
                "max_tokens": _env_int("TRACECODER_LEGACY_MAX_TOKENS", 8192, 1024, 16384),
                "thinking": os.getenv("TRACECODER_LEGACY_THINKING", "enabled"),
                "max_retries": _env_int("TRACECODER_LEGACY_MAX_RETRIES", 2, 0, 5),
                "max_repair_attempts": _env_int("TRACECODER_LEGACY_MAX_REPAIR_ATTEMPTS", 2, 1, 5),
            },
        },
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
    run_id = str(input_json.get("run_id") or task_id)
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

    capabilities = normalize_capabilities(input_json.get("capabilities"))
    strategy_validation = validate_shared_strategy(
        strategy_v1,
        task=task,
        capabilities=capabilities,
    )
    if not strategy_validation["passed"]:
        raise ValueError(
            "strategy 安全校验失败：" + "; ".join(strategy_validation["errors"])
        )

    llm_mode, llm_provider = _active_llm()
    budget, trigger_reasons = _select_tracecoder_budget(input_json, llm_mode)
    if budget is None:
        return _build_skipped_feedback(
            task,
            execution,
            reasons=["healthy_success"],
            llm_mode=llm_mode,
            run_id=run_id,
        )

    if budget.call_style == "compact" and not isinstance(llm_provider, LLMProvider):
        budget = TraceCoderBudget(**{**budget.__dict__, "call_style": "roles"})

    native_strategy = normalize_strategy(_strategy_v1_to_native(strategy_v1))
    task_data = resolve_task_data(
        task,
        native_strategy,
        execution,
        perception=perception,
    )

    # 每次调用按风险选择独立 Provider 配置，避免普通任务继承 8192/reasoning。
    call_log: list = []
    result = process_policy(
        task_data,
        initial_strategy=native_strategy,
        max_repair_attempts=budget.max_repair_attempts,
        optimize_quality=budget.optimize_quality,
        call_style=budget.call_style,
        max_no_improvement=1,
        # HLLM 经验库：进程内记忆，跨 run() 调用复用成功修复组合。
        experience_store=experience_store,
        llm_mode=llm_mode,
        llm_provider=_provider_for_budget(llm_provider, budget),
        call_log=call_log,
    )
    return _build_feedback(
        result,
        execution,
        strategy_v1,
        task=task,
        capabilities=capabilities,
        llm_mode=llm_mode,
        budget=budget,
        trigger_reasons=trigger_reasons,
        run_id=run_id,
    )

def reset_experience_store() -> None:
    """清空进程级经验库，供测试和可重复演示使用。"""
    global _EXPERIENCE_STORE
    _EXPERIENCE_STORE = ExperienceStore()
