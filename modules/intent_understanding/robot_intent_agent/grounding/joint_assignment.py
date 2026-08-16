"""Joint role assignment with conflict penalties."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
import re
from typing import Any, Dict, Iterable, List, Optional

from robot_intent_agent.domain.action_schemas import get_action_schema
from .grounding_scorer import GroundingScorer
from .spatial_reasoner import SpatialReasoner


@dataclass
class GroundingDecision:
    role: str
    selected_entity_id: Optional[str]
    candidate_ids: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    margin: float = 0.0
    decision: str = "UNRESOLVED"


class JointGroundingSolver:
    def __init__(self, ambiguity_threshold: float = 0.15):
        self.ambiguity_threshold = ambiguity_threshold
        self.scorer = GroundingScorer()
        self.spatial = SpatialReasoner()

    def solve(self, role_candidates: Dict[str, List[Any]], action: str = "CUSTOM",
              forbidden_ids: Optional[set[str]] = None,
              role_queries: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, GroundingDecision]:
        forbidden_ids = forbidden_ids or set()
        schema = get_action_schema(action)
        roles = list(role_candidates)
        scored: Dict[str, List[tuple[Any, float, List[str]]]] = {}
        for role, candidates in role_candidates.items():
            values = []
            for candidate in candidates:
                entity_id = getattr(candidate, "id", "")
                if entity_id in forbidden_ids:
                    continue
                if role == "theme":
                    required = ["graspable", "movable"]
                elif role == "recipient" and action == "HANDOVER":
                    required = ["recipient"]
                elif role == "destination" and action in {"PLACE", "TRANSFER", "FETCH"}:
                    # The scene adapter marks stable receive surfaces as
                    # fixed/container.  FETCH therefore prefers fixed or
                    # container destinations; PLACE keeps the stricter
                    # support-surface contract.
                    required = ["support_surface"] if action == "PLACE" else (
                        ["fixed"] if action == "FETCH" else []
                    )
                else:
                    required = []
                query = (role_queries or {}).get(role, {})
                result = self.scorer.score(
                    candidate,
                    category=query.get("category"),
                    attributes=query.get("attributes"),
                    required_affordances=required,
                    mention=query.get("mention"),
                    peers=candidates,
                )
                # Comparative size language is a hard part of the object
                # description.  If a candidate contradicts it, do not let a
                # separate spatial cue rescue that candidate.  With only one
                # candidate there is no peer comparison, so retain the
                # ordinary category/affordance decision.
                relative_size_conflict = any(
                    evidence.startswith("relative_size!=")
                    for evidence in result.evidence
                )
                if relative_size_conflict and len(candidates) > 1:
                    continue
                values.append((candidate, result.score, result.evidence))
            scored[role] = sorted(values, key=lambda item: item[1], reverse=True)
        best_assignment = None
        best_score = float("-inf")
        second_score = float("-inf")
        # Missing optional/symbolic roles must not prevent independent scene
        # roles from being grounded.  The previous all-or-nothing product
        # made a missing recipient erase a perfectly resolvable theme.
        assignable_roles = [role for role in roles if scored.get(role)]
        assignments = (
            product(*(scored.get(role, [])[:5] for role in assignable_roles))
            if assignable_roles else []
        )
        for choices in assignments:
            ids = [getattr(choice[0], "id", "") for choice in choices]
            score = sum(choice[1] for choice in choices)
            # Theme and obstacle must be distinct.  Theme and destination may
            # not alias for manipulation actions.
            for left, right in (("theme", "obstacle"), ("theme", "destination")):
                if left in assignable_roles and right in assignable_roles:
                    li, ri = assignable_roles.index(left), assignable_roles.index(right)
                    if ids[li] == ids[ri]:
                        score -= 2.0
            if score > best_score:
                second_score = best_score; best_score = score; best_assignment = choices
            elif score > second_score:
                second_score = score
        decisions: Dict[str, GroundingDecision] = {}
        for role in roles:
            candidates = scored.get(role, [])
            ids = [str(getattr(item[0], "id", "")) for item in candidates]
            selected = None
            evidence: List[str] = []
            if best_assignment is not None and role in assignable_roles:
                idx = assignable_roles.index(role)
                selected = str(getattr(best_assignment[idx][0], "id", ""))
                evidence = list(best_assignment[idx][2])
            top = candidates[0][1] if candidates else 0.0
            next_score = candidates[1][1] if len(candidates) > 1 else 0.0
            margin = top - next_score
            semantic_tie = False
            if len(candidates) > 1:
                first_obj = candidates[0][0]
                second_obj = candidates[1][0]
                query = (role_queries or {}).get(role, {})
                query_attrs = query.get("attributes") or {}
                first_class = str(getattr(first_obj, "specific_class", None)
                                  or getattr(first_obj, "label", None) or "").lower()
                second_class = str(getattr(second_obj, "specific_class", None)
                                   or getattr(second_obj, "label", None) or "").lower()
                same_category = bool(query.get("category")) and first_class == second_class
                same_attributes = all(
                    str((getattr(first_obj, "attributes", {}) or {}).get(key, "")).lower()
                    == str((getattr(second_obj, "attributes", {}) or {}).get(key, "")).lower()
                    for key in query_attrs
                )
                mention = str(query.get("mention") or "")
                has_explicit_disambiguator = bool(re.search(
                    r"左|右|前|后|中间|最|偏大|偏小|较大|较小|大型|小型|细长|矮胖|"
                    r"第[一二三四五六七八九十]|编号|id|near|left|right|front|behind|largest|smallest",
                    mention, re.IGNORECASE,
                ))
                semantic_tie = same_category and same_attributes and not has_explicit_disambiguator
            # Candidate retrieval has already applied the semantic mention
            # and attribute filters.  A single surviving candidate is a
            # resolved binding even when its generic category contributes no
            # numeric score (e.g. a stack destination).
            decision = "RESOLVED" if selected and (
                not semantic_tie and (margin >= self.ambiguity_threshold or len(candidates) == 1)
            ) else "NEEDS_CLARIFICATION"
            decisions[role] = GroundingDecision(role, selected if decision == "RESOLVED" else None,
                                                ids, evidence, margin, decision)
        # A required role with no candidate is never silently bound.
        for role in schema.required_roles:
            if role not in decisions:
                decisions[role] = GroundingDecision(role, None, [], [], 0.0, "NEEDS_CLARIFICATION")
        return decisions
