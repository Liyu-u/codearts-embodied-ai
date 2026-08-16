"""Explicit ambiguity classification for complex semantic instructions."""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List


class AmbiguityType(str, Enum):
    REFERENCE_AMBIGUITY = "REFERENCE_AMBIGUITY"
    ATTRIBUTE_SCOPE_AMBIGUITY = "ATTRIBUTE_SCOPE_AMBIGUITY"
    SPATIAL_REFERENCE_AMBIGUITY = "SPATIAL_REFERENCE_AMBIGUITY"
    NEGATION_SCOPE_AMBIGUITY = "NEGATION_SCOPE_AMBIGUITY"
    ROLE_AMBIGUITY = "ROLE_AMBIGUITY"
    ACTION_AMBIGUITY = "ACTION_AMBIGUITY"
    COREFERENCE_AMBIGUITY = "COREFERENCE_AMBIGUITY"
    TEMPORAL_AMBIGUITY = "TEMPORAL_AMBIGUITY"
    EXECUTION_CONTEXT_AMBIGUITY = "EXECUTION_CONTEXT_AMBIGUITY"


AMBIGUITY_TYPES = tuple(item.value for item in AmbiguityType)


def classify_ambiguities(instruction: str, parsed_task: Any = None, scene: Any = None) -> List[Dict[str, Any]]:
    text = instruction or ""
    findings: List[Dict[str, Any]] = []
    if re.search(r"(?:这个|那个|它|其|该物体|上一个|前面提到的)", text):
        findings.append({"type": AmbiguityType.COREFERENCE_AMBIGUITY.value,
                         "legacy_type": "REFERENCE", "strategy": "CONTEXT_OR_SCENE_RESOLUTION", "hard": True})
    if re.search(r"(?:左边|右边|前面|后面|上面|下面|最左|最右)", text):
        if re.search(r"(?:我左边|我右边|我前面|我后面)", text) and not getattr(scene, "user_pose", None):
            findings.append({"type": AmbiguityType.SPATIAL_REFERENCE_AMBIGUITY.value,
                             "strategy": "NEEDS_CLARIFICATION", "hard": True,
                             "reason": "user_pose_missing"})
        elif scene is not None:
            findings.append({"type": AmbiguityType.SPATIAL_REFERENCE_AMBIGUITY.value,
                             "strategy": "GEOMETRIC_QUERY_WITHIN_SAME_CATEGORY", "hard": False})
    if re.search(r"(?:不要|别|禁止|避免).+?(?:，|,).+(?:拿|抓|取|放)", text):
        findings.append({"type": AmbiguityType.NEGATION_SCOPE_AMBIGUITY.value,
                         "strategy": "SEPARATE_PROHIBITED_THEME_FROM_THEME", "hard": True})
    if re.search(r"(?:上料|搬运|移到|放入|放到|转移)", text) and not re.search(r"(?:到|至|放入|放到|移到).+", text):
        findings.append({"type": AmbiguityType.ROLE_AMBIGUITY.value,
                         "strategy": "ACTION_SCHEMA_REQUIRED_ROLES", "hard": True})
    if len(re.findall(r"抓|拿|取|放|移|递|交|送", text)) >= 2:
        findings.append({"type": AmbiguityType.ACTION_AMBIGUITY.value,
                         "strategy": "PRESERVE_MULTIPLE_EVENTS", "hard": False})
    if re.search(r"(?:先|再|然后|之后|完成后|等待|直到|如果|除非|并且|同时)", text):
        steps = getattr(parsed_task, "steps", []) if parsed_task is not None else []
        if len(steps) < 2:
            findings.append({"type": AmbiguityType.TEMPORAL_AMBIGUITY.value,
                             "legacy_type": "SEQUENCE_INCOMPLETE",
                             "strategy": "LLM_SEQUENCE_PARSE_THEN_RULE_BT", "hard": True})
    if parsed_task is not None:
        for role in getattr(parsed_task, "unmet_roles", []) or []:
            findings.append({"type": AmbiguityType.ROLE_AMBIGUITY.value, "role": role,
                             "strategy": "ASK_CLARIFICATION", "hard": True})
        for entity_role in ("theme", "destination", "recipient", "source"):
            entity = getattr(parsed_task, entity_role, None)
            if entity is not None and getattr(entity, "entity_id", None) is None and scene is not None:
                findings.append({"type": AmbiguityType.REFERENCE_AMBIGUITY.value,
                                 "role": entity_role, "strategy": "GROUNDING_MARGIN_CHECK", "hard": True})
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) >= 2 and parsed_task is not None:
        constraints = getattr(parsed_task, "user_constraints", []) or []
        if len(constraints) < len(numbers):
            findings.append({"type": AmbiguityType.EXECUTION_CONTEXT_AMBIGUITY.value,
                             "legacy_type": "NUMERIC_INCOMPLETE",
                             "strategy": "LLM_NUMERIC_EXTRACTION_THEN_SAFE_INTERSECTION", "hard": True})
    return findings


class AmbiguityManager:
    """Classify first, then resolve only when evidence makes it deterministic."""

    def classify(self, instruction: str, parsed_task: Any = None, scene: Any = None):
        return classify_ambiguities(instruction, parsed_task, scene)

    def resolve(self, instruction: str, parsed_task: Any = None, scene: Any = None) -> Dict[str, Any]:
        findings = self.classify(instruction, parsed_task, scene)
        hard = [item for item in findings if item.get("hard")]
        clarifications = [item for item in hard if item.get("strategy") == "NEEDS_CLARIFICATION"
                          or item.get("type") in {AmbiguityType.REFERENCE_AMBIGUITY.value,
                                                   AmbiguityType.SPATIAL_REFERENCE_AMBIGUITY.value}]
        return {
            "findings": findings,
            "status": "NEEDS_CLARIFICATION" if clarifications else ("RESOLVED" if not hard else "REQUIRES_REVIEW"),
            "clarifications": clarifications,
        }
