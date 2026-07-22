#!/usr/bin/env python3
"""
Review Submission Validator — Golden Data Review
=================================================
Validates human-filled review CSV submissions for:
  - Object ID existence in scene
  - Required field presence
  - Enum value correctness
  - Role conflict detection
  - Reviewer A vs B comparison → adjudication CSV

Usage:
  # Validate a single entity review submission
  python validate_review_submission.py --input holdout_v3_review_a.csv --type entity

  # Validate a TC review submission
  python validate_review_submission.py --input tc_reviewer_a_tc_cases.csv --type tc

  # Compare Reviewer A and B → generate adjudication CSV
  python validate_review_submission.py --compare review_a.csv review_b.csv --output adjudication.csv

ABSOLUTE RULE:
  This tool reports ERRORS and DIFFS. It NEVER auto-corrects or decides who is right.
  All decisions must be made by a human adjudicator.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ══════════════════════════════════════════════════════════════════════════════
# Data Loading
# ══════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent
HOLDOUT_V3_PATH = SCRIPT_DIR / "holdout_v3.json"

# Cache of valid object IDs per case
_valid_ids_cache: Optional[Dict[str, Set[str]]] = None


def get_valid_ids() -> Dict[str, Set[str]]:
    """Load valid object_ids per case_id from holdout_v3.json."""
    global _valid_ids_cache
    if _valid_ids_cache is not None:
        return _valid_ids_cache

    if not HOLDOUT_V3_PATH.exists():
        print(f"WARNING: holdout_v3.json not found at {HOLDOUT_V3_PATH}")
        _valid_ids_cache = {}
        return _valid_ids_cache

    with open(HOLDOUT_V3_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    _valid_ids_cache = {}
    for case in data.get("cases", []):
        cid = case["case_id"]
        ids = {o["object_id"] for o in case.get("objects", [])}
        _valid_ids_cache[cid] = ids

    return _valid_ids_cache


# ══════════════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════════════

VALID_GROUNDING_STATUSES = {"UNIQUE", "AMBIGUOUS", "NOT_FOUND", "NOT_APPLICABLE", ""}
VALID_TC_DECISIONS = {"KEEP_OLD", "ACCEPT_PROPOSED", "ALTERNATIVE", "NEEDS_SCHEMA_CHANGE", ""}
VALID_BOOLS = {"TRUE", "FALSE", "true", "false", ""}

ENTITY_ROLE_COLUMNS = [
    ("theme_entity_ids", "theme_grounding_status"),
    ("destination_entity_ids", "destination_grounding_status"),
    ("recipient_entity_ids", "recipient_grounding_status"),
    ("prohibition_entity_ids", "prohibition_grounding_status"),
    ("condition_subject_entity_ids", "condition_subject_grounding_status"),
]

ENTITY_REQUIRED_FIELDS = [
    "theme_grounding_status",
    "destination_grounding_status",
    "recipient_grounding_status",
    "prohibition_grounding_status",
    "condition_subject_grounding_status",
    "rationale",
    "reviewer_id",
]

TC_REQUIRED_FIELDS = [
    "reviewer_decision",
    "expected_execution_allowed",
    "rationale",
    "reviewer_id",
]


def validate_entity_row(row: Dict[str, str], valid_ids: Dict[str, Set[str]]) -> List[str]:
    """Validate a single entity review row. Returns list of error messages."""
    errors = []
    cid = row.get("case_id", "UNKNOWN")
    case_ids = valid_ids.get(cid, set())

    # ── Required fields ──
    for field in ENTITY_REQUIRED_FIELDS:
        if field in row and not row[field].strip():
            errors.append(f"[{cid}] Required field '{field}' is empty")

    # ── Grounding status enum check ──
    for _, status_col in ENTITY_ROLE_COLUMNS:
        if status_col in row:
            val = row[status_col].strip().upper()
            if val and val not in VALID_GROUNDING_STATUSES:
                errors.append(
                    f"[{cid}] Invalid grounding_status '{row[status_col]}' in '{status_col}'. "
                    f"Must be one of: {', '.join(sorted(VALID_GROUNDING_STATUSES - {''}))}"
                )

    # ── Object ID existence check ──
    for ids_col, status_col in ENTITY_ROLE_COLUMNS:
        if ids_col not in row or status_col not in row:
            continue
        ids_str = row[ids_col].strip()
        status = row[status_col].strip().upper()

        if ids_str:
            # Has entity IDs → status must be UNIQUE or AMBIGUOUS
            if status and status not in ("UNIQUE", "AMBIGUOUS"):
                errors.append(
                    f"[{cid}] {ids_col} has IDs '{ids_str}' but {status_col}='{status}'. "
                    f"When IDs are provided, status must be UNIQUE or AMBIGUOUS"
                )

            # Check each ID exists
            for oid in ids_str.split(","):
                oid = oid.strip()
                if oid and oid not in case_ids:
                    errors.append(
                        f"[{cid}] Object ID '{oid}' in '{ids_col}' does NOT exist in scene. "
                        f"Available: {', '.join(sorted(case_ids)) if case_ids else '(none)'}"
                    )
        else:
            # No entity IDs → status should be NOT_FOUND, NOT_APPLICABLE, or empty
            if status == "UNIQUE":
                errors.append(
                    f"[{cid}] {ids_col} is empty but {status_col}='UNIQUE'. "
                    f"If unique, you must provide the entity ID"
                )

    # ── Cross-role conflict: same object_id cannot be both theme and prohibition ──
    role_ids: Dict[str, Set[str]] = {}
    for ids_col, _ in ENTITY_ROLE_COLUMNS:
        if ids_col in row and row[ids_col].strip():
            role_ids[ids_col] = {x.strip() for x in row[ids_col].split(",") if x.strip()}

    # Theme ∩ Prohibition conflict
    theme_ids = role_ids.get("theme_entity_ids", set())
    prohibition_ids = role_ids.get("prohibition_entity_ids", set())
    conflict = theme_ids & prohibition_ids
    if conflict:
        errors.append(
            f"[{cid}] ROLE CONFLICT: Object(s) {', '.join(sorted(conflict))} "
            f"assigned as BOTH theme AND prohibition"
        )

    # Theme ∩ Destination (may be intentional but flag it)
    dest_ids = role_ids.get("destination_entity_ids", set())
    theme_dest_conflict = theme_ids & dest_ids
    if theme_dest_conflict:
        errors.append(
            f"[{cid}] NOTE: Object(s) {', '.join(sorted(theme_dest_conflict))} "
            f"assigned as BOTH theme AND destination — verify this is intentional"
        )

    return errors


def validate_tc_row(row: Dict[str, str]) -> List[str]:
    """Validate a single TC review row. Returns list of error messages."""
    errors = []
    cid = row.get("case_id", "UNKNOWN")

    for field in TC_REQUIRED_FIELDS:
        if field in row and not row[field].strip():
            errors.append(f"[{cid}] Required field '{field}' is empty")

    decision = row.get("reviewer_decision", "").strip().upper()
    if decision and decision not in VALID_TC_DECISIONS:
        errors.append(
            f"[{cid}] Invalid reviewer_decision '{row['reviewer_decision']}'. "
            f"Must be one of: {', '.join(sorted(VALID_TC_DECISIONS - {''}))}"
        )

    exec_allowed = row.get("expected_execution_allowed", "").strip().upper()
    if exec_allowed and exec_allowed not in VALID_BOOLS:
        errors.append(
            f"[{cid}] Invalid expected_execution_allowed '{row['expected_execution_allowed']}'. "
            f"Must be TRUE or FALSE"
        )

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# Comparison (Reviewer A vs B)
# ══════════════════════════════════════════════════════════════════════════════

def compare_reviews(path_a: str, path_b: str, output_path: str) -> None:
    """Compare two review CSVs and write disagreements to adjudication CSV."""

    def load_csv(path: str) -> Dict[str, Dict[str, str]]:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            return {row["case_id"]: row for row in reader}

    data_a = load_csv(path_a)
    data_b = load_csv(path_b)

    # Determine type by checking column presence
    sample_row = next(iter(data_a.values()), {})
    is_entity = "theme_entity_ids" in sample_row
    is_tc = "reviewer_decision" in sample_row and not is_entity

    # Fields to compare
    if is_entity:
        compare_fields = [
            "theme_entity_ids", "theme_grounding_status",
            "destination_entity_ids", "destination_grounding_status",
            "recipient_entity_ids", "recipient_grounding_status",
            "prohibition_entity_ids", "prohibition_grounding_status",
            "condition_subject_entity_ids", "condition_subject_grounding_status",
            "clarification_required", "expected_grounding_status_overall",
        ]
    elif is_tc:
        compare_fields = [
            "reviewer_decision", "expected_action", "expected_plan_status",
            "expected_execution_allowed", "blocking_reason",
            "clarification_required", "schema_change_needed",
        ]
    else:
        print("ERROR: Could not determine review type from CSV columns.")
        sys.exit(1)

    all_case_ids = sorted(set(data_a.keys()) | set(data_b.keys()))

    disagreements = []
    for cid in all_case_ids:
        row_a = data_a.get(cid, {})
        row_b = data_b.get(cid, {})

        if not row_a:
            disagreements.append({
                "case_id": cid, "field_name": "ENTIRE_ROW",
                "reviewer_a_value": "MISSING", "reviewer_b_value": "PRESENT",
                "conflict_type": "MISSING_IN_A",
                "adjudicator_decision": "", "adjudicator_rationale": "",
                "adjudicator_id": "", "signed_at": "",
            })
            continue
        if not row_b:
            disagreements.append({
                "case_id": cid, "field_name": "ENTIRE_ROW",
                "reviewer_a_value": "PRESENT", "reviewer_b_value": "MISSING",
                "conflict_type": "MISSING_IN_B",
                "adjudicator_decision": "", "adjudicator_rationale": "",
                "adjudicator_id": "", "signed_at": "",
            })
            continue

        for field in compare_fields:
            val_a = row_a.get(field, "").strip()
            val_b = row_b.get(field, "").strip()

            # Normalize for comparison
            norm_a = _normalize(val_a)
            norm_b = _normalize(val_b)

            if norm_a != norm_b:
                # Both empty → skip (not a real disagreement)
                if norm_a == "" and norm_b == "":
                    continue
                # One empty, one not
                if norm_a == "":
                    ctype = "A_EMPTY_B_FILLED"
                elif norm_b == "":
                    ctype = "B_EMPTY_A_FILLED"
                else:
                    ctype = "VALUE_MISMATCH"

                disagreements.append({
                    "case_id": cid,
                    "field_name": field,
                    "reviewer_a_value": val_a,
                    "reviewer_b_value": val_b,
                    "conflict_type": ctype,
                    "adjudicator_decision": "",
                    "adjudicator_rationale": "",
                    "adjudicator_id": "",
                    "signed_at": "",
                })

    # Write adjudication CSV
    adj_fieldnames = [
        "case_id", "field_name", "reviewer_a_value", "reviewer_b_value",
        "conflict_type", "adjudicator_decision", "adjudicator_rationale",
        "adjudicator_id", "signed_at",
    ]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=adj_fieldnames)
        writer.writeheader()
        writer.writerows(disagreements)

    print(f"\n══ Review Comparison Complete ══")
    print(f"  File A: {path_a}")
    print(f"  File B: {path_b}")
    print(f"  Cases compared: {len(all_case_ids)}")
    print(f"  Disagreements found: {len(disagreements)}")
    print(f"  Adjudication CSV: {out_path}")
    print()
    if disagreements:
        # Summarize by conflict type
        by_type = defaultdict(int)
        for d in disagreements:
            by_type[d["conflict_type"]] += 1
        print("  Conflict breakdown:")
        for ctype, count in sorted(by_type.items()):
            print(f"    {ctype}: {count}")
    print()
    print("  ⚠ All adjudicator_decision fields are EMPTY.")
    print("  ⚠ A HUMAN adjudicator must review each disagreement and fill the decisions.")
    print("  ⚠ This tool does NOT decide who is correct.")


def _normalize(val: str) -> str:
    """Normalize a value for comparison (case-insensitive, whitespace-normalized)."""
    return " ".join(val.upper().split())


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Review Submission Validator — Golden Data Review"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=str, help="Single review CSV to validate")
    group.add_argument("--compare", nargs=2, metavar=("FILE_A", "FILE_B"),
                       help="Compare two review CSVs")
    parser.add_argument("--type", choices=["entity", "tc"], help="Review type (required with --input)")
    parser.add_argument("--output", type=str, default="adjudication_output.csv",
                        help="Output path for adjudication CSV (with --compare)")

    args = parser.parse_args()

    if args.compare:
        compare_reviews(args.compare[0], args.compare[1], args.output)
        return

    if not args.type:
        print("ERROR: --type is required with --input")
        sys.exit(1)

    # ── Single file validation ──
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total_errors = 0
    valid_ids = get_valid_ids() if args.type == "entity" else {}

    for i, row in enumerate(rows, 2):  # 1-indexed, line 1 is header
        if args.type == "entity":
            errors = validate_entity_row(row, valid_ids)
        else:
            errors = validate_tc_row(row)

        if errors:
            total_errors += len(errors)
            for err in errors:
                print(f"  Line {i}: {err}")

    print()
    print(f"══ Validation Summary ══")
    print(f"  File: {input_path}")
    print(f"  Rows checked: {len(rows)}")
    print(f"  Errors found: {total_errors}")
    print()

    if total_errors == 0:
        print("  ✓ All checks passed. Ready for submission.")
    else:
        print("  ⚠ Errors found. Please fix before submitting.")

    sys.exit(0 if total_errors == 0 else 1)


if __name__ == "__main__":
    main()
