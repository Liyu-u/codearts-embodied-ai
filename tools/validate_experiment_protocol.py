"""Validate the repository's unified experiment protocol and its datasets.

This is a preflight check, not a benchmark runner.  It verifies that the
protocol, the comparison manifest, the full regression manifest, and the LLM
holdout manifest agree on their basic shape before any experiment is run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "testdata" / "benchmark" / "experiment_protocol_v1.json"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_manifest(
    protocol: dict[str, Any],
    dataset_id: str,
    errors: list[str],
) -> dict[str, Any]:
    spec = protocol["datasets"][dataset_id]
    path = ROOT / spec["manifest"]
    if not path.is_file():
        errors.append(f"{dataset_id}: manifest not found: {spec['manifest']}")
        return {"manifest": spec["manifest"], "exists": False}

    try:
        document = _read_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"{dataset_id}: invalid JSON: {exc}")
        return {"manifest": spec["manifest"], "exists": True, "valid_json": False}

    cases = document.get("cases")
    if not isinstance(cases, list):
        errors.append(f"{dataset_id}: cases must be an array")
        cases = []

    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(set(ids)):
        errors.append(f"{dataset_id}: case IDs are duplicated")

    required_case_fields = spec.get(
        "required_case_fields",
        ("id", "category", "source", "expected_status"),
    )
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"{dataset_id}: case[{index}] must be an object")
            continue
        for field in required_case_fields:
            if not case.get(field):
                errors.append(f"{dataset_id}: case[{index}] missing {field}")
        if "expected_world_state" in required_case_fields and not isinstance(
            case.get("expected_world_state"), dict
        ):
            errors.append(
                f"{dataset_id}: case[{index}] expected_world_state must be an object"
            )
        world_state = case.get("expected_world_state")
        if isinstance(world_state, dict):
            world_type = world_state.get("type")
            if world_type not in {
                "placed",
                "stacked",
                "held",
                "not_executed",
                "safe_stop",
                "execution_failed",
            }:
                errors.append(
                    f"{dataset_id}: case[{index}] has unsupported world state type: {world_type}"
                )
            if world_type in {"placed", "stacked"} and not all(
                world_state.get(field)
                for field in ("object_id", "destination_id")
            ):
                errors.append(
                    f"{dataset_id}: case[{index}] placed/stacked state needs object_id and destination_id"
                )
            if world_type == "held" and not world_state.get("object_id"):
                errors.append(
                    f"{dataset_id}: case[{index}] held state needs object_id"
                )
            expected_status = case.get("expected_status")
            allowed_by_status = {
                "SUCCEEDED": {"placed", "stacked", "held"},
                "BLOCKED": {"not_executed"},
                "NEEDS_CLARIFICATION": {"not_executed"},
                "SAFE_STOP": {"safe_stop"},
                "FAILED": {"execution_failed"},
            }
            allowed_types = allowed_by_status.get(expected_status)
            if allowed_types is not None and world_type not in allowed_types:
                errors.append(
                    f"{dataset_id}: case[{index}] world state {world_type} "
                    f"does not match expected_status {expected_status}"
                )

    categories: dict[str, int] = {}
    for case in cases:
        if isinstance(case, dict):
            category = str(case.get("category", ""))
            categories[category] = categories.get(category, 0) + 1

    expected_count = spec.get("case_count")
    if isinstance(expected_count, int) and len(cases) != expected_count:
        errors.append(
            f"{dataset_id}: expected {expected_count} cases, got {len(cases)}"
        )

    expected_categories = spec.get("categories")
    if isinstance(expected_categories, dict) and categories != expected_categories:
        errors.append(
            f"{dataset_id}: category counts differ; expected={expected_categories}, "
            f"actual={categories}"
        )

    return {
        "manifest": spec["manifest"],
        "exists": True,
        "valid_json": True,
        "schema_version": document.get("schema_version"),
        "case_count": len(cases),
        "categories": categories,
        "sha256": _sha256(path),
    }


def validate(protocol_path: Path) -> dict[str, Any]:
    errors: list[str] = []
    protocol: dict[str, Any]
    try:
        protocol = _read_json(protocol_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            "schema_version": "experiment-preflight-report.v1",
            "status": "FAIL",
            "errors": [f"protocol is not valid JSON: {exc}"],
        }

    if protocol.get("schema_version") != "experiment-protocol.v1":
        errors.append("protocol schema_version must be experiment-protocol.v1")
    if protocol.get("status") != "established":
        errors.append("protocol status must be established")

    run_config_report: dict[str, Any] = {}
    run_config_value = protocol.get("run_config")
    if run_config_value:
        run_config_path = ROOT / str(run_config_value)
        if not run_config_path.is_file():
            errors.append(f"run_config not found: {run_config_value}")
        else:
            try:
                run_config = _read_json(run_config_path)
                if run_config.get("schema_version") != "experiment-run-config.v1":
                    errors.append("run_config schema_version must be experiment-run-config.v1")
                if run_config.get("protocol_version") != protocol.get("version"):
                    errors.append("run_config protocol_version differs from protocol")
                dataset = run_config.get("dataset") or {}
                comparison = (protocol.get("datasets") or {}).get("comparison") or {}
                if dataset.get("manifest") != comparison.get("manifest"):
                    errors.append("run_config dataset manifest differs from comparison dataset")
                if dataset.get("case_count") != comparison.get("case_count"):
                    errors.append("run_config case_count differs from comparison dataset")
                run_config_report = {
                    "path": str(run_config_path.resolve()),
                    "sha256": _sha256(run_config_path),
                    "schema_version": run_config.get("schema_version"),
                    "experiment_id": run_config.get("experiment_id"),
                    "variants": [
                        item.get("id")
                        for item in run_config.get("variants", [])
                        if isinstance(item, dict)
                    ],
                }
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"run_config invalid: {exc}")

    datasets = protocol.get("datasets")
    if not isinstance(datasets, dict):
        errors.append("protocol.datasets must be an object")
        datasets = {}

    dataset_reports: dict[str, Any] = {}
    for dataset_id in ("comparison", "full_regression", "llm_holdout"):
        if dataset_id not in datasets:
            errors.append(f"missing dataset definition: {dataset_id}")
            continue
        dataset_reports[dataset_id] = _validate_manifest(
            protocol,
            dataset_id,
            errors,
        )

    variants = protocol.get("variants")
    if not isinstance(variants, list) or not variants:
        errors.append("protocol.variants must be a non-empty array")
    else:
        variant_ids = [item.get("id") for item in variants if isinstance(item, dict)]
        if len(variant_ids) != len(set(variant_ids)):
            errors.append("variant IDs are duplicated")

    metrics = protocol.get("metrics", {}).get("primary")
    if not isinstance(metrics, list) or not metrics:
        errors.append("protocol.metrics.primary must be a non-empty array")

    return {
        "schema_version": "experiment-preflight-report.v1",
        "protocol_id": protocol.get("protocol_id"),
        "protocol_version": protocol.get("version"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "protocol": str(protocol_path.resolve()),
        "protocol_sha256": _sha256(protocol_path) if protocol_path.is_file() else None,
        "run_config": run_config_report,
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "datasets": dataset_reports,
        "variants": [
            item.get("id")
            for item in variants or []
            if isinstance(item, dict)
        ],
        "primary_metric_count": len(metrics or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    report = validate(args.protocol)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
