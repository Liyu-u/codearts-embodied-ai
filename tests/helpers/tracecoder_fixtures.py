"""TraceCoder 联调测试的共享 fixture 与 Mock 执行器。

demo 场景：『红杯放入左托盘』，注入一次抓取失败（grasp: 1）。
贪心基线策略没有 on_failure 恢复 → 初始失败；TraceCoder 生成修复 patch，
由上游重新执行后才能确认是否通过。

Mock 执行器（mock_executor_run）是 C 模块（Isaac Sim 执行器）接入前的
替身：把 strategy.v1 放到 TraceCoder 轻量仿真里跑一遍，产出 execution.v1。
这样反馈环节（D 模块 TraceCoder）能独立跑通 Mock 闭环，符合仓库
『先 Mock，后仿真，最后真机』的原则。
"""

from __future__ import annotations

from integration.adapters.tracecoder import _strategy_v1_to_native
from integration.adapters.tracecoder import resolve_task_data
from modules.evaluator.tracecoder.evaluator import evaluate_policy
from modules.evaluator.tracecoder.executor import execute_strategy

DEMO_TASK_V1 = {
    "schema_version": "task.v1",
    "task_id": "demo_place_cup",
    "action": "place_object",
    "target_ids": ["red_cup"],
    "destination_id": "left_bin",
    "constraints": ["max_api_calls=12"],
    "status": "READY",
    # TraceCoder 轻量仿真阶段的任务描述（附加字段，schema 允许 additionalProperties）。
    # 接入 Isaac Sim 后，objects 位姿改由 perception.v1 提供。
    "tracecoder": {
        "objects": [
            {"id": "red_cup", "name": "red_cup",
             "position": [0.5, 0.0, 0.0], "visible": True, "reachable": True,
             "orientation": 0.0},
            {"id": "left_bin", "name": "left_bin",
             "position": [0.5, 0.5, 0.0], "visible": True, "reachable": True,
             "orientation": 0.0},
        ],
        "goals": [{"type": "object_inside", "object": "red_cup", "container": "left_bin"}],
        "scenarios": [{"name": "normal", "required": True, "failures": {"grasp": 1}}],
    },
}

# 贪心基线：识别→靠近→抓取→移动→释放，无失败恢复（工业界常规写法，非人工埋缺陷）。
DEMO_STRATEGY_V1 = {
    "schema_version": "strategy.v1",
    "task_id": "demo_place_cup",
    "steps": [
        {"step_id": "detect_cup", "action": "detect_object",
         "arguments": {"object_id": "red_cup"}},
        {"step_id": "approach_cup", "action": "move_to_object",
         "arguments": {"object_id": "$detect_cup.object_id"}},
        {"step_id": "grasp_cup", "action": "grasp",
         "arguments": {"object_id": "$detect_cup.object_id"}},
        {"step_id": "move_bin", "action": "move_to_target",
         "arguments": {"destination_id": "left_bin"}},
        {"step_id": "release_cup", "action": "release", "arguments": {}},
    ],
    "code": None,
}


def demo_task_data() -> dict:
    native = _strategy_v1_to_native(DEMO_STRATEGY_V1)
    return resolve_task_data(DEMO_TASK_V1, native, {"status": "RUNNING"})


def mock_executor_run(strategy_v1: dict, task_v1: dict) -> dict:
    """Mock 执行器：strategy.v1 → execution.v1（用 TraceCoder 轻量仿真）。

    返回满足 execution.schema.json 的执行日志；status 由引擎目标+安全检查判定。
    这是 C 模块（Isaac Sim 执行器）接入前的临时替身，接入后替换为真执行器，
    TraceCoder 消费 execution.v1 的逻辑不变。
    """
    native = _strategy_v1_to_native(strategy_v1)
    task_data = resolve_task_data(task_v1, native, {"status": "RUNNING"})
    scenario = (task_data.get("scenarios") or [{"name": "normal"}])[0]
    exec_result = execute_strategy(native, task_data["initial_state"], scenario)
    evaluation = evaluate_policy(task_data, native)

    steps = []
    for event in exec_result["trace"]:
        steps.append({
            "step_id": event["step_id"],
            "phase": event["phase"],
            "action": event["action"],
            "status": (event["result"] or {}).get("status"),
            "duration_ms": event["duration_ms"],
        })
    return {
        "schema_version": "execution.v1",
        "task_id": task_v1["task_id"],
        "status": "SUCCEEDED" if evaluation.get("passed") else "FAILED",
        "steps": steps,
        "trajectory_points": exec_result["trajectory_points"],
        "total_duration_ms": exec_result["total_duration_ms"],
        "safety_events": [
            {"type": "collision", "count": 0},
        ],
    }
