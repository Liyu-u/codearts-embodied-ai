"""Three policy-repair agents with offline rules and optional LLM enhancement."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any


def _failed_events(evaluation: dict) -> list[dict]:
    events = []
    for scenario in evaluation.get("scenario_results", []):
        for event in scenario.get("execution", {}).get("trace", []):
            if event.get("result", {}).get("status") != "SUCCESS":
                events.append({
                    "scenario": scenario.get("name"),
                    **deepcopy(event),
                })
    return events


class ObservationAgent:
    """Select the steps and state fields that deserve extra attention."""

    def advise(
        self,
        strategy: dict,
        evaluation: dict,
        history: list[dict],
    ) -> dict:
        del strategy, history
        failed = _failed_events(evaluation)
        focus_steps = list(dict.fromkeys(
            event.get("step_id") for event in failed if event.get("step_id")
        ))
        return {
            "focus_steps": focus_steps,
            "observe": [
                "robot.position",
                "robot.gripper_empty",
                "robot.gripper_object",
                "action.arguments",
                "action.result",
                "target_object.position",
                "safety.collision_count",
            ],
            "reason": (
                "重点记录失败动作前后的机器人、目标物体和安全状态。"
                if focus_steps else
                "未发现明确动作失败，重点记录最终任务状态。"
            ),
        }


class AnalysisAgent:
    """Turn static issues and execution traces into an evidence-based diagnosis."""

    def diagnose(
        self,
        strategy: dict,
        evaluation: dict,
        observation_advice: dict,
        history: list[dict],
    ) -> dict:
        del observation_advice
        if evaluation.get("stage") == "static_check":
            issue = evaluation.get("issues", [{}])[0]
            return {
                "failure_step": issue.get("step_id"),
                "failure_type": issue.get("type", "STATIC_CHECK_FAILED"),
                "evidence": [issue.get("message", "策略基础检查失败")],
                "root_cause": issue.get("message", "策略格式或参数不正确"),
                "repair_plan": ["只修改检查报告指出的步骤，然后重新执行全部场景。"],
                "mistakes_to_avoid": _recent_lessons(history),
            }

        # 策略已通过全部检查：恢复过程中的失败事件属于预期恢复（on_failure 重试），
        # 不算真实失败。直接进入质量优化阶段（找通过 → 调最优）。
        if evaluation.get("passed"):
            return {
                "failure_step": None,
                "failure_type": "QUALITY_OPTIMIZATION",
                "evidence": ["策略已通过全部检查，进入质量优化阶段。"],
                "root_cause": "策略功能正确，但可能存在冗余步骤或可压缩的执行路径。",
                "repair_plan": ["检查并删除无效果的冗余步骤，保持功能不变。"],
                "mistakes_to_avoid": _recent_lessons(history),
            }

        failed = _failed_events(evaluation)
        # 只把「主流程里、且尚未配备 on_failure」的失败视为真实失败：
        #  - 恢复阶段（on_failure 重试 / safe_stop）的失败属于预期恢复过程；
        #  - 已带 on_failure 的主流程步骤，其首次失败正是留给恢复去处理的，
        #    也不应再驱动 ACTION_FAILED 修复。
        # 否则多物体任务里修复某次抓取后，残留失败事件会被反复诊断为同一
        # ACTION_FAILED，掩盖真实的目标未达成，造成修复循环空转。
        actionable = [
            event for event in failed
            if event.get("phase", "main") == "main"
            and not (_find_step(strategy, event.get("step_id")) or {}).get("on_failure")
        ]
        if actionable:
            event = actionable[0]
            step_id = event.get("step_id")
            reason = event.get("result", {}).get("reason", "ACTION_FAILED")
            return {
                "failure_step": step_id,
                "failure_type": "ACTION_FAILED",
                "action": event.get("action"),
                "reason": reason,
                "evidence": [
                    f"场景 {event.get('scenario')} 中步骤 {step_id} 返回 {reason}。",
                    (
                        "失败后夹爪仍为空。"
                        if event.get("after", {}).get("robot", {}).get(
                            "gripper_empty", True
                        ) else
                        "失败后的夹爪状态异常。"
                    ),
                ],
                "root_cause": "动作失败后缺少有效的确认或恢复处理。",
                "repair_plan": [
                    "在失败步骤上增加有限次数的恢复处理。",
                    "恢复前重新获取可能已经变化的环境信息。",
                    "恢复仍失败时安全停止。",
                ],
                "mistakes_to_avoid": _recent_lessons(history),
            }

        failed_goals = []
        for scenario in evaluation.get("scenario_results", []):
            for goal in scenario.get("goals", []):
                if not goal.get("passed"):
                    failed_goals.append(goal)
        if not failed_goals:
            return {
                "failure_step": None,
                "failure_type": "QUALITY_OPTIMIZATION",
                "evidence": ["策略已通过全部检查，进入质量优化阶段。"],
                "root_cause": "策略功能正确，但可能存在冗余步骤或可压缩的执行路径。",
                "repair_plan": ["检查并删除无效果的冗余步骤，保持功能不变。"],
                "mistakes_to_avoid": _recent_lessons(history),
            }
        return {
            "failure_step": None,
            "failure_type": "GOAL_NOT_REACHED",
            "evidence": [item.get("message") for item in failed_goals],
            "root_cause": "动作虽然执行完成，但最终环境状态没有满足任务要求。",
            "repair_plan": ["根据最终状态补充缺失的移动、抓取或释放动作。"],
            "mistakes_to_avoid": _recent_lessons(history),
        }


class RepairAgent:
    """Generate a small policy patch from a diagnosis."""

    def propose(
        self,
        strategy: dict,
        diagnosis: dict,
        evaluation: dict,
        history: list[dict],
    ) -> dict:
        del history
        failure_type = diagnosis.get("failure_type")
        step_id = diagnosis.get("failure_step")

        if failure_type == "MISSING_ARGUMENT":
            issue = evaluation.get("issues", [{}])[0]
            argument = issue.get("argument")
            source = _latest_detection_before(strategy, step_id)
            if argument == "object_id" and source:
                return {
                    "summary": "使用前一次识别结果补全物体编号。",
                    "changes": [{
                        "operation": "update_argument",
                        "target_step": step_id,
                        "argument": argument,
                        "value": f"${source}.object_id",
                    }],
                }

        if failure_type == "ACTION_FAILED":
            step = _find_step(strategy, step_id)
            if step and not step.get("on_failure"):
                action = step.get("action")
                if action == "grasp":
                    object_name = _object_name_from_strategy(strategy) or "target_object"
                    detect_id = f"{step_id}_redetect"
                    return {
                        "summary": "抓取失败时重新识别并有限重试，耗尽后安全停止。",
                        "changes": [{
                            "operation": "update_step",
                            "target_step": step_id,
                            "content": {
                                "on_failure": {
                                    "max_attempts": 2,
                                    "steps": [
                                        {
                                            "id": detect_id,
                                            "action": "detect_object",
                                            "arguments": {"object_name": object_name},
                                        },
                                        {
                                            "id": f"{step_id}_retry",
                                            "action": "grasp",
                                            "arguments": {
                                                "object_id": f"${detect_id}.object_id"
                                            },
                                        },
                                    ],
                                    "on_exhausted": "stop",
                                }
                            },
                        }],
                    }
                if action in ("rotate", "sweep"):
                    # 旋转/扫除失败时按原参数有限重试，耗尽后安全停止。
                    return {
                        "summary": f"{action} 失败时有限重试，耗尽后安全停止。",
                        "changes": [{
                            "operation": "update_step",
                            "target_step": step_id,
                            "content": {
                                "on_failure": {
                                    "max_attempts": 2,
                                    "steps": [{
                                        "id": f"{step_id}_retry",
                                        "action": action,
                                        "arguments": deepcopy(step.get("arguments", {})),
                                    }],
                                    "on_exhausted": "stop",
                                }
                            },
                        }],
                    }

        if failure_type == "GOAL_NOT_REACHED":
            goal = _first_failed_goal(evaluation)
            final_state = _first_final_state(evaluation)
            if goal and goal.get("type") in {"object_inside", "object_on"}:
                robot = final_state.get("robot", {})
                if robot.get("gripper_object") == goal.get("object"):
                    target = goal.get("container") or goal.get("base")
                    move_arguments = {"target": target}
                    if goal.get("type") == "object_on":
                        move_arguments["placement_mode"] = "stack_on"
                    return {
                        "summary": "物体仍在夹爪中，补充移动到目标并释放。",
                        "changes": [
                            {
                                "operation": "append_step",
                                "content": {
                                    "id": "repair_move_to_target",
                                    "action": "move_to_target",
                                    "arguments": move_arguments,
                                },
                            },
                            {
                                "operation": "append_step",
                                "content": {
                                    "id": "repair_release",
                                    "action": "release",
                                    "arguments": {},
                                },
                            },
                        ],
                    }

        if failure_type == "QUALITY_OPTIMIZATION":
            redundant = _find_redundant_step(strategy)
            if redundant:
                return {
                    "summary": "删除连续重复的冗余步骤，提升效率分。",
                    "changes": [{
                        "operation": "delete_step",
                        "target_step": redundant,
                    }],
                }
            return {
                "summary": "策略已通过且未发现可优化项，无需修改。",
                "changes": [],
            }

        return {
            "summary": "当前规则修复器无法安全确定修改内容。",
            "changes": [],
        }


class PolicyAgentSuite:
    def __init__(self):
        self.observation = ObservationAgent()
        self.analysis = AnalysisAgent()
        self.repair = RepairAgent()

    def observation_advice(self, strategy, evaluation, history):
        return self.observation.advise(strategy, evaluation, history)

    def diagnosis(self, strategy, evaluation, advice, history):
        return self.analysis.diagnose(
            strategy, evaluation, advice, history
        )

    def patch(self, strategy, diagnosis, evaluation, history):
        return self.repair.propose(
            strategy, diagnosis, evaluation, history
        )


class LLMPolicyAgentSuite(PolicyAgentSuite):
    """Use the configured model for each role, falling back to safe local rules."""

    def __init__(self, model: str):
        super().__init__()
        self.model = model

    def observation_advice(self, strategy, evaluation, history):
        fallback = self.observation.advise(strategy, evaluation, history)
        return self._ask(
            "观察角色",
            {
                "task": "选择下一轮需要重点观察的步骤和状态字段",
                "strategy": strategy,
                "evaluation": evaluation,
                "history": history[-2:],
                "required_output": {
                    "focus_steps": ["step id"],
                    "observe": ["state path"],
                    "reason": "text",
                },
            },
            fallback,
        )

    def diagnosis(self, strategy, evaluation, advice, history):
        fallback = self.analysis.diagnose(strategy, evaluation, advice, history)
        return self._ask(
            "分析角色",
            {
                "task": "只根据执行证据定位第一个关键失败并提出修复计划",
                "strategy": strategy,
                "evaluation": evaluation,
                "observation_advice": advice,
                "history": history[-2:],
            },
            fallback,
        )

    def patch(self, strategy, diagnosis, evaluation, history):
        fallback = self.repair.propose(strategy, diagnosis, evaluation, history)
        return self._ask(
            "修改角色",
            {
                "task": "生成最小范围的策略修改，只允许使用既定 patch 操作",
                "strategy": strategy,
                "diagnosis": diagnosis,
                "evaluation": evaluation,
                "allowed_operations": [
                    "update_argument", "update_step", "insert_before",
                    "insert_after", "append_step", "delete_step", "replace_action",
                ],
            },
            fallback,
        )

    def _ask(self, role: str, payload: dict, fallback: dict) -> dict:
        prompt = (
            f"你是新版 TraceCoder 的{role}。仅输出一个 JSON 对象，不要输出 Markdown。\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str)}"
        )
        try:
            # LLM 增强是可选能力（默认离线规则即可运行）。
            # 本包（modules/evaluator/tracecoder）不内置 LLM 调用代码——
            # 独立仓库 Codearts-Tracecoder 的 src.generation 提供 OpenAI 兼容
            # 接口，通过 TRACECODER_API_KEY / TRACECODER_BASE_URL 配置。
            # 在统一联调仓库中该依赖不可用时抛 ImportError，被外层 except
            # 捕获后自动回退到纯离线规则修复，不阻塞闭环。
            from src.generation import generator

            response, _, _ = generator(prompt, "policy_json", self.model)
            parsed = _extract_json(response)
            return parsed if isinstance(parsed, dict) else fallback
        except Exception:
            return fallback


def _extract_json(text: str) -> dict | None:
    if not isinstance(text, str):
        return None
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", candidate, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


def _recent_lessons(history: list[dict]) -> list[str]:
    lessons = []
    for item in history[-2:]:
        result = item.get("result", {})
        lessons.append(
            f"第 {item.get('attempt')} 次修改未完全通过：{result.get('result', '未知原因')}"
        )
    return lessons


def _find_step(strategy: dict, step_id: str | None) -> dict | None:
    return next(
        (step for step in strategy.get("steps", []) if step.get("id") == step_id),
        None,
    )


def _latest_detection_before(strategy: dict, step_id: str | None) -> str | None:
    latest = None
    for step in strategy.get("steps", []):
        if step.get("id") == step_id:
            break
        if step.get("action") == "detect_object":
            latest = step.get("id")
    return latest


def _object_name_from_strategy(strategy: dict) -> str | None:
    for step in strategy.get("steps", []):
        if step.get("action") == "detect_object":
            return step.get("arguments", {}).get("object_name")
    return None


def _first_failed_goal(evaluation: dict) -> dict | None:
    for scenario in evaluation.get("scenario_results", []):
        for goal_result in scenario.get("goals", []):
            if not goal_result.get("passed"):
                return goal_result.get("goal")
    return None


def _first_final_state(evaluation: dict) -> dict:
    scenarios = evaluation.get("scenario_results", [])
    if not scenarios:
        return {}
    return scenarios[0].get("execution", {}).get("final_state", {})


def _is_referenced(strategy: dict, step_id: str | None) -> bool:
    """判断步骤 id 是否被策略中其他步骤的参数引用。"""
    if not step_id:
        return False
    pattern = f"${step_id}."
    for step in strategy.get("steps", []):
        for value in (step.get("arguments") or {}).values():
            if isinstance(value, str) and value.startswith(pattern):
                return True
    return False


def _find_redundant_step(strategy: dict) -> str | None:
    """找到第一处动作与参数完全相同的连续步骤，且后者未被后续引用。"""
    steps = strategy.get("steps", [])
    for index in range(len(steps) - 1):
        first, second = steps[index], steps[index + 1]
        if (
            first.get("action") == second.get("action")
            and first.get("arguments") == second.get("arguments")
            and second.get("action") != "stop"
            and not _is_referenced(strategy, second.get("id"))
        ):
            return second.get("id")
    return None
