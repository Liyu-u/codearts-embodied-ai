"""TraceCoder-style iterative repair loop for robot policies."""

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from .agents import LLMPolicyAgentSuite, PolicyAgentSuite
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
    optimize_quality: bool = True,
    agent_suite: PolicyAgentSuite | None = None,
    evaluator: Callable[[dict, dict, dict | None], dict] = evaluate_policy,
    experience_store: ExperienceStore | None = None,
) -> dict:
    """Evaluate and repair a robot policy while retaining the best safe version.

    experience_store: 传入 HLLM 经验库时启用『经验优先』——修复阶段先查库，
    命中则直接复用已知成功补丁（跳过三角色推理），未命中才走规则流水线；
    任务收敛后把成功修复的生成器组合回写经验库。默认 None 保持原行为。
    optimize_quality=True 时，策略通过全部检查后不立即停止，而是继续尝试
    提升 安全/平滑/效率 质量分，直到连续无改善收敛。
    """
    strategy = normalize_strategy(
        initial_strategy or task_data.get("initial_strategy", {})
    )
    suite = agent_suite or (
        LLMPolicyAgentSuite(model) if use_llm else PolicyAgentSuite()
    )

    initial_result = evaluator(task_data, strategy, None)
    initial_signature = eval_signature(initial_result) if experience_store else None
    best_strategy = deepcopy(strategy)
    best_result = initial_result
    history: list[dict] = []

    if initial_result.get("passed"):
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
        }

    no_improvement = 0
    achieved_pass = False
    stopped_reason = "达到最大修改次数。"
    attempted_patch_signatures: set[str] = set()
    hllm_hits = 0
    hllm_misses = 0
    applied_gen_names: list[str] = []  # 修复阶段（通过前）用过的生成器名，用于回写经验

    for attempt in range(1, max_repair_attempts + 1):
        source = "rules"
        # HLLM 前置层：仅在“尚未通过”时查库，命中则跳过三角色推理
        if experience_store is not None and not achieved_pass and not best_result.get("passed"):
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
            observation = suite.observation_advice(
                best_strategy, best_result, history
            )
            diagnosis = suite.diagnosis(
                best_strategy, best_result, observation, history
            )
            patch = suite.patch(
                best_strategy, diagnosis, best_result, history
            )
            source = "rules"
        validation = validate_patch(patch)

        attempt_record = {
            "attempt": attempt,
            "source": source,
            "observation_advice": observation,
            "diagnosis": diagnosis,
            "patch": patch,
            "patch_validation": validation,
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
    return {
        "task_id": task_data.get("task_id"),
        "status": "PASSED" if final_passed else "NOT_READY",
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
