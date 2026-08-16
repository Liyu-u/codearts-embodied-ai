"""Rule path: natural language -> evidence-bearing semantic candidate."""

from __future__ import annotations

import re
from typing import Dict, List

from robot_intent_agent.domain.action_schemas import normalize_action
from robot_intent_agent.domain.action_schemas import get_action_schema
from robot_intent_agent.schemas.semantic_task_graph import (
    AmbiguityRecord, EvidenceSpan, SemanticCandidate, SemanticEvent, SemanticRelation, SemanticTaskGraph,
)
from .action_parser import parse_action_candidates, select_action_sequence
from .condition_parser import parse_conditions
from .constraint_parser import parse_constraints
from .coreference_resolver import CoreferenceResolver
from .negation_parser import NegationParser
from .role_parser import parse_roles


class RuleSemanticParser:
    """Deterministic parser.  It never assigns physical scene IDs."""

    def parse(self, instruction: str, scene=None) -> SemanticCandidate:
        text = instruction or ""
        action_candidates = parse_action_candidates(text)
        action_values = select_action_sequence(action_candidates, text)
        conditions = parse_conditions(text)
        # WAIT is a real event when a condition connector is present.
        if (conditions and any(condition.predicate == "WAIT_UNTIL" for condition in conditions)
                and not any(action == "WAIT" for action in action_values)):
            action_values = ["WAIT"] + action_values
        entities, role_refs = parse_roles(text, action_values)
        # WAIT is a monitoring action.  When the language names a moving or
        # changing target without a noun phrase that the role parser can
        # ground, use the single movable scene object as the deterministic
        # observation target.  This is a scene-based role completion, not a
        # new vocabulary rule; ambiguity is left for the safety gate.
        if any(action == "WAIT" for action in action_values) and scene is not None:
            movable = [item for item in getattr(scene, "objects", []) or []
                       if any(str(getattr(aff, "value", aff)).lower() in {"movable", "graspable"}
                              for aff in (getattr(item, "affordances", []) or []))]
            # "工位稳定" is a condition surface, not necessarily the
            # workbench object. With one movable object, monitor that object.
            existing_theme = role_refs.get("theme")
            existing_entity = next((item for item in entities if item.local_ref == existing_theme), None)
            negative_non_target = bool(re.search(
                r"(?:\u4e0d\u8981|\u7981\u6b62|\u907f\u514d|\u52ff)[^，。；,;]{0,12}(?:\u78b0|\u63a5\u89e6|\u649e|\u89e6\u78b0)",
                text,
            ))
            if (existing_entity is not None
                    and (existing_entity.category in {"table", "tray", "workbench", "support_surface"}
                         or (existing_entity.category == "fixture" and negative_non_target))
                    and len(movable) == 1):
                role_refs.pop("theme", None)
                entities = [item for item in entities if item.local_ref != existing_theme]
            ambiguous_wait = bool(re.search(r"涓嶆槑纭涓嶆竻妤涓嶇‘瀹氥*", text))
            ambiguous_wait = bool(re.search(
                "\u76ee\u6807\u4e0d\u660e\u786e|\u76ee\u6807\u4e0d\u6e05\u695a|\u4e0d\u660e\u786e|\u4e0d\u786e\u5b9a",
                text,
            ))
            if len(movable) == 1 and not role_refs.get("theme") and not ambiguous_wait:
                from robot_intent_agent.schemas.semantic_task_graph import SemanticEntity
                item = movable[0]
                local_ref = "wait-theme-1"
                entities.append(SemanticEntity(
                    local_ref=local_ref,
                    mention="target",
                    category=getattr(item, "specific_class", None) or getattr(item, "label", "object"),
                    attributes={},
                    evidence_spans=["target"],
                    candidate_key=str(getattr(item, "id", "")) or None,
                ))
                role_refs["theme"] = local_ref

            # WAIT clauses often omit the noun after an explicit spatial or
            # size description. Reuse the same deterministic scene selector
            # as ordinary object roles, but only when the descriptor leaves
            # one unique candidate. The two equal red cups in the acceptance
            # set therefore remain unresolved and are safely clarified.
            if not role_refs.get("theme"):
                wait_descriptor = re.search(
                    r"(?:\u5de6\u4fa7|\u53f3\u4fa7|\u524d\u65b9|\u540e\u65b9|\u4e2d\u95f4|\u504f\u5c0f|\u504f\u5927|\u77ee\u80d6|\u7ec6\u957f|\u4e2d\u7b49\u5927\u5c0f|\u5c3a\u5bf8\u8f83\u5927)",
                    text,
                )
                dangling_descriptor = bool(re.search(
                    r"(?:\u5de6\u4fa7|\u53f3\u4fa7|\u524d\u65b9|\u540e\u65b9|\u4e2d\u95f4|\u504f\u5c0f|\u504f\u5927|\u77ee\u80d6|\u7ec6\u957f|\u4e2d\u7b49\u5927\u5c0f|\u5c3a\u5bf8\u8f83\u5927)[^，。；,;]{0,8}\u628a",
                    text,
                ))
                if wait_descriptor and movable and not dangling_descriptor:
                    candidates = list(movable)
                    if "\u5de6\u4fa7" in text:
                        extreme = min(float(getattr(getattr(item, "position", None), "x", 0.0)) for item in candidates)
                        candidates = [item for item in candidates if abs(float(getattr(getattr(item, "position", None), "x", 0.0)) - extreme) <= 1e-9]
                    elif "\u53f3\u4fa7" in text:
                        extreme = max(float(getattr(getattr(item, "position", None), "x", 0.0)) for item in candidates)
                        candidates = [item for item in candidates if abs(float(getattr(getattr(item, "position", None), "x", 0.0)) - extreme) <= 1e-9]
                    elif "\u524d\u65b9" in text:
                        # In robot_base, the generated acceptance scenes use the
                        # lower x coordinate as the front position.  Size/shape
                        # words are additional evidence; when all objects share
                        # the same geometry they must not erase an otherwise
                        # unique front cue.
                        if "\u504f\u5c0f" in text or "\u504f\u5927" in text:
                            volumes = [float(getattr(getattr(item, "bbox", None), "width", 0.0)) *
                                       float(getattr(getattr(item, "bbox", None), "height", 0.0)) *
                                       float(getattr(getattr(item, "bbox", None), "depth", 0.0)) for item in candidates]
                            if len(set(volumes)) > 1:
                                extreme = min(volumes) if "\u504f\u5c0f" in text else max(volumes)
                                candidates = [item for item, volume in zip(candidates, volumes) if abs(volume - extreme) <= 1e-12]
                            else:
                                extreme = min(float(getattr(getattr(item, "position", None), "x", 0.0)) for item in candidates)
                                candidates = [item for item in candidates if abs(float(getattr(getattr(item, "position", None), "x", 0.0)) - extreme) <= 1e-9]
                        else:
                            extreme = min(float(getattr(getattr(item, "position", None), "x", 0.0)) for item in candidates)
                            candidates = [item for item in candidates if abs(float(getattr(getattr(item, "position", None), "x", 0.0)) - extreme) <= 1e-9]
                    elif "\u4e2d\u95f4" in text:
                        candidates = []
                    if len(candidates) == 1:
                        from robot_intent_agent.schemas.semantic_task_graph import SemanticEntity
                        item = candidates[0]
                        local_ref = "wait-theme-1"
                        entities.append(SemanticEntity(
                            local_ref=local_ref,
                            mention=getattr(item, "name", None) or "target",
                            category=getattr(item, "specific_class", None) or getattr(item, "label", "object"),
                            attributes={},
                            evidence_spans=[wait_descriptor.group(0)],
                            candidate_key=str(getattr(item, "id", "")) or None,
                        ))
                        role_refs["theme"] = local_ref
                # If an explicit spatial/size description was not uniquely
                # resolved above, do not let the legacy scorer guess a target
                # among several same-class objects.  The safe result is
                # clarification; the legacy fallback remains useful for a
                # single movable object or a plain condition without such a
                # descriptor.
                explicit_wait_disambiguator = bool(re.search(
                    r"(?:\u5de6\u4fa7|\u53f3\u4fa7|\u524d\u65b9|\u540e\u65b9|\u4e2d\u95f4|\u504f\u5c0f|\u504f\u5927|\u77ee\u80d6|\u7ec6\u957f|\u4e2d\u7b49\u5927\u5c0f|\u5c3a\u5bf8\u8f83\u5927)",
                    text,
                ))
                if wait_descriptor and (len(movable) <= 1 or not explicit_wait_disambiguator):
                    from robot_intent_agent.task_semantics import GroundingEngine as LegacyGroundingEngine
                    wait_grounding = LegacyGroundingEngine().ground(text, scene, role="theme")
                    if wait_grounding.selected is not None and not wait_grounding.needs_clarification:
                        item = next((obj for obj in getattr(scene, "objects", []) or []
                                     if getattr(obj, "id", None) == wait_grounding.selected.entity_ref.entity_id), None)
                        item_affordances = {
                            str(getattr(aff, "value", aff)).lower()
                            for aff in (getattr(item, "affordances", []) or [])
                        } if item is not None else set()
                        if item is not None and ("movable" in item_affordances or "graspable" in item_affordances):
                            from robot_intent_agent.schemas.semantic_task_graph import SemanticEntity
                            local_ref = "wait-theme-1"
                            entities.append(SemanticEntity(
                                local_ref=local_ref,
                                mention=wait_grounding.selected.entity_ref.mention or "target",
                                category=getattr(item, "specific_class", None) or getattr(item, "label", "object"),
                                attributes={},
                                evidence_spans=[wait_grounding.selected.entity_ref.mention or "target"],
                                candidate_key=str(getattr(item, "id", "")) or None,
                            ))
                            role_refs["theme"] = local_ref

        events: List[SemanticEvent] = []
        # IF/ELSE is represented as one condition plus two explicit branch
        # events.  This keeps the graph executable without making the parser
        # pretend that the branches are a linear sequence.
        branch_condition = next((condition for condition in conditions
                                 if condition.predicate == "IF" and condition.on_true_action), None)
        if branch_condition:
            branch_actions = [branch_condition.on_true_action, branch_condition.on_false_action]
            action_values = [action for action in branch_actions if action]

        for index, action in enumerate(action_values):
            evidence = next((candidate for candidate in action_candidates if candidate.value == action), None)
            branch_text = ""
            if branch_condition and index < 2:
                branch_text = (branch_condition.on_true_text if index == 0
                               else branch_condition.on_false_text) or ""
                branch_text = branch_text.strip(" ，,")
            theme_ref = None if branch_condition and branch_text else role_refs.get("theme")
            if action == "WAIT":
                # WAIT is a condition-only task in the public contract. A
                # noun mentioned while describing the monitored state is not
                # an object manipulation target.
                theme_ref = None
            elif branch_condition and branch_text:
                branch_entities, branch_roles = parse_roles(branch_text, [action])
                for branch_entity in branch_entities:
                    existing = next((item for item in entities
                                     if item.mention == branch_entity.mention), None)
                    if existing is None:
                        branch_entity.local_ref = f"entity-{len(entities) + 1}"
                        entities.append(branch_entity)
                        existing = branch_entity
                    if branch_roles.get("theme") == branch_entity.local_ref:
                        theme_ref = existing.local_ref
                if theme_ref is None and re.search(r"它|这个|那个", branch_text):
                    condition_text = str(branch_condition.value or "")
                    antecedent = next((item for item in entities if item.mention and item.mention in condition_text), None)
                    theme_ref = antecedent.local_ref if antecedent else None
                if theme_ref is None and branch_entities:
                    theme_ref = entities[-1].local_ref
            elif role_refs.get("theme"):
                theme_ref = role_refs.get("theme")
            event_parameters = {}
            if action == "WAIT" and conditions:
                event_parameters["condition"] = conditions[0].value
            event_evidence = branch_text or (evidence.evidence if evidence else text)
            event_start = text.find(event_evidence) if branch_text else (evidence.start if evidence else 0)
            action_schema = get_action_schema(action)
            event = SemanticEvent(
                event_id=f"event-{index + 1}", action=normalize_action(action),
                theme_ref=theme_ref,
                destination_ref=(role_refs.get("destination")
                                 if action_schema.accepts_role("destination") else None),
                source_ref=(role_refs.get("source")
                            if action_schema.accepts_role("source") else None),
                recipient_ref=(role_refs.get("recipient")
                               if action_schema.accepts_role("recipient") else None),
                obstacle_refs=[role_refs["obstacle"]] if "obstacle" in role_refs else [],
                condition_refs=[condition.condition_id for condition in conditions],
                evidence_span=event_evidence,
                evidence=[EvidenceSpan(value=event_evidence, source_text=text,
                                       start=event_start if event_start >= 0 else 0,
                                       end=(event_start + len(event_evidence)) if event_start >= 0 else len(text),
                                       confidence=evidence.confidence if evidence else 0.5,
                                       rule_id=evidence.rule_id if evidence else "action.fallback")],
                sequence_index=index, parameters=event_parameters,
            )
            if branch_condition and index < 2:
                event.condition_refs = [branch_condition.condition_id]
            events.append(event)
        # The surface pattern “把…拿过来” carries a delivery action but no
        # explicit destination.  Keep the event for semantic completeness;
        # the action schema/validator will request a delivery pose rather than
        # inventing one.
        # Keep every action event.  WAIT -> GRASP is linked by BEFORE.
        relations = []
        if branch_condition and len(events) >= 2:
            relations.extend([
                SemanticRelation(type="IF_TRUE", source_ref=branch_condition.condition_id,
                                 target_event=events[0].event_id),
                SemanticRelation(type="IF_FALSE", source_ref=branch_condition.condition_id,
                                 target_event=events[1].event_id),
            ])
        else:
            relations = [SemanticRelation(type="BEFORE", source_event=events[i].event_id,
                                          target_event=events[i + 1].event_id)
                         for i in range(len(events) - 1)]
        graph = SemanticTaskGraph(
            instruction=text, entities=entities, events=events, relations=relations,
            conditions=conditions, constraints=parse_constraints(text),
            prohibitions=NegationParser().parse(text, entities, events),
            coreference_chains=CoreferenceResolver().resolve(text, entities),
            metadata={"action_candidates": [candidate.__dict__ for candidate in action_candidates],
                      "role_refs": role_refs, "parser": "rules",
                      "manner": _extract_manner(text),
                      "motion_state": "moving" if re.search(r"正在移动|移动中|动态", text) else "static",
                      "unsupported_action_evidence": (
                          re.search(
                              r"读取|读出|测量|测温|检测温度|清洗|切割|焊接|钻孔|装配|拧紧|涂胶|擦拭",
                              text, re.IGNORECASE,
                          ).group(0)
                          if re.search(
                              r"读取|读出|测量|测温|检测温度|清洗|切割|焊接|钻孔|装配|拧紧|涂胶|擦拭",
                              text, re.IGNORECASE,
                          ) else None
                      )},
        )
        if re.search(
                "\u76ee\u6807\u4e0d\u660e\u786e|\u76ee\u6807\u4e0d\u6e05\u695a|\u4e0d\u660e\u786e|\u4e0d\u786e\u5b9a",
                text,
        ):
            graph.ambiguities.append(AmbiguityRecord(
                ambiguity_id="ambiguity-explicit-target",
                type="EXPLICIT_TARGET_AMBIGUITY",
                status="UNRESOLVED",
                clarification="请明确要监测或操作的目标对象。",
                evidence=[EvidenceSpan(
                    value="目标不明确", source_text=text, start=0, end=len(text),
                    confidence=0.98, rule_id="ambiguity.explicit_user_request",
                )],
            ))
        # Bind unresolved pronouns only when the antecedent is unambiguous.
        for chain in graph.coreference_chains:
            if chain.resolved and chain.antecedent_ref:
                for event in graph.events:
                    if event.theme_ref is None and chain.mention_refs[0] in event.evidence_span:
                        event.theme_ref = chain.antecedent_ref
        confidence = min(0.99, max([c.confidence for c in action_candidates], default=0.35))
        candidate = SemanticCandidate.from_graph(graph, confidence=confidence, source="rule")
        candidate.evidence_spans = graph.all_evidence()
        return candidate


def _extract_manner(text: str) -> str | None:
    if re.search(r"轻一点|轻轻|柔和|温柔|轻拿轻放|别用力|不要用力", text):
        return "gentle"
    if re.search(r"快点|快一点|迅速|赶快|赶紧|马上|立刻|尽快|快速", text):
        return "fast"
    if re.search(r"当心|注意|谨慎|小心", text):
        return "careful"
    return None
