"""Task, safety, robustness, smoothness, and efficiency evaluation."""

from __future__ import annotations

from copy import deepcopy
from math import acos

from .defaults import DEFAULT_API_CATALOG, DEFAULT_SAFETY_RULES
from .executor import execute_strategy
from .models import distance, find_object, normalize_strategy
from .static_checker import check_strategy


def _check_goal(goal: dict, final_state: dict) -> tuple[bool, str]:
    goal_type = goal.get("type")
    if goal_type == "object_inside":
        item = find_object(final_state, goal.get("object"))
        passed = bool(item and item.get("container") == goal.get("container"))
        return passed, (
            f"{goal.get('object')} 位于 {goal.get('container')} 内"
            if passed else
            f"{goal.get('object')} 未位于 {goal.get('container')} 内"
        )
    if goal_type == "gripper_empty":
        actual = final_state.get("robot", {}).get("gripper_empty", True)
        expected = goal.get("expected", True)
        return actual == expected, f"夹爪空闲状态为 {actual}，期望为 {expected}"
    if goal_type == "robot_stopped":
        actual = final_state.get("robot", {}).get("stopped", False)
        expected = goal.get("expected", True)
        return actual == expected, f"机器人停止状态为 {actual}，期望为 {expected}"
    if goal_type == "object_at":
        item = find_object(final_state, goal.get("object"))
        passed = bool(
            item and distance(item.get("position"), goal.get("position"))
            <= float(goal.get("tolerance", 0.1))
        )
        return passed, f"{goal.get('object')} 的位置检查{'通过' if passed else '失败'}"
    if goal_type == "object_oriented":
        item = find_object(final_state, goal.get("object"))
        actual = float(item.get("orientation", 0.0)) if item else None
        expected = float(goal.get("angle", 0.0))
        passed = actual is not None and abs(actual - expected) < 1e-6
        return passed, (
            f"{goal.get('object')} 朝向为 {actual}，期望为 {expected}"
            if actual is not None else
            f"{goal.get('object')} 未找到，无法检查朝向"
        )
    return False, f"不支持的任务目标类型：{goal_type}"


def _check_safety(
    execution: dict,
    safety_rules: dict,
) -> tuple[bool, list[str]]:
    state = execution["final_state"]
    safety = state.get("safety", {})
    violations = []
    if safety_rules.get("forbid_collision", True) and safety.get("collision_count", 0):
        violations.append(f"发生 {safety.get('collision_count')} 次碰撞")
    if (
        safety_rules.get("forbid_human_zone", True)
        and safety.get("entered_human_zone", False)
    ):
        violations.append("机器人进入人员禁区")
    max_calls = safety_rules.get("max_api_calls")
    if max_calls is not None and execution["api_call_count"] > max_calls:
        violations.append(
            f"动作调用次数 {execution['api_call_count']} 超过限制 {max_calls}"
        )
    return not violations, violations


def _turn_irregularity(positions: list[list[float]]) -> float:
    """位置序列的转向/震荡程度，0 表示完全平滑，1 表示剧烈震荡。"""
    if len(positions) < 3:
        return 0.0
    turns = []
    for index in range(1, len(positions) - 1):
        first = [a - b for a, b in zip(positions[index], positions[index - 1])]
        second = [a - b for a, b in zip(positions[index + 1], positions[index])]
        first_length = sum(value * value for value in first) ** 0.5
        second_length = sum(value * value for value in second) ** 0.5
        if not first_length or not second_length:
            continue
        cosine = sum(a * b for a, b in zip(first, second)) / (
            first_length * second_length
        )
        turns.append(acos(max(-1.0, min(1.0, cosine))) / 3.141592653589793)
    return sum(turns) / len(turns) if turns else 0.0


def _smoothness(
    trace: list[dict],
    trajectory_points: list[list[float]] | None = None,
) -> float:
    positions = []
    for event in trace:
        before = event["before"].get("robot", {}).get("position")
        after = event["after"].get("robot", {}).get("position")
        if before and not positions:
            positions.append(before)
        if after and (not positions or after != positions[-1]):
            positions.append(after)
    # 优先使用 move 产生的中间轨迹点，点数更密集、更能反映高频震荡
    if trajectory_points and len(trajectory_points) >= 2 and positions:
        full = positions[:1] + trajectory_points + positions[-1:]
        return max(0.0, 1.0 - _turn_irregularity(full))
    return max(0.0, 1.0 - _turn_irregularity(positions))


def _safety_score(
    execution: dict,
    safety_rules: dict,
) -> float:
    """安全合规分（0-1），从满分按违规严重程度扣减。"""
    safety = execution["final_state"].get("safety", {})
    score = 100.0
    if safety_rules.get("forbid_collision", True):
        score -= 30.0 * safety.get("collision_count", 0)
    if (
        safety_rules.get("forbid_human_zone", True)
        and safety.get("entered_human_zone", False)
    ):
        score -= 40.0
    max_calls = safety_rules.get("max_api_calls")
    if max_calls is not None and execution["api_call_count"] > max_calls:
        score -= 20.0
    return max(0.0, min(100.0, score)) / 100.0


