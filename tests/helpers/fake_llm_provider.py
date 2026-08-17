"""可注入的确定性假 LLM Provider —— 离线验证『模型确实参与』而非静默回退。

真实 Provider（LLMProvider）走 OpenAI 兼容 REST，需要 API Key 且 CI 无网络。
假 Provider 实现同一接口 complete(system, user, json_mode)，由测试提供
handler 决定每次『模型输出』，因此可离线、确定性地验证三模式语义与调用证据，
且绝不触碰真实密钥。

接口约定（与 LLMProvider 一致）：
    complete(system: str, user: str, json_mode: bool | None) -> LLMResult

用法：
    fake = FakeLLMProvider(
        handler=my_handler,                # (role, payload, seq) -> dict | None
        fail=lambda role, seq: ...         # (role, seq) -> bool，True=本次调用失败
    )
    # 直接传给 LLMPolicyAgentSuite(provider=fake) 或
    # configure_llm(mode="required", provider=fake)
    assert fake.calls                      # 每次调用的 (role, payload, output) 证据

handler(role, payload, seq) -> dict | None：
    role   从 prompt 里的角色名识别（observation / analysis / repair）
    payload 本次调用的结构化内容（含 strategy / evaluation / diagnosis 等）
    seq    本次调用序号（1 起）
    返回 dict 即模型输出 JSON；返回 None 表示输出无法解析（走 fallback/中止）。
"""

from __future__ import annotations

import json
import re

from modules.evaluator.tracecoder.llm_provider import LLMResult

_ROLE_RE = re.compile(r"TraceCoder 的(\w+)。")


