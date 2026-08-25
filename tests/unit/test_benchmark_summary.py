"""批量统计增强单元测试：失败分类、恢复分离、元数据必填字段。"""

import unittest

from tools.run_closed_loop_benchmark import _summarize


def _record(**overrides) -> dict:
    value = {
        "case_id": "c1",
        "category": "happy_path",
        "source": "demo",
        "repeat": 1,
        "request_id": "benchmark-c1-r1",
        "run_id": "benchmark-c1-r1",
        "expected_status": "SUCCEEDED",
        "actual_status": "SUCCEEDED",
        "passed": True,
        "status_passed": True,
        "retry_passed": True,
        "elapsed_ms": 120.0,
        "backend": "mock",
        "failure_class": None,
        "original_error": None,
        "evidence_path": None,
        "c_internal_recovery": False,
        "d_repair_attempted": False,
        "d_repair_succeeded": None,
        "task": {"status": "READY"},
        "strategy": {
            "mode": "primitive_plan",
            "provider": None,
            "contract_valid": True,
            "code_null": True,
            "actions": ["detect_object"],
            "fallback": False,
            "latency_ms": None,
        },
        "intent": {"llm_call_attempted": False, "llm_call_succeeded": False, "fallback_used": False},
        "feedback_provenance": {"source": "tracecoder_skipped", "mode": "off", "fallback": False, "llm_stats": {}},
        "execution": {"status": "SUCCEEDED", "safety_events": []},
        "feedback": {"status": "D_ACCEPTED"},
        "tracecoder_invoked": False,
        "tracecoder_requests": 0,
        "retry_count": 0,
        "d_repair_required": False,
        "attempt_count": 1,
        "stop_reason": "EXECUTION_SUCCEEDED",
        "signature": ["SUCCEEDED"],
        "replay": {},
    }
    value.update(overrides)
    return value


class SummarizeFailureClassificationTest(unittest.TestCase):
    def test_transport_failures_excluded_from_pass_rate_denominator(self):
        records = [
            _record(case_id="c1", failure_class="transport_auth", actual_status="FAILED", passed=False),
            _record(case_id="c2", failure_class=None, actual_status="SUCCEEDED", passed=True),
        ]
        cases = [{"id": "c1", "category": "happy_path"}, {"id": "c2", "category": "happy_path"}]
        summary = _summarize(records, cases)
        self.assertEqual(summary["transport_failures"], 1)
        self.assertEqual(summary["pass_rate"], 1.0)
        self.assertEqual(summary["runs"], 2)

    def test_four_failure_categories_counted(self):
        records = [
            _record(case_id="c1", failure_class="transport_auth", actual_status="FAILED", passed=False),
            _record(case_id="c2", actual_status="FAILED", passed=False),
            _record(case_id="c3", actual_status="SAFE_STOP", passed=False, category="safe_stop"),
            _record(case_id="c4", strategy={**_record()["strategy"], "contract_valid": False}, passed=False),
            _record(case_id="c5", actual_status="SUCCEEDED", passed=True),
        ]
        cases = [{"id": f"c{i}", "category": r["category"]} for i, r in enumerate(records, 1)]
        summary = _summarize(records, cases)
        self.assertEqual(summary["transport_failures"], 1)
        self.assertEqual(summary["contract_failures"], 1)
        self.assertEqual(summary["safety_failures"], 1)
        self.assertIn("business_failures", summary)

    def test_dc_recovery_separation(self):
        records = [
            _record(
                case_id="c1",
                c_internal_recovery=True,
                actual_status="SUCCEEDED",
                passed=True,
                category="recoverable_failure",
                requires_d_repair=False,
            ),
            _record(
                case_id="c2",
                d_repair_attempted=True,
                d_repair_succeeded=True,
                actual_status="SUCCEEDED",
                passed=True,
                retry_count=1,
                category="recoverable_failure",
            ),
            _record(
                case_id="c3",
                d_repair_attempted=True,
                d_repair_succeeded=False,
                actual_status="FAILED",
                passed=False,
                retry_count=1,
                category="recoverable_failure",
            ),
        ]
        cases = [{"id": f"c{i}", "category": "recoverable_failure", "requires_d_repair": i > 1} for i in (1, 2, 3)]
        summary = _summarize(records, cases)
        self.assertEqual(summary["c_internal_recovery_rate"], 1.0)
        self.assertEqual(summary["d_repair_success_rate"], 0.5)

    def test_p50_p95_top_level_fields(self):
        records = [_record(elapsed_ms=100.0), _record(case_id="c2", elapsed_ms=300.0)]
        cases = [{"id": "c1", "category": "happy_path"}, {"id": "c2", "category": "happy_path"}]
        summary = _summarize(records, cases)
        self.assertEqual(summary["p50_latency_ms"], 100.0)
        self.assertEqual(summary["p95_latency_ms"], 300.0)


if __name__ == "__main__":
    unittest.main()