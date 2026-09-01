"""Source-level tests for the truthful browser UI (Task 12).

The browser must render only API data, must never call the retired
POST /api/run, must recover the last run after refresh, must poll
events by after_sequence, and must play the same-origin HLS feed.
No fabricated CPU/memory/robot/IP/joint/load/safety values or fake
counts may appear.
"""

from __future__ import annotations

import unittest
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "demo" / "frontend"
INDEX = FRONTEND / "index.html"
APP = FRONTEND / "app.js"
VENDOR = FRONTEND / "vendor" / "hls.min.js"

REQUIRED_DOM_IDS = (
    "systemHealth",
    "scenarioList",
    "instruction",
    "run",
    "livestream",
    "stageA",
    "stageB",
    "stageC",
    "stageD",
    "currentAction",
    "eventTimeline",
    "resultSummary",
)

# 旧页面中确切的假数据（无真实来源），新页面必须全部移除。
FAKE_FRAGMENTS = (
    "AUBO",
    "192.168.",
    "RBT-001",
    "接口占位",
    "接口预留",
    "CPU</b>",
    "内存</b>",
    "关节状态",
    "防护门",
    "碰撞检测",
    "速度倍率",
    "系统初始化",
    "服务连接正常",
    "实时仿真",
    "Sim-RTX",
)


class CloudFrontendSourceTests(unittest.TestCase):
    def test_required_dom_ids_present(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        for dom_id in REQUIRED_DOM_IDS:
            self.assertIn(f'id="{dom_id}"', html, f"missing id={dom_id}")

    def test_uses_new_runs_api_and_never_legacy_run(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        combined = html + app
        self.assertIn("/api/runs", combined)
        self.assertNotIn('"/api/run"', combined)
        self.assertNotIn("'/api/run'", combined)
        self.assertNotIn("POST /api/run", combined)

    def test_events_polled_by_after_sequence(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertIn("after_sequence", app)
        self.assertIn("/events?after_sequence=", app)

    def test_run_id_recovered_after_refresh(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertTrue("localStorage" in app or "sessionStorage" in app)
        self.assertIn("run_id", app)

    def test_relative_same_origin_hls_url(self) -> None:
        app = APP.read_text(encoding="utf-8")
        # HLS constant must be a relative same-origin path, never an absolute host.
        self.assertRegex(app, r'DEFAULT_HLS_URL\s*=\s*"/live/isaac/index\.m3u8"')
        self.assertNotIn("113.44.1.44", app)

    def test_hls_js_vendored_with_license(self) -> None:
        self.assertTrue(VENDOR.is_file(), "vendor/hls.min.js is missing")
        self.assertGreater(VENDOR.stat().st_size, 100_000, "hls.min.js looks truncated")
        license_files = list((FRONTEND / "vendor").glob("hls.min.js.LICENSE.txt"))
        self.assertTrue(license_files, "hls.js license file is missing")

    def test_live_status_driven_by_media_progress(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertIn("LIVE", app)
        self.assertIn("OFFLINE", app)
        self.assertIn("currentTime", app)

    def test_native_hls_fallback_present(self) -> None:
        app = APP.read_text(encoding="utf-8")
        self.assertIn("canPlayType", app)
        self.assertIn("application/vnd.apple.mpegurl", app)

    def test_no_fabricated_values_in_markup(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        for fragment in FAKE_FRAGMENTS:
            self.assertNotIn(fragment, html, f"fake fragment still present: {fragment}")

    def test_no_percentage_or_fixed_metrics_markup(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        self.assertNotRegex(html, r"<b>\d+\s*%</b>")
        self.assertNotRegex(html, r"[-−]?\d+\.\d+°")

    def test_no_data_placeholders_are_literal_guesses(self) -> None:
        html = INDEX.read_text(encoding="utf-8")
        for literal in ("未连接", "等待中", "无数据"):
            self.assertIn(literal, html)


if __name__ == "__main__":
    unittest.main()
