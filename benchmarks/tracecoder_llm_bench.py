"""三组效果对比 bench：纯规则 / 纯 LLM / 规则+LLM × 五类验收场景。

用法：
    PYTHONIOENCODING=utf-8 python benchmarks/tracecoder_llm_bench.py            # 表格
    PYTHONIOENCODING=utf-8 python benchmarks/tracecoder_llm_bench.py --json    # JSON 表格

说明：
  - required（纯 LLM）与 optional（规则+LLM）用 FakeLLMProvider 离线注入，
    确定性结果，CI / 本地无 Key 也能跑，与验收测试同一套『智能模型』行为；
  - off（纯规则）零 LLM 调用；
  - 真实 API 冒烟：在本地 .env 填 TRACECODER_LLM_API_KEY 后，
    把 env TRACECODER_LLM_MODE=required 运行本脚本，required 行即为真实调用。
    未配置 Key 时 required 行会如实显示 LLM_REQUIRED_FAILED（结构上拒绝静默回退）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 脚本直接运行（python benchmarks/...）时把仓库根目录加进 import 路径
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import integration.adapters.tracecoder as adapter_mod  # noqa: E402
from integration.adapters.tracecoder import configure_llm, run  # noqa: E402
from tests.helpers.fake_llm_provider import FakeLLMProvider, smart_handler  # noqa: E402
_CASES = json.loads(
    (_ROOT / "testdata" / "tracecoder_llm_cases.json").read_text(encoding="utf-8")
)["cases"]

_CASE_LABELS = {
    "normal": "正常任务(初始通过)",
    "grasp_failure": "抓取失败(grasp:1)",
    "goal_not_reached": "目标未达成(放错容器)",
    "invalid_repair": "无效修复(LLM输出非法)",
    "persistent_failure_safe_stop": "持续失败(grasp:3)→安全停止",
}

_GROUPS = [
    ("纯规则", "off"),
    ("纯LLM", "required"),
    ("规则+LLM", "optional"),
]


def _provider_for(mode: str, case_key: str):
    """LLM 组的 Provider。

    - 已配置 TRACECODER_LLM_API_KEY → 返回 None，让 configure_llm 落到 env 的
      真实 Provider（真实 DeepSeek 调用；此时『无效修复』场景由真实模型给出
      合法修复，不再复现 fake 的非法输出，属于预期）。
    - 未配置 Key → 离线 FakeLLMProvider（确定性，CI 无 Key 也能跑）。
      『无效修复』场景给模型一个『会输出非法操作』的行为，以暴露 required
      中止 vs optional 回退 vs 纯规则不依赖 LLM 的真实差异。
    """
    if mode == "off":
        return None
    if adapter_mod._LLM_CONFIG.key_configured:
        return None  # 真实 Provider（env / 本地 .env 配置）
    invalid = case_key == "invalid_repair"
    return FakeLLMProvider(handler=smart_handler(invalid=invalid))


def _provider_mode_label() -> str:
    if adapter_mod._LLM_CONFIG.key_configured:
        model = adapter_mod._LLM_CONFIG.model or "未配置模型名"
        return "真实 API（{}，{}）".format(
            adapter_mod._LLM_CONFIG.base_url, model
        )
    return "FakeLLMProvider（离线确定性，未配置 Key）"


def _run_one(case_key: str, mode: str, provider) -> dict:
    case = _CASES[case_key]
    configure_llm(mode=mode, provider=provider)
    result = run({
        "task": case["task_v1"],
        "strategy": case["strategy_v1"],
        "execution": {"status": "RUNNING"},
    })
    return json.loads(result["diagnosis"])


def _verdict(diag: dict) -> str:
    """把结果映射为可读结论（含安全停止识别）。"""
    status = diag.get("status")
    if diag.get("llm", {}).get("required_failed"):
        return "中止:LLM不可用"
    if status == "PASSED":
        return "通过"
    if status == "NOT_READY":
        # 检查修复过程是否触发过 safe_stop（恢复耗尽 → 机器人安全停止）
        for entry in diag.get("repair_log") or []:
            detail = entry.get("result_detail") or {}
            for scenario in detail.get("scenario_results", []):
                if any(e.get("phase") == "safe_stop"
                       for e in scenario.get("execution", {}).get("trace", [])):
                    return "未收敛(已安全停止)"
        return "未收敛"
    return status


def run_benchmark() -> dict:
    """跑完全部组合，返回结构化结果（表格数据源）。"""
    rows = []
    for group_label, mode in _GROUPS:
        for case_key in _CASES:
            adapter_mod._EXPERIENCE_STORE = None  # 每格独立，避免经验库串扰
            diag = _run_one(case_key, mode, _provider_for(mode, case_key))
            stats = diag.get("llm", {}).get("stats") or {}
            rows.append({
                "group": group_label,
                "mode": mode,
                "case": _CASE_LABELS.get(case_key, case_key),
                "status": diag.get("status"),
                "verdict": _verdict(diag),
                "final_passed": bool(diag.get("final_passed")),
                "repair_rounds": diag.get("repair_rounds", 0),
                "llm_calls": stats.get("calls", 0),
                "ok": stats.get("ok_calls", 0),
                "fallback": stats.get("fallback_calls", 0),
                "failed": stats.get("failed_calls", 0),
                "latency_ms": stats.get("total_latency_ms", 0.0),
                "prompt_tokens": stats.get("prompt_tokens", 0),
                "completion_tokens": stats.get("completion_tokens", 0),
                "reasoning_tokens": stats.get("reasoning_tokens", 0),
                "total_tokens": stats.get("total_tokens", 0),
                "stopped_reason": diag.get("stopped_reason"),
            })
    return {"rows": rows}


def _print_table(rows: list[dict]) -> None:
    header = ("组", "场景", "结论", "通过", "修复轮", "LLM调用", "回退", "失败", "耗时ms")
    width = [4, 28, 16, 4, 6, 8, 6, 6, 8]
    line = "+".join("-" * w for w in width)
    print(line)
    print("+".join(h.ljust(w) for h, w in zip(header, width)))
    print(line)
    for row in rows:
        cells = (
            row["group"],
            row["case"],
            row["verdict"],
            "是" if row["final_passed"] else "否",
            str(row["repair_rounds"]),
            str(row["llm_calls"]),
            str(row["fallback"]),
            str(row["failed"]),
            "{:.1f}".format(row["latency_ms"]),
        )
        print("+".join(cell.ljust(w) for cell, w in zip(cells, width)))
    print(line)


def main() -> None:
    want_json = "--json" in sys.argv
    data = run_benchmark()
    if want_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print("TraceCoder 三组效果对比（纯规则 / 纯 LLM / 规则+LLM × 五类场景）")
    print("LLM 行 Provider：" + _provider_mode_label())
    _print_table(data["rows"])
    print("\n说明：未配置 Key 时 LLM 行用 FakeLLMProvider 离线注入（确定性）；")
    print("在本地 .env 填 TRACECODER_LLM_API_KEY / TRACECODER_LLM_MODEL 后再跑，")
    print("LLM 行即为真实 API 调用（无效修复场景由真实模型给出合法修复）。")


if __name__ == "__main__":
    main()
