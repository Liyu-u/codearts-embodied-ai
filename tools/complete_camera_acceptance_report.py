"""Complete D feedback and status for an already collected camera C run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools import run_real_acceptance_batch as base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args()
    report_dir = args.report_dir
    ab = json.loads((report_dir / "ab.json").read_text(encoding="utf-8"))
    perception = json.loads((report_dir / "perception.json").read_text(encoding="utf-8"))
    execution = json.loads((report_dir / "execution.json").read_text(encoding="utf-8"))
    case = {
        "id": ab["case_id"],
        "category": ab["category"],
        "expected_status": ab["expected_status"],
        "seed": ab.get("seed"),
    }
    feedback = base._feedback(ab, perception, execution, report_dir)
    result = base._make_status(ab, case, execution, feedback, "manual-camera-isaac", report_dir)
    print(json.dumps({"status": result.get("status"), "failure_class": result.get("failure_class"), "feedback_final_passed": feedback.get("final_passed")}, ensure_ascii=False))
    return 0 if result.get("status") == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