class FakeLLMProvider:
    """确定性的 LLM 替身：记录每次调用，按 handler 返回结构化 JSON。"""

    def __init__(self, handler=None, fail=None, model="fake-deepseek"):
        self.handler = handler or self._default_handler
        self.fail = fail  # callable(role, seq) -> bool；None 表示从不失败
        self.model = model
        self.calls = []  # [{seq, role, payload, output}]
        self._seq = 0

    def complete(self, system, user, json_mode=None):
        del system, json_mode
        self._seq += 1
        role = self._detect_role(user)
        if self.fail is not None and self.fail(role, self._seq):
            # 模拟网络/超时/限流等调用失败（与真实 Provider 的 ok=False 对齐）
            return LLMResult(
                ok=False,
                model=self.model,
                error="模拟模型调用失败（测试注入，seq={}）".format(self._seq),
            )
        payload = self._extract_payload(user)
        output = self.handler(role, payload, self._seq)
        self.calls.append({
            "seq": self._seq,
            "role": role,
            "payload": payload,
            "output": output,
        })
        return LLMResult(
            ok=True,
            text=json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else "",
            json=output if isinstance(output, dict) else None,
            model=self.model,
            request_id="fake-{:04d}".format(self._seq),
            latency_ms=2.0,
            prompt_tokens=max(1, len(user) // 4),
            completion_tokens=64,
        )

    # ------------------------------------------------------------------
    # 内部：从 prompt 还原角色与结构化内容
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_role(user: str) -> str:
        first_line = user.split("\n", 1)[0]
        match = _ROLE_RE.search(first_line)
        return match.group(1) if match else "unknown"

    @staticmethod
    def _extract_payload(user: str) -> dict:
        _, _, body = user.partition("\n")
        if not body:
            return {}
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _default_handler(role, payload, seq):
        del payload, seq
        if role == "observation":
            return {
                "focus_steps": [],
                "observe": ["robot.position", "robot.gripper_empty"],
                "reason": "fake 观察：未见失败。",
            }
        if role == "analysis":
            return {
                "failure_type": "QUALITY_OPTIMIZATION",
                "failure_step": None,
                "evidence": ["fake 分析：无失败证据。"],
                "root_cause": "fake 根因",
                "repair_plan": [],
            }
        return {"summary": "fake 修改：未提出变更。", "changes": []}


# ---------------------------------------------------------------------------
# 『智能模型』行为：从 payload 里的 strategy/evaluation 推导合理输出，模拟一个
# 能力正常的模型。invalid=True 时 repair 故意输出过不了本地白名单的操作。
# 测试与三组对比 bench 共用同一套行为，保证口径一致。
# ---------------------------------------------------------------------------

def smart_observation(payload: dict) -> dict:
    failed = [
        event.get("step_id")
        for scenario in payload.get("evaluation", {}).get("scenario_results", [])
        for event in scenario.get("execution", {}).get("trace", [])
        if event.get("result", {}).get("status") != "SUCCESS"
        and event.get("phase", "main") == "main"
        and event.get("step_id")
    ]
    return {
        "focus_steps": list(dict.fromkeys(failed)),
        "observe": ["robot.position", "robot.gripper_empty", "action.result"],
        "reason": "LLM观察：" + ("失败步骤 " + repr(failed) if failed else "全部成功"),
    }


def smart_analysis(payload: dict) -> dict:
    evaluation = payload.get("evaluation", {})
    if evaluation.get("passed"):
        return {
            "failure_step": None,
            "failure_type": "QUALITY_OPTIMIZATION",
            "evidence": ["LLM分析：策略已通过全部检查，进入质量优化。"],
            "root_cause": "LLM定位：功能正确，寻找可压缩的冗余步骤。",
            "repair_plan": ["LLM 判断是否删除冗余步骤。"],
        }
    for scenario in evaluation.get("scenario_results", []):
        for event in scenario.get("execution", {}).get("trace", []):
            if (event.get("phase", "main") == "main"
                    and event.get("result", {}).get("status") != "SUCCESS"
                    and event.get("step_id")):
                return {
                    "failure_step": event["step_id"],
                    "failure_type": "ACTION_FAILED",
                    "action": event.get("action"),
                    "reason": event.get("result", {}).get("reason"),
                    "evidence": [
                        "LLM分析：步骤 {} 返回 {}。".format(
                            event["step_id"], event.get("result", {}).get("reason")
                        )
                    ],
                    "root_cause": "LLM定位：动作失败后缺少恢复处理。",
                    "repair_plan": ["LLM 将补充有限次恢复并安全停止。"],
                }
    for scenario in evaluation.get("scenario_results", []):
        for goal in scenario.get("goals", []):
            if not goal.get("passed"):
                return {
                    "failure_step": None,
                    "failure_type": "GOAL_NOT_REACHED",
                    "evidence": ["LLM分析：{}".format(goal.get("message"))],
                    "root_cause": "LLM定位：动作完成但最终环境状态未满足任务要求。",
                    "repair_plan": ["LLM 将补充移动到目标容器并释放。"],
                }
    return {
        "failure_step": None,
        "failure_type": "QUALITY_OPTIMIZATION",
        "evidence": ["LLM分析：无可定位失败。"],
        "root_cause": "LLM定位",
        "repair_plan": [],
    }


def smart_repair(payload: dict, invalid: bool = False) -> dict:
    diagnosis = payload.get("diagnosis", {})
    ftype = diagnosis.get("failure_type")
    if invalid:
        return {
            "summary": "LLM修改：故意输出不支持的修改操作。",
            "changes": [{"operation": "hack_robot", "target_step": "grasp_cup"}],
        }
    if ftype == "QUALITY_OPTIMIZATION":
        return {"summary": "LLM修改：策略已通过且无可优化项。", "changes": []}
    if ftype == "ACTION_FAILED":
        step_id = diagnosis.get("failure_step")
        strategy = payload.get("strategy", {})
        step = next(
            (s for s in strategy.get("steps", []) if s.get("id") == step_id), None
        )
        # 无论步骤是否已带 on_failure 都提出同一个修复（模拟模型反复给同一方案）。
        # 该方案对 grasp:1 有效（通过）；对 grasp:3 无效，重复提出 → 修复循环
        # 检测到相同签名提前收敛，机器人则已在恢复耗尽时 safe_stop。
        if step:
            object_name = next(
                (s.get("arguments", {}).get("object_name")
                 for s in strategy.get("steps", [])
                 if s.get("action") == "detect_object"),
                "target_object",
            )
            detect_id = step_id + "_redetect"
            return {
                "summary": "LLM修改：抓取失败时重新识别并有限重试，耗尽后安全停止。",
                "changes": [{
                    "operation": "update_step",
                    "target_step": step_id,
                    "content": {
                        "on_failure": {
                            "max_attempts": 2,
                            "steps": [
                                {"id": detect_id, "action": "detect_object",
                                 "arguments": {"object_name": object_name}},
                                {"id": step_id + "_retry", "action": "grasp",
                                 "arguments": {"object_id": "${}.object_id".format(detect_id)}},
                            ],
                            "on_exhausted": "stop",
                        }
                    },
                }],
            }
    if ftype == "GOAL_NOT_REACHED":
        goal = None
        for scenario in payload.get("evaluation", {}).get("scenario_results", []):
            for candidate in scenario.get("goals", []):
                if not candidate.get("passed"):
                    goal = candidate.get("goal")
                    break
            if goal:
                break
        if goal:
            detect_id = next(
                (s.get("id") for s in payload.get("strategy", {}).get("steps", [])
                 if s.get("action") == "detect_object"),
                None,
            )
            return {
                "summary": "LLM修改：补充移动到目标容器并释放。",
                "changes": [
                    {"operation": "append_step",
                     "content": {"id": "llm_grasp_again", "action": "grasp",
                                 "arguments": {"object_id": "${}.object_id".format(detect_id)}}},
                    {"operation": "append_step",
                     "content": {"id": "llm_move_target", "action": "move_to_target",
                                 "arguments": {"target": goal["container"]}}},
                    {"operation": "append_step",
                     "content": {"id": "llm_release", "action": "release", "arguments": {}}},
                ],
            }
    return {"summary": "LLM修改：未知诊断，不修改。", "changes": []}


def smart_handler(invalid: bool = False):
    """构造一个『能力正常』的假模型 handler（role, payload, seq）-> dict。"""

    def handle(role, payload, seq):
        del seq
        if role == "observation":
            return smart_observation(payload)
        if role == "analysis":
            return smart_analysis(payload)
        return smart_repair(payload, invalid=invalid)

    return handle
