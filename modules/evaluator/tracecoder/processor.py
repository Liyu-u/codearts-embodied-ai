"""TraceCoder-style iterative repair loop for robot policies."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from .agents import LLMRequiredError, LLMPolicyAgentSuite, PolicyAgentSuite
from .evaluator import evaluate_policy, is_better
from .experience import ExperienceStore, classify_generator, compose_patch, eval_signature
from .models import normalize_strategy
from .patcher import apply_patch, validate_patch


def _history_lesson(candidate: dict, previous: dict) -> str:
    if candidate.get("passed"):
        return "修改通过全部基础检查、任务检查和安全检查。"
    if candidate.get("stage") == "static_check":
        return "修改引入或未解决基础格式与参数问题。"
    if not candidate.get("score", {}).get("safety_passed", False):
        return "修改造成安全问题，后续不得以效率或任务完成抵消安全违规。"
    if not is_better(candidate, previous):
        return "修改没有提高场景通过率或任务完成率。"
    return "修改有所改善，但仍有测试场景没有通过。"


def process_policy(
    task_data: dict,
    initial_strategy: dict | None = None,
    *,
    max_repair_attempts: int = 5,
    max_no_improvement: int = 2,
    use_llm: bool = False,
    model: str = "",
    llm_mode: str = "off",  # off | optional | required（默认 off = 纯规则）
    llm_provider=None,  # 注入的 LLM Provider（测试用）；None 时按 env/参数构造
    call_log: list = None,  # 传入则追加 LLM 调用证据，None 则内部新建
    optimize_quality: bool = True,
    call_style: str = "roles",
    agent_suite: PolicyAgentSuite | None = None,
    evaluator: Callable[[dict, dict, dict | None], dict] = evaluate_policy,
    experience_store: ExperienceStore | None = None,
) -> dict:
    """Evaluate and repair a robot policy while retaining the best safe version.

    LLM 三模式（llm_mode）：
      - off：纯规则三角色，零 LLM 调用（历史行为，完全兼容）。
      - optional：三角色先问 LLM，成功用 LLM 输出；失败回退规则并记录
        used_fallback=True（不静默）。HLLM 经验库仍生效。
      - required：每轮必须用 LLM，LLM 失败/输出不可用即中止
        （status=LLM_REQUIRED_FAILED），绝不产出规则 patch；不经经验库。
    所有模式下，LLM 生成的 patch 仍必须过本地白名单 validate_patch。
    证据：每次调用记入 call_log（模型/请求号/耗时/是否回退），结果返回
    llm_stats 汇总 + 完整 call_log。

    experience_store: 传入 HLLM 经验库时启用『经验优先』——修复阶段先查库，
    命中则直接复用已知成功补丁（跳过三角色推理），未命中才走规则流水线；
    任务收敛后把成功修复的生成器组合回写经验库。默认 None 保持原行为。
    optimize_quality=True 时，策略通过全部检查后不立即停止，而是继续尝试
    提升 安全/平滑/效率 质量分，直到连续无改善收敛。

    call_style="compact" 时，普通故障把 observation/diagnosis/patch 合并为一次
    结构化请求；模型要求升级时才追加一次详细 repair 请求。
    """
    strategy = normalize_strategy(
        initial_strategy or task_data.get("initial_strategy", {})
    )
    # 兼容旧参数：use_llm=True 视为 optional（LLM 优先、失败回退规则）
    mode = llm_mode if llm_mode != "off" else ("optional" if use_llm else "off")
    if call_log is None:
        call_log = []
    suite = agent_suite or (
        LLMPolicyAgentSuite(
            provider=llm_provider, mode=mode, call_log=call_log, model=model,
            call_style=call_style,
        )
        if mode != "off" else PolicyAgentSuite()
    )

    initial_result = evaluator(task_data, strategy, None)
    initial_signature = eval_signature(initial_result) if experience_store else None
    best_strategy = deepcopy(strategy)
    best_result = initial_result
    history: list[dict] = []

    # 纯规则（off）模式：初始通过直接返回，与历史行为完全一致。
    # LLM 模式（optional/required）：初始通过也要走一轮 LLM 质量确认，
    # 以留下『模型确实参与』的调用证据，再在质量轮收敛（见下）。
    if initial_result.get("passed") and mode == "off":
        return {
            "task_id": task_data.get("task_id"),
            "status": "PASSED",
            "initial_passed": True,
            "final_passed": True,
            "best_strategy": best_strategy,
            "initial_evaluation": initial_result,
            "final_evaluation": best_result,
            "repair_history": history,
            "stopped_reason": "初始策略已通过全部检查。",
            "deployment_advice": _deployment_advice(best_result),
            "hllm_stats": {"hits": 0, "misses": 0, "source": "none"},
            "llm_stats": _llm_stats(call_log, mode),
            "llm_required_failed": False,
            "call_log": call_log,
        }

    no_improvement = 0
    # LLM 模式初始即通过时，视为『已通过』直接进入质量优化轮（LLM 参与确认）。
    achieved_pass = bool(initial_result.get("passed"))
    stopped_reason = "达到最大修改次数。"
    llm_required_failed = False
    attempted_patch_signatures: set[str] = set()
    hllm_hits = 0
    hllm_misses = 0
    applied_gen_names: list[str] = []  # 修复阶段（通过前）用过的生成器名，用于回写经验

    for attempt in range(1, max_repair_attempts + 1):
        source = "rules"
        round_start = len(call_log)  # 本轮 LLM 调用证据起点
        # HLLM 前置层：仅在“尚未通过”时查库，命中则跳过三角色推理；
        # required（纯 LLM）模式不经经验库——每轮都必须由 LLM 产出结果。
        if (
            experience_store is not None
            and mode != "required"
            and not achieved_pass
            and not best_result.get("passed")
        ):
            hit = experience_store.lookup(best_result)
            if hit:
                gen_names, key = hit
                hllm_composed = compose_patch(best_strategy, best_result, gen_names)
                if hllm_composed is not None:
                    patch = hllm_composed
                    source = "hllm"
                    hllm_hits += 1
                    observation = _minimal_observation(best_result)
                    diagnosis = {
                        "failure_type": "HLLM_HIT",
                        "failure_step": None,
                        "evidence": ["命中经验库，直接复用已知成功修复。"],
                        "repair_plan": ["应用经验补丁后仿真验证；若失败则回退规则流水线。"],
                    }
                    hit_key = key
                else:
                    hllm_misses += 1
                    patch = None
            else:
                hllm_misses += 1
                patch = None
        else:
            patch = None

        if patch is None:
            try:
                observation = suite.observation_advice(
                    best_strategy, best_result, history
                )
                diagnosis = suite.diagnosis(
                    best_strategy, best_result, observation, history
                )
                patch = suite.patch(
                    best_strategy, diagnosis, best_result, history
                )
                source = "llm" if isinstance(suite, LLMPolicyAgentSuite) else "rules"
            except LLMRequiredError as error:
                # required 模式：LLM 未产出可用结果 → 立即中止，拒绝回退规则
                llm_required_failed = True
                attempt_record = {
                    "attempt": attempt,
                    "source": "llm_required_failed",
                    "observation_advice": {},
                    "diagnosis": {},
                    "patch": {},
                    "patch_validation": {
                        "passed": False,
                        "issues": [str(error)],
                    },
                    "llm_calls": list(call_log[round_start:]),
                }
                attempt_record["result"] = {
                    "passed": False,
                    "stage": "llm_required_failed",
                    "result": str(error),
                }
                attempt_record["lesson"] = (
                    "required 模式：LLM 未产出可用结果，拒绝回退规则，中止。"
                )
                history.append(attempt_record)
                stopped_reason = (
                    "required 模式：模型调用失败，拒绝回退到规则（{}）。"
                ).format(error)
                break
        validation = validate_patch(patch)

        attempt_record = {
            "attempt": attempt,
            "source": source,
            "observation_advice": observation,
            "diagnosis": diagnosis,
            "patch": patch,
            "patch_validation": validation,
            "llm_calls": list(call_log[round_start:]),
        }

        if not validation["passed"]:
            # 已通过后修复器提不出合法方案，视为质量优化收敛
            if achieved_pass and optimize_quality:
                attempt_record["result"] = {
                    "passed": True,
                    "stage": "quality_optimization",
                    "result": "策略已通过全部检查，修复器未提出新的质量改进。",
                }
                attempt_record["lesson"] = "质量优化阶段无可应用改进，停止优化。"
                history.append(attempt_record)
                stopped_reason = "策略已通过全部检查，质量分收敛。"
                break
            attempt_record["result"] = {
                "passed": False,
                "stage": "patch_validation",
                "result": "; ".join(validation["issues"]),
            }
            attempt_record["lesson"] = "修改内容不合法，没有应用到最佳策略。"
            history.append(attempt_record)
            stopped_reason = "修改角色无法生成可安全应用的修改。"
            break

        signature = repr(patch.get("changes"))
        if signature in attempted_patch_signatures:
            if achieved_pass:
                attempt_record["result"] = {
                    "passed": True,
                    "stage": "quality_optimization",
                    "result": "连续生成相同修改，质量优化提前收敛。",
                }
                attempt_record["lesson"] = "质量优化重复提交相同修改，停止优化。"
                history.append(attempt_record)
                stopped_reason = "策略已通过全部检查，质量分收敛。"
                break
            attempt_record["result"] = {
                "passed": False,
                "stage": "patch_validation",
                "result": "检测到重复修改。",
            }
            attempt_record["lesson"] = "该修改已经尝试过，不能继续重复。"
            history.append(attempt_record)
            stopped_reason = "连续生成相同修改，提前停止。"
            break
        attempted_patch_signatures.add(signature)

        try:
            candidate_strategy = apply_patch(best_strategy, patch)
        except ValueError as error:
            attempt_record["result"] = {
                "passed": False,
                "stage": "patch_application",
                "result": str(error),
            }
            attempt_record["lesson"] = "修改目标不存在或修改无法应用。"
            history.append(attempt_record)
            stopped_reason = "修改无法应用。"
            break

        candidate_result = evaluator(task_data, candidate_strategy, observation)
        attempt_record["candidate_strategy"] = candidate_strategy
        attempt_record["result"] = candidate_result
        attempt_record["lesson"] = _history_lesson(candidate_result, best_result)
        history.append(attempt_record)

        # 只有“通过 或 明显改善”的轮次才计入经验（失败的轮次不污染经验库）
        if source == "rules":
            gen = classify_generator(diagnosis)
            if gen and (candidate_result.get("passed") or is_better(candidate_result, best_result)):
                applied_gen_names.append(gen)
        elif source == "hllm" and not candidate_result.get("passed"):
            # 经验补丁应用后仍失败 → 记 fail，不再推荐该签名组合
            if experience_store is not None:
                experience_store.mark_fail(hit_key)

        if candidate_result.get("passed"):
            achieved_pass = True
            improved = is_better(candidate_result, best_result)
            if improved:
                best_strategy = candidate_strategy
                best_result = candidate_result
            if not optimize_quality:
                stopped_reason = "修改后的策略通过全部检查。"
                break
            # 质量优化阶段：已通过，只有质量分提高才算改善；改善则重置连续无改善计数
            if improved:
                no_improvement = 0
            else:
                no_improvement += 1
            if no_improvement >= max_no_improvement:
                stopped_reason = "策略已通过全部检查，质量分连续无改善。"
                break
            continue

        if is_better(candidate_result, best_result):
            best_strategy = candidate_strategy
            best_result = candidate_result
            no_improvement = 0
        else:
            no_improvement += 1

        if no_improvement >= max_no_improvement:
            stopped_reason = f"连续 {no_improvement} 次修改没有改善。"
            break

    final_passed = bool(best_result.get("passed"))
    # HLLM 学习回写：任务收敛后，把这次用到的修复生成器组合记入经验库，
    # 使后续同签名的任务能“直接利用、不用重复判断”。
    if experience_store is not None and initial_signature is not None:
        if final_passed and applied_gen_names:
            experience_store.learn(
                initial_signature, applied_gen_names,
                task_id=str(task_data.get("task_id")),
            )
    if llm_required_failed:
        status = "LLM_REQUIRED_FAILED"
    elif final_passed:
        status = "PASSED"
    else:
        status = "NOT_READY"
    return {
        "task_id": task_data.get("task_id"),
        "status": status,
        "initial_passed": False,
        "final_passed": final_passed,
        "best_strategy": best_strategy,
        "initial_evaluation": initial_result,
        "final_evaluation": best_result,
        "repair_history": history,
        "stopped_reason": stopped_reason,
        "deployment_advice": _deployment_advice(best_result),
        "hllm_stats": {
            "hits": hllm_hits,
            "misses": hllm_misses,
            "source": "experience" if experience_store is not None else "none",
        },
        "llm_stats": _llm_stats(call_log, mode),
        "llm_required_failed": llm_required_failed,
        "call_log": call_log,
    }


def _minimal_observation(evaluation: dict) -> dict:
    """HLLM 命中时不再调用观察角色，直接给出最小观察（聚焦失败步骤）。"""
    focus = []
    for scenario in evaluation.get("scenario_results", []):
        for event in scenario.get("execution", {}).get("trace", []):
            if (event.get("phase", "main") == "main"
                    and event.get("result", {}).get("status") != "SUCCESS"
                    and event.get("step_id")):
                focus.append(event["step_id"])
    return {
        "focus_steps": list(dict.fromkeys(focus)),
        "observe": ["robot.position", "robot.gripper_empty", "action.result"],
        "reason": "HLLM 经验库命中，跳过三角色推理，仅聚焦失败步骤。",
    }


def _deployment_advice(result: dict) -> dict:
    passed = bool(result.get("passed"))
    return {
        "validation_level": "LIGHTWEIGHT_SIMULATION",
        "ready_for_simulation": passed,
        "ready_for_real_robot": False,
        "must_not_deploy_to_real_robot": True,
        "reason": (
            "已通过轻量模拟检查，仍需在正式仿真器中验证后才能进行真机低速测试。"
            if passed else
            "策略尚未通过全部检查，不得部署到真实机器人。"
        ),
    }


def _llm_stats(call_log: list, mode: str) -> dict:
    """Summarize calls, latency, and token usage for cost/quality comparison."""
    calls = len(call_log)
    prompt_tokens = sum(int(record.get("prompt_tokens", 0) or 0) for record in call_log)
    completion_tokens = sum(int(record.get("completion_tokens", 0) or 0) for record in call_log)
    reasoning_tokens = sum(int(record.get("reasoning_tokens", 0) or 0) for record in call_log)
    if calls == 0:
        return {
            "mode": mode, "calls": 0, "ok_calls": 0,
            "fallback_calls": 0, "failed_calls": 0, "total_latency_ms": 0.0,
            "prompt_tokens": 0, "completion_tokens": 0,
            "reasoning_tokens": 0, "total_tokens": 0,
        }
    statuses = [record.get("status") for record in call_log]
    return {
        "mode": mode,
        "calls": calls,
        "ok_calls": statuses.count("ok"),
        "fallback_calls": statuses.count("fallback"),
        "failed_calls": statuses.count("failed"),
        "total_latency_ms": round(
            sum(record.get("latency_ms", 0.0) for record in call_log), 1,
        ),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
