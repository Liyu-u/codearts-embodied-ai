"""Resumable 20-case x 4-variant x 3-repeat live acceptance matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.live_intelligent_e2e import build_normal_schedule, write_summary_report


def _read_cases(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8"))
    cases = list(document.get("tasks") or [])
    cases = [case for case in cases if str(case.get("category")) not in {"safety_speed", "safety_force", "safety_collision", "safety_timeout", "safety_estop"}]
    return cases[:20]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="testdata/benchmark/real_isaac_supplement_v2.json")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260831)
    parser.add_argument("--ssh-key", required=True)
    parser.add_argument("--gpu-index", choices=("0", "1"), default="0")
    parser.add_argument("--output", default="reports/live_intelligent_e2e_matrix_20260831.jsonl")
    parser.add_argument("--summary", default="reports/live_intelligent_e2e_summary_20260831.json")
    parser.add_argument("--run-prefix", default="live-matrix", help="独立证据目录前缀，禁止覆盖历史运行")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    cases = _read_cases(ROOT / args.manifest)
    schedule = build_normal_schedule(cases, repeats=args.repeats, seed=args.seed)
    end = len(schedule) if args.limit is None else min(len(schedule), args.start + args.limit)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    if output.is_file() and args.start > 0:
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    for index in range(args.start, end):
        item = schedule[index]
        run_id = f"{args.run_prefix}-{index+1:03d}-{item['variant_id']}-{item['case_id']}-r{item['repeat']}"
        command = [
            sys.executable, str(ROOT / "tools" / "run_live_intelligent_bridge.py"),
            "--task-config", args.manifest, "--case-id", item["case_id"], "--variant", item["variant_id"],
            "--run-id", run_id, "--seed", str(item["seed"]), "--ssh-key", args.ssh_key, "--gpu-index", args.gpu_index,
        ]
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        evidence_dir = ROOT / "reports" / run_id
        audit = {}
        status = "UNKNOWN"
        execution = evidence_dir / "execution.json"
        if (evidence_dir / "audit.json").is_file():
            audit = json.loads((evidence_dir / "audit.json").read_text(encoding="utf-8"))
        if execution.is_file():
            status = json.loads(execution.read_text(encoding="utf-8")).get("status", "UNKNOWN")
        task_file = evidence_dir / "task.json"
        task_status = None
        if task_file.is_file():
            task_status = json.loads(task_file.read_text(encoding="utf-8")).get("status")
            if status == "UNKNOWN" and task_status:
                status = str(task_status)
        record = {"index": index, "run_id": run_id, "case_id": item["case_id"], "variant_id": item["variant_id"], "repeat": item["repeat"], "seed": item["seed"], "status": status, "exit_code": completed.returncode, "task_status": task_status, "error": (completed.stderr or "")[-2000:], "audit": audit, "evidence_dir": str(evidence_dir)}
        records.append(record)
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
        print(json.dumps(record, ensure_ascii=False), flush=True)
    write_summary_report(ROOT / args.summary, records, required_runs=len(schedule))
    return 0 if len(records) >= len(schedule) and all(row.get("audit", {}).get("eligible") for row in records) else 2


if __name__ == "__main__":
    raise SystemExit(main())
