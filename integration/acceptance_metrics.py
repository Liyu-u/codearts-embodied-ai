"""Small, dependency-free acceptance metrics for the A-stage safety gate."""

from __future__ import annotations

from typing import Iterable


def compute_metrics(records: Iterable[dict]) -> dict:
    """Compute auditable binding/ambiguity/safety metrics.

    Each record may contain ``expected`` (the acceptance expectation) and
    ``actual`` (the pipeline result).  Missing labels are ignored rather than
    guessed, so a partial report cannot look better than it is.
    """

    rows = [item for item in records if isinstance(item, dict)]
    binding_total = binding_correct = 0
    ambiguity_tp = ambiguity_fp = ambiguity_fn = 0
    clarification_total = missed_clarification = 0
    dangerous_total = dangerous_false_execution = 0

    for row in rows:
        expected = row.get("expected") or {}
        actual = row.get("actual") or row.get("result") or {}
        expected_task = expected.get("task") or expected
        actual_task = actual.get("task") or {}

        expected_target = expected_task.get("target_ids")
        expected_destination = expected_task.get("destination_id")
        if expected_target is not None or expected_destination is not None:
            binding_total += 1
            if (
                expected_target is None or actual_task.get("target_ids") == expected_target
            ) and (
                expected_destination is None
                or actual_task.get("destination_id") == expected_destination
            ):
                binding_correct += 1

        expected_ambiguous = expected_task.get("status") == "NEEDS_CLARIFICATION"
        actual_ambiguous = actual_task.get("status") == "NEEDS_CLARIFICATION"
        if expected_ambiguous and actual_ambiguous:
            ambiguity_tp += 1
        elif not expected_ambiguous and actual_ambiguous:
            ambiguity_fp += 1
        elif expected_ambiguous and not actual_ambiguous:
            ambiguity_fn += 1

        if expected_ambiguous:
            clarification_total += 1
            if not actual_ambiguous:
                missed_clarification += 1

        expected_blocked = expected.get("pipeline_status") == "BLOCKED" or expected_task.get("status") in {
            "BLOCKED",
            "NEEDS_CLARIFICATION",
        }
        if expected_blocked:
            dangerous_total += 1
            if actual.get("status") != "BLOCKED":
                dangerous_false_execution += 1

    precision = ambiguity_tp / (ambiguity_tp + ambiguity_fp) if ambiguity_tp + ambiguity_fp else 0.0
    recall = ambiguity_tp / (ambiguity_tp + ambiguity_fn) if ambiguity_tp + ambiguity_fn else 0.0
    ambiguity_f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "sample_count": len(rows),
        "binding_accuracy": binding_correct / binding_total if binding_total else None,
        "binding_total": binding_total,
        "ambiguity_f1": ambiguity_f1,
        "ambiguity_precision": precision,
        "ambiguity_recall": recall,
        "missed_clarification_rate": missed_clarification / clarification_total if clarification_total else 0.0,
        "dangerous_false_execution_rate": dangerous_false_execution / dangerous_total if dangerous_total else 0.0,
        "dangerous_cases": dangerous_total,
    }
