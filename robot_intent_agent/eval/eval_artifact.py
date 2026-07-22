"""
EvaluationRunArtifact — Single authoritative evaluation run result.

ONE run produces ONE artifact. JSON, Markdown, CSV are all exported
from this same artifact. Never re-run evaluation during export.

Design invariant:
    runner.run() → EvaluationRunArtifact
    → export_json(artifact)
    → export_markdown(artifact)
    → export_csv(artifact)
    All three share the same run_id.
"""

from __future__ import annotations

import csv
import json
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from robot_intent_agent.eval.assertion_scorer import (
    CaseVerdict,
    DimensionScore,
    MetricsSummary,
    Severity,
    ALL_DIMS,
    verify_consistency,
)


# ══════════════════════════════════════════════════════════════
# Engine Stats
# ══════════════════════════════════════════════════════════════

@dataclass
class EngineStats:
    """Auditable engine usage statistics for one evaluation run."""
    requested_engine: str = "RuleEngine"
    total_cases: int = 0
    deepseek_call_attempted: int = 0
    deepseek_call_succeeded: int = 0
    deepseek_schema_failed: int = 0
    deepseek_repair_attempted: int = 0
    deepseek_repair_succeeded: int = 0
    fallback_count: int = 0
    rule_engine_direct_count: int = 0
    precheck_rejected_count: int = 0
    validator_executed_count: int = 0
    actual_engine_counts: Dict[str, int] = field(default_factory=dict)

    def validate_conservation(self) -> List[str]:
        """Check that case counts are conserved."""
        errors = []
        total_paths = (
            self.deepseek_call_succeeded
            + self.fallback_count
            + self.rule_engine_direct_count
            + self.precheck_rejected_count
        )
        if total_paths != self.total_cases:
            errors.append(
                f"Engine count conservation failed: "
                f"succeeded({self.deepseek_call_succeeded}) + "
                f"fallback({self.fallback_count}) + "
                f"rule_direct({self.rule_engine_direct_count}) + "
                f"precheck_rejected({self.precheck_rejected_count}) = "
                f"{total_paths} ≠ total({self.total_cases})"
            )
        return errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requested_engine": self.requested_engine,
            "total_cases": self.total_cases,
            "deepseek_call_attempted": self.deepseek_call_attempted,
            "deepseek_call_succeeded": self.deepseek_call_succeeded,
            "deepseek_schema_failed": self.deepseek_schema_failed,
            "deepseek_repair_attempted": self.deepseek_repair_attempted,
            "deepseek_repair_succeeded": self.deepseek_repair_succeeded,
            "fallback_count": self.fallback_count,
            "rule_engine_direct_count": self.rule_engine_direct_count,
            "precheck_rejected_count": self.precheck_rejected_count,
            "validator_executed_count": self.validator_executed_count,
            "actual_engine_counts": dict(self.actual_engine_counts),
        }


# ══════════════════════════════════════════════════════════════
# Latency Breakdown
# ══════════════════════════════════════════════════════════════

@dataclass
class LatencyBreakdown:
    """Per-stage latency breakdown."""
    total_avg_ms: float = 0.0
    total_p50_ms: float = 0.0
    total_p95_ms: float = 0.0
    total_p99_ms: float = 0.0
    planner_avg_ms: float = 0.0
    llm_api_avg_ms: float = 0.0
    normalization_avg_ms: float = 0.0
    grounding_avg_ms: float = 0.0
    compiler_avg_ms: float = 0.0
    validator_avg_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_avg_ms": self.total_avg_ms,
            "total_p50_ms": self.total_p50_ms,
            "total_p95_ms": self.total_p95_ms,
            "total_p99_ms": self.total_p99_ms,
            "planner_avg_ms": self.planner_avg_ms,
            "llm_api_avg_ms": self.llm_api_avg_ms,
            "normalization_avg_ms": self.normalization_avg_ms,
            "grounding_avg_ms": self.grounding_avg_ms,
            "compiler_avg_ms": self.compiler_avg_ms,
            "validator_avg_ms": self.validator_avg_ms,
        }


