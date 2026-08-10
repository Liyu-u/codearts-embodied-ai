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
    return {"status": execution.get("status"), "task": task, "strategy": strategy,
            "execution": execution, "feedback": feedback.run(execution) if feedback else None}
