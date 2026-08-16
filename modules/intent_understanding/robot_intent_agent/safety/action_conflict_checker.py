"""Deterministic action/precondition/effect conflict checks.

This module is deliberately independent of the planner.  It consumes the
normalized ParsedTask and is therefore applied equally to rule and LLM plans.
It never repairs a contradiction; callers must block execution.
"""
from __future__ import annotations

import re
from typing import Any, List


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read both Pydantic atoms and their JSON compatibility projections."""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def find_action_constraint_conflicts(
    parsed_task: Any,
    instruction: str = "",
    scene: Any = None,
    semantic_authority: bool = False,
) -> List[str]:
    reasons: List[str] = []
    action = getattr(getattr(parsed_task, "action", None), "value", getattr(parsed_task, "action", ""))
    theme = getattr(parsed_task, "theme", None)
    destination = getattr(parsed_task, "destination", None) or getattr(parsed_task, "support_surface", None)
    recipient = getattr(parsed_task, "recipient", None)
    semantic_graph = getattr(parsed_task, "semantic_task_graph", None) or {}
    graph_events = semantic_graph.get("events", []) if isinstance(semantic_graph, dict) else []
    theme_local_refs = {
        event.get("theme_ref") for event in graph_events
        if isinstance(event, dict) and event.get("theme_ref")
    }

    # Hard action preconditions.  Missing roles are unsafe, not a suggestion.
    if scene is not None and theme is not None and getattr(theme, "entity_id", None):
        obj = scene.find_object(theme.entity_id) if hasattr(scene, "find_object") else None
        if obj is None:
            reasons.append("GROUNDING_ENTITY_NOT_IN_SCENE")
        elif action in {"GRASP", "DYNAMIC_GRASP", "FETCH", "HANDOVER", "TRANSFER"}:
            affordances = {getattr(a, "value", str(a)) for a in (getattr(obj, "affordances", []) or [])}
            if "graspable" not in affordances:
                reasons.append("PERCEPTION_EXECUTABILITY_CONFLICT:theme_not_graspable")
            attrs = getattr(obj, "attributes", {}) or {}
            pos = attrs.get("position") or getattr(obj, "position", None)
            if pos is not None and hasattr(pos, "x"):
                pos = (pos.x, pos.y, pos.z)
            if isinstance(pos, (list, tuple)) and len(pos) >= 3 and any(abs(float(v)) > 10 for v in pos[:3]):
                reasons.append("PERCEPTION_EXECUTABILITY_CONFLICT:theme_out_of_workspace")

    # Explicit semantic contradictions which must never be silently normalized.
    text = "" if semantic_authority else (instruction or getattr(parsed_task, "instruction", "") or "")
    if action in {"GRASP", "DYNAMIC_GRASP"} and re.search(r"(?:保持|仍然|继续).{0,8}(?:未抓|没抓|不拿|未拿|空手)", text):
        reasons.append("ACTION_CONSTRAINT_CONFLICT:grasp_vs_not_holding")
    if action == "PLACE" and re.search(r"(?:保持|仍然|不要|不能).{0,8}(?:未抓|没抓|不拿|未拿|空手)", text):
        reasons.append("ACTION_CONSTRAINT_CONFLICT:place_without_holding")

    # Lexical fallback covers a contradiction even when the semantic parser
    # has not yet assigned a high-level action.  This keeps the safety gate
    # independent from parser coverage.
    if re.search(r"(?:拿起|抓取|抓住|取起|拿过来)", text):
        contradiction = (
            re.search(r"(?:保持|维持|同时|并且|却|但).{0,16}(?:未拿起|没有被拿起|未被抓住|没有拿起|不在抓持)", text)
            or re.search(r"(?:拿起|抓取|抓住).{0,16}(?:保持|维持).{0,8}(?:未拿起|没有被拿起|未被抓住|不在抓持)", text)
        )
        if contradiction:
            reasons.append("ACTION_CONSTRAINT_CONFLICT:grasp_vs_not_holding")

    for prohibition in getattr(parsed_task, "prohibitions", []) or []:
        ptype = _enum_value(_field(prohibition, "type", ""))
        target_ref = _field(prohibition, "target_ref")
        prohibited_action = _enum_value(_field(prohibition, "action"))
        scoped_to_theme = not target_ref or not theme or target_ref in {
            getattr(theme, "entity_id", None), getattr(theme, "mention", None)
        } or target_ref in theme_local_refs
        if ptype == "NO_CONTACT" and scoped_to_theme and action in {"GRASP", "PLACE", "HANDOVER", "TRANSFER"}:
            reasons.append("ACTION_CONSTRAINT_CONFLICT:NO_CONTACT")
        if ptype == "FORBID_ACTION":
            forbidden = prohibited_action
            if forbidden and forbidden == action and scoped_to_theme:
                reasons.append("ACTION_CONSTRAINT_CONFLICT:FORBID_ACTION")

    # A normalized graph may represent the same contradiction as a positive
    # event plus a prohibition.  Detect it without relying on surface text.
    for prohibition in getattr(parsed_task, "prohibitions", []) or []:
        ptype = _enum_value(_field(prohibition, "type", ""))
        if ptype != "FORBID_ACTION":
            continue
        target_ref = _field(prohibition, "target_ref")
        if theme and (target_ref in {getattr(theme, "entity_id", None), getattr(theme, "mention", None)}
                      or target_ref in theme_local_refs):
            span = str(_field(prohibition, "evidence_span", "")).lower()
            action_words = {
                "GRASP": ("抓", "拿", "取", "grasp", "grab", "pick"),
                "PLACE": ("放", "置", "place", "put"),
                "TRANSFER": ("转移", "transfer", "搬运"),
                "HANDOVER": ("递", "交", "handover", "give"),
            }
            if any(word in span for word in action_words.get(action, ())):
                reasons.append("ACTION_CONSTRAINT_CONFLICT:FORBID_ACTION")

    # Intersect all hard numeric bounds.  Do not choose a midpoint when the
    # intersection is empty (the previous behavior could accidentally pass it).
    bounds = {}
    for c in getattr(parsed_task, "user_constraints", []) or []:
        if not getattr(c, "is_hard", True):
            continue
        p = getattr(c, "parameter", "")
        lo, hi = bounds.get(p, (None, None))
        op = getattr(getattr(c, "operator", None), "value", getattr(c, "operator", ""))
        value = getattr(c, "value", None)
        if op in {"min", "MIN"} and value is not None:
            lo = value if lo is None else max(lo, value)
        elif op in {"max", "MAX"} and value is not None:
            hi = value if hi is None else min(hi, value)
        elif op in {"range", "RANGE"}:
            cmin, cmax = getattr(c, "min_value", None), getattr(c, "max_value", None)
            lo = cmin if lo is None else (max(lo, cmin) if cmin is not None else lo)
            hi = cmax if hi is None else (min(hi, cmax) if cmax is not None else hi)
        bounds[p] = (lo, hi)
    for parameter, (lo, hi) in bounds.items():
        if lo is not None and hi is not None and lo > hi:
            reasons.append(f"ACTION_CONSTRAINT_CONFLICT:{parameter}_min_gt_max")
    return list(dict.fromkeys(reasons))
