"""五类验收测试：证明 TraceCoder 的 LLM 真实参与，而非静默回退到规则。

三组对比全部覆盖（见 README/任务需求）：
  - off      纯规则：零 LLM 调用，规则闭环照常通过
  - required 纯 LLM：每轮必须用 LLM；模型失败/输出不可用 → LLM_REQUIRED_FAILED，
                     结构上绝不回退规则（不产生任何规则 patch）
  - optional 规则+LLM：LLM 优先，失败/无效 → 回退规则并记录 used_fallback=True

五类在线场景（素材见 testdata/tracecoder_llm_cases.json）：
  normal / grasp_failure / goal_not_reached / invalid_repair / persistent_failure_safe_stop

所有模型调用经 FakeLLMProvider 离线注入，CI 无需 API Key 与网络；
另有『未配置 Key』用例，验证 required 模式在真实 Provider 缺配置时如实中止。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import integration.adapters.tracecoder as adapter_mod
from integration.adapters.tracecoder import configure_llm, run
from modules.evaluator.tracecoder.llm_provider import LLMConfig, LLMProvider
from tests.helpers.fake_llm_provider import FakeLLMProvider, smart_handler

_ROOT = Path(__file__).resolve().parents[2]
_CASES = json.loads(
    (_ROOT / "testdata" / "tracecoder_llm_cases.json").read_text(encoding="utf-8")
)["cases"]


class TestTraceCoderLLM(unittest.TestCase):
    def setUp(self):
        # 每用例独立：清空经验库 + 恢复 env 默认（off），避免用例间状态串扰
        adapter_mod._EXPERIENCE_STORE = None
        configure_llm(mode=None, provider=None)

    def _run_case(self, case_key: str, mode: str, provider=None) -> dict:
        case = _CASES[case_key]
        configure_llm(mode=mode, provider=provider)
        return run({
            "task": case["task_v1"],
            "strategy": case["strategy_v1"],
            "execution": {"status": "RUNNING"},
        })

    def _diag(self, result: dict) -> dict:
        return json.loads(result["diagnosis"])

    def _grasp_step(self, strategy_v1: dict) -> dict:
        return next(s for s in strategy_v1["steps"] if s["action"] == "grasp")

    # ------------------------------------------------------------------
    # 三组对比
    # ------------------------------------------------------------------

    def test_off_mode_zero_llm_calls(self):
        """纯规则组：off 模式零 LLM 调用，规则闭环照常通过。"""
        result = self._run_case("normal", mode="off")
        diag = self._diag(result)
        self.assertTrue(diag["final_passed"])
        self.assertEqual(diag["llm"]["stats"]["calls"], 0)
        self.assertEqual(diag["llm"]["calls"], [])

    def test_required_normal_task_llm_participates(self):
        """纯 LLM 组-正常任务：初始即通过也走 LLM 质量确认轮，证据在案。"""
        fake = FakeLLMProvider(handler=smart_handler())
        result = self._run_case("normal", mode="required", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "PASSED", diag.get("stopped_reason"))
        self.assertTrue(diag["final_passed"])
        self.assertFalse(diag["llm"]["required_failed"])
        stats = diag["llm"]["stats"]
        self.assertEqual(stats["mode"], "required")
        self.assertGreaterEqual(stats["calls"], 3)
        self.assertEqual(stats["ok_calls"], stats["calls"])
        self.assertEqual(stats["fallback_calls"], 0)
        self.assertEqual(stats["failed_calls"], 0)
        # 证据：三角色都有真实调用记录，模型名/请求号/耗时齐全
        roles = {c["role"] for c in diag["llm"]["calls"]}
        self.assertEqual(roles, {"observation", "analysis", "repair"})
        for record in diag["llm"]["calls"]:
            self.assertEqual(record["model"], "fake-deepseek")
            self.assertTrue(record["request_id"])
            self.assertGreaterEqual(record["latency_ms"], 0)

    def test_optional_provider_failure_falls_back_recorded(self):
        """规则+LLM 组：模型调用失败 → 规则兜底，证据明确记录 fallback。"""
        # 第一次 repair 调用（seq=3）模拟网络/超时失败
        fake = FakeLLMProvider(
            handler=smart_handler(),
            fail=lambda role, seq: role == "repair" and seq == 3,
        )
        result = self._run_case("grasp_failure", mode="optional", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "PASSED", diag.get("stopped_reason"))
        self.assertFalse(diag["llm"]["required_failed"])
        self.assertEqual(diag["llm"]["stats"]["fallback_calls"], 1)
        # 规则兜底把抓取恢复补上，策略通过
        grasp = self._grasp_step(result["patch"])
        self.assertEqual(grasp["on_failure"]["on_exhausted"], "stop")
        # 证据：seq=3 的 repair 调用明确标记回退
        record = next(c for c in diag["llm"]["calls"] if c["seq"] == 3)
        self.assertEqual(record["status"], "fallback")
        self.assertTrue(record["used_fallback"])

    # ------------------------------------------------------------------
    # 五类场景（required = 纯 LLM）
    # ------------------------------------------------------------------

    def test_required_grasp_failure_llm_repairs(self):
        """抓取失败：LLM 补 on_failure 恢复，修复来源确实是模型。"""
        fake = FakeLLMProvider(handler=smart_handler())
        result = self._run_case("grasp_failure", mode="required", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "PASSED", diag.get("stopped_reason"))
        self.assertFalse(diag["llm"]["required_failed"])
        stats = diag["llm"]["stats"]
        self.assertGreaterEqual(stats["calls"], 6)  # 修复轮 + 质量优化轮
        self.assertEqual(stats["fallback_calls"], 0)
        self.assertEqual(stats["failed_calls"], 0)
        # 修复轮来源是 LLM，诊断带 LLM 专属标记（证明模型真的参与并产出归因）
        first = diag["repair_log"][0]
        self.assertEqual(first["source"], "llm")
        self.assertTrue(first["diagnosis"]["root_cause"].startswith("LLM定位"))
        # 最终策略的抓取步骤带 on_failure 安全停止
        grasp = self._grasp_step(result["patch"])
        self.assertEqual(grasp["on_failure"]["on_exhausted"], "stop")
        # fake 侧证据：repair 角色确实被调用且输出过修复补丁
        repair_calls = [c for c in fake.calls if c["role"] == "repair"]
        self.assertTrue(repair_calls)
        self.assertEqual(repair_calls[0]["output"]["changes"][0]["operation"], "update_step")

    def test_required_goal_not_reached_llm_repairs(self):
        """目标未达成：动作全成功但物体放错容器，LLM 补充移动+释放到正确容器。"""
        fake = FakeLLMProvider(handler=smart_handler())
        result = self._run_case("goal_not_reached", mode="required", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "PASSED", diag.get("stopped_reason"))
        self.assertFalse(diag["llm"]["required_failed"])
        first = diag["repair_log"][0]
        self.assertEqual(first["source"], "llm")
        self.assertEqual(first["diagnosis"]["failure_type"], "GOAL_NOT_REACHED")
        self.assertTrue(first["diagnosis"]["root_cause"].startswith("LLM定位"))
        # LLM 的 patch 追加了移动到 right_bin 并释放的步骤
        actions = [s["action"] for s in result["patch"]["steps"]]
        self.assertIn("move_to_target", actions)
        last_move = next(
            s for s in reversed(result["patch"]["steps"])
            if s["action"] == "move_to_target"
        )
        self.assertEqual(last_move["arguments"]["target"], "right_bin")

    def test_required_invalid_repair_aborts_no_fallback(self):
        """无效修复：required 模式 LLM 输出过不了白名单 → 如实中止，绝不留规则补丁。"""
        fake = FakeLLMProvider(handler=smart_handler(invalid=True))
        result = self._run_case("invalid_repair", mode="required", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "LLM_REQUIRED_FAILED")
        self.assertTrue(diag["llm"]["required_failed"])
        self.assertFalse(diag["final_passed"])
        self.assertIn("拒绝回退", diag["stopped_reason"])
        # 证据：repair 调用被标记 failed，且没有任何规则补丁被应用
        self.assertGreaterEqual(diag["llm"]["stats"]["failed_calls"], 1)
        first = diag["repair_log"][0]
        self.assertEqual(first["source"], "llm_required_failed")
        grasp = self._grasp_step(result["patch"])
        self.assertNotIn("on_failure", grasp)
        # fake 侧证据：LLM 确实输出了非法操作
        repair_outputs = [c["output"] for c in fake.calls if c["role"] == "repair"]
        self.assertTrue(repair_outputs)
        self.assertEqual(repair_outputs[0]["changes"][0]["operation"], "hack_robot")

    def test_optional_invalid_repair_falls_back_recorded(self):
        """规则+LLM 组：LLM 输出无效 → 回退规则并记录 used_fallback=True。"""
        fake = FakeLLMProvider(handler=smart_handler(invalid=True))
        result = self._run_case("invalid_repair", mode="optional", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "PASSED", diag.get("stopped_reason"))
        self.assertEqual(diag["llm"]["stats"]["mode"], "optional")
        self.assertGreaterEqual(diag["llm"]["stats"]["fallback_calls"], 1)
        # 证据里能明确看到那次回退
        fallback_records = [c for c in diag["llm"]["calls"] if c["status"] == "fallback"]
        self.assertTrue(fallback_records)
        self.assertTrue(all(c["used_fallback"] for c in fallback_records))
        # 规则兜底成功：抓取步骤补上 on_failure
        grasp = self._grasp_step(result["patch"])
        self.assertEqual(grasp["on_failure"]["on_exhausted"], "stop")

    def test_required_missing_key_honest_stop(self):
        """required + 真实 Provider 未配置 Key：如实中止，绝不静默用规则修复任务。"""
        provider = LLMProvider(LLMConfig(
            mode="required", model="deepseek-v4-pro", api_key="",
        ))
        result = self._run_case("grasp_failure", mode="required", provider=provider)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "LLM_REQUIRED_FAILED")
        self.assertTrue(diag["llm"]["required_failed"])
        self.assertFalse(diag["final_passed"])
        first = diag["llm"]["calls"][0]
        self.assertEqual(first["status"], "failed")
        self.assertIn("API_KEY", first["error"])
        # 抓取步骤保持原样：没有任何规则补丁被偷偷应用
        grasp = self._grasp_step(result["patch"])
        self.assertNotIn("on_failure", grasp)

    def test_persistent_failure_safe_stop(self):
        """持续失败安全停止：LLM 恢复吸收不了 grasp:3，机器人 safe_stop；循环提前收敛。"""
        fake = FakeLLMProvider(handler=smart_handler())
        result = self._run_case("persistent_failure_safe_stop", mode="required", provider=fake)
        diag = self._diag(result)
        self.assertEqual(diag["status"], "NOT_READY")
        self.assertFalse(diag["final_passed"])
        self.assertFalse(diag["llm"]["required_failed"])
        # LLM 确实参与了多轮修复
        self.assertGreaterEqual(diag["llm"]["stats"]["calls"], 6)
        # 修复过程中出现过机器人安全停止（on_exhausted=stop 触发 safe_stop）
        first = diag["repair_log"][0]
        trace = first["result_detail"]["scenario_results"][0]["execution"]["trace"]
        self.assertTrue(
            any(e.get("phase") == "safe_stop" for e in trace),
            "持续失败后应触发 safe_stop，而不是带病继续",
        )
        # 循环收敛：没有无限重试（修复轮数 < 最大轮数 5）
        self.assertEqual(diag["repair_rounds"], 2)
        # 最终状态不允许部署真机
        self.assertFalse(diag["final_passed"])
        self.assertIn("相同修改", diag["stopped_reason"])


if __name__ == "__main__":
    unittest.main()
