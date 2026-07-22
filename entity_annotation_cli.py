#!/usr/bin/env python3
"""
Interactive Entity Grounding Annotation CLI
============================================
Human-in-the-loop tool for manual entity grounding annotation
of the holdout_v3 dataset.

── ABSOLUTE PROHIBITION ──
This script is a DATA-ENTRY TOOL only. It does NOT generate,
guess, or auto-fill any golden-standard answer. All entity
grounding values MUST be entered by a human reviewer.

Usage:
    python entity_annotation_cli.py

Input:
    robot_intent_agent/eval/holdout_v3_annotation_draft.json

Output:
    holdout_v3_entity_annotation.csv          (root)
    DELIVERY_HUMAN_REVIEW.md                  (root)
    robot_intent_agent/eval/holdout_v3_entity_annotation_progress.json

Architecture:
    - Reads the annotation draft (150 cases with scene_objects)
    - Iterates interactively: clear screen → show case → wait for human input
    - Saves progress after EVERY case → Ctrl+C safe, resume on re-run
    - Exports CSV + SHA256 hash + review template on completion
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Colorama (cross-platform terminal colors) ──
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    HAS_COLOR = True
except ImportError:
    HAS_COLOR = False


# ── ANSI fallback (for environments without colorama) ──
class Ansi:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"


def c(text: str, color: str, bold: bool = False) -> str:
    """Wrap text in color codes. Uses colorama if available, else raw ANSI."""
    if HAS_COLOR:
        colorama_map = {
            "red": Fore.RED,
            "green": Fore.GREEN,
            "yellow": Fore.YELLOW,
            "blue": Fore.BLUE,
            "magenta": Fore.MAGENTA,
            "cyan": Fore.CYAN,
            "white": Fore.WHITE,
            "dim": Style.DIM,
            "reset": Style.RESET_ALL,
        }
        prefix = colorama_map.get(color, "")
        suffix = Style.RESET_ALL
        if bold:
            prefix = Style.BRIGHT + prefix
        return f"{prefix}{text}{suffix}"
    else:
        ansi_map = {
            "red": Ansi.RED,
            "green": Ansi.GREEN,
            "yellow": Ansi.YELLOW,
            "blue": Ansi.BLUE,
            "magenta": Ansi.MAGENTA,
            "cyan": Ansi.CYAN,
            "white": Ansi.WHITE,
            "dim": Ansi.DIM,
            "reset": Ansi.RESET,
        }
        prefix = ansi_map.get(color, "")
        if bold:
            prefix = Ansi.BOLD + prefix
        return f"{prefix}{text}{ansi_map['reset']}"


# ══════════════════════════════════════════════════════════════════════════════
# Path Configuration
# ══════════════════════════════════════════════════════════════════════════════

REPO_ROOT = Path(__file__).resolve().parent
INPUT_PATH = REPO_ROOT / "robot_intent_agent" / "eval" / "holdout_v3_annotation_draft.json"
PROGRESS_PATH = REPO_ROOT / "robot_intent_agent" / "eval" / "holdout_v3_entity_annotation_progress.json"
CSV_OUTPUT_PATH = REPO_ROOT / "holdout_v3_entity_annotation.csv"
REVIEW_MD_PATH = REPO_ROOT / "DELIVERY_HUMAN_REVIEW.md"


# ══════════════════════════════════════════════════════════════════════════════
# Data I/O
# ══════════════════════════════════════════════════════════════════════════════

def load_annotation_draft() -> Dict[str, Any]:
    """Load the holdout_v3 annotation draft JSON."""
    if not INPUT_PATH.exists():
        print(c(f"✗ Input file not found: {INPUT_PATH}", "red", bold=True))
        print(c(f"  Expected the annotation draft at: {INPUT_PATH}", "dim"))
        sys.exit(1)
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_progress() -> Dict[str, Dict[str, Any]]:
    """Load existing annotation progress. Returns empty dict if no progress file."""
    if PROGRESS_PATH.exists():
        with open(PROGRESS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(progress: Dict[str, Dict[str, Any]]) -> None:
    """Atomically save annotation progress to disk."""
    tmp_path = PROGRESS_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    tmp_path.replace(PROGRESS_PATH)


# ══════════════════════════════════════════════════════════════════════════════
# Terminal UI
# ══════════════════════════════════════════════════════════════════════════════

def clear_screen() -> None:
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def render_banner() -> None:
    """Render the top banner."""
    print(c("╔══════════════════════════════════════════════════════════════════╗", "cyan", bold=True))
    print(c("║       Entity Grounding Annotation — Holdout v3 (Human-Only)     ║", "cyan", bold=True))
    print(c("╚══════════════════════════════════════════════════════════════════╝", "cyan", bold=True))
    print()
    print(c("  ⚠ ABSOLUTE RULE: This tool does NOT generate answers.", "yellow", bold=True))
    print(c("    You (the human reviewer) must type every value yourself.", "yellow"))
    print()


def render_case_header(case: Dict[str, Any], index: int, total: int) -> None:
    """Render the case header with ID, category, and instruction."""
    pct = (index / total) * 100
    completed = sum(1 for v in load_progress().values() if v.get("reviewer_status") == "AGREED")

    # Progress bar (30 chars)
    bar_width = 30
    filled = int(bar_width * completed / total) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_width - filled)

    print(c(f"Case {index}/{total}  [{bar}]  {pct:.0f}% complete", "dim"))
    print()

    # Case ID + Category
    print(c("Case ID:    ", "white", bold=True) + c(case["case_id"], "green", bold=True))
    print(c("Category:   ", "white", bold=True) + c(case["category"], "yellow"))
    print()

    # Instruction — the most important part
    print(c("Instruction:", "white", bold=True))
    instruction = case["instruction"]
    # Draw a box around the instruction for emphasis
    instr_width = min(len(instruction) + 4, 78)
    print(c(f"  ┌{'─' * instr_width}┐", "magenta"))
    print(c(f"  │  {instruction}{' ' * (instr_width - len(instruction) - 2)}│", "magenta", bold=True))
    print(c(f"  └{'─' * instr_width}┘", "magenta"))
    print()


def render_scene_objects(case: Dict[str, Any]) -> List[str]:
    """Render the available scene objects table. Returns list of valid IDs."""
    scene_objects = case.get("scene_objects", [])

    print(c("Available Scene Objects:", "white", bold=True))
    print(c(f"  {'─' * 74}", "dim"))

    if not scene_objects:
        print(c("  (no scene objects — this case may only involve the robot itself)", "dim"))
        print()
        return []

    # Table header
    header = f"  {'ID':<16s} {'Name':<18s} {'Label':<14s} {'Color':<10s} {'Material':<12s}"
    print(c(header, "dim"))
    print(c(f"  {'─' * 74}", "dim"))

    ids = []
    for obj in scene_objects:
        obj_id = obj["id"]
        ids.append(obj_id)

        name = obj.get("name", "?")[:17]
        label = obj.get("label", "?")[:13]
        attrs = obj.get("attributes", {})
        color_val = attrs.get("color", "?")[:9]
        material = attrs.get("material", "?")[:11]

        row = f"  {c(obj_id, 'cyan'):<30s} {name:<18s} {label:<14s} {color_val:<10s} {material:<12s}"
        # Manual padding for colored IDs (ANSI codes consume width)
        print(f"  {c(obj_id, 'cyan'):<40s} {name:<18s} {label:<14s} {color_val:<10s} {material:<12s}")

    print(c(f"  {'─' * 74}", "dim"))
    print()
    return ids


def render_pipeline_hints(case: Dict[str, Any]) -> None:
    """Show pipeline-proposed values as context (NOT as answers)."""
    print(c("Pipeline Context (for reference only — NOT ground truth):", "dim"))

    proposed_theme = case.get("proposed_theme_entity_id")
    if proposed_theme:
        print(c(f"  proposed_theme:         {proposed_theme}", "dim"))

    proposed_dest = case.get("proposed_destination_entity_id")
    if proposed_dest:
        print(c(f"  proposed_destination:   {proposed_dest}", "dim"))

    proposed_obs = case.get("proposed_obstacle_entity_ids", [])
    if proposed_obs:
        print(c(f"  proposed_obstacles:     {', '.join(proposed_obs)}", "dim"))

    if case.get("has_ambiguity"):
        print(c(f"  ⚠ Pipeline flagged AMBIGUITY", "red"))

    unmet = case.get("unmet_roles", [])
    if unmet:
        print(c(f"  ⚠ Unmet roles: {', '.join(unmet)}", "yellow"))

    print()


# ══════════════════════════════════════════════════════════════════════════════
# Input Prompts
# ══════════════════════════════════════════════════════════════════════════════

def input_entity_id(
    field_name: str,
    valid_ids: List[str],
    hint: Optional[str] = None,
) -> Optional[str]:
    """
    Prompt the human for a single entity ID.
    Returns the ID string, or None for NULL/empty.
    """
    hint_str = f" [pipeline: {hint}]" if hint else ""
    print(c(f"{field_name}{hint_str}", "green", bold=True))

    if not valid_ids:
        # No scene objects available — auto-NULL
        print(c(f"  (No scene objects in this case — auto NULL)", "dim"))
        print()
        return None

    print(c(f"  Valid IDs: {', '.join(valid_ids)}", "dim"))
    print(c(f"  Enter an ID, or press Enter for NULL", "dim"))

    while True:
        user_input = input("  → ").strip()

        if user_input == "":
            return None
        if user_input.lower() in ("null", "none", "n/a", "-"):
            return None
        if user_input in valid_ids:
            return user_input

        print(c(f"  ⚠ '{user_input}' is NOT in the available scene object IDs.", "red"))
        print(c(f"  Please enter one of: {', '.join(valid_ids)}", "yellow"))
        print(c(f"  Or press Enter for NULL.", "dim"))


def input_prohibition_ids(
    field_name: str,
    valid_ids: List[str],
    hints: Optional[List[str]] = None,
) -> List[str]:
    """
    Prompt the human for prohibition entity IDs (comma-separated, can be multiple).
    Returns a list of ID strings, or empty list for none.
    """
    hint_str = f" [pipeline: {', '.join(hints)}]" if hints else ""
    print(c(f"{field_name}{hint_str}", "green", bold=True))

    if not valid_ids:
        # No scene objects available — auto-empty
        print(c(f"  (No scene objects in this case — auto empty)", "dim"))
        print()
        return []

    print(c(f"  Valid IDs: {', '.join(valid_ids)}", "dim"))
    print(c(f"  Enter comma-separated IDs (e.g., obj-abc,obj-def), or press Enter for none", "dim"))

    while True:
        user_input = input("  → ").strip()

        if user_input == "":
            return []
        if user_input.lower() in ("null", "none", "n/a", "-"):
            return []

        # Parse comma-separated IDs
        ids = [x.strip() for x in user_input.split(",") if x.strip()]

        # Deduplicate while preserving order
        seen = set()
        unique_ids = []
        for x in ids:
            if x not in seen:
                seen.add(x)
                unique_ids.append(x)

        # Validate
        invalid = [x for x in unique_ids if x not in valid_ids]
        if invalid:
            print(c(f"  ⚠ Invalid IDs: {', '.join(invalid)}", "red"))
            print(c(f"  Available IDs: {', '.join(valid_ids)}", "yellow"))
            continue

        return unique_ids


def confirm_entry(
    case_id: str,
    theme: Optional[str],
    dest: Optional[str],
    prohibitions: List[str],
) -> str:
    """Show summary and get confirmation. Returns 'confirm', 'retry', or 'quit'."""
    print()
    print(c("── Annotation Summary ──", "white", bold=True))
    print(f"  Case ID:                  {c(case_id, 'cyan')}")
    print(f"  expected_theme_entity_id:        {c(theme or 'NULL', 'green' if theme else 'dim')}")
    print(f"  expected_destination_entity_id:  {c(dest or 'NULL', 'green' if dest else 'dim')}")
    print(f"  expected_prohibition_entity_ids: {c(str(prohibitions) if prohibitions else '[]', 'green' if prohibitions else 'dim')}")
    print()

    while True:
        choice = input(c("Confirm? [Y]es / [N]o (re-do) / [Q]uit: ", "white", bold=True)).strip().lower()

        if choice in ("y", "yes", ""):
            return "confirm"
        elif choice in ("n", "no"):
            return "retry"
        elif choice in ("q", "quit"):
            return "quit"
        else:
            print(c("  Please enter Y, N, or Q", "red"))


# ══════════════════════════════════════════════════════════════════════════════
# Export
# ══════════════════════════════════════════════════════════════════════════════

def export_csv(
    progress: Dict[str, Dict[str, Any]],
    cases: List[Dict[str, Any]],
) -> Path:
    """Export completed annotations to CSV. Returns the output path."""
    fieldnames = [
        "case_id",
        "category",
        "instruction",
        "expected_theme_entity_id",
        "expected_destination_entity_id",
        "expected_prohibition_entity_ids",
        "reviewer_status",
    ]

    rows = []
    for case in cases:
        case_id = case["case_id"]
        if case_id in progress:
            ann = progress[case_id]
            rows.append({
                "case_id": case_id,
                "category": case.get("category", ""),
                "instruction": case.get("instruction", ""),
                "expected_theme_entity_id": ann.get("expected_theme_entity_id") or "",
                "expected_destination_entity_id": ann.get("expected_destination_entity_id") or "",
                "expected_prohibition_entity_ids": ";".join(
                    ann.get("expected_prohibition_entity_ids", [])
                ) if ann.get("expected_prohibition_entity_ids") else "",
                "reviewer_status": ann.get("reviewer_status", "AGREED"),
            })

    with open(CSV_OUTPUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return CSV_OUTPUT_PATH


def compute_sha256(filepath: Path) -> str:
    """Compute the full SHA256 hex digest of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def generate_review_md() -> Path:
    """Generate the DELIVERY_HUMAN_REVIEW.md template. Returns the output path."""
    ts = datetime.now(timezone.utc).isoformat()

    content = f"""# DELIVERY_HUMAN_REVIEW.md
## Golden Data Human Review Sign-off

**Generated**: {ts}
**Dataset**: holdout_v3.json (via holdout_v3_annotation_draft.json)
**Reviewers**: [待填写 — Reviewer A name, Reviewer B name]

> ⚠ **IMPORTANT**: All conclusion fields below are intentionally blank.
> This file is a TEMPLATE. Fill in every `[待填写]` manually.
> AI-generated values are STRICTLY PROHIBITED in golden data.

---

### TC_005 — Spatial Ambiguity: Destination = Obstacle

| Field | Value |
|-------|-------|
| **Case ID** | TC_005 |
| **Instruction** | 绕过桌子，把杯子放到桌子上 |
| **Semantic Question** | 桌子同时是obstacle和destination。当前场景无法区分桌体和桌面区域。应判定为NEEDS_CLARIFICATION还是允许执行？ |
| **Old Value** (`expected.execution_ready`) | `true` |
| **Proposed Value** | `false` |
| **Pipeline Rationale** | 管道将table同时识别为destination和obstacle，因为场景对象无区域细分。若场景支持区分table.body_volume和table.top_surface，则可安全生成计划。当前场景无此能力，应安全澄清。 |
| **Evidence** | 1) 指令: "绕过桌子，把杯子放到桌子上" 2) destination = table, obstacle = table 3) FinalPlanValidator._validate_role_non_conflict: DESTINATION_IN_AVOID 4) 场景对象无region字段 5) 需SpatialRegion Schema升级或NEEDS_CLARIFICATION |

#### Reviewer A Decision

- **Reviewer Name**: [待填写]
- **Decision**: [ ] KEEP_OLD &nbsp;&nbsp; [ ] ACCEPT_PROPOSED &nbsp;&nbsp; [ ] ALTERNATIVE &nbsp;&nbsp; [ ] NEEDS_SCHEMA_CHANGE
- **Proposed Value** (if ALTERNATIVE): [待填写]
- **Rationale**: [待填写 — explain WHY this decision was made]
- **Signed At**: [待填写 — ISO 8601 timestamp]

#### Reviewer B Decision

- **Reviewer Name**: [待填写]
- **Decision**: [ ] KEEP_OLD &nbsp;&nbsp; [ ] ACCEPT_PROPOSED &nbsp;&nbsp; [ ] ALTERNATIVE &nbsp;&nbsp; [ ] NEEDS_SCHEMA_CHANGE
- **Proposed Value** (if ALTERNATIVE): [待填写]
- **Rationale**: [待填写 — explain WHY this decision was made]
- **Signed At**: [待填写 — ISO 8601 timestamp]

#### Adjudicator Decision (required only if Reviewers disagree)

- **Adjudicator Name**: [待填写]
- **Decision**: [ ] KEEP_OLD &nbsp;&nbsp; [ ] ACCEPT_PROPOSED &nbsp;&nbsp; [ ] ALTERNATIVE &nbsp;&nbsp; [ ] NEEDS_SCHEMA_CHANGE
- **Proposed Value**: [待填写]
- **Rationale**: [待填写]
- **Signed At**: [待填写 — ISO 8601 timestamp]

#### Final Resolution — TC_005

| Field | Value |
|-------|-------|
| **Final Status** | [ ] AGREED &nbsp;&nbsp; [ ] ADJUDICATED &nbsp;&nbsp; [ ] ESCALATED |
| **Final Value** (`expected.execution_ready`) | [待填写 — `true` or `false`] |
| **Finalized At** | [待填写 — ISO 8601 timestamp] |

---

### TC_008 — Constraint Conflict: EXACT 8N vs MAX 2N

| Field | Value |
|-------|-------|
| **Case ID** | TC_008 |
| **Instruction** | 使用8N抓住杯子，同时抓力不能超过2N |
| **Semantic Question** | EXACT 8N与MAX 2N不可同时满足。应判定为USER_CONSTRAINT_CONFLICT→NEEDS_CLARIFICATION而不是安全替代后继续？ |
| **Old Value** (`expected.execution_ready`) | `true` |
| **Proposed Value** | `false` |
| **Pipeline Rationale** | 用户显式请求8N(EXACT)和≤2N(MAX)，两个要求互斥。不能静默替代为2N后继续。应返回NEEDS_CLARIFICATION: "你同时要求8N且不超过2N，请确认采用哪个限制"。管道当前正确阻断，Golden需更新。 |
| **Evidence** | 1) 指令: "使用8N抓住杯子，同时抓力不能超过2N" 2) user_constraints: [EXACT 8N, MAX 2N] 3) 8N > 2N → conflict 4) constraint_compiler: USER_EXACT_EXCEEDS_OBJECT_HARD_LIMIT→NEEDS_CLARIFICATION 5) 系统正确阻断，Golden预期execution_ready=True是旧行为 |

#### Reviewer A Decision

- **Reviewer Name**: [待填写]
- **Decision**: [ ] KEEP_OLD &nbsp;&nbsp; [ ] ACCEPT_PROPOSED &nbsp;&nbsp; [ ] ALTERNATIVE &nbsp;&nbsp; [ ] NEEDS_SCHEMA_CHANGE
- **Proposed Value** (if ALTERNATIVE): [待填写]
- **Rationale**: [待填写 — explain WHY this decision was made]
- **Signed At**: [待填写 — ISO 8601 timestamp]

#### Reviewer B Decision

- **Reviewer Name**: [待填写]
- **Decision**: [ ] KEEP_OLD &nbsp;&nbsp; [ ] ACCEPT_PROPOSED &nbsp;&nbsp; [ ] ALTERNATIVE &nbsp;&nbsp; [ ] NEEDS_SCHEMA_CHANGE
- **Proposed Value** (if ALTERNATIVE): [待填写]
- **Rationale**: [待填写 — explain WHY this decision was made]
- **Signed At**: [待填写 — ISO 8601 timestamp]

#### Adjudicator Decision (required only if Reviewers disagree)

- **Adjudicator Name**: [待填写]
- **Decision**: [ ] KEEP_OLD &nbsp;&nbsp; [ ] ACCEPT_PROPOSED &nbsp;&nbsp; [ ] ALTERNATIVE &nbsp;&nbsp; [ ] NEEDS_SCHEMA_CHANGE
- **Proposed Value**: [待填写]
- **Rationale**: [待填写]
- **Signed At**: [待填写 — ISO 8601 timestamp]

#### Final Resolution — TC_008

| Field | Value |
|-------|-------|
| **Final Status** | [ ] AGREED &nbsp;&nbsp; [ ] ADJUDICATED &nbsp;&nbsp; [ ] ESCALATED |
| **Final Value** (`expected.execution_ready`) | [待填写 — `true` or `false`] |
| **Finalized At** | [待填写 — ISO 8601 timestamp] |

---

### Sign-off

| Role | Name | Signature | Date (ISO 8601) |
|------|------|-----------|------------------|
| Reviewer A | [待填写] | | |
| Reviewer B | [待填写] | | |
| Adjudicator | [待填写] | | |
| Release Approver | [待填写] | | |

---

*Template auto-generated by `entity_annotation_cli.py` on {ts}*
*All `[待填写]` fields must be filled manually by human reviewers.*
*No AI model may complete any field in this document.*
"""
    with open(REVIEW_MD_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    return REVIEW_MD_PATH


# ══════════════════════════════════════════════════════════════════════════════
# Main Loop
# ══════════════════════════════════════════════════════════════════════════════

def main() -> None:
    """Main interactive annotation loop."""
    clear_screen()
    render_banner()

    # ── Load data ──
    print(c("Loading holdout_v3_annotation_draft.json ...", "dim"))
    try:
        data = load_annotation_draft()
    except SystemExit:
        return

    cases: List[Dict[str, Any]] = data.get("cases", [])
    total = len(cases)
    if total == 0:
        print(c("✗ No cases found in the annotation draft.", "red", bold=True))
        sys.exit(1)

    # ── Load progress ──
    progress = load_progress()
    completed_ids = {cid for cid, v in progress.items() if v.get("reviewer_status") == "AGREED"}
    print(c(f"  Total cases:       {total}", "dim"))
    print(c(f"  Already annotated: {len(completed_ids)}", "dim"))
    print(c(f"  Remaining:         {total - len(completed_ids)}", "dim"))
    print()

    if len(completed_ids) == total:
        print(c("✓ All 150 cases are already annotated!", "green", bold=True))
        print()
        # Still allow re-export
        print(c("Re-exporting CSV and regenerating review template...", "yellow"))

        csv_path = export_csv(progress, cases)
        dataset_hash = compute_sha256(csv_path)
        review_path = generate_review_md()

        print()
        print(c("══ Export Complete ══", "green", bold=True))
        print(f"  CSV:           {csv_path}")
        print(f"  dataset_hash:  {c(dataset_hash, 'cyan', bold=True)}")
        print(f"  Review MD:     {review_path}")
        return

    if completed_ids:
        print(c(f"Resuming from previous session. {len(completed_ids)} cases already done.", "yellow"))
        print(c("Already-completed cases will be skipped.", "yellow"))
        print()

    input(c("Press Enter to begin annotation...", "dim"))
    print()

    # ── Annotate loop ──
    try:
        for i, case in enumerate(cases, 1):
            case_id = case["case_id"]

            # Skip already annotated
            if case_id in completed_ids:
                continue

            valid_ids = [obj["id"] for obj in case.get("scene_objects", [])]

            while True:
                clear_screen()
                render_banner()
                render_case_header(case, i, total)
                render_scene_objects(case)
                render_pipeline_hints(case)

                # ── Separator ──
                print(c("── Entity Grounding — Human Input Required ──", "yellow", bold=True))
                print(c("  (Enter an ID from the list above, or press Enter for NULL)", "dim"))
                print()

                # ── Field 1: Theme ──
                theme = input_entity_id(
                    "expected_theme_entity_id",
                    valid_ids,
                    hint=case.get("proposed_theme_entity_id"),
                )
                print()

                # ── Field 2: Destination ──
                dest = input_entity_id(
                    "expected_destination_entity_id",
                    valid_ids,
                    hint=case.get("proposed_destination_entity_id"),
                )
                print()

                # ── Field 3: Prohibition ──
                prohibitions = input_prohibition_ids(
                    "expected_prohibition_entity_ids",
                    valid_ids,
                    hints=case.get("proposed_obstacle_entity_ids"),
                )
                print()

                # ── Confirmation ──
                result = confirm_entry(case_id, theme, dest, prohibitions)

                if result == "confirm":
                    progress[case_id] = {
                        "expected_theme_entity_id": theme,
                        "expected_destination_entity_id": dest,
                        "expected_prohibition_entity_ids": prohibitions,
                        "reviewer_status": "AGREED",
                        "annotated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    completed_ids.add(case_id)
                    save_progress(progress)

                    remaining = total - len(completed_ids)
                    print()
                    print(c(f"✓ {case_id} saved.", "green"))
                    print(c(f"  {len(completed_ids)}/{total} done, {remaining} remaining.", "dim"))
                    if remaining > 0:
                        print(c("  Next case in 1 second...", "dim"))
                        # Brief pause so the human can see the confirmation
                        import time
                        time.sleep(1.0)
                    break

                elif result == "quit":
                    save_progress(progress)
                    print()
                    print(c(f"Progress saved. {len(completed_ids)}/{total} cases annotated.", "yellow"))
                    print(c("Run this script again to resume where you left off.", "dim"))
                    return

                # else: "retry" → loop back (re-display the case)

    except KeyboardInterrupt:
        save_progress(progress)
        print()
        print()
        print(c(f"╔══════════════════════════════════════════════════════════════════╗", "yellow"))
        print(c(f"║  Interrupted! Progress saved.                                   ║", "yellow"))
        print(c(f"║  {len(completed_ids)}/{total} cases annotated.                                  ║", "yellow"))
        print(c(f"║  Run this script again to resume.                               ║", "yellow"))
        print(c(f"╚══════════════════════════════════════════════════════════════════╝", "yellow"))
        return

    # ═══════════════════════════════════════════════════════════════
    # All cases done — export & freeze
    # ═══════════════════════════════════════════════════════════════
    clear_screen()
    render_banner()
    print(c("══ All 150 Cases Annotated! ══", "green", bold=True))
    print()

    # Export CSV
    print(c("Exporting CSV...", "dim"))
    csv_path = export_csv(progress, cases)
    print(c(f"✓ CSV saved to: {csv_path}", "green"))
    print()

    # Compute SHA256
    print(c("Computing dataset hash (SHA256)...", "dim"))
    dataset_hash = compute_sha256(csv_path)
    print()
    print(c("╔══════════════════════════════════════════════════════════════════╗", "white", bold=True))
    print(c("║  dataset_hash (SHA256):                                        ║", "white", bold=True))
    print(c(f"║  {dataset_hash}  ║", "cyan", bold=True))
    print(c("╚══════════════════════════════════════════════════════════════════╝", "white", bold=True))
    print()
    # Also save hash to a sidecar file
    hash_path = CSV_OUTPUT_PATH.with_suffix(".csv.sha256")
    with open(hash_path, "w", encoding="utf-8") as f:
        f.write(f"{dataset_hash}  {CSV_OUTPUT_PATH.name}\n")
    print(c(f"  Hash sidecar: {hash_path}", "dim"))
    print()

    # Generate review MD template
    print(c("Generating DELIVERY_HUMAN_REVIEW.md template...", "dim"))
    review_path = generate_review_md()
    print(c(f"✓ Review template saved to: {review_path}", "green"))
    print()

    # ── Final summary ──
    print(c("══ Entity Grounding Annotation — Complete ══", "green", bold=True))
    print()
    print(f"  CSV:              {csv_path}")
    print(f"  dataset_hash:     {c(dataset_hash, 'cyan', bold=True)}")
    print(f"  Review MD:        {review_path}")
    print(f"  Progress file:    {PROGRESS_PATH}")
    print()
    print(c("Next steps:", "yellow", bold=True))
    print(c("  1. Complete TC_005 and TC_008 reviews in DELIVERY_HUMAN_REVIEW.md", "dim"))
    print(c("  2. Commit holdout_v3_entity_annotation.csv + .sha256", "dim"))
    print(c("  3. Update DELIVERY_REPORT_SEMANTIC_ACCEPTANCE.md with final status", "dim"))
    print(c("  4. Run release_gate.py to re-evaluate", "dim"))
    print()


if __name__ == "__main__":
    main()
