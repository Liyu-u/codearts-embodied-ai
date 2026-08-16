"""统一闭环编排骨架。各模块接入后，在 adapters 中实现调用。"""

from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]


def run_pipeline(perception: dict, instruction: str, adapters: dict) -> dict:
    """感知 -> 意图 -> 策略 -> 执行 -> 反馈；适配器由联调环境注入。"""
    task = adapters["intent"].run({"instruction": instruction, "perception": perception})
    if task.get("status") != "READY":
        return {"status": "BLOCKED", "task": task}
    strategy = adapters["strategy"].run(task)
    execution = adapters["executor"].run(strategy)
    feedback = adapters.get("tracecoder")
    # TraceCoder 修复需要『任务 + 当前策略 + 执行日志』三份上下文：
    # execution.v1 是执行证据（未来 Isaac Sim 接入后由真实仿真日志承担），
    # task.v1 / strategy.v1 用于还原任务目标与待修复策略。
    feedback_out = (
        feedback.run(
            {"task": task, "strategy": strategy, "execution": execution}
        )
        if feedback
        else None
    )
    return {"status": execution.get("status"), "task": task, "strategy": strategy,
            "execution": execution, "feedback": feedback_out}
