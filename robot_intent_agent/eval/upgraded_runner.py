"""
Upgraded Evaluation Runner v2.0 — 13-dimension intent understanding metrics.

Delegates all per-case scoring to assertion_scorer.score_case() (the single
authoritative entry point). The runner handles only:
  - Pipeline execution (Scene → BT → CG → IR)
  - Aggregation (via assertion_scorer.compute_metrics())
  - Multi-format export with run_id + consistency checks
"""

from __future__ import annotations

import csv
import json
import statistics
import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator

# ── Import the single authoritative scoring interface ──
from robot_intent_agent.eval.assertion_scorer import (
    # Data types (re-exported for backward compatibility)
    Severity,
    EvalFinding,
    CaseVerdict,
    DimensionScore,
    MetricsSummary,
    ALL_DIMS,
    # Scoring entry point
    score_case,
    # Metrics aggregation
    compute_metrics,
    # Consistency check
    verify_consistency,
    # Applicable dimensions
    derive_applicable_dimensions,
    derive_category,
    # Helpers
    _build_scene_id_map_from_scene,
)


# ══════════════════════════════════════════════════════════════
# Upgraded Eval Runner
# ══════════════════════════════════════════════════════════════

class UpgradedEvalRunner:
    """13-dimension intent understanding evaluator.

    Orchestrates pipeline execution, delegates scoring to
    assertion_scorer.score_case(), and aggregates results via
    assertion_scorer.compute_metrics().

    Supports planner injection for DeepSeek/RuleEngine/Both modes.
    """

    def __init__(self, dataset_path: str, planner=None, requested_engine: str = "RuleEngine"):
        self.dataset_path = dataset_path
        with open(dataset_path, "r", encoding="utf-8") as f:
            self.dataset = json.load(f)
        self.verdicts: List[CaseVerdict] = []
        self._builder = SemanticSceneBuilder()
        self.run_id: str = ""
        self._planner = planner  # Injected planner (LLMPlanner, etc.)
        self._requested_engine = requested_engine

        # Engine audit stats
        self.engine_stats: Dict[str, Any] = {
            "requested_engine": requested_engine,
            "total_cases": 0,
            "deepseek_call_attempted": 0,
            "deepseek_call_succeeded": 0,
            "fallback_count": 0,
            "rule_engine_direct_count": 0,
            "validator_executed": 0,
        }

    # ── Main entry ─────────────────────────────────────────

    def run_all(self) -> "EvaluationRunArtifact":
        """Run all cases and return an EvaluationRunArtifact."""
        from robot_intent_agent.eval.eval_artifact import (
            EvaluationRunArtifact, EngineStats, LatencyBreakdown,
        )
        self.run_id = f"eval-{_uuid.uuid4().hex[:12]}"
        self.verdicts = []
        started_at = datetime.now(timezone.utc).isoformat()

        for case in self.dataset.get("cases", []):
            verdict = self._evaluate_case(case)
            self.verdicts.append(verdict)

        completed_at = datetime.now(timezone.utc).isoformat()
        metrics = compute_metrics(self.verdicts, dataset_name=Path(self.dataset_path).name,
                                  run_id=self.run_id)

        engine_stats = EngineStats(
            requested_engine=self._requested_engine,
            total_cases=len(self.verdicts),
            deepseek_call_attempted=self.engine_stats.get("deepseek_call_attempted", 0),
            deepseek_call_succeeded=self.engine_stats.get("deepseek_call_succeeded", 0),
            deepseek_schema_failed=self.engine_stats.get("deepseek_schema_failed", 0),
            fallback_count=self.engine_stats.get("fallback_count", 0),
            rule_engine_direct_count=self.engine_stats.get("rule_engine_direct_count", 0),
            precheck_rejected_count=self.engine_stats.get("precheck_rejected_count", 0),
            validator_executed_count=self.engine_stats.get("validator_executed", 0),
            actual_engine_counts=self.engine_stats.get("actual_engine_counts", {}),
        )

        # Compute latency breakdown from verdicts
        latencies = [v.elapsed_ms for v in self.verdicts if v.elapsed_ms > 0]
        lb = LatencyBreakdown()
        if latencies:
            sorted_lats = sorted(latencies)
            lb.total_avg_ms = round(statistics.mean(latencies), 1)
            lb.total_p50_ms = round(sorted_lats[len(sorted_lats) // 2], 1)
            lb.total_p95_ms = round(sorted_lats[int(len(sorted_lats) * 0.95)], 1)
            lb.total_p99_ms = round(sorted_lats[int(len(sorted_lats) * 0.99)], 1)

        artifact = EvaluationRunArtifact(
            run_id=self.run_id,
            dataset_name=Path(self.dataset_path).name,
            dataset_hash="",  # Will be computed by caller
            requested_engine=self._requested_engine,
            started_at=started_at,
            completed_at=completed_at,
            summary=metrics,
            case_results=list(self.verdicts),
            engine_stats=engine_stats,
            latency_breakdown=lb,
        )
        return artifact

    def _evaluate_case(self, case: Dict[str, Any]) -> CaseVerdict:
        # Ensure category is never "unknown"
        if not case.get("category") or case.get("category") == "unknown":
            case["category"] = derive_category(case)

        instruction = case["instruction"]
        objects_raw = case.get("objects", [])
        t0 = time.time()

        # ── Edge case: empty instruction ──
        if not instruction.strip():
            v = CaseVerdict(case_id=case["case_id"],
                           category=case.get("category", "unknown"),
                           instruction=instruction)
            v.elapsed_ms = (time.time() - t0) * 1000
            # No pipeline to run; score_case handles the empty check via expected
            _apply_empty_check(v, case.get("expected", {}), case.get("severity", {}))
            return v

        # ── Normal pipeline ──
        try:
            raw_objects = self._build_raw_objects(objects_raw)
            if not raw_objects:
                v = CaseVerdict(case_id=case["case_id"],
                               category=case.get("category", "unknown"),
                               instruction=instruction)
                v.exception = "No valid objects"
                v.elapsed_ms = (time.time() - t0) * 1000
                return v

            scene = self._builder.build(raw_objects)
            target = raw_objects[0].name if raw_objects else "target"

            # ── Use injected planner or default RuleEngine ──
            actual_engine = "RuleEngine"
            fallback_used = False
            fallback_reason = None
            llm_attempted = False
            llm_succeeded = False

            if self._planner is not None:
                llm_attempted = True
                self.engine_stats["deepseek_call_attempted"] += 1
                try:
                    bt = self._planner.plan(instruction, scene=scene)
                    llm_succeeded = True
                    self.engine_stats["deepseek_call_succeeded"] += 1
                    actual_engine = getattr(self._planner, 'name', 'DeepSeek')
                except Exception as e:
                    fallback_used = True
                    fallback_reason = f"LLM error: {e}"
                    self.engine_stats["fallback_count"] += 1
                    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
                    actual_engine = f"RuleEngine(fallback:{type(e).__name__})"
            else:
                bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
                self.engine_stats["rule_engine_direct_count"] += 1

            # Ensure engine trace in BT metadata
            if "engine_trace" not in bt.metadata:
                bt.metadata["engine_trace"] = {}
            bt.metadata["engine_trace"].update({
                "requested_engine": self._requested_engine,
                "actual_engine": actual_engine,
                "llm_call_attempted": llm_attempted,
                "llm_call_succeeded": llm_succeeded,
                "fallback_used": fallback_used,
                "fallback_reason": fallback_reason,
            })

            cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
            ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
            self.engine_stats["validator_executed"] += 1

            # Build scene_id_map for entity grounding verification
            scene_id_map = self._build_scene_id_map(scene)

            # Derive applicable dimensions from case (not from expected keys)
            applicable_dims = derive_applicable_dimensions(case)

            # ── Delegate ALL scoring to the unified entry point ──
            v = score_case(case, ir, scene, bt, cg, scene_id_map=scene_id_map,
                          applicable_dimensions=applicable_dims)
            v.elapsed_ms = (time.time() - t0) * 1000
            return v

        except Exception as e:
            v = CaseVerdict(case_id=case["case_id"],
                           category=case.get("category", "unknown"),
                           instruction=instruction)
            v.exception = f"{type(e).__name__}: {str(e)[:300]}"
            v.elapsed_ms = (time.time() - t0) * 1000
            return v

    # ── Raw object builder ──────────────────────────────────

    @staticmethod
    def _build_raw_objects(objects_raw: List[Dict]) -> List[RawObjectPercept]:
        raw_objects = []
        for obj in objects_raw:
            if not isinstance(obj, dict):
                continue
            pos = obj.get("pose", {}).get("position", {})
            geom = obj.get("geometry", {}).get("size", obj.get("geometry", {}))
            if not isinstance(geom, dict):
                geom = {}
            app = obj.get("appearance", {}) if isinstance(obj.get("appearance"), dict) else {}
            cats = obj.get("category_candidates", [{"name": "unknown", "score": 0.5}])
            if not cats:
                cats = [{"name": "unknown", "score": 0.5}]
            valid_cats = [c for c in cats if isinstance(c, dict) and c.get("name")]
            if not valid_cats:
                valid_cats = [{"name": "unknown", "score": 0.5}]
            top_cat = max(valid_cats, key=lambda c: c.get("score", 0))

            def _sf(v, d=0.0):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return d

            # Detect invalid input data (non-numeric position, negative size, etc.)
            has_invalid = False
            px, py, pz = pos.get("x", 0), pos.get("y", 0), pos.get("z", 0.03)
            try:
                float(px); float(py); float(pz)
            except (TypeError, ValueError):
                has_invalid = True
            gw, gh, gd = geom.get("width", 0.05), geom.get("height", 0.08), geom.get("depth", 0.05)
            try:
                fw, fh, fd = float(gw), float(gh), float(gd)
                if fw <= 0 or fh <= 0 or fd <= 0:
                    has_invalid = True
            except (TypeError, ValueError):
                has_invalid = True

            raw_objects.append(RawObjectPercept(
                name=top_cat["name"],
                x=_sf(px), y=_sf(py), z=_sf(pz),
                width=max(0.001, _sf(gw, 0.05)),
                height=max(0.001, _sf(gh, 0.08)),
                depth=max(0.001, _sf(gd, 0.05)),
                color=app.get("color", "unknown"),
                material=app.get("material", "unknown"),
                object_id=obj.get("object_id"),
                has_invalid_data=has_invalid,
            ))
        return raw_objects

    @staticmethod
    def _build_scene_id_map(scene) -> Dict[str, str]:
        """Build mapping: dataset object_id → scene UUID."""
        return _build_scene_id_map_from_scene(scene)


def _apply_empty_check(v: CaseVerdict, expected: Dict, sev_rules: Dict) -> None:
    """Handle edge case: empty instruction with no pipeline output."""
    if expected.get("empty_scene") and v.execution_allowed_actual is True:
        v.findings.append(EvalFinding(
            metric="dangerous_error_pass_through", severity=Severity.CRITICAL,
            expected="blocked", actual="allowed",
            detail="Execution allowed with empty scene"))
    v.passed = len(v.findings) == 0


# ══════════════════════════════════════════════════════════════
# Export functions — with run_id + consistency checks
# ══════════════════════════════════════════════════════════════

def _check_then_export(metrics: MetricsSummary, verdicts: List[CaseVerdict]) -> None:
    """Run consistency check; raise RuntimeError if any violation found."""
    errors = verify_consistency(metrics, verdicts)
    if errors:
        raise RuntimeError(
            f"Consistency check FAILED for run {metrics.run_id}:\n" +
            "\n".join(f"  - {e}" for e in errors)
        )


def export_summary_json(metrics: MetricsSummary, filepath: str) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(metrics.to_dict(), f, ensure_ascii=False, indent=2)


def export_report_md(metrics: MetricsSummary, verdicts: List[CaseVerdict], filepath: str) -> None:
    _check_then_export(metrics, verdicts)
    m = metrics
    lines = [
        "# Intent Understanding — Evaluation Report (v2.0)",
        "",
        f"**Run ID**: `{m.run_id}`",
        f"**Dataset**: {m.dataset}",
        f"**Date**: 2026-07-21",
        f"**Total**: {m.total} | **Passed**: {m.passed} | **Failed**: {m.failed}",
        f"**Severe Veto**: {m.severe_veto_count} cases failed by CRITICAL-only",
        f"**Pass Rate**: {m.pass_rate:.1%}",
        "",
        "## Severity Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ]
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]:
        lines.append(f"| {sev} | {m.severity_counts.get(sev, 0)} |")

    lines.extend([
        "",
        "## 13-Dimension Accuracy",
        "",
        "| # | Dimension | Accuracy | Applicable | Critical | High | Medium |",
        "|---|-----------|----------|------------|----------|------|--------|",
    ])
    dim_labels = {
        "action_recognition": "1. Action Recognition",
        "role_extraction": "2. Role Extraction",
        "entity_grounding": "3. Entity Grounding",
        "multi_object_disambiguation": "4. Multi-Object Disambiguation",
        "negation_constraint_retention": "5. Negation Constraint Retention",
        "conditional_sequential_understanding": "6. Conditional/Sequential Understanding",
        "numeric_operator_unit": "7. Numeric/Operator/Unit Accuracy",
        "perception_factual_fidelity": "8. Perception Factual Fidelity",
        "robot_capability_constraint": "9. Robot Capability Constraint",
        "bt_ir_cross_field_consistency": "10. BT/IR Cross-Field Consistency",
        "schema_validity": "11. Schema Validity",
        "dangerous_error_pass_through": "12. Dangerous Error Pass-Through",
    }
    for key, d in m.dimensions.items():
        label = dim_labels.get(key, key)
        acc_str = d.accuracy_display if hasattr(d, 'accuracy_display') else (f"{d.accuracy:.1%}" if d.accuracy >= 0 else "N/A")
        lines.append(f"| {label} | {acc_str} | {d.applicable} | {d.critical_errors} | {d.high_errors} | {d.medium_errors} |")

    lines.extend([
        "",
        "## Latency",
        "",
        f"| Avg | P50 | P95 | P99 |",
        f"|-----|-----|-----|-----|",
        f"| {m.latency_avg_ms:.1f}ms | {m.latency_p50_ms:.1f}ms | {m.latency_p95_ms:.1f}ms | {m.latency_p99_ms:.1f}ms |",
        "",
        "## By Category",
        "",
        "| Category | Total | Passed | Critical | High |",
        "|----------|-------|--------|----------|------|",
    ])
    for cat, counts in sorted(m.by_category.items()):
        lines.append(f"| {cat} | {counts['total']} | {counts['passed']} | {counts['critical']} | {counts['high']} |")

    lines.extend([
        "",
        "## Legacy Metrics (backward compatible)",
        "",
        f"| Metric | Accuracy | Cases |",
        f"|--------|----------|-------|",
        f"| Action | {m.action_accuracy:.1%} | {m.action_cases} |",
        f"| Entity Grounding | {m.entity_grounding_accuracy:.1%} | {m.entity_cases} |",
        f"| Force Parsing | {m.force_parsing_accuracy:.1%} | {m.force_cases} |",
        f"| Role Detection | {m.role_detection_accuracy:.1%} | {m.role_cases} |",
        f"| Schema Pass | {m.schema_pass_rate:.1%} | {m.total} |",
        f"| Overall | {m.overall_pass_rate:.1%} | {m.total} |",
        "",
        "## Failed Cases",
        "",
    ])
    for v in verdicts:
        if not v.passed:
            icon = "❌" if v.has_critical else "⚠️"
            lines.append(f"### {icon} {v.case_id} [{v.category}]: {v.instruction[:60]}")
            if v.exception:
                lines.append(f"- **Exception**: {v.exception[:200]}")
            lines.append(f"- Action: expected={v.action_expected}, actual={v.action_actual}")
            lines.append(f"- Entity: expected={v.theme_entity_expected}, actual={v.theme_entity_actual}")
            lines.append(f"- Execution: expected={v.execution_allowed_expected}, actual={v.execution_allowed_actual}")
            for f in v.findings:
                lines.append(f"- [{f.severity.value}] **{f.metric}**: {f.detail} (expected={f.expected}, actual={f.actual})")
            lines.append("")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def export_case_results_json(verdicts: List[CaseVerdict], filepath: str,
                             run_id: str = "") -> None:
    data = []
    for v in verdicts:
        data.append({
            "run_id": run_id,
            "case_id": v.case_id,
            "category": v.category,
            "instruction": v.instruction,
            "passed": v.passed,
            "has_critical": v.has_critical,
            "action_actual": v.action_actual,
            "action_expected": v.action_expected,
            "theme_entity_actual": v.theme_entity_actual,
            "theme_entity_expected": v.theme_entity_expected,
            "execution_allowed_actual": v.execution_allowed_actual,
            "execution_allowed_expected": v.execution_allowed_expected,
            "force_actual": v.force_actual,
            "force_expected": v.force_expected,
            "elapsed_ms": v.elapsed_ms,
            "exception": v.exception,
            "critical_count": v.critical_count,
            "findings": [{"metric": f.metric, "severity": f.severity.value,
                          "expected": f.expected, "actual": f.actual, "detail": f.detail}
                         for f in v.findings],
        })
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_failures_csv(verdicts: List[CaseVerdict], filepath: str,
                        run_id: str = "") -> None:
    failures = [v for v in verdicts if not v.passed]
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["run_id", "case_id", "category", "instruction", "severity",
                        "metric", "expected", "actual", "detail", "elapsed_ms"])
        for v in failures:
            if v.findings:
                for fi in v.findings:
                    writer.writerow([
                        run_id, v.case_id, v.category, v.instruction[:80],
                        fi.severity.value, fi.metric,
                        fi.expected[:100], fi.actual[:100], fi.detail[:200],
                        f"{v.elapsed_ms:.1f}",
                    ])
            else:
                writer.writerow([
                    run_id, v.case_id, v.category, v.instruction[:80],
                    "UNKNOWN", "exception", "", "", v.exception[:200],
                    f"{v.elapsed_ms:.1f}",
                ])


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    ds_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).parent / "blind_dataset.json")
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent

    print(f"Upgraded Eval Runner v2.0 — Unified Scoring")
    print(f"Dataset: {ds_path}")
    print()

    runner = UpgradedEvalRunner(ds_path)
    metrics = runner.run_all()

    # Consistency check before export
    try:
        _check_then_export(metrics, runner.verdicts)
    except RuntimeError as e:
        print(f"FATAL: {e}")
        sys.exit(1)

    # Print summary
    print(f"Run ID: {metrics.run_id}")
    print(f"Total: {metrics.total}  Passed: {metrics.passed}  Failed: {metrics.failed}")
    print(f"Pass Rate: {metrics.pass_rate:.1%}  Severe Veto: {metrics.severe_veto_count}")
    print(f"CRITICAL: {metrics.severity_counts.get('CRITICAL', 0)}  "
          f"HIGH: {metrics.severity_counts.get('HIGH', 0)}  "
          f"MEDIUM: {metrics.severity_counts.get('MEDIUM', 0)}")
    print()
    print("Dimension Accuracy:")
    for key, d in metrics.dimensions.items():
        acc_str = d.accuracy_display if hasattr(d, 'accuracy_display') else (f"{d.accuracy:.1%}" if d.accuracy >= 0 else "N/A")
        print(f"  {d.name:45s} {acc_str:>6s}  (C:{d.critical_errors} H:{d.high_errors})")
    print(f"\nLatency — Avg: {metrics.latency_avg_ms:.1f}ms  P50: {metrics.latency_p50_ms:.1f}ms  "
          f"P95: {metrics.latency_p95_ms:.1f}ms  P99: {metrics.latency_p99_ms:.1f}ms")

    # Export
    run_id = metrics.run_id
    export_summary_json(metrics, str(out_dir / "summary.json"))
    export_report_md(metrics, runner.verdicts, str(out_dir / "report.md"))
    export_case_results_json(runner.verdicts, str(out_dir / "case_results.json"), run_id=run_id)
    export_failures_csv(runner.verdicts, str(out_dir / "failures.csv"), run_id=run_id)
    print(f"\nExported: summary.json, report.md, case_results.json, failures.csv")
    print(f"All files share run_id: {run_id}")
