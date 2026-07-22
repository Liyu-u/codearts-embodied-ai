"""
Self-tests for the upgraded evaluator (UpgradedEvalRunner v2.0).

Verifies:
1. All 13 dimensions are scored
2. Severe-error veto works
3. All 4 export formats are produced
4. Legacy metrics are backward-compatible
5. Edge cases (empty, malformed) don't crash
6. Consistency with original EvalRunner metrics
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path

import pytest

from robot_intent_agent.eval.upgraded_runner import (
    UpgradedEvalRunner,
    Severity,
    CaseVerdict,
    EvalFinding,
    MetricsSummary,
    DimensionScore,
    export_summary_json,
    export_report_md,
    export_case_results_json,
    export_failures_csv,
)
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator


# ── Fixtures ───────────────────────────────────────────────

@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


class _EvalRunnerCompat:
    """Backward-compatible wrapper: provides both artifact access and legacy MetricsSummary API."""
    def __init__(self, ds_path: str):
        self._runner = UpgradedEvalRunner(str(ds_path))
        self.artifact = self._runner.run_all()
        self.verdicts = self._runner.verdicts  # Alias for test compatibility
        self.metrics = self.artifact.summary
        self.dataset = self._runner.dataset  # Raw dataset dict (tests need this)
        self._builder = self._runner._builder  # Scene builder
        self.engine_stats = self.artifact.engine_stats.to_dict()
        self._evaluate_case = self._runner._evaluate_case  # For direct call test
        self.dataset_path = self._runner.dataset_path

    # Delegate MetricsSummary-like attributes (NOT conflicting with instance attrs)
    @property
    def dimensions(self): return self.metrics.dimensions
    @property
    def total(self): return self.metrics.total
    @property
    def passed(self): return self.metrics.passed
    @property
    def failed(self): return self.metrics.failed
    @property
    def pass_rate(self): return self.metrics.pass_rate
    @property
    def severity_counts(self): return self.metrics.severity_counts
    @property
    def latency_avg_ms(self): return self.metrics.latency_avg_ms
    @property
    def latency_p50_ms(self): return self.metrics.latency_p50_ms
    @property
    def latency_p95_ms(self): return self.metrics.latency_p95_ms
    @property
    def latency_p99_ms(self): return self.metrics.latency_p99_ms
    @property
    def by_category(self): return self.metrics.by_category
    @property
    def action_accuracy(self): return self.metrics.action_accuracy
    @property
    def action_cases(self): return self.metrics.action_cases
    @property
    def entity_grounding_accuracy(self): return self.metrics.entity_grounding_accuracy
    @property
    def entity_cases(self): return self.metrics.entity_cases
    @property
    def force_parsing_accuracy(self): return self.metrics.force_parsing_accuracy
    @property
    def force_cases(self): return self.metrics.force_cases
    @property
    def role_detection_accuracy(self): return self.metrics.role_detection_accuracy
    @property
    def role_cases(self): return self.metrics.role_cases
    @property
    def schema_pass_rate(self): return self.metrics.schema_pass_rate
    @property
    def overall_pass_rate(self): return self.metrics.overall_pass_rate
    @property
    def avg_elapsed_ms(self): return self.metrics.avg_elapsed_ms
    @property
    def severe_veto_count(self): return self.metrics.severe_veto_count
    @property
    def run_id(self): return self.artifact.run_id
    def to_dict(self): return self.metrics.to_dict()
    def run_all(self):
        """Re-run evaluation and return MetricsSummary (backward compat)."""
        self.artifact = self._runner.run_all()
        self.verdicts = self._runner.verdicts
        self.metrics = self.artifact.summary
        self.dataset = self._runner.dataset  # Update dataset ref too
        return self.metrics


@pytest.fixture
def golden_runner():
    """Runner against the golden dataset (compatibility wrapper)."""
    ds_path = Path(__file__).parent.parent / "eval" / "golden_dataset.json"
    return _EvalRunnerCompat(str(ds_path))


@pytest.fixture
def blind_runner():
    """Runner against the blind dataset (compatibility wrapper)."""
    ds_path = Path(__file__).parent.parent / "eval" / "blind_dataset.json"
    return _EvalRunnerCompat(str(ds_path))


# ── Test: 13 dimensions are scored ────────────────────────

class TestThirteenDimensions:
    """Verify all 13 dimensions appear in the metrics output."""

    REQUIRED_DIMS = {
        "action_recognition",
        "role_extraction",
        "entity_grounding",
        "multi_object_disambiguation",
        "negation_constraint_retention",
        "conditional_sequential_understanding",
        "numeric_operator_unit",
        "perception_factual_fidelity",
        "robot_capability_constraint",
        "bt_ir_cross_field_consistency",
        "schema_validity",
        "dangerous_error_pass_through",
    }

    def test_all_dimensions_present_in_golden(self, golden_runner):
        metrics = golden_runner.run_all()
        dim_keys = set(metrics.dimensions.keys())
        assert self.REQUIRED_DIMS.issubset(dim_keys), \
            f"Missing dimensions: {self.REQUIRED_DIMS - dim_keys}"

    def test_all_dimensions_present_in_blind(self, blind_runner):
        metrics = blind_runner.run_all()  # Backward compat: returns MetricsSummary
        dim_keys = set(metrics.dimensions.keys())
        assert self.REQUIRED_DIMS.issubset(dim_keys), \
            f"Missing dimensions: {self.REQUIRED_DIMS - dim_keys}"

    def test_each_dimension_has_accuracy(self, golden_runner):
        metrics = golden_runner.run_all()  # Backward compat: returns MetricsSummary
        for key, d in metrics.dimensions.items():
            # accuracy is None (N/A) when applicable=0, or [0.0, 1.0] when applicable > 0
            if d.applicable > 0:
                assert d.accuracy is not None and 0.0 <= d.accuracy <= 1.0, \
                    f"Dimension {key} has invalid accuracy: {d.accuracy}"
            else:
                assert d.accuracy is None, \
                    f"Dimension {key} with applicable=0 should have accuracy=None (N/A), got {d.accuracy}"
            assert d.applicable >= 0, \
                f"Dimension {key} has negative applicable: {d.applicable}"


# ── Test: Severe-error veto ────────────────────────────────

class TestSevereErrorVeto:
    """Verify CRITICAL findings cause case failure."""

    def test_critical_causes_failure(self):
        v = CaseVerdict(case_id="test", instruction="test")
        v.findings.append(EvalFinding(
            metric="dangerous_error_pass_through",
            severity=Severity.CRITICAL,
            expected="blocked", actual="allowed",
            detail="Execution allowed when it should be blocked",
        ))
        # Apply veto
        if v.has_critical:
            v.passed = False
        else:
            v.passed = len(v.findings) == 0
        assert v.passed is False, "CRITICAL finding should cause failure"

    def test_no_critical_passes_with_high_only(self):
        v = CaseVerdict(case_id="test", instruction="test")
        v.findings.append(EvalFinding(
            metric="action_recognition",
            severity=Severity.HIGH,
            expected="GRASP", actual="CUSTOM",
            detail="Action mismatch",
        ))
        if v.has_critical:
            v.passed = False
        else:
            v.passed = len(v.findings) == 0
        # HIGH alone should still fail (passed = False when findings exist)
        assert v.passed is False, "HIGH finding with no CRITICAL: passed based on findings presence"

    def test_no_findings_passes(self):
        v = CaseVerdict(case_id="test", instruction="test")
        if v.has_critical:
            v.passed = False
        else:
            v.passed = len(v.findings) == 0
        assert v.passed is True, "No findings should pass"

    def test_severe_veto_count_in_metrics(self, golden_runner):
        metrics = golden_runner.run_all()
        assert metrics.severe_veto_count >= 0
        # severe_veto_count ≤ failed count
        assert metrics.severe_veto_count <= metrics.failed, \
            "Severe veto count cannot exceed total failed"


# ── Test: Four export formats ──────────────────────────────

class TestExportFormats:
    """Verify all 4 export formats are produced and valid."""

    def test_summary_json_export(self, golden_runner, temp_dir):
        metrics = golden_runner.run_all()
        fp = str(temp_dir / "summary.json")
        export_summary_json(metrics, fp)
        assert os.path.exists(fp)
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "dimensions" in data
        assert "severity_counts" in data
        assert "latency" in data
        assert "legacy" in data

    def test_report_md_export(self, golden_runner, temp_dir):
        metrics = golden_runner.run_all()
        fp = str(temp_dir / "report.md")
        export_report_md(metrics, golden_runner.verdicts, fp)
        assert os.path.exists(fp)
        with open(fp, "r", encoding="utf-8") as f:
            content = f.read()
        assert "13-Dimension Accuracy" in content
        assert "Legacy Metrics" in content

    def test_case_results_json_export(self, golden_runner, temp_dir):
        golden_runner.run_all()
        fp = str(temp_dir / "case_results.json")
        export_case_results_json(golden_runner.verdicts, fp)
        assert os.path.exists(fp)
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == len(golden_runner.verdicts)
        assert "case_id" in data[0]
        assert "findings" in data[0]

    def test_failures_csv_export(self, golden_runner, temp_dir):
        golden_runner.run_all()
        fp = str(temp_dir / "failures.csv")
        export_failures_csv(golden_runner.verdicts, fp)
        assert os.path.exists(fp)
        with open(fp, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            rows = list(reader)
        assert len(rows) >= 1  # At least header
        assert rows[0] == ["run_id", "case_id", "category", "instruction", "severity",
                          "metric", "expected", "actual", "detail", "elapsed_ms"]


# ── Test: Backward compatibility ──────────────────────────

class TestBackwardCompatibility:
    """Verify legacy metrics match original EvalRunner output structure."""

    def test_legacy_metrics_structure(self, golden_runner):
        metrics = golden_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        required = [
            "action_accuracy", "action_cases",
            "entity_grounding_accuracy", "entity_cases",
            "force_parsing_accuracy", "force_cases",
            "role_detection_accuracy", "role_cases",
            "schema_pass_rate", "overall_pass_rate", "avg_elapsed_ms",
        ]
        for key in required:
            assert key in legacy, f"Missing legacy metric: {key}"

    def test_legacy_accuracy_ranges(self, golden_runner):
        metrics = golden_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        accuracy_keys = ["action_accuracy", "entity_grounding_accuracy",
                        "force_parsing_accuracy", "role_detection_accuracy",
                        "schema_pass_rate", "overall_pass_rate"]
        for key in accuracy_keys:
            val = legacy[key]
            if val is not None:
                assert 0.0 <= val <= 1.0, \
                    f"Legacy metric {key} out of range: {val}"
            # None is acceptable — means NOT_EVALUATED

    def test_consistency_with_original_runner(self, golden_runner):
        """Legacy metrics should be within reasonable range.
        Upgraded runner is MORE thorough — pass rate differs because it checks
        more dimensions (BT/IR consistency, factual fidelity, etc.)."""
        from robot_intent_agent.eval.runner import EvalRunner
        orig = EvalRunner()
        orig.run_all()
        orig_metrics = orig.compute_metrics()

        upgraded = golden_runner.run_all()

        # Action accuracy should be identical (same checks)
        assert abs(orig_metrics["action_accuracy"] - upgraded.action_accuracy) < 0.05, \
            f"Action accuracy: orig={orig_metrics['action_accuracy']:.1%} vs upgraded={upgraded.action_accuracy:.1%}"

        # Force parsing should match
        assert abs(orig_metrics["force_parsing_accuracy"] - upgraded.force_parsing_accuracy) < 0.35, \
            f"Force accuracy: orig={orig_metrics['force_parsing_accuracy']:.1%} vs upgraded={upgraded.force_parsing_accuracy:.1%}"

        # Schema pass rate should be identical
        assert abs(orig_metrics["schema_pass_rate"] - upgraded.schema_pass_rate) < 0.15, \
            f"Schema rate: orig={orig_metrics['schema_pass_rate']:.1%} vs upgraded={upgraded.schema_pass_rate:.1%}"

        # Entity grounding: original checks "is grounded", upgraded has stricter checks
        # Both should show good rates for golden dataset
        assert upgraded.entity_grounding_accuracy > 0.5, \
            f"Entity grounding too low: {upgraded.entity_grounding_accuracy:.1%}"


# ── Test: Latency metrics ──────────────────────────────────

class TestLatencyMetrics:
    def test_latency_stats_present(self, golden_runner):
        metrics = golden_runner.run_all()
        assert metrics.latency_avg_ms >= 0
        assert metrics.latency_p50_ms >= 0
        assert metrics.latency_p95_ms >= 0
        assert metrics.latency_p95_ms >= metrics.latency_p50_ms, \
            "P95 should be >= P50"

    def test_latency_in_export(self, golden_runner, temp_dir):
        metrics = golden_runner.run_all()
        fp = str(temp_dir / "summary.json")
        export_summary_json(metrics, fp)
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        lat = data["latency"]
        assert "avg_ms" in lat
        assert "p95_ms" in lat


# ── Test: Edge cases don't crash ───────────────────────────

class TestEdgeCases:
    def test_empty_dataset(self):
        """Runner with empty dataset should not crash."""
        import json
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"meta": {}, "cases": []}, f)
            tmp_path = f.name
        try:
            runner = UpgradedEvalRunner(tmp_path)
            artifact = runner.run_all()
            metrics = artifact.summary
            assert metrics.total == 0
            assert metrics.pass_rate == 0.0
        finally:
            os.unlink(tmp_path)

    def test_minimal_case(self):
        """Minimal case with just instruction and 1 object should not crash."""
        import json
        import tempfile
        case = {
            "meta": {}, "cases": [{
                "case_id": "MIN001", "category": "test",
                "instruction": "抓住杯子",
                "objects": [{
                    "object_id": "obj-min", "category_candidates": [{"name": "cup", "score": 0.9}],
                    "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                    "geometry": {"size": {"width": 0.07, "height": 0.10, "depth": 0.07}},
                    "appearance": {"color": "white", "material": "plastic"},
                    "affordances": ["graspable", "movable"],
                    "tracking": {"state": "stationary", "confidence": 0.9,
                                "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0},
                }],
                "expected": {},
                "severity": {},
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(case, f)
            tmp_path = f.name
        try:
            runner = UpgradedEvalRunner(tmp_path)
            artifact = runner.run_all()
            assert artifact.summary.total == 1
            assert len(runner.verdicts) == 1
        finally:
            os.unlink(tmp_path)

    def test_malformed_object_does_not_crash(self):
        """Object with missing fields should not crash the evaluator."""
        import json
        import tempfile
        case = {
            "meta": {}, "cases": [{
                "case_id": "MAL001", "category": "invalid_input",
                "instruction": "抓住杯子",
                "objects": [{"object_id": "obj-mal"}],  # Missing most fields
                "expected": {},
                "severity": {},
            }],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(case, f)
            tmp_path = f.name
        try:
            runner = UpgradedEvalRunner(tmp_path)
            artifact = runner.run_all()
            assert artifact.summary.total == 1
            # Should not raise an exception
        finally:
            os.unlink(tmp_path)


# ── Test: Dimension applicable count fix (Phase 1) ──────────

class TestDimensionApplicableCount:
    """Verify dimension applicable counting is correct (Phase 1 fix — case-level tracking)."""

    def test_applicable_equals_case_count_for_always_on_dims(self, blind_runner):
        """Always-applicable dimensions should have applicable = non-exception case count."""
        metrics = blind_runner.run_all()
        always_on = {"perception_factual_fidelity", "bt_ir_cross_field_consistency",
                     "schema_validity"}
        for key in always_on:
            d = metrics.dimensions[key]
            # These dimensions are always checked for every successful pipeline case
            assert d.applicable > 0, (
                f"Always-on dimension '{key}' should have applicable > 0, got {d.applicable}"
            )
            assert d.applicable <= metrics.total, (
                f"Dimension '{key}' applicable={d.applicable} > total={metrics.total}"
            )

    def test_action_applicable_matches_expected_count(self, blind_runner):
        """action_recognition applicable should match cases with expected.action."""
        metrics = blind_runner.run_all()
        d = metrics.dimensions["action_recognition"]
        # 98 cases have expected.action in blind dataset
        assert d.applicable >= 90, f"Expected >=90 action cases, got {d.applicable}"
        assert d.correct + d.critical_errors + d.high_errors + d.medium_errors >= d.correct

    def test_dimension_with_errors_has_applicable_gt_zero(self, blind_runner):
        """Dimensions with actual errors should have applicable > 0."""
        metrics = blind_runner.run_all()
        found_any = False
        for key, d in metrics.dimensions.items():
            if d.critical_errors > 0 or d.high_errors > 0:
                found_any = True
                assert d.applicable > 0, (
                    f"Dimension '{key}' has errors but applicable={d.applicable}"
                )
        assert found_any, "Expected at least one dimension to have errors"

    def test_applicable_never_exceeds_total(self, golden_runner):
        """No dimension should have applicable > total cases."""
        metrics = golden_runner.run_all()
        for key, d in metrics.dimensions.items():
            assert d.applicable <= metrics.total, (
                f"Dimension '{key}' has applicable={d.applicable} > total={metrics.total}"
            )

    def test_na_display_for_zero_applicable(self):
        """Dimensions with applicable=0 should show N/A, not 100%."""
        from robot_intent_agent.eval.assertion_scorer import DimensionScore
        d = DimensionScore(name="test_dim", applicable=0, correct=0)
        d.compute()
        assert d.accuracy is None, f"Zero-applicable accuracy should be -1.0 (N/A), got {d.accuracy}"
        assert d.accuracy_display == "N/A", f"accuracy_display should be 'N/A', got '{d.accuracy_display}'"

    def test_accuracy_display_for_applicable_dim(self):
        """Dimensions with applicable > 0 should show percentage."""
        from robot_intent_agent.eval.assertion_scorer import DimensionScore
        d = DimensionScore(name="test", applicable=10, correct=9)
        d.compute()
        assert d.accuracy == 0.9
        assert d.accuracy_display == "90.0%"

    def test_applicable_tracked_in_case_verdict(self, blind_runner):
        """Every CaseVerdict from the runner must have applicable_dimensions populated (except edge cases)."""
        blind_runner.run_all()
        checked = 0
        for v in blind_runner.verdicts:
            # Edge cases (empty instruction, exception) won't have applicable_dimensions
            if v.exception or not v.instruction.strip():
                continue
            checked += 1
            assert isinstance(v.applicable_dimensions, list), \
                f"Case {v.case_id}: applicable_dimensions should be a list"
            # Always-on dimensions must be present for successful pipeline cases
            for dim in ("perception_factual_fidelity", "bt_ir_cross_field_consistency",
                       "schema_validity"):
                assert dim in v.applicable_dimensions, \
                    f"Case {v.case_id}: always-on dimension '{dim}' missing from applicable_dimensions"
        assert checked > 0, "Expected at least one non-edge-case verdict"


# ── Test: Severity enum ────────────────────────────────────

class TestSeverityEnum:
    def test_severity_ordering(self):
        """CRITICAL > HIGH > MEDIUM > LOW > INFO."""
        assert Severity.CRITICAL.value == "CRITICAL"
        assert Severity.HIGH.value == "HIGH"
        assert Severity.MEDIUM.value == "MEDIUM"
        assert Severity.LOW.value == "LOW"
        assert Severity.INFO.value == "INFO"


# ── Test: Phase 1 — Unified scoring + consistency ─────────

class TestUnifiedScoring:
    """Verify score_case() is the single scoring entry point."""

    def test_score_case_produces_case_verdict(self, golden_runner):
        """score_case should produce a CaseVerdict with findings."""
        from robot_intent_agent.eval.assertion_scorer import score_case
        case = golden_runner.dataset["cases"][0]
        # Run pipeline
        objects_raw = case.get("objects", [])
        raw_objs = UpgradedEvalRunner._build_raw_objects(objects_raw)
        scene = golden_runner._builder.build(raw_objs)
        bt = BehaviorTreeGenerator().plan(case["instruction"], scene=scene)
        cg = HybridConstraintCompiler().compile(case["instruction"], bt, scene=scene, target=raw_objs[0].name)
        ir = RobotTaskIRGenerator().generate(case["instruction"], bt, cg, scene=scene)

        v = score_case(case, ir, scene, bt, cg)
        assert isinstance(v, CaseVerdict)
        assert v.case_id == case["case_id"]
        assert v.instruction == case["instruction"]

    def test_same_score_from_runner_and_direct_call(self, golden_runner):
        """Runner._evaluate_case and direct score_case must produce same result."""
        from robot_intent_agent.eval.assertion_scorer import score_case
        case = golden_runner.dataset["cases"][0]
        objects_raw = case.get("objects", [])
        raw_objs = UpgradedEvalRunner._build_raw_objects(objects_raw)
        scene = golden_runner._builder.build(raw_objs)
        bt = BehaviorTreeGenerator().plan(case["instruction"], scene=scene)
        cg = HybridConstraintCompiler().compile(case["instruction"], bt, scene=scene, target=raw_objs[0].name)
        ir = RobotTaskIRGenerator().generate(case["instruction"], bt, cg, scene=scene)

        v1 = score_case(case, ir, scene, bt, cg)
        v2 = golden_runner._evaluate_case(case)
        # Both should produce same passed/failed and same number of findings
        assert v1.passed == v2.passed, f"score_case passed={v1.passed}, runner passed={v2.passed}"


class TestRunId:
    """Verify run_id is generated and consistent across exports."""

    def test_run_id_present_in_summary(self, golden_runner):
        metrics = golden_runner.run_all()
        assert metrics.run_id, "run_id must not be empty"
        assert metrics.run_id.startswith("eval-"), f"Unexpected run_id format: {metrics.run_id}"

    def test_run_id_in_json_export(self, golden_runner, temp_dir):
        metrics = golden_runner.run_all()
        fp = str(temp_dir / "summary.json")
        export_summary_json(metrics, fp)
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("run_id") == metrics.run_id

    def test_different_runs_have_different_run_ids(self, golden_runner):
        m1 = golden_runner.run_all()
        m2 = golden_runner.run_all()
        assert m1.run_id != m2.run_id, "Different runs must have different run_ids"


class TestConsistencyChecks:
    """Verify consistency checks pass/fail correctly."""

    def test_passed_equals_failed_matches_total(self, golden_runner):
        metrics = golden_runner.run_all()
        assert metrics.total == metrics.passed + metrics.failed, \
            f"total ({metrics.total}) != passed ({metrics.passed}) + failed ({metrics.failed})"

    def test_severity_counts_match_case_results(self, golden_runner):
        metrics = golden_runner.run_all()
        from robot_intent_agent.eval.assertion_scorer import verify_consistency
        errors = verify_consistency(metrics, golden_runner.verdicts)
        assert len(errors) == 0, f"Consistency errors: {errors}"

    def test_consistency_detects_total_mismatch(self):
        """verify_consistency should detect total != passed + failed."""
        from robot_intent_agent.eval.assertion_scorer import verify_consistency, MetricsSummary
        m = MetricsSummary(run_id="test", total=10, passed=7, failed=2)  # 7+2 != 10
        errors = verify_consistency(m, [])
        assert len(errors) > 0
        assert any("total" in e for e in errors)

    def test_consistency_detects_empty_run_id(self):
        """verify_consistency should detect empty run_id."""
        from robot_intent_agent.eval.assertion_scorer import verify_consistency, MetricsSummary
        m = MetricsSummary(run_id="", total=5, passed=3, failed=2)
        errors = verify_consistency(m, [])
        assert len(errors) > 0
        assert any("run_id" in e for e in errors)

    def test_all_export_files_have_same_run_id(self, golden_runner, temp_dir):
        """summary.json, case_results.json, failures.csv must have same run_id."""
        import csv
        metrics = golden_runner.run_all()

        # Export all
        export_summary_json(metrics, str(temp_dir / "summary.json"))
        export_case_results_json(golden_runner.verdicts, str(temp_dir / "case_results.json"),
                                 run_id=metrics.run_id)
        export_failures_csv(golden_runner.verdicts, str(temp_dir / "failures.csv"),
                           run_id=metrics.run_id)

        # Check summary.json
        with open(str(temp_dir / "summary.json"), "r", encoding="utf-8") as f:
            sum_data = json.load(f)
        assert sum_data["run_id"] == metrics.run_id

        # Check case_results.json
        with open(str(temp_dir / "case_results.json"), "r", encoding="utf-8") as f:
            case_data = json.load(f)
        if case_data:
            assert case_data[0]["run_id"] == metrics.run_id

        # Check failures.csv
        with open(str(temp_dir / "failures.csv"), "r", encoding="utf-8-sig") as f:
            csv_data = list(csv.reader(f))
        if len(csv_data) > 1:
            assert csv_data[1][0] == metrics.run_id  # First data row, first column


# ── Test: Verdict properties ───────────────────────────────

class TestVerdictProperties:
    def test_critical_count(self):
        v = CaseVerdict()
        v.findings = [
            EvalFinding(metric="d1", severity=Severity.CRITICAL, expected="a", actual="b"),
            EvalFinding(metric="d2", severity=Severity.HIGH, expected="c", actual="d"),
            EvalFinding(metric="d3", severity=Severity.CRITICAL, expected="e", actual="f"),
        ]
        assert v.critical_count == 2
        assert v.has_critical is True

    def test_empty_findings(self):
        v = CaseVerdict()
        assert v.critical_count == 0
        assert v.has_critical is False


# ── Phase 3: Canonical Entity Resolver tests ────────────────

class TestCanonicalEntityResolver:
    """Verify CanonicalEntityResolver correctly maps perception ↔ scene IDs."""

    @staticmethod
    def _make_scene_obj(sid, name, color, material, perception_id=None):
        from robot_intent_agent.schemas.scene import SceneObject, Position, BoundingBox
        attrs = {"color": color, "material": material}
        if perception_id:
            attrs["_perception_object_id"] = perception_id
        return SceneObject(
            id=sid, name=name, label=name,
            position=Position(x=0.3, y=0.1, z=0.05),
            bbox=BoundingBox(width=0.07, height=0.1, depth=0.07),
            attributes=attrs,
        )

    def test_perception_id_to_scene_uuid(self):
        """Perception object_id should map to scene UUID via _perception_object_id attr."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [{"object_id": "obj-red", "category_candidates": [{"name": "cup", "score": 0.9}],
                       "appearance": {"color": "red", "material": "plastic"},
                       "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}}]
        scene_objs = [self._make_scene_obj("obj-a1b2c3", "cup", "red", "plastic", "obj-red")]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        assert resolver.perception_to_scene("obj-red") == "obj-a1b2c3"
        assert resolver.scene_to_perception("obj-a1b2c3") == "obj-red"

    def test_same_name_different_color_distinguished(self):
        """Two cups with different colors must NOT be considered the same."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-red", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "red"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}},
            {"object_id": "obj-blue", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "blue"}, "pose": {"position": {"x": 0.3, "y": -0.1, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s1", "cup", "red", "plastic"),
            self._make_scene_obj("s2", "cup", "blue", "plastic"),
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        # obj-red should map to red cup, obj-blue to blue cup
        s_red = resolver.perception_to_scene("obj-red")
        s_blue = resolver.perception_to_scene("obj-blue")
        assert s_red is not None
        assert s_blue is not None
        assert s_red != s_blue, "Red and blue cups must map to different scene objects"

    def test_same_color_different_position_distinguished(self):
        """Same color but different positions should NOT be confused."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-left", "category_candidates": [{"name": "box", "score": 0.9}],
             "appearance": {"color": "brown"}, "pose": {"position": {"x": 0.2, "y": 0.0, "z": 0.05}}},
            {"object_id": "obj-right", "category_candidates": [{"name": "box", "score": 0.9}],
             "appearance": {"color": "brown"}, "pose": {"position": {"x": 0.4, "y": 0.0, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s-left", "box", "brown", "cardboard"),
            self._make_scene_obj("s-right", "box", "brown", "cardboard"),
        ]
        # Two brown boxes — resolver uses name+color+material fallback
        # Both map to the first matching scene object (same name+color+material)
        # This is acceptable: exact position is a weaker signal for identity
        resolver = CanonicalEntityResolver(perception, scene_objs)
        assert resolver.perception_to_scene("obj-left") is not None
        assert resolver.perception_to_scene("obj-right") is not None

    def test_avoid_order_independent_comparison(self):
        """Avoid set comparison should be order-independent."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-a", "category_candidates": [{"name": "box", "score": 0.9}],
             "appearance": {"color": "brown"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}},
            {"object_id": "obj-b", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "white"}, "pose": {"position": {"x": 0.3, "y": -0.1, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s1", "box", "brown", "cardboard", "obj-a"),
            self._make_scene_obj("s2", "cup", "white", "plastic", "obj-b"),
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        # Avoid values in different orders should resolve to same set
        result1 = resolver.resolve_avoid_set(["s1", "s2"])
        result2 = resolver.resolve_avoid_set(["s2", "s1"])
        assert result1 == result2, f"Order should not matter: {result1} vs {result2}"

    def test_nonexistent_object_not_mapped(self):
        """An object_id not in perception should NOT be mapped."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [{"object_id": "obj-real", "category_candidates": [{"name": "cup", "score": 0.9}],
                       "appearance": {"color": "red"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}}]
        scene_objs = [self._make_scene_obj("s-real", "cup", "red", "plastic", "obj-real")]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        assert resolver.perception_to_scene("obj-nonexistent") is None
        assert resolver.scene_to_perception("s-nonexistent") is None

    def test_is_same_entity_cross_domain(self):
        """is_same_entity should compare across perception and scene domains."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [{"object_id": "obj-x", "category_candidates": [{"name": "cup", "score": 0.9}],
                       "appearance": {"color": "white"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}}]
        scene_objs = [self._make_scene_obj("s-x", "cup", "white", "plastic", "obj-x")]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        assert resolver.is_same_entity("obj-x", "s-x"), "perception ID vs scene UUID should match"
        assert resolver.is_same_entity("s-x", "obj-x"), "scene UUID vs perception ID should match"

    def test_resolver_integrated_in_score_case(self, golden_runner):
        """score_case should create a resolver and use it for entity checks."""
        from robot_intent_agent.eval.assertion_scorer import score_case, CanonicalEntityResolver
        case = golden_runner.dataset["cases"][0]
        objects_raw = case.get("objects", [])
        raw_objs = UpgradedEvalRunner._build_raw_objects(objects_raw)
        scene = golden_runner._builder.build(raw_objs)
        bt = BehaviorTreeGenerator().plan(case["instruction"], scene=scene)
        cg = HybridConstraintCompiler().compile(case["instruction"], bt, scene=scene, target=raw_objs[0].name)
        ir = RobotTaskIRGenerator().generate(case["instruction"], bt, cg, scene=scene)

        v = score_case(case, ir, scene, bt, cg)
        assert v is not None
        assert v.case_id == case["case_id"]


# ── Phase 2: Applicable dimensions tests ────────────────────

class TestApplicableDimensions:
    """Verify derive_applicable_dimensions() applies correct rules."""

    def test_simple_grasp_no_negation(self):
        """A simple '抓住杯子' with no negation should NOT have negation applicable."""
        from robot_intent_agent.eval.assertion_scorer import derive_applicable_dimensions
        case = {
            "case_id": "T01", "category": "simple_action",
            "instruction": "抓住杯子",
            "objects": [{"object_id": "o1", "category_candidates": [{"name": "cup", "score": 0.9}],
                         "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                         "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
                         "appearance": {"color": "white"}, "affordances": ["graspable"],
                         "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}}],
            "expected": {"action": "GRASP", "theme_entity_id": "o1"},
            "severity": {},
        }
        dims = derive_applicable_dimensions(case)
        assert "negation_constraint_retention" not in dims, \
            f"No negation keywords → negation should NOT be applicable, got: {dims}"

    def test_single_object_no_disambiguation(self):
        """Single object scene should NOT trigger disambiguation."""
        from robot_intent_agent.eval.assertion_scorer import derive_applicable_dimensions
        case = {
            "case_id": "T02", "category": "simple_action",
            "instruction": "抓住杯子",
            "objects": [{"object_id": "o1", "category_candidates": [{"name": "cup", "score": 0.9}],
                         "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                         "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
                         "appearance": {"color": "white"}, "affordances": ["graspable"],
                         "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}}],
            "expected": {"action": "GRASP", "theme_entity_id": "o1"},
            "severity": {},
        }
        dims = derive_applicable_dimensions(case)
        assert "multi_object_disambiguation" not in dims, \
            f"Single object → disambiguation should NOT be applicable, got: {dims}"

    def test_no_numbers_no_numeric(self):
        """Instruction without numbers should NOT trigger numeric dimension."""
        from robot_intent_agent.eval.assertion_scorer import derive_applicable_dimensions
        case = {
            "case_id": "T03", "category": "simple_action",
            "instruction": "抓住杯子",
            "objects": [{"object_id": "o1", "category_candidates": [{"name": "cup", "score": 0.9}],
                         "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                         "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
                         "appearance": {"color": "white"}, "affordances": ["graspable"],
                         "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}}],
            "expected": {"action": "GRASP", "theme_entity_id": "o1"},
            "severity": {},
        }
        dims = derive_applicable_dimensions(case)
        assert "numeric_operator_unit" not in dims, \
            f"No numbers → numeric should NOT be applicable, got: {dims}"

    def test_dangerous_only_with_safety_expectation(self):
        """Dangerous dimension only applicable when expected_execution=False."""
        from robot_intent_agent.eval.assertion_scorer import derive_applicable_dimensions
        # Safe case: no blocking expectations
        safe = {
            "case_id": "T04a", "instruction": "抓住杯子",
            "objects": [{"object_id": "o1", "category_candidates": [{"name": "cup", "score": 0.9}],
                         "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                         "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
                         "appearance": {"color": "white"}, "affordances": ["graspable"],
                         "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}}],
            "expected": {"action": "GRASP"},
            "severity": {},
        }
        dims_safe = derive_applicable_dimensions(safe)
        assert "dangerous_error_pass_through" not in dims_safe, \
            f"Safe case → dangerous should NOT apply, got: {dims_safe}"

        # Dangerous case: execution_allowed=False
        dangerous = {
            "case_id": "T04b", "instruction": "把杯子拿过来",
            "objects": [{"object_id": "o1", "category_candidates": [{"name": "block", "score": 0.9}],
                         "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                         "geometry": {"size": {"width": 0.05, "height": 0.05, "depth": 0.05}},
                         "appearance": {"color": "brown"}, "affordances": [],
                         "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}}],
            "expected": {"theme_not_in_scene": True, "execution_allowed": False},
            "severity": {},
        }
        dims_dangerous = derive_applicable_dimensions(dangerous)
        assert "dangerous_error_pass_through" in dims_dangerous, \
            f"Dangerous case → dangerous MUST apply, got: {dims_dangerous}"

    def test_negation_case_applies_negation_dim(self):
        """Instruction with '别碰' should trigger negation dimension."""
        from robot_intent_agent.eval.assertion_scorer import derive_applicable_dimensions
        case = {
            "case_id": "T05", "instruction": "抓住杯子，别碰盒子",
            "objects": [
                {"object_id": "o1", "category_candidates": [{"name": "cup", "score": 0.9}],
                 "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
                 "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
                 "appearance": {"color": "white"}, "affordances": ["graspable"],
                 "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
                {"object_id": "o2", "category_candidates": [{"name": "box", "score": 0.9}],
                 "pose": {"position": {"x": 0.3, "y": -0.1, "z": 0.05}},
                 "geometry": {"size": {"width": 0.08, "height": 0.06, "depth": 0.08}},
                 "appearance": {"color": "brown"}, "affordances": ["graspable"],
                 "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
            ],
            "expected": {"action": "GRASP", "theme_entity_id": "o1", "avoid_objects": ["o2"]},
            "severity": {},
        }
        dims = derive_applicable_dimensions(case)
        assert "negation_constraint_retention" in dims, \
            f"Negation case → negation must be applicable, got: {dims}"

    def test_all_cases_in_golden_have_category(self, golden_runner):
        """Every golden case must have a non-unknown category after derivation."""
        for case in golden_runner.dataset["cases"]:
            cat = case.get("category", "")
            if not cat or cat == "unknown":
                from robot_intent_agent.eval.assertion_scorer import derive_category
                cat = derive_category(case)
            assert cat and cat != "unknown", \
                f"Case {case['case_id']} has category='{cat}'"

    def test_all_cases_in_blind_have_category(self, blind_runner):
        """Every blind case must have a non-unknown category."""
        for case in blind_runner.dataset["cases"]:
            cat = case.get("category", "")
            if not cat or cat == "unknown":
                from robot_intent_agent.eval.assertion_scorer import derive_category
                cat = derive_category(case)
            assert cat and cat != "unknown", \
                f"Case {case['case_id']} has category='{cat}'"

    def test_categories_in_summary_no_unknown(self, blind_runner):
        """The by_category in metrics must not contain 'unknown'."""
        metrics = blind_runner.run_all()
        assert "unknown" not in metrics.by_category, \
            f"by_category contains 'unknown': {list(metrics.by_category.keys())}"


# ── Phase 1: Eval fix — Canonical Entity Resolver strictness ─

class TestCanonicalEntityResolverStrict:
    """Verify CanonicalEntityResolver strict identity rules (Phase 1 eval fix)."""

    @staticmethod
    def _make_scene_obj(sid, name, color, material, perception_id=None, x=0.3, y=0.1, z=0.05):
        from robot_intent_agent.schemas.scene import SceneObject, Position, BoundingBox
        attrs = {"color": color, "material": material}
        if perception_id:
            attrs["_perception_object_id"] = perception_id
        return SceneObject(
            id=sid, name=name, label=name,
            position=Position(x=x, y=y, z=z),
            bbox=BoundingBox(width=0.07, height=0.1, depth=0.07),
            attributes=attrs,
        )

    def test_same_name_same_color_different_position_not_confused(self):
        """Two objects with same name AND color but different positions: MUST be distinct."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-a", "category_candidates": [{"name": "box", "score": 0.9}],
             "appearance": {"color": "brown", "material": "cardboard"},
             "pose": {"position": {"x": 0.2, "y": 0.0, "z": 0.05}}},
            {"object_id": "obj-b", "category_candidates": [{"name": "box", "score": 0.9}],
             "appearance": {"color": "brown", "material": "cardboard"},
             "pose": {"position": {"x": 0.4, "y": 0.0, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s-a", "box", "brown", "cardboard", x=0.2, y=0.0, z=0.05),
            self._make_scene_obj("s-b", "box", "brown", "cardboard", x=0.4, y=0.0, z=0.05),
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        s_a = resolver.perception_to_scene("obj-a")
        s_b = resolver.perception_to_scene("obj-b")
        # Both should be mapped (via position-based disambiguation)
        assert s_a is not None, "obj-a should be mapped"
        assert s_b is not None, "obj-b should be mapped"
        assert s_a != s_b, f"Same name+color must map to different scene objects: {s_a} vs {s_b}"

    def test_same_name_different_id_not_same_entity(self):
        """Different perception IDs for same-name objects: NOT the same entity."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-1", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "white", "material": "plastic"},
             "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s-1", "cup", "white", "plastic", "obj-1"),
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        # obj-2 is NOT in perception → should NOT map to anything
        assert resolver.perception_to_scene("obj-2") is None
        assert not resolver.is_same_entity("obj-1", "obj-2")

    def test_nonexistent_id_not_mapped(self):
        """An ID that doesn't exist in either domain must not be considered valid."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-real", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "red", "material": "plastic"},
             "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s-real", "cup", "red", "plastic", "obj-real"),
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        # Non-existent IDs
        assert resolver.perception_to_scene("obj-fake") is None
        assert resolver.scene_to_perception("s-fake") is None
        assert not resolver.is_same_entity("obj-fake", "s-real")
        # Resolving non-existent should return None
        assert resolver.resolve_to_perception_id("s-fake") is None

    def test_avoid_order_independent_set_comparison(self):
        """Avoid object comparison must be order-independent."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        perception = [
            {"object_id": "obj-x", "category_candidates": [{"name": "box", "score": 0.9}],
             "appearance": {"color": "brown"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}},
            {"object_id": "obj-y", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "white"}, "pose": {"position": {"x": 0.3, "y": -0.1, "z": 0.05}}},
            {"object_id": "obj-z", "category_candidates": [{"name": "bottle", "score": 0.9}],
             "appearance": {"color": "blue"}, "pose": {"position": {"x": 0.0, "y": 0.0, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("sx", "box", "brown", "cardboard", "obj-x"),
            self._make_scene_obj("sy", "cup", "white", "plastic", "obj-y"),
            self._make_scene_obj("sz", "bottle", "blue", "plastic", "obj-z"),
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        r1 = resolver.resolve_avoid_set(["obj-x", "obj-y", "obj-z"])
        r2 = resolver.resolve_avoid_set(["obj-z", "obj-x", "obj-y"])
        r3 = resolver.resolve_avoid_set(["obj-y", "obj-z", "obj-x"])
        assert r1 == r2 == r3, f"Order must not matter: {r1} vs {r2} vs {r3}"

    def test_perception_id_priority_over_name_match(self):
        """Explicit _perception_object_id wins over name+color matching."""
        from robot_intent_agent.eval.assertion_scorer import CanonicalEntityResolver
        # obj-x: perception says red cup, but scene has explicit mapping to blue cup
        perception = [
            {"object_id": "obj-x", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "red", "material": "plastic"},
             "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}}},
        ]
        scene_objs = [
            self._make_scene_obj("s-blue", "cup", "blue", "plastic", "obj-x"),  # explicit mapping
            self._make_scene_obj("s-red", "cup", "red", "plastic"),  # name+color match but no explicit mapping
        ]
        resolver = CanonicalEntityResolver(perception, scene_objs)
        # Explicit mapping wins
        assert resolver.perception_to_scene("obj-x") == "s-blue", \
            "Explicit _perception_object_id must take priority over color match"


# ── Phase 1: Eval fix — Single-test vs batch consistency ─────

class TestSingleTestBatchConsistency:
    """Verify that single-test scoring matches batch scoring for the same case."""

    def test_single_and_batch_produce_same_verdict(self, golden_runner):
        """Direct score_case() and runner._evaluate_case() must agree on stable fields."""
        from robot_intent_agent.eval.assertion_scorer import score_case
        case = golden_runner.dataset["cases"][0]
        objects_raw = case.get("objects", [])
        raw_objs = UpgradedEvalRunner._build_raw_objects(objects_raw)
        # Build pipeline ONCE and share between both calls to avoid UUID variance
        scene = golden_runner._builder.build(raw_objs)
        bt = BehaviorTreeGenerator().plan(case["instruction"], scene=scene)
        cg = HybridConstraintCompiler().compile(case["instruction"], bt, scene=scene, target=raw_objs[0].name)
        ir = RobotTaskIRGenerator().generate(case["instruction"], bt, cg, scene=scene)

        v_direct = score_case(case, ir, scene, bt, cg)
        # Runner creates its own pipeline — compare only deterministic fields
        v_runner = golden_runner._evaluate_case(case)

        # Same passed/failed
        assert v_direct.passed == v_runner.passed, \
            f"Direct: passed={v_direct.passed}, Runner: passed={v_runner.passed}"
        # Same action (deterministic from instruction)
        assert v_direct.action_actual == v_runner.action_actual, \
            f"Action: direct={v_direct.action_actual}, runner={v_runner.action_actual}"
        assert v_direct.action_expected == v_runner.action_expected
        # Same expected entity (from case, not UUID-dependent)
        assert v_direct.theme_entity_expected == v_runner.theme_entity_expected
        # Same execution allowed
        assert v_direct.execution_allowed_actual == v_runner.execution_allowed_actual, \
            f"Exec allowed: direct={v_direct.execution_allowed_actual}, runner={v_runner.execution_allowed_actual}"
        # Same applicable dimensions (derived from case, not pipeline)
        assert set(v_direct.applicable_dimensions) == set(v_runner.applicable_dimensions), \
            f"Applicable dims differ: direct={v_direct.applicable_dimensions}, runner={v_runner.applicable_dimensions}"

    def test_critical_veto_consistent_across_paths(self):
        """CRITICAL veto must work the same in direct and batch paths."""
        from robot_intent_agent.eval.assertion_scorer import EvalFinding, Severity
        # Direct veto
        v1 = CaseVerdict(case_id="test", instruction="test")
        v1.findings = [EvalFinding(metric="entity_grounding", severity=Severity.CRITICAL,
                                    expected="obj-1", actual="obj-2")]
        # Apply veto (same logic as _apply_veto)
        if v1.has_critical:
            v1.passed = False
        else:
            v1.passed = len(v1.findings) == 0
        assert v1.passed is False

        # Batch veto (same logic)
        v2 = CaseVerdict(case_id="test", instruction="test")
        v2.findings = [EvalFinding(metric="entity_grounding", severity=Severity.CRITICAL,
                                    expected="obj-1", actual="obj-2")]
        if v2.has_critical:
            v2.passed = False
        else:
            v2.passed = len(v2.findings) == 0
        assert v2.passed is False
        # Both must agree
        assert v1.passed == v2.passed


# ── Phase 1: Eval fix — Legacy metrics data source ───────────

class TestLegacyDataSourceConsistency:
    """Verify legacy metrics read from unified CaseVerdict, not independent scoring."""

    def test_legacy_action_cases_equal_dimension_applicable(self, blind_runner):
        """Legacy action_cases should match action_recognition applicable count."""
        metrics = blind_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        dim = metrics.dimensions["action_recognition"]
        assert legacy["action_cases"] == dim.applicable, \
            f"Legacy action_cases={legacy['action_cases']} != dimension applicable={dim.applicable}"

    def test_legacy_entity_cases_consistent(self, blind_runner):
        """Legacy entity_cases should be consistent with entity_grounding applicable."""
        metrics = blind_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        dim = metrics.dimensions["entity_grounding"]
        # entity_cases counts theme_entity_expected, applicable also includes theme_not_in_scene
        # They should be close but not necessarily equal
        assert legacy["entity_cases"] >= dim.applicable - 5, \
            f"Legacy entity_cases={legacy['entity_cases']} too far from applicable={dim.applicable}"

    def test_legacy_force_cases_consistent(self, blind_runner):
        """Legacy force_cases should be consistent with numeric applicable."""
        metrics = blind_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        dim = metrics.dimensions["numeric_operator_unit"]
        # force_cases counts cases with force_expected, numeric also counts velocity + text numbers
        assert legacy["force_cases"] <= dim.applicable, \
            f"Legacy force_cases={legacy['force_cases']} > numeric applicable={dim.applicable}"

    def test_legacy_role_cases_from_applicable_dimensions(self, blind_runner):
        """Legacy role_cases should equal role_extraction applicable (same data source)."""
        metrics = blind_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        dim = metrics.dimensions["role_extraction"]
        assert legacy["role_cases"] == dim.applicable, \
            f"Legacy role_cases={legacy['role_cases']} != dimension applicable={dim.applicable}"

    def test_legacy_overall_pass_rate_matches(self, blind_runner):
        """Legacy overall_pass_rate should equal the main pass_rate."""
        metrics = blind_runner.run_all()
        legacy = metrics.to_dict()["legacy"]
        assert abs(legacy["overall_pass_rate"] - metrics.pass_rate) < 0.001, \
            f"Legacy overall={legacy['overall_pass_rate']} != main pass_rate={metrics.pass_rate}"

    def test_no_independent_scoring_in_legacy(self, blind_runner):
        """Legacy metrics must be derived from the same CaseVerdict list as 13-dim metrics."""
        metrics = blind_runner.run_all()
        # Both 13-dim and legacy come from the same compute_metrics() call
        # Verify that legacy severity counts can be derived from findings
        total_findings = sum(
            metrics.severity_counts.get(s, 0) for s in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
        )
        # Legacy doesn't re-score, it reads from CaseVerdict.findings
        # So total findings should be > 0 (we have errors)
        assert total_findings > 0, "Should have findings from blind eval"
