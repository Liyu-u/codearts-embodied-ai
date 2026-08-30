"""Compare experiment reports produced by ``run_closed_loop_benchmark.py``.

The tool intentionally refuses to calculate a headline improvement when the
reports do not use the same protocol, manifest, backend, or case IDs.  This
keeps a PPT number from being assembled from incomparable runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTOCOL = ROOT / "testdata" / "benchmark" / "experiment_protocol_v1.json"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须是 JSON 对象")
    return value


def _report_items(path: Path) -> list[dict[str, Any]]:
    document = _read_json(path)
    nested = document.get("reports")
    if isinstance(nested, list):
        items = [item for item in nested if isinstance(item, dict)]
    else:
        items = [document]
    if not items:
        raise ValueError(f"{path} 没有可比较的 report")
    for item in items:
        item["_source_path"] = str(path.resolve())
    return items


def _variant_id(report: dict[str, Any]) -> str:
    return str(report.get("variant_id") or report.get("mode") or "unknown")


def _manifest(report: dict[str, Any]) -> str | None:
    metadata = report.get("metadata") or {}
    value = metadata.get("manifest_path") or report.get("manifest")
    if value:
        return str(Path(str(value)).resolve())
    records = report.get("records") or []
    if records and isinstance(records[0], dict) and records[0].get("manifest"):
        return str(Path(str(records[0]["manifest"])).resolve())
    return None


def _case_ids(report: dict[str, Any]) -> list[str]:
    return sorted(
        str(item.get("case_id"))
        for item in report.get("records", [])
        if isinstance(item, dict) and item.get("case_id")
    )


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _delta(
    baseline: Any,
    value: Any,
    direction: str,
) -> tuple[float | None, float | None]:
    if not isinstance(baseline, (int, float)) or not isinstance(value, (int, float)):
        return None, None
    absolute = float(value) - float(baseline)
    if direction == "lower":
        improvement = -absolute
    else:
        improvement = absolute
    relative = improvement / float(baseline) if baseline else None
    return _round(absolute), _round(relative)


def compare(
    report_paths: list[Path],
    *,
    protocol_path: Path = DEFAULT_PROTOCOL,
    baseline_id: str = "V0_RULE_BASELINE",
    reference_id: str = "V4_FULL",
) -> dict[str, Any]:
    protocol = _read_json(protocol_path)
    metric_specs = protocol.get("metrics", {}).get("primary") or []
    reports: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in report_paths:
        try:
            reports.extend(_report_items(path))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))

    by_variant: dict[str, dict[str, Any]] = {}
    for report in reports:
        variant = _variant_id(report)
        if variant in by_variant:
            errors.append(f"variant 重复：{variant}")
        else:
            by_variant[variant] = report

    protocol_versions = {str(item.get("protocol_version")) for item in reports}
    if len(protocol_versions) > 1:
        errors.append(f"protocol_version 不一致：{sorted(protocol_versions)}")
    experiment_ids = {
        str(item.get("experiment_id"))
        for item in reports
        if item.get("experiment_id")
    }
    if len(experiment_ids) > 1:
        errors.append(f"experiment_id 不一致：{sorted(experiment_ids)}")
    manifests = {_manifest(item) for item in reports}
    if len(manifests) > 1:
        errors.append("manifest 不一致，不能做横向比较")
    backends = {str(item.get("backend")) for item in reports}
    if len(backends) > 1:
        errors.append(f"backend 不一致：{sorted(backends)}")
    case_sets = {tuple(_case_ids(item)) for item in reports}
    if len(case_sets) > 1:
        errors.append("case_id 集合不一致，不能做横向比较")

    required_fields = protocol.get("required_record_fields") or []
    missing_fields: dict[str, list[str]] = {}
    for variant, report in by_variant.items():
        missing = sorted(
            {
                field
                for record in report.get("records", [])
                if isinstance(record, dict)
                for field in required_fields
                if field not in record
            }
        )
        if missing:
            missing_fields[variant] = missing
    if missing_fields:
        errors.append(f"报告缺少协议字段：{missing_fields}")

    baseline_report = by_variant.get(baseline_id)
    if baseline_report is None:
        errors.append(f"找不到 baseline 报告：{baseline_id}")

    metric_rows: list[dict[str, Any]] = []
    baseline_summary = (baseline_report or {}).get("summary") or {}
    for spec in metric_specs:
        metric_id = str(spec.get("id"))
        direction = str(spec.get("direction", "higher"))
        baseline_value = baseline_summary.get(metric_id)
        row: dict[str, Any] = {
            "id": metric_id,
            "direction": direction,
            "baseline": baseline_value,
            "values": {},
            "absolute_delta_vs_baseline": {},
            "relative_improvement_vs_baseline": {},
        }
        for variant, report in by_variant.items():
            value = (report.get("summary") or {}).get(metric_id)
            absolute, relative = _delta(baseline_value, value, direction)
            row["values"][variant] = value
            row["absolute_delta_vs_baseline"][variant] = absolute
            row["relative_improvement_vs_baseline"][variant] = relative
        metric_rows.append(row)

    return {
        "schema_version": "experiment-comparison.v1",
        "protocol_id": protocol.get("protocol_id"),
        "protocol_version": next(iter(protocol_versions), None),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "baseline_id": baseline_id,
        "reference_id": reference_id,
        "report_count": len(by_variant),
        "variants": list(by_variant),
        "reports": [
            {
                "variant_id": variant,
                "source": report.get("_source_path"),
                "experiment_id": report.get("experiment_id"),
                "backend": report.get("backend"),
                "manifest": _manifest(report),
                "cases": len(report.get("records", [])),
                "summary": report.get("summary") or {},
            }
            for variant, report in by_variant.items()
        ],
        "metrics": metric_rows,
        "missing_fields": missing_fields,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path, help="单份报告或 suite report")
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--baseline", default="V0_RULE_BASELINE")
    parser.add_argument("--reference", default="V4_FULL")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    result = compare(
        args.reports,
        protocol_path=args.protocol,
        baseline_id=args.baseline,
        reference_id=args.reference,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
