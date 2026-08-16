"""HLLM 经验库：纯规则『历史教训 / 记事本』模块。

老师说的『回滚机制 / 记事本』，这里实现其核心价值：把『失败签名 → 已知成功
修复方案』记下来，下次遇到同样的失败**直接复用、不用重复判断**，而不是每次都
重新走 观察→分析→修改 的三角色推理。

设计要点：
1. **失败签名从评估结果直接归一化**（不经过 agent），确定、可复现：
   - ACTION_FAILED  → ``ACTION|<action>``（如 ``ACTION|grasp``）
   - GOAL_NOT_REACHED → ``GOAL|<goal_type>``（如 ``GOAL|object_inside``）
   - 同一种失败在不同任务里共享签名 → 跨任务学习成为可能。
2. 经验条目存的是『修复方案生成器名』组合 + 成功/失败计数；生成器按当前策略
   上下文实时生成白名单补丁（参数占位符在渲染时从当前任务解析）。
3. **命中后仍做仿真验证（不跳过）**——签名匹配但上下文不同时绝不盲目套用；
   应用失败则记 fail 并回退到正常规则流水线。
4. 纯规则、离线、零 API 成本；可持久化为 JSON，供跨会话复用与审计。
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


# ---------------------------------------------------------------------------
# 失败签名
# ---------------------------------------------------------------------------

def _main_failed_events(evaluation: dict) -> list[dict]:
    """收集所有『主流程 + 失败』的事件（带场景名），不经过 agent 推理。"""
    events = []
    for scenario in evaluation.get("scenario_results", []):
        for event in scenario.get("execution", {}).get("trace", []):
            if event.get("phase", "main") == "main" and (
                event.get("result", {}).get("status") != "SUCCESS"
            ):
                events.append(event)
    return events


def _failed_goals(evaluation: dict) -> list[dict]:
    goals = []
    for scenario in evaluation.get("scenario_results", []):
        for goal in scenario.get("goals", []):
            if not goal.get("passed"):
                goals.append(goal.get("goal", {}))
    return goals


def eval_signature(evaluation: dict) -> frozenset[str]:
    """把一次评估结果归一化为失败签名集合。

    同一 action 的失败共享签名（不区分 GRASP_SLIPPED / OBJECT_DROPPED），
    因为它们的修复方案相同（都是加失败恢复）；GOAL 失败按目标类型归并。
    """
    sigs = set()
    for event in _main_failed_events(evaluation):
        action = event.get("action")
        if action:
            sigs.add(f"ACTION|{action}")
    for goal in _failed_goals(evaluation):
        goal_type = goal.get("type")
        if goal_type:
            sigs.add(f"GOAL|{goal_type}")
    return frozenset(sorted(sigs))


# ---------------------------------------------------------------------------
# 修复方案生成器（白名单补丁，参数从当前策略/评估上下文实时解析）
# ---------------------------------------------------------------------------

def _find_step(strategy: dict, step_id: str | None) -> dict | None:
    return next(
        (s for s in strategy.get("steps", []) if s.get("id") == step_id), None
    )


def _grasp_object_name(strategy: dict, grasp_step_id: str) -> str | None:
    """解析 grasp 步骤引用的 detect 步骤的 object_name，比取第一个 detect 更精确。"""
    step = _find_step(strategy, grasp_step_id)
    if not step:
        return None
    ref = (step.get("arguments") or {}).get("object_id", "")
    match = re.match(r"\$(\w+)\.", ref)
    if not match:
        return None
    detect = _find_step(strategy, match.group(1))
    if detect and detect.get("action") == "detect_object":
        return (detect.get("arguments") or {}).get("object_name")
    return None


def _gen_grasp_recovery(strategy: dict, evaluation: dict) -> dict | None:
    """第一个主流程抓取失败 → 加『重新识别 + 有限重试 + 安全停止』。"""
    for event in _main_failed_events(evaluation):
        if event.get("action") == "grasp":
            step_id = event.get("step_id")
            step = _find_step(strategy, step_id)
            if not step or step.get("on_failure"):
                continue
            object_name = _grasp_object_name(strategy, step_id) or "target_object"
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
                                {"id": detect_id, "action": "detect_object",
                                 "arguments": {"object_name": object_name}},
                                {"id": f"{step_id}_retry", "action": "grasp",
                                 "arguments": {"object_id": f"${detect_id}.object_id"}},
                            ],
                            "on_exhausted": "stop",
                        }
                    },
                }],
            }
    return None


def _gen_sweep_recovery(strategy: dict, evaluation: dict) -> dict | None:
    for event in _main_failed_events(evaluation):
        if event.get("action") == "sweep":
            step_id = event.get("step_id")
            step = _find_step(strategy, step_id)
            if not step or step.get("on_failure"):
                continue
            return {
                "summary": "扫除失败时按原参数有限重试，耗尽后安全停止。",
                "changes": [{
                    "operation": "update_step",
                    "target_step": step_id,
                    "content": {
                        "on_failure": {
                            "max_attempts": 2,
                            "steps": [{
                                "id": f"{step_id}_retry",
                                "action": "sweep",
                                "arguments": deepcopy(step.get("arguments", {})),
                            }],
                            "on_exhausted": "stop",
                        }
                    },
                }],
            }
    return None


def _gen_rotate_recovery(strategy: dict, evaluation: dict) -> dict | None:
    for event in _main_failed_events(evaluation):
        if event.get("action") == "rotate":
            step_id = event.get("step_id")
            step = _find_step(strategy, step_id)
            if not step or step.get("on_failure"):
                continue
            return {
                "summary": "旋转失败时按原参数有限重试，耗尽后安全停止。",
                "changes": [{
                    "operation": "update_step",
                    "target_step": step_id,
                    "content": {
                        "on_failure": {
                            "max_attempts": 2,
                            "steps": [{
                                "id": f"{step_id}_retry",
                                "action": "rotate",
                                "arguments": deepcopy(step.get("arguments", {})),
                            }],
                            "on_exhausted": "stop",
                        }
                    },
                }],
            }
    return None


def _gen_release_append(strategy: dict, evaluation: dict) -> dict | None:
    """物体仍在夹爪（GOAL_NOT_REACHED + object_inside）→ 补移动+释放。"""
    for scenario in evaluation.get("scenario_results", []):
        final_state = scenario.get("execution", {}).get("final_state", {})
        robot = final_state.get("robot", {})
        held = robot.get("gripper_object")
        for goal in scenario.get("goals", []):
            g = goal.get("goal", {})
            if (not goal.get("passed") and g.get("type") == "object_inside"
                    and held == g.get("object")):
                return {
                    "summary": "物体仍在夹爪中，补充移动到目标并释放。",
                    "changes": [
                        {"operation": "append_step",
                         "content": {"id": "repair_move_to_target",
                                     "action": "move_to_target",
                                     "arguments": {"target": g["container"]}}},
                        {"operation": "append_step",
                         "content": {"id": "repair_release",
                                     "action": "release", "arguments": {}}},
                    ],
                }
    return None


GENERATORS: dict[str, Any] = {
    "grasp_recovery": _gen_grasp_recovery,
    "sweep_recovery": _gen_sweep_recovery,
    "rotate_recovery": _gen_rotate_recovery,
    "release_append": _gen_release_append,
}

# 诊断 → 生成器名的映射（用于把成功修复的轮次记录成经验）
def classify_generator(diagnosis: dict) -> str | None:
    failure_type = diagnosis.get("failure_type")
    if failure_type == "ACTION_FAILED":
        return {
            "grasp": "grasp_recovery",
            "sweep": "sweep_recovery",
            "rotate": "rotate_recovery",
        }.get(diagnosis.get("action"))
    if failure_type == "GOAL_NOT_REACHED":
        return "release_append"
    return None


def compose_patch(strategy: dict, evaluation: dict, gen_names: list[str]) -> dict | None:
    """按生成器名列表，为当前策略/评估组合出完整补丁。"""
    changes: list[dict] = []
    summaries: list[str] = []
    for name in gen_names:
        generator = GENERATORS.get(name)
        if not generator:
            continue
        patch = generator(strategy, evaluation)
        if patch and patch.get("changes"):
            changes.extend(patch["changes"])
            summaries.append(patch["summary"])
    if not changes:
        return None
    return {
        "summary": "（HLLM 经验库命中，跳过三角色推理）" + "；".join(summaries),
        "changes": changes,
    }


# ---------------------------------------------------------------------------
# 经验库存储
# ---------------------------------------------------------------------------

def _key(signature: frozenset[str]) -> str:
    return ",".join(sorted(signature))


class ExperienceStore:
    """失败签名 → 成功修复生成器组合 的经验库。

    条目：:
        key -> {"gen_names": [...], "success": int, "fail": int, "task_id": str}
    """

    def __init__(self, entries: dict | None = None):
        self.entries: dict = entries or {}

    # ---- 查 ----
    def lookup(self, evaluation: dict) -> tuple[list[str], str] | None:
        signature = eval_signature(evaluation)
        key = _key(signature)
        entry = self.entries.get(key)
        if entry and entry.get("success", 0) > entry.get("fail", 0):
            return list(entry["gen_names"]), key
        return None

    # ---- 写（学习） ----
    def learn(self, signature: frozenset[str], gen_names: list[str],
              task_id: str = "") -> None:
        if not gen_names:
            return
        key = _key(signature)
        entry = self.entries.setdefault(key, {"gen_names": [], "success": 0, "fail": 0})
        if not entry["gen_names"]:
            entry["gen_names"] = list(gen_names)
        entry["success"] = entry.get("success", 0) + 1
        entry["task_id"] = task_id

    def mark_fail(self, key: str) -> None:
        entry = self.entries.get(key)
        if entry:
            entry["fail"] = entry.get("fail", 0) + 1

    # ---- 统计 / 持久化 ----
    def stats(self) -> dict:
        return {
            "entries": len(self.entries),
            "signatures": sorted(self.entries.keys()),
        }

    def to_dict(self) -> dict:
        return self.entries

    @classmethod
    def from_dict(cls, data: dict) -> "ExperienceStore":
        return cls(data)

    def save(self, path: str) -> None:
        import json
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps(self.entries, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @classmethod
    def load(cls, path: str) -> "ExperienceStore":
        import json
        from pathlib import Path
        data = {}
        if Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)
