"""
Immutable Golden Review Mechanism.

Provides append-only golden data review workflow with:
- Dual reviewer + adjudication
- Immutable audit trail (JSONL)
- Dataset versioning and hash tracking
- Pending/Permanent state separation
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional


# ══════════════════════════════════════════════════════════════
# Enums and Constants
# ══════════════════════════════════════════════════════════════

class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    AGREED = "AGREED"
    DISAGREED = "DISAGREED"
    ADJUDICATED = "ADJUDICATED"
    REJECTED = "REJECTED"


class ReviewerDecision(str, Enum):
    KEEP_OLD = "KEEP_OLD"
    ACCEPT_PROPOSED = "ACCEPT_PROPOSED"
    ALTERNATIVE = "ALTERNATIVE"
    NEEDS_SCHEMA_CHANGE = "NEEDS_SCHEMA_CHANGE"


# ══════════════════════════════════════════════════════════════
# Data Models
# ══════════════════════════════════════════════════════════════

@dataclass
class SingleReviewerDecision:
    reviewer_id: str
    decision: ReviewerDecision
    proposed_value: Any = None
    rationale: str = ""
    signed_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reviewer_id": self.reviewer_id,
            "decision": self.decision.value,
            "proposed_value": self.proposed_value,
            "rationale": self.rationale,
            "signed_at": self.signed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SingleReviewerDecision":
        return cls(
            reviewer_id=d.get("reviewer_id", ""),
            decision=ReviewerDecision(d.get("decision", "KEEP_OLD")),
            proposed_value=d.get("proposed_value"),
            rationale=d.get("rationale", ""),
            signed_at=d.get("signed_at", ""),
        )


@dataclass
class GoldenReviewDecision:
    review_id: str = field(default_factory=lambda: f"gr-{_uuid.uuid4().hex[:10]}")
    case_id: str = ""
    dataset_name: str = ""
    dataset_hash_before: str = ""
    field_path: str = ""
    old_value: Any = None
    proposed_value: Any = None
    semantic_question: str = ""
    rationale: str = ""
    evidence: List[str] = field(default_factory=list)
    reviewer_a: Optional[SingleReviewerDecision] = None
    reviewer_b: Optional[SingleReviewerDecision] = None
    adjudicator: Optional[SingleReviewerDecision] = None
    final_status: ReviewStatus = ReviewStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finalized_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "review_id": self.review_id,
            "case_id": self.case_id,
            "dataset_name": self.dataset_name,
            "dataset_hash_before": self.dataset_hash_before,
            "field_path": self.field_path,
            "old_value": self.old_value,
            "proposed_value": self.proposed_value,
            "semantic_question": self.semantic_question,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "reviewer_a": self.reviewer_a.to_dict() if self.reviewer_a else None,
            "reviewer_b": self.reviewer_b.to_dict() if self.reviewer_b else None,
            "adjudicator": self.adjudicator.to_dict() if self.adjudicator else None,
            "final_status": self.final_status.value,
            "created_at": self.created_at,
            "finalized_at": self.finalized_at,
        }

    def sign_reviewer_a(self, reviewer_id: str, decision: ReviewerDecision,
                        proposed_value: Any = None, rationale: str = "") -> None:
        self.reviewer_a = SingleReviewerDecision(
            reviewer_id=reviewer_id, decision=decision,
            proposed_value=proposed_value, rationale=rationale,
            signed_at=datetime.now(timezone.utc).isoformat(),
        )

    def sign_reviewer_b(self, reviewer_id: str, decision: ReviewerDecision,
                        proposed_value: Any = None, rationale: str = "") -> None:
        self.reviewer_b = SingleReviewerDecision(
            reviewer_id=reviewer_id, decision=decision,
            proposed_value=proposed_value, rationale=rationale,
            signed_at=datetime.now(timezone.utc).isoformat(),
        )

    def sign_adjudicator(self, reviewer_id: str, decision: ReviewerDecision,
                         proposed_value: Any = None, rationale: str = "") -> None:
        self.adjudicator = SingleReviewerDecision(
            reviewer_id=reviewer_id, decision=decision,
            proposed_value=proposed_value, rationale=rationale,
            signed_at=datetime.now(timezone.utc).isoformat(),
        )

    def finalize(self) -> None:
        if self.reviewer_a is None or self.reviewer_b is None:
            return
        if self.reviewer_a.decision == self.reviewer_b.decision:
            self.final_status = ReviewStatus.AGREED
        elif self.adjudicator is not None:
            self.final_status = ReviewStatus.ADJUDICATED
        else:
            self.final_status = ReviewStatus.DISAGREED
        self.finalized_at = datetime.now(timezone.utc).isoformat()

    def get_final_value(self) -> Any:
        """Return the agreed-upon value after review."""
        if self.final_status == ReviewStatus.AGREED:
            return self.reviewer_a.proposed_value if self.reviewer_a.decision == ReviewerDecision.ALTERNATIVE else (
                self.proposed_value if self.reviewer_a.decision == ReviewerDecision.ACCEPT_PROPOSED else self.old_value
            )
        if self.final_status == ReviewStatus.ADJUDICATED and self.adjudicator:
            return self.adjudicator.proposed_value if self.adjudicator.decision == ReviewerDecision.ALTERNATIVE else (
                self.proposed_value if self.adjudicator.decision == ReviewerDecision.ACCEPT_PROPOSED else self.old_value
            )
        return self.old_value


# ══════════════════════════════════════════════════════════════
# Review Logger — append-only JSONL
# ══════════════════════════════════════════════════════════════

class GoldenReviewLogger:
    """Append-only audit trail for golden data reviews."""

    def __init__(self, log_path: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, review: GoldenReviewDecision) -> None:
        entry = review.to_dict()
        entry["_logged_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        entries = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def get_by_case(self, case_id: str) -> List[Dict[str, Any]]:
        return [e for e in self.read_all() if e.get("case_id") == case_id]

    def get_pending(self) -> List[Dict[str, Any]]:
        return [e for e in self.read_all() if e.get("final_status") == "PENDING"]


# ══════════════════════════════════════════════════════════════
# Dataset Freeze
# ══════════════════════════════════════════════════════════════

def compute_dataset_hash(data: Dict[str, Any]) -> str:
    """Compute deterministic SHA256 hash of dataset content."""
    serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def freeze_dataset(
    dataset_path: str,
    output_path: str,
    review_log_path: str,
    dataset_version: str = "1.0.0",
    frozen_by: str = "system",
    intended_usage: str = "REGRESSION",
) -> Dict[str, Any]:
    """Freeze a dataset with metadata, version, and hash."""
    with open(dataset_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    dataset_hash = compute_dataset_hash(data)

    # Compute review log hash
    log_path = Path(review_log_path)
    review_log_hash = ""
    if log_path.exists():
        review_log_hash = hashlib.sha256(log_path.read_bytes()).hexdigest()[:16]

    # Count review stats
    logger = GoldenReviewLogger(review_log_path)
    all_reviews = logger.read_all()
    pending = [r for r in all_reviews if r.get("final_status") == "PENDING"]
    agreed = [r for r in all_reviews if r.get("final_status") in ("AGREED", "ADJUDICATED")]
    total = len(all_reviews)

    metadata = {
        "dataset_version": dataset_version,
        "dataset_hash": dataset_hash,
        "case_count": len(data.get("cases", [])),
        "annotation_version": dataset_version,
        "golden_review_log_hash": review_log_hash,
        "total_reviews": total,
        "agreed_reviews": len(agreed),
        "pending_reviews": len(pending),
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "frozen_by": frozen_by,
        "intended_usage": intended_usage,
    }

    frozen = {
        "metadata": metadata,
        "data": data,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(frozen, f, ensure_ascii=False, indent=2)

    # Write metadata separately
    meta_path = output_path.replace(".json", ".metadata.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    return metadata


# ══════════════════════════════════════════════════════════════
# Review Report Generator
# ══════════════════════════════════════════════════════════════

def generate_review_report(reviews: List[GoldenReviewDecision], output_path: str) -> None:
    """Generate human-readable Markdown review report."""
    lines = [
        "# Golden Data Review Report",
        "",
        f"**Generated**: {datetime.now(timezone.utc).isoformat()}",
        f"**Total Reviews**: {len(reviews)}",
        "",
        "## Summary",
        "",
        f"| Status | Count |",
        f"|--------|-------|",
    ]
    status_counts = {}
    for r in reviews:
        status_counts[r.final_status.value] = status_counts.get(r.final_status.value, 0) + 1
    for status, count in sorted(status_counts.items()):
        lines.append(f"| {status} | {count} |")

    lines.extend(["", "## Reviews", ""])
    for r in reviews:
        lines.append(f"### {r.case_id}: `{r.field_path}`")
        lines.append(f"- **Status**: {r.final_status.value}")
        lines.append(f"- **Question**: {r.semantic_question}")
        lines.append(f"- **Old**: `{json.dumps(r.old_value, ensure_ascii=False)}`")
        lines.append(f"- **Proposed**: `{json.dumps(r.proposed_value, ensure_ascii=False)}`")
        if r.reviewer_a:
            lines.append(f"- **Reviewer A** ({r.reviewer_a.reviewer_id}): {r.reviewer_a.decision.value} — {r.reviewer_a.rationale}")
        if r.reviewer_b:
            lines.append(f"- **Reviewer B** ({r.reviewer_b.reviewer_id}): {r.reviewer_b.decision.value} — {r.reviewer_b.rationale}")
        if r.adjudicator:
            lines.append(f"- **Adjudicator** ({r.adjudicator.reviewer_id}): {r.adjudicator.decision.value} — {r.adjudicator.rationale}")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
