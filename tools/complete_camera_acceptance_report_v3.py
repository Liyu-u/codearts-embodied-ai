"""Complete D feedback with an explicit camera-to-TraceCoder field adapter."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_real_acceptance_batch as batch


def main() -> int:
    report_dir = Path(sys.argv[sys.argv.index("--report-dir") + 1])
    ab = json.loads((report_dir / "ab.json").read_text(encoding="utf-8"))
    perception = json.loads((report_dir / "perception.json").read_text(encoding="utf-8"))
    execution = json.loads((report_dir / "execution.json").read_text(encoding="utf-8"))
    feedback_perception = copy.deepcopy(perception)
    conversions = []
    for item in feedback_perception.get("objects", []):
        if isinstance(item.get("orientation"), dict):
            item["orientation"] = 0.0
            conversions.append(item.get("id"))
    (report_dir / "feedback_input_normalization.json").write_text(
        json.dumps({"adapter": "tracecoder.orientation_dict_to_scalar", "objects": conversions}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    feedback = batch._feedback(ab, feedback_perception, execution, report_dir)
    case = {"id": ab["case_id"], "category": ab["category"], "expected_status": ab["expected_status"], "seed": ab.get("seed")}
    result = batch._make_status(ab, case, execution, feedback, "manual-camera-isaac", report_dir)
    print(json.dumps({"status": result.get("status"), "failure_class": result.get("failure_class"), "feedback_final_passed": feedback.get("final_passed"), "orientation_conversions": conversions}, ensure_ascii=False))
    return 0 if result.get("status") == "SUCCEEDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
