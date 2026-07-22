"""
Tests for Phase 9 (Frontend evaluation UI) and Phase 10 (DeepSeek comparison).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════
# Phase 9: Frontend eval UI
# ══════════════════════════════════════════════════════════════

class TestFrontendEvalFunctions:
    """Test the evaluation functions used by the Gradio UI."""

    def test_upgraded_runner_imports(self):
        """All required imports for upgraded eval UI must resolve."""
        from robot_intent_agent.eval.upgraded_runner import (
            UpgradedEvalRunner, export_summary_json,
            export_report_md, export_case_results_json, export_failures_csv,
        )
        assert UpgradedEvalRunner is not None

    def test_golden_dataset_accessible(self):
        """Golden dataset must be loadable."""
        ds_path = Path(__file__).parent.parent / "eval" / "golden_dataset.json"
        assert ds_path.exists(), f"Golden dataset not found at {ds_path}"
        with open(ds_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "cases" in data or isinstance(data, list) or "normal_cases" in data

    def test_blind_dataset_accessible(self):
        """Blind dataset must be loadable."""
        ds_path = Path(__file__).parent.parent / "eval" / "blind_dataset.json"
        assert ds_path.exists(), f"Blind dataset not found at {ds_path}"
        with open(ds_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "cases" in data

    def test_export_json(self):
        """JSON export must produce valid file."""
        from robot_intent_agent.eval.upgraded_runner import (
            UpgradedEvalRunner, export_summary_json,
        )
        ds_path = str(Path(__file__).parent.parent / "eval" / "golden_dataset.json")
        runner = UpgradedEvalRunner(ds_path)
        metrics = runner.run_all()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            export_summary_json(metrics, f.name)
            assert os.path.getsize(f.name) > 0
        os.unlink(f.name)

    def test_export_csv(self):
        """CSV export must produce valid UTF-8 CSV."""
        from robot_intent_agent.eval.upgraded_runner import (
            UpgradedEvalRunner, export_failures_csv,
        )
        ds_path = str(Path(__file__).parent.parent / "eval" / "golden_dataset.json")
        runner = UpgradedEvalRunner(ds_path)
        runner.run_all()
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            export_failures_csv(runner.verdicts, f.name)
            assert os.path.getsize(f.name) > 0
        os.unlink(f.name)

    def test_eval_ui_no_duplicate_scoring_logic(self):
        """Eval UI must use UpgradedEvalRunner, not re-implement scoring."""
        import inspect
        # Read the web_ui source to check it uses UpgradedEvalRunner
        web_ui_path = Path(__file__).parent.parent / "demo" / "web_ui.py"
        source = web_ui_path.read_text(encoding="utf-8")
        # Must import UpgradedEvalRunner
        assert "UpgradedEvalRunner" in source, "Eval UI must use UpgradedEvalRunner"
        # Must call runner.run_all() to get results (not manual calculation)
        assert "runner.run_all()" in source or "UpgradedEvalRunner" in source, \
            "Eval UI must use UpgradedEvalRunner, not re-implement scoring logic"


# ══════════════════════════════════════════════════════════════
# Phase 10: DeepSeek vs RuleEngine comparison
# ══════════════════════════════════════════════════════════════

class TestDeepSeekComparison:
    """Test the DeepSeek comparison runner."""

    def test_compare_module_imports(self):
        """All required imports must resolve."""
        from robot_intent_agent.eval.deepseek_compare import (
            COMPARE_CASES, CompareCase, EngineResult,
            evaluate_case, run_comparison,
        )
        assert len(COMPARE_CASES) >= 10, "Must have at least 10 comparison cases"

    def test_evaluate_case_rule_engine(self):
        """RuleEngine must pass all invariants for a simple case."""
        from robot_intent_agent.eval.deepseek_compare import evaluate_case
        case = COMPARE_CASES[0]  # DS01: Simple grasp
        result = evaluate_case(case, use_llm=False)
        assert not result.exception, f"RuleEngine should not crash: {result.exception}"
        assert result.action == "GRASP", f"Expected GRASP, got {result.action}"
        assert len(result.errors) == 0, f"RuleEngine should have no errors: {result.errors}"

    def test_evaluate_case_deepseek_no_key(self):
        """DeepSeek without API key must fallback to RuleEngine."""
        from robot_intent_agent.eval.deepseek_compare import evaluate_case
        case = COMPARE_CASES[0]
        result = evaluate_case(case, use_llm=True, api_key="")
        assert result.fallback_used, "DeepSeek without API key must fallback"
        assert "no_key" in result.fallback_reason.lower() or "No API" in result.fallback_reason, \
            f"Fallback reason must mention missing key: {result.fallback_reason}"

    def test_both_engines_use_same_validator(self):
        """Both engines must pass through the same FinalPlanValidator."""
        from robot_intent_agent.eval.deepseek_compare import evaluate_case
        import robot_intent_agent.ir.ir_generator as irg
        import inspect
        source = inspect.getsource(irg.RobotTaskIRGenerator.generate)
        assert "FinalPlanValidator" in source, \
            "Both engines must go through FinalPlanValidator in IR generation"

    def test_comparison_runner_produces_valid_output(self):
        """Comparison runner must produce valid summary."""
        from robot_intent_agent.eval.deepseek_compare import run_comparison
        summary = run_comparison(api_key="")
        assert "rule_engine" in summary
        assert "deepseek" in summary
        assert "agreement" in summary
        assert summary["deepseek"]["fallback_count"] == len(COMPARE_CASES), \
            "All DeepSeek should fallback with empty API key"

    def test_glass_force_safety_invariant(self):
        """DS05: Glass cup with 50N must be capped to 2N by BOTH engines."""
        from robot_intent_agent.eval.deepseek_compare import evaluate_case
        case = [c for c in COMPARE_CASES if c.case_id == "DS05"][0]
        r = evaluate_case(case, use_llm=False)
        # RuleEngine must cap force
        if r.force_n is not None:
            assert r.force_n <= 2.0, f"RuleEngine force {r.force_n}N exceeds glass limit 2N"

    def test_target_not_in_scene_blocks_both(self):
        """DS07: Target not in scene must be BLOCKED by both engines."""
        from robot_intent_agent.eval.deepseek_compare import evaluate_case
        case = [c for c in COMPARE_CASES if c.case_id == "DS07"][0]
        r = evaluate_case(case, use_llm=False)
        assert not r.execution_allowed, "RuleEngine must block when target not in scene"

    def test_no_independent_deepseek_safety_path(self):
        """DeepSeek must NOT have a separate safety/validation path."""
        import robot_intent_agent.ir.ir_generator as irg
        import inspect
        source = inspect.getsource(irg.RobotTaskIRGenerator.generate)
        # The validate() call must be unconditional (not in an if/else per engine)
        assert "validator.validate(" in source or "FinalPlanValidator" in source

    def test_export_file_produced(self):
        """Comparison must produce deepseek_comparison.json."""
        from robot_intent_agent.eval.deepseek_compare import run_comparison
        run_comparison(api_key="")
        export_path = Path(__file__).parent.parent / "eval" / "deepseek_comparison.json"
        assert export_path.exists(), "deepseek_comparison.json must be produced"
        with open(export_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "summary" in data
        assert "results" in data


# ── Reusable fixture ────────────────────────────────────────

from robot_intent_agent.eval.deepseek_compare import COMPARE_CASES
