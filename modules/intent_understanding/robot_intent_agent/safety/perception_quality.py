"""Quality gate for upstream perception observations."""
from __future__ import annotations

from typing import Any, Dict, List


def assess_perception_quality(observation: Dict[str, Any], mode: str = "legacy") -> Dict[str, Any]:
    """Validate upstream perception.

    ``mode='schema'`` is the production boundary: category confidence and
    category ambiguity are not language-understanding errors and therefore do
    not become intent blockers.  The default legacy mode preserves the public
    regression behavior and is useful for the separate perception-degradation
    test set.
    """
    issues: List[str] = []
    objects = observation.get("objects", []) if isinstance(observation, dict) else []
    if not isinstance(objects, list):
        return {"status": "BLOCKED", "issues": ["PERCEPTION_SCHEMA_INVALID"]}
    for idx, obj in enumerate(objects):
        if not isinstance(obj, dict):
            issues.append(f"OBJECT_{idx}_INVALID")
            continue
        cats = obj.get("category_candidates") or []
        scores = sorted([float(c.get("score", 0.0)) for c in cats if isinstance(c, dict)], reverse=True)
        if mode != "schema" and scores and scores[0] < 0.35:
            issues.append(f"OBJECT_{idx}_LOW_CATEGORY_CONFIDENCE")
        if mode != "schema" and len(scores) >= 2 and scores[0] - scores[1] < 0.10:
            issues.append(f"OBJECT_{idx}_CATEGORY_AMBIGUOUS")
        # Graspability is role-dependent: a tray/table is correctly
        # non-graspable when it is a destination.  It is checked after
        # grounding against the actual theme, not globally here.
        pose = obj.get("pose", {})
        pos = pose.get("position", {}) if isinstance(pose, dict) else {}
        if isinstance(pos, dict) and any(abs(float(pos.get(k, 0))) > 10 for k in ("x", "y", "z")):
            issues.append(f"OBJECT_{idx}_POSITION_IMPLAUSIBLE")
    # A missing unit is tolerated for the existing normalized scene contract;
    # an explicitly unsupported unit is not.
    if isinstance(observation, dict) and observation.get("unit") not in (None, "", "m", "cm", "mm"):
        issues.append("PERCEPTION_UNIT_INVALID")
    invalid = any("INVALID" in item or "IMPLAUSIBLE" in item for item in issues)
    status = "BLOCKED" if invalid else ("READY" if not issues else "NEEDS_CLARIFICATION")
    return {"status": status, "issues": issues}


def validate_perception_schema(observation: Dict[str, Any]) -> Dict[str, Any]:
    """Production perception contract, separate from intent semantics."""
    return assess_perception_quality(observation, mode="schema")