# ══════════════════════════════════════════════════════════════
# EvaluationRunArtifact
# ══════════════════════════════════════════════════════════════

@dataclass
class EvaluationRunArtifact:
    """Single, immutable result of one complete evaluation run.

    All exports (JSON, Markdown, CSV) read from this same object.
    Never re-run evaluation during export.
    """
    run_id: str = field(default_factory=lambda: f"eval-{_uuid.uuid4().hex[:12]}")
    dataset_name: str = ""
    dataset_hash: str = ""
    code_commit: Optional[str] = None
    requested_engine: str = "RuleEngine"
    model_name: Optional[str] = None
    prompt_version: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    summary: Optional[MetricsSummary] = None
    case_results: List[CaseVerdict] = field(default_factory=list)
    engine_stats: EngineStats = field(default_factory=EngineStats)
    latency_breakdown: LatencyBreakdown = field(default_factory=LatencyBreakdown)

    def to_dict(self) -> Dict[str, Any]:
        """Backward-compatible: delegate to summary.to_dict()."""
        return self.summary.to_dict() if self.summary else {}

    # ══ Consistency invariants ══

    def check_consistency(self) -> List[str]:
        """Run all consistency invariants. Returns list of violations (empty = OK)."""
        errors: List[str] = []

        # 1. Total = passed + failed
        if self.summary:
            if self.summary.total != self.summary.passed + self.summary.failed:
                errors.append(
                    f"Total mismatch: {self.summary.total} ≠ "
                    f"{self.summary.passed} + {self.summary.failed}"
                )

        # 2. Total matches case_results
        if self.summary and len(self.case_results) > 0:
            if self.summary.total != len(self.case_results):
                errors.append(
                    f"Summary total ({self.summary.total}) ≠ "
                    f"len(case_results) ({len(self.case_results)})"
                )

        # 3. Passed count matches
        if self.summary and len(self.case_results) > 0:
            actual_passed = sum(1 for c in self.case_results if c.passed)
            if self.summary.passed != actual_passed:
                errors.append(
                    f"Summary passed ({self.summary.passed}) ≠ "
                    f"actual passed ({actual_passed})"
                )

        # 4. Failed count matches
        if self.summary and len(self.case_results) > 0:
            actual_failed = sum(1 for c in self.case_results if not c.passed)
            if self.summary.failed != actual_failed:
                errors.append(
                    f"Summary failed ({self.summary.failed}) ≠ "
                    f"actual failed ({actual_failed})"
                )

        # 5. All case_results share same run_id
        for c in self.case_results:
            if hasattr(c, 'run_id') and c.run_id and c.run_id != self.run_id:
                errors.append(
                    f"Case {c.case_id} has run_id={c.run_id} ≠ artifact.run_id={self.run_id}"
                )

        # 6. Engine stats conservation
        errors.extend(self.engine_stats.validate_conservation())

        # 7. Failure count in CSV matches
        failure_cases = [c for c in self.case_results if not c.passed]
        if self.summary and len(failure_cases) != self.summary.failed:
            errors.append(
                f"filtered failure_cases ({len(failure_cases)}) ≠ "
                f"summary.failed ({self.summary.failed})"
            )

        # 8. Run verification from scorer
        if self.summary and len(self.case_results) > 0:
            scorer_errors = verify_consistency(self.summary, self.case_results)
            errors.extend(scorer_errors)

        return errors

    # ══ Properties ══

    @property
    def failure_cases(self) -> List[CaseVerdict]:
        return [c for c in self.case_results if not c.passed]

    @property
    def passed_cases(self) -> List[CaseVerdict]:
        return [c for c in self.case_results if c.passed]

    @property
    def failed_case_count(self) -> int:
        return len(self.failure_cases)

    @property
    def failure_event_count(self) -> int:
        return sum(len(c.findings) for c in self.failure_cases)

    # ══ Export — JSON ══

    def export_json(self, filepath: str) -> None:
        """Export summary to JSON. Uses temp file + atomic rename."""
        data = {
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "dataset_hash": self.dataset_hash,
            "code_commit": self.code_commit,
            "requested_engine": self.requested_engine,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": self.summary.to_dict() if self.summary else {},
            "engine_stats": self.engine_stats.to_dict(),
            "latency_breakdown": self.latency_breakdown.to_dict(),
            "failed_case_count": self.failed_case_count,
            "failure_event_count": self.failure_event_count,
        }
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        Path(tmp).replace(filepath)

    # ══ Export — Markdown ══

    def export_markdown(self, filepath: str) -> None:
        """Export full report to Markdown."""
        m = self.summary
        if m is None:
            raise ValueError("Cannot export Markdown: summary is None")

        lines = [
            "# Intent Understanding — Evaluation Report (v3.0)",
            "",
            f"**Run ID**: `{self.run_id}`",
            f"**Dataset**: {self.dataset_name}",
            f"**Dataset Hash**: `{self.dataset_hash}`",
            f"**Engine**: {self.requested_engine}",
            f"**Model**: {self.model_name or 'N/A'}",
            f"**Started**: {self.started_at}",
            f"**Completed**: {self.completed_at}",
            f"**Total**: {m.total} | **Passed**: {m.passed} | **Failed**: {m.failed}",
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
            "## Dimension Accuracy",
            "",
            "| Dimension | Status | Accuracy | Applicable | Correct | Critical | High |",
            "|-----------|--------|----------|------------|---------|----------|------|",
        ])
        dim_labels = {
            "action_recognition": "Action Recognition",
            "role_extraction": "Role Extraction",
            "entity_grounding": "Entity Grounding",
            "multi_object_disambiguation": "Multi-Object Disambiguation",
            "negation_constraint_retention": "Negation Constraint Retention",
            "conditional_sequential_understanding": "Conditional/Sequential",
            "numeric_operator_unit": "Numeric/Operator/Unit",
            "perception_factual_fidelity": "Perception Factual Fidelity",
            "robot_capability_constraint": "Robot Capability Constraint",
            "bt_ir_cross_field_consistency": "BT/IR Cross-Field Consistency",
            "schema_validity": "Schema Validity",
            "dangerous_error_pass_through": "Dangerous Error Pass-Through",
        }
        for key, d in m.dimensions.items():
            label = dim_labels.get(key, key)
            if d.applicable > 0:
                acc_str = f"{d.accuracy:.1%}"
                status = "EVALUATED"
            else:
                acc_str = "N/A"
                status = "NOT_APPLICABLE"
            lines.append(
                f"| {label} | {status} | {acc_str} | {d.applicable} | "
                f"{d.correct} | {d.critical_errors} | {d.high_errors} |"
            )

        lines.extend([
            "",
            "## Latency",
            "",
            f"| Avg | P50 | P95 | P99 |",
            f"|-----|-----|-----|-----|",
            f"| {m.latency_avg_ms:.1f}ms | {m.latency_p50_ms:.1f}ms | "
            f"{m.latency_p95_ms:.1f}ms | {m.latency_p99_ms:.1f}ms |",
            "",
            "## By Category",
            "",
            "| Category | Total | Passed | Critical | High |",
            "|----------|-------|--------|----------|------|",
        ])
        for cat, counts in sorted(m.by_category.items()):
            lines.append(
                f"| {cat} | {counts['total']} | {counts['passed']} | "
                f"{counts['critical']} | {counts['high']} |"
            )

        lines.extend([
            "",
            "## Engine Stats",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Requested Engine | {self.engine_stats.requested_engine} |",
            f"| DeepSeek Calls Attempted | {self.engine_stats.deepseek_call_attempted} |",
            f"| DeepSeek Calls Succeeded | {self.engine_stats.deepseek_call_succeeded} |",
            f"| Schema Failed | {self.engine_stats.deepseek_schema_failed} |",
            f"| Fallback Count | {self.engine_stats.fallback_count} |",
            f"| Rule Engine Direct | {self.engine_stats.rule_engine_direct_count} |",
            f"| Validator Executed | {self.engine_stats.validator_executed_count} |",
            "",
            "## Failed Cases",
            "",
        ])
        for v in self.failure_cases:
            icon = "❌" if v.has_critical else "⚠️"
            lines.append(f"### {icon} {v.case_id} [{v.category}]: {v.instruction[:60]}")
            if v.exception:
                lines.append(f"- **Exception**: {v.exception[:200]}")
            lines.append(f"- Action: expected={v.action_expected}, actual={v.action_actual}")
            lines.append(f"- Entity: expected={v.theme_entity_expected}, actual={v.theme_entity_actual}")
            lines.append(f"- Execution: expected={v.execution_allowed_expected}, actual={v.execution_allowed_actual}")
            for f in v.findings:
                lines.append(
                    f"- [{f.severity.value}] **{f.metric}**: {f.detail} "
                    f"(expected={f.expected}, actual={f.actual})"
                )
            lines.append("")

        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        Path(tmp).replace(filepath)

    # ══ Export — CSV ══

    def export_csv(self, filepath: str) -> None:
        """Export failure cases to CSV. One row per failed case."""
        failures = self.failure_cases
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "run_id", "case_id", "category", "instruction",
                "requested_engine", "actual_engine",
                "passed", "expected_action", "actual_action",
                "expected_theme_entity_id", "actual_theme_entity_id",
                "expected_plan_status", "actual_plan_status",
                "expected_execution_allowed", "actual_execution_allowed",
                "failed_dimensions", "highest_severity",
                "failure_messages", "elapsed_ms",
            ])
            for v in failures:
                dims = ",".join(f.metric for f in v.findings)
                highest = max(
                    (f.severity.value for f in v.findings),
                    key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0),
                    default="UNKNOWN"
                )
                messages = "; ".join(f.detail[:120] for f in v.findings)
                writer.writerow([
                    self.run_id, v.case_id, v.category, v.instruction[:100],
                    self.requested_engine, self.engine_stats.requested_engine,
                    v.passed,
                    v.action_expected, v.action_actual,
                    v.theme_entity_expected or "", v.theme_entity_actual or "",
                    "", "",  # plan_status fields
                    str(v.execution_allowed_expected), str(v.execution_allowed_actual),
                    dims, highest, messages,
                    f"{v.elapsed_ms:.1f}",
                ])
        Path(tmp).replace(filepath)

    # ══ Export — Cases JSONL ══

    def export_cases_jsonl(self, filepath: str) -> None:
        """Export all case results as JSONL."""
        tmp = filepath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for v in self.case_results:
                entry = {
                    "run_id": self.run_id,
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
                    "elapsed_ms": v.elapsed_ms,
                    "findings": [
                        {"metric": fi.metric, "severity": fi.severity.value,
                         "expected": str(fi.expected)[:200], "actual": str(fi.actual)[:200],
                         "detail": fi.detail}
                        for fi in v.findings
                    ],
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        Path(tmp).replace(filepath)

    # ══ Export all ══

    def export_all(self, output_dir: str) -> str:
        """Export all formats to output_dir/<run_id>/.

        Returns the output directory path.
        """
        out = Path(output_dir) / self.run_id
        out.mkdir(parents=True, exist_ok=True)

        # Run consistency check before export
        errors = self.check_consistency()
        if errors:
            raise RuntimeError(
                f"Consistency check FAILED for run {self.run_id}:\n" +
                "\n".join(f"  - {e}" for e in errors)
            )

        self.export_json(str(out / "results.json"))
        self.export_markdown(str(out / "report.md"))
        self.export_csv(str(out / "failures.csv"))
        self.export_cases_jsonl(str(out / "cases.jsonl"))

        # Write metadata
        meta = {
            "run_id": self.run_id,
            "dataset_name": self.dataset_name,
            "dataset_hash": self.dataset_hash,
            "requested_engine": self.requested_engine,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
        with open(str(out / "metadata.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        return str(out)