def evaluate_policy(
    task_data: dict,
    strategy: dict,
    observation_advice: dict | None = None,
) -> dict:
    strategy = normalize_strategy(strategy)
    api_catalog = task_data.get("api_catalog") or DEFAULT_API_CATALOG
    static_result = check_strategy(strategy, api_catalog)
    if not static_result["passed"]:
        return {
            "passed": False,
            "stage": "static_check",
            "passed_count": 0,
            "failed_count": len(static_result["issues"]),
            "total_count": len(static_result["issues"]),
            "result": "策略基础检查失败。",
            "issues": static_result["issues"],
            "scenario_results": [],
            "score": {
                "safety_passed": True,
                "scenario_success_rate": 0.0,
                "goal_success_rate": 0.0,
                "smoothness": 0.0,
                "efficiency": 0.0,
                "average_api_calls": 0.0,
                "average_duration_ms": 0.0,
                "safety_score": 0.0,
                "quality_score": 0.0,
                "dimensions": {
                    "safety": 0.0,
                    "smoothness": 0.0,
                    "efficiency": 0.0,
                },
            },
        }

    scenarios = task_data.get("scenarios") or [{"name": "default"}]
    goals = task_data.get("goals", [])
    safety_rules = {
        **DEFAULT_SAFETY_RULES,
        **task_data.get("safety_rules", {}),
    }
    scenario_results = []
    passed_assertions = 0
    total_assertions = 0
    total_goals = 0
    passed_goals = 0
    required_scenarios = 0
    passed_required_scenarios = 0

    for scenario in scenarios:
        execution = execute_strategy(
            strategy,
            task_data.get("initial_state", {}),
            scenario,
            observation_advice,
        )
        goal_results = []
        for goal in goals:
            goal_passed, message = _check_goal(goal, execution["final_state"])
            goal_results.append({
                "goal": deepcopy(goal),
                "passed": goal_passed,
                "message": message,
            })
            total_goals += 1
            total_assertions += 1
            if goal_passed:
                passed_goals += 1
                passed_assertions += 1

        safety_passed, violations = _check_safety(execution, safety_rules)
        total_assertions += 1
        if safety_passed:
            passed_assertions += 1

        scenario_passed = all(item["passed"] for item in goal_results) and safety_passed
        required = scenario.get("required", True)
        if required:
            required_scenarios += 1
            if scenario_passed:
                passed_required_scenarios += 1

        scenario_results.append({
            "name": scenario.get("name", "default"),
            "required": required,
            "passed": scenario_passed,
            "goals": goal_results,
            "safety_passed": safety_passed,
            "safety_score": _safety_score(execution, safety_rules),
            "violations": violations,
            "execution": execution,
            "smoothness": _smoothness(
                execution["trace"], execution.get("trajectory_points")
            ),
            "duration_ms": execution.get("total_duration_ms", 0),
        })

    safety_passed = all(item["safety_passed"] for item in scenario_results)
    scenario_rate = (
        passed_required_scenarios / required_scenarios if required_scenarios else 1.0
    )
    goal_rate = passed_goals / total_goals if total_goals else 1.0
    average_calls = sum(
        item["execution"]["api_call_count"] for item in scenario_results
    ) / len(scenario_results)
    average_duration_ms = sum(
        item["duration_ms"] for item in scenario_results
    ) / len(scenario_results)
    average_smoothness = sum(
        item["smoothness"] for item in scenario_results
    ) / len(scenario_results)
    average_safety_score = sum(
        item["safety_score"] for item in scenario_results
    ) / len(scenario_results)
    # 效率分：若任务提供 reference_duration_ms（理想完成时长），用相对基准；
    # 否则回退到基于总执行时长的口径。
    reference_duration_ms = task_data.get("reference_duration_ms")
    if reference_duration_ms:
        efficiency = min(1.0, float(reference_duration_ms) / average_duration_ms)
    else:
        efficiency = 1.0 / (1.0 + average_duration_ms / 1000.0)
    passed = safety_passed and scenario_rate == 1.0
    # 百分制综合质量分（展示用）：通过率 × 三维加权（安全 > 平滑 > 效率）
    quality_score = round(
        100.0 * scenario_rate
        * (0.5 * average_safety_score + 0.3 * average_smoothness + 0.2 * efficiency),
        1,
    )
    failed_scenarios = [
        item["name"] for item in scenario_results if item["required"] and not item["passed"]
    ]

    return {
        "passed": passed,
        "stage": "execution",
        "passed_count": passed_assertions,
        "failed_count": total_assertions - passed_assertions,
        "total_count": total_assertions,
        "result": (
            "全部测试场景通过。"
            if passed else
            f"未通过场景：{', '.join(failed_scenarios) or '安全检查'}"
        ),
        "issues": [],
        "scenario_results": scenario_results,
        "score": {
            "safety_passed": safety_passed,
            "scenario_success_rate": scenario_rate,
            "goal_success_rate": goal_rate,
            "smoothness": average_smoothness,
            "efficiency": efficiency,
            "average_api_calls": average_calls,
            "average_duration_ms": average_duration_ms,
            "safety_score": average_safety_score,
            "quality_score": quality_score,
            "dimensions": {
                "safety": round(average_safety_score, 4),
                "smoothness": round(average_smoothness, 4),
                "efficiency": round(efficiency, 4),
            },
        },
    }


def result_rank(result: dict) -> tuple:
    score = result.get("score", {})
    executable = result.get("stage") == "execution"
    return (
        executable and bool(score.get("safety_passed", False)),
        executable,
        float(score.get("scenario_success_rate", 0.0)),
        float(score.get("goal_success_rate", 0.0)),
        float(score.get("smoothness", 0.0)),
        float(score.get("efficiency", 0.0)),
    )


def is_better(candidate: dict, current: dict) -> bool:
    return result_rank(candidate) > result_rank(current)
