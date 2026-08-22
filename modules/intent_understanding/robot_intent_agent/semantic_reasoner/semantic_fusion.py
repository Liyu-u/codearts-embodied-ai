"""Field-wise fusion of rule and LLM semantic candidates.

Rule evidence is a safety floor.  LLM data can add a field only when it has
text evidence, and it can never alter IDs, prohibitions, hard constraints or
execution state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Optional

from robot_intent_agent.domain.action_schemas import ACTION_SCHEMAS, get_action_schema, normalize_action
from robot_intent_agent.schemas.semantic_task_graph import SemanticCandidate, SemanticTaskGraph
from robot_intent_agent.semantic_parser.action_parser import parse_action_candidates


@dataclass
class FusionAuditRecord:
    field: str
    rule_value: Any
    llm_value: Any
    final_value: Any
    decision: str
    evidence: str = ""

    def model_dump(self) -> Dict[str, Any]:
        return self.__dict__.copy()


class SemanticFusion:
    PROTECTED_FIELDS = {
        "prohibitions", "constraints", "conditions", "relations", "entity_id",
        "execution_allowed", "plan_status", "grounding_decisions",
    }

    def _has_evidence(self, value: Any, instruction: str) -> str:
        if isinstance(value, list):
            for item in value:
                evidence = self._has_evidence(item, instruction)
                if evidence:
                    return evidence
            return ""
        if isinstance(value, dict):
            for key in ("evidence_span", "evidence", "source_text", "mention"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate and candidate in instruction:
                    return candidate
                if isinstance(candidate, list):
                    for item in candidate:
                        if isinstance(item, dict):
                            text = item.get("source_text") or item.get("value")
                            if isinstance(text, str) and text and text in instruction:
                                return text
                        elif isinstance(item, str) and item in instruction:
                            return item
            return ""
        return str(value) if isinstance(value, str) and value in instruction else ""

    @staticmethod
    def _has_explicit_obstacle_language(instruction: str) -> bool:
        """Return whether the user explicitly asked for avoidance/contact control."""
        text = str(instruction or "")
        return bool(re.search(
            r"不要\s*(?:碰|接触|触碰)|不能\s*(?:碰|接触|触碰)|不得\s*(?:碰|接触|触碰)|"
            r"避免|避开|远离|避让|不许\s*(?:碰|接触)|don't\s+touch|avoid|no\s+contact",
            text,
            flags=re.IGNORECASE,
        ))

    @staticmethod
    def _value(graph: SemanticTaskGraph, field: str):
        return getattr(graph, field, None)

    @staticmethod
    def _align_entity_refs(rule_graph: SemanticTaskGraph, llm_graph: SemanticTaskGraph) -> Dict[str, str]:
        """Map provider-local references onto rule-local references.

        LLM candidates are allowed to choose their own labels (e1, target-1,
        ...).  Those labels must never leak into the final graph when the
        corresponding rule entity already exists.  The previous merger joined
        the entity records but left event references untouched, creating
        UNKNOWN_ENTITY_REF errors and discarding otherwise useful candidates.
        """
        aliases: Dict[str, str] = {}
        for incoming in llm_graph.entities:
            mention_matches = [current for current in rule_graph.entities
                               if incoming.mention and current.mention == incoming.mention]
            match = mention_matches[0] if len(mention_matches) == 1 else None
            if match is None and incoming.category:
                compatible = [current for current in rule_graph.entities
                              if current.category == incoming.category and
                              all(current.attributes.get(key) in (None, value)
                                  for key, value in (incoming.attributes or {}).items())]
                # Only rewrite provider-local references when the descriptor
                # identifies one deterministic rule entity.  If several
                # same-category objects remain possible, keep the provider
                # local atom so GroundingEngine can resolve or reject it.
                match = compatible[0] if len(compatible) == 1 else None
            if match is not None:
                aliases[incoming.local_ref] = match.local_ref
        if not aliases:
            return aliases

        def ref(value: Optional[str]) -> Optional[str]:
            return aliases.get(value, value) if value else value

        for event in llm_graph.events:
            event.theme_ref = ref(event.theme_ref)
            event.destination_ref = ref(event.destination_ref)
            event.source_ref = ref(event.source_ref)
            event.recipient_ref = ref(event.recipient_ref)
            event.obstacle_refs = [ref(item) or item for item in event.obstacle_refs]
        for item in llm_graph.prohibitions:
            item.target_ref = ref(item.target_ref)
        for item in llm_graph.conditions:
            item.subject_ref = ref(item.subject_ref)
        for item in llm_graph.relations:
            item.source_ref = ref(item.source_ref)
            item.target_ref = ref(item.target_ref)
        return aliases

    @staticmethod
    def _align_event_refs(rule_graph: SemanticTaskGraph, llm_graph: SemanticTaskGraph) -> Dict[str, str]:
        """Map provider event IDs/aliases onto deterministic event IDs."""
        rule_events = sorted(rule_graph.events, key=lambda item: item.sequence_index)
        llm_events = sorted(llm_graph.events, key=lambda item: item.sequence_index)
        aliases: Dict[str, str] = {}
        for index, incoming in enumerate(llm_events):
            target = rule_events[index] if index < len(rule_events) else None
            if target is None:
                target = next((item for item in rule_events
                               if item.action == incoming.action), None)
            if target is None:
                continue
            aliases[incoming.event_id] = target.event_id
            # The event ID is the merge key.  Rewriting only references in
            # conditions/relations leaves the provider event as a new event,
            # bypassing action-lock and role-preservation logic.  Normalize
            # the provider atom itself before collection fusion.
            incoming.event_id = target.event_id
            aliases[str(index)] = target.event_id
            aliases[str(index + 1)] = target.event_id
            for prefix in ("event", "evt"):
                aliases[f"{prefix}-{index}"] = target.event_id
                aliases[f"{prefix}-{index + 1}"] = target.event_id
            action_slug = str(incoming.action or "").lower()
            if action_slug:
                aliases[f"event-{action_slug}-{index + 1}"] = target.event_id
                aliases[f"evt-{action_slug}-{index + 1}"] = target.event_id

        known = {item.event_id for item in rule_events}

        def resolve(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            return aliases.get(str(value), value)

        for item in llm_graph.relations:
            item.source_event = resolve(item.source_event)
            item.target_event = resolve(item.target_event)
        for item in llm_graph.conditions:
            item.on_true_event_ids = [resolve(value) or value for value in item.on_true_event_ids]
            item.on_false_event_ids = [resolve(value) or value for value in item.on_false_event_ids]
        for item in llm_graph.prohibitions:
            item.scope_event_ids = [resolve(value) or value for value in item.scope_event_ids]
        return aliases

    @staticmethod
    def _event_match(rule_items: List[Any], incoming: Any) -> Optional[Any]:
        return next((item for item in rule_items
                     if item.sequence_index == incoming.sequence_index
                     or (item.evidence_span and item.evidence_span == incoming.evidence_span)), None)

    @staticmethod
    def _allow_action_correction(current: Any, incoming: Any, graph: SemanticTaskGraph,
                                 evidence: str) -> bool:
        """Allow ordinary semantic correction while keeping role safety.

        A specialized action may replace a generic rule action when the LLM
        supplies text evidence and at least the required roles for the new
        action.  It cannot replace a valid action with CUSTOM or an action
        whose required role references are missing.
        """
        proposed = normalize_action(incoming.action)
        existing = normalize_action(current.action)
        if proposed == existing or proposed == "CUSTOM" or not evidence:
            return proposed == existing
        if existing == "CUSTOM":
            # A generic rule candidate is intentionally non-committal.  Do
            # not let a provider turn words such as “处理一下/操作一下” into
            # a physical manipulation merely because an object is present.
            # An upgrade from CUSTOM is valid only when the exact evidence
            # span contains a supported action trigger recognized by the
            # deterministic action lexicon.
            explicit_actions = parse_action_candidates(evidence)
            if not any(normalize_action(item.value) == proposed
                       for item in explicit_actions):
                return False
        # Action correction is allowed at the semantic layer.  The old
        # implementation treated every deterministic action as immutable,
        # which made the LLM unable to correct the exact errors it was meant
        # to solve (FETCH/TRANSFER/PLACE, STACK/PLACE, and dynamic grasp).
        # Safety is retained by requiring source evidence and validating the
        # proposed template's roles below; the scene grounder remains the
        # identity authority.
        wait_evidence = bool(re.search(
            r"等待|等到|直到|暂缓|先别动作|先不动作|保持当前状态|保持等待|"
            r"停止|静止|稳定|不再变化|运动结束|wait|stable",
            evidence or "", re.IGNORECASE,
        ))
        if proposed == "WAIT":
            return bool(graph.conditions and any(
                item.predicate == "WAIT_UNTIL" for item in graph.conditions
            ) and wait_evidence)
        if existing == "WAIT":
            # A condition-only WAIT is never upgraded to a physical action by
            # a provider's incidental object or motion mention.
            return False
        schema = get_action_schema(proposed)
        refs = {
            "theme": incoming.theme_ref,
            "destination": incoming.destination_ref,
            "source": incoming.source_ref,
            "recipient": incoming.recipient_ref,
        }
        if schema.missing_roles(name for name, value in refs.items() if value):
            return False
        # Never let a generic provider answer erase a specialized rule event.
        specialized = {"DYNAMIC_GRASP", "HANDOVER", "TRANSFER", "FETCH", "POUR", "STACK"}
        if existing == "TRANSFER" and proposed == "PLACE" and re.search(
                r"\u6536\u8fdb|\u6536\u7eb3|\u6536\u5165|\u88c5\u8fdb|\u653e\u5165|\u653e\u8fdb|\u653e\u56de|\u9001\u5165|\u843d\u5165|\u5f52\u5165|\u627f\u6258|\u5185|\u8868\u9762|\u4e0a\u9762|put|place",
                evidence or "", re.IGNORECASE):
            return True
        if existing == "PLACE" and proposed == "TRANSFER" and re.search(
                r"收纳|收入|放入|放进|装进|装入|归入|安置到|置入|放回|落在|承托面|支撑面|里面|内部|上面|put|place",
                evidence or "", re.IGNORECASE):
            return False
        if existing == "FETCH" and proposed == "TRANSFER" and re.search(
                r"取出|取回|送回|带回|拿回|拿来|带来|取到|带到[^，。；,;]{0,20}(?:收纳箱|接收|收取|回收|机器人|身边|这边|托盘)",
                evidence or "", re.IGNORECASE):
            # FETCH is the receive-endpoint delivery template. Do not let a
            # generic provider rewrite “bring back to the bin/robot” as a
            # plain TRANSFER when the deterministic candidate already
            # carries the endpoint semantics.
            return False
        if existing in specialized and proposed in {"GRASP", "PLACE"}:
            return False
        if existing in specialized and proposed not in specialized:
            return False
        return True

    def fuse(self, rule: SemanticCandidate, llm: Optional[SemanticCandidate], instruction: str = "") -> tuple[SemanticCandidate, List[FusionAuditRecord]]:
        if llm is None:
            return rule, []
        rule_graph = rule.graph.model_copy(deep=True)
        llm_graph = llm.graph.model_copy(deep=True)
        aliases = self._align_entity_refs(rule_graph, llm_graph)
        self._align_event_refs(rule_graph, llm_graph)
        audit: List[FusionAuditRecord] = []
        # Graph collections are merged by stable IDs.  Rule records win when
        # both sides carry evidence; LLM records can enrich empty slots.
        for field in ("entities", "events", "relations", "conditions", "constraints", "prohibitions", "coreference_chains", "ambiguities"):
            rule_items = list(getattr(rule_graph, field) or [])
            llm_items = list(getattr(llm_graph, field) or [])
            if not llm_items:
                continue
            key = "local_ref" if field == "entities" else ("event_id" if field == "events" else ("condition_id" if field == "conditions" else ("constraint_id" if field == "constraints" else ("prohibition_id" if field == "prohibitions" else "type"))))
            index = {getattr(item, key, None): item for item in rule_items}
            for incoming in llm_items:
                marker = getattr(incoming, key, None)
                current = index.get(marker)
                if current is None and field == "entities":
                    mention_matches = [item for item in rule_items
                                       if incoming.mention and item.mention == incoming.mention]
                    if len(mention_matches) == 1:
                        current = mention_matches[0]
                    else:
                        compatible = [item for item in rule_items
                                      if incoming.category and item.category == incoming.category and
                                      all(item.attributes.get(key) in (None, value)
                                          for key, value in (incoming.attributes or {}).items())]
                        current = compatible[0] if len(compatible) == 1 else None
                if current is None and field == "events":
                    current = self._event_match(rule_items, incoming)
                if current is None and field == "prohibitions":
                    current = next((item for item in rule_items
                                    if item.type == incoming.type and
                                    item.target_ref == incoming.target_ref), None)
                if current is None and field == "events":
                    # A provider event may use a different event_id but its
                    # sequence/evidence can identify the deterministic event.
                    # Never accept a new event whose role refs are unknown to
                    # the rule graph; this prevents an incomplete candidate
                    # from becoming the only final event.
                    # Entities are merged before events.  A valid LLM event
                    # may therefore refer to a local entity that was absent
                    # from the rule candidate.  Reject only references that
                    # are absent from the combined semantic graph; requiring
                    # them to exist in the rule graph was the old gate that
                    # made LLM-only action/role recovery impossible.
                    known_entities = {
                        item.local_ref for item in (*rule_graph.entities, *llm_graph.entities)
                    }
                    refs = [incoming.theme_ref, incoming.destination_ref,
                            incoming.source_ref, incoming.recipient_ref,
                            *incoming.obstacle_refs]
                    if any(ref and ref not in known_entities for ref in refs):
                        audit.append(FusionAuditRecord(
                            f"{field}.{marker}", None, incoming.model_dump(mode="json"), None,
                            "REJECT_LLM_UNKNOWN_REF", ""))
                        continue
                evidence = self._has_evidence(incoming.model_dump(mode="json"), instruction)
                if current is None:
                    if not evidence and field in self.PROTECTED_FIELDS:
                        audit.append(FusionAuditRecord(field, None, incoming.model_dump(mode="json"), None, "REJECT_LLM_NO_EVIDENCE", ""))
                        continue
                    rule_items.append(deepcopy(incoming)); index[marker] = rule_items[-1]
                    audit.append(FusionAuditRecord(field, None, incoming.model_dump(mode="json"), incoming.model_dump(mode="json"), "ACCEPT_LLM_DELTA", evidence))
                    continue
                current_data = current.model_dump(mode="json")
                incoming_data = incoming.model_dump(mode="json")
                final_data = dict(current_data)
                if field == "events":
                    if self._allow_action_correction(current, incoming, rule_graph, evidence):
                        final_data["action"] = normalize_action(incoming_data.get("action"))
                    # Ordinary semantic roles may be corrected or filled by
                    # the LLM, but an explicitly established obstacle/event
                    # relation is never removed.
                    for name in ("theme_ref", "destination_ref", "source_ref", "recipient_ref",
                                 "condition_refs", "parameters"):
                        value = incoming_data.get(name)
                        if value not in (None, "", [], {}):
                            if name.endswith("_ref"):
                                # A complete deterministic action is a
                                # semantic floor.  The provider may fill a
                                # missing required role, but it must not add
                                # an optional role merely because its
                                # extraction schema has a slot for it.  This
                                # prevents e.g. interpreting "交给托盘" as
                                # both destination and recipient, which
                                # grounds the same physical object twice and
                                # can turn a valid TRANSFER into BLOCKED.
                                role = name[:-4]
                                current_refs = {
                                    role_name for role_name, ref in {
                                        "theme": current_data.get("theme_ref"),
                                        "destination": current_data.get("destination_ref"),
                                        "source": current_data.get("source_ref"),
                                        "recipient": current_data.get("recipient_ref"),
                                }.items() if ref
                                }
                                schema = get_action_schema(normalize_action(current.action))
                                if (normalize_action(current.action) != "CUSTOM" and
                                        not schema.accepts_role(role)):
                                    audit.append(FusionAuditRecord(
                                        f"{field}.{marker}.{name}", current_data.get(name), value,
                                        current_data.get(name), "REJECT_LLM_UNSUPPORTED_ROLE", evidence
                                    ))
                                    continue
                                generic_fetch_destination = (
                                    normalize_action(current.action) == "FETCH" and
                                    role == "recipient" and
                                    current_data.get("destination_ref") and
                                    self._is_unresolved_generic_entity(
                                        rule_graph, current_data.get("destination_ref")
                                    )
                                )
                                if (current_data.get(name) in (None, "", [], {}) and
                                        role not in schema.required_roles and
                                        not schema.missing_roles(current_refs) and
                                        not generic_fetch_destination):
                                    audit.append(FusionAuditRecord(
                                        f"{field}.{marker}.{name}", current_data.get(name), value,
                                        current_data.get(name), "REJECT_LLM_OPTIONAL_ROLE", evidence
                                    ))
                                    continue
                            if name.endswith("_ref"):
                                known_refs = {
                                    item.local_ref for item in (*rule_graph.entities, *llm_graph.entities)
                                }
                                if value not in known_refs:
                                    audit.append(FusionAuditRecord(
                                        f"{field}.{marker}.{name}", current_data.get(name), value,
                                        current_data.get(name), "REJECT_LLM_UNKNOWN_REF", ""))
                                    continue
                            if name == "condition_refs":
                                final_data[name] = list(dict.fromkeys(
                                    list(final_data.get(name) or []) + list(value or [])))
                            else:
                                final_data[name] = value
                    final_data["obstacle_refs"] = list(dict.fromkeys(
                        list(final_data.get("obstacle_refs") or []) +
                        (list(incoming_data.get("obstacle_refs") or [])
                         if self._has_explicit_obstacle_language(instruction) else [])))
                    # WAIT has a condition role only.  If an action correction
                    # changes a manipulation event into WAIT, clear inherited
                    # manipulation roles instead of exposing a stale target
                    # from the rule candidate.
                    if normalize_action(final_data.get("action")) == "WAIT":
                        final_data["theme_ref"] = None
                        final_data["destination_ref"] = None
                        final_data["source_ref"] = None
                        final_data["recipient_ref"] = None
                        final_data["obstacle_refs"] = []
                else:
                    for name, value in incoming_data.items():
                        if name in {"entity_id", "execution_allowed", "plan_status"}:
                            continue
                        if final_data.get(name) in (None, "", [], {}):
                            final_data[name] = value
                updated = type(current).model_validate(final_data)
                index[marker] = updated
                rule_items[rule_items.index(current)] = updated
                if incoming_data != current_data:
                    audit.append(FusionAuditRecord(f"{field}.{marker}", current_data, incoming_data,
                                                    updated.model_dump(mode="json"),
                                                    "MERGE_RULE_WITH_LLM_DELTA" if evidence else "KEEP_RULE",
                                                    evidence))
            setattr(rule_graph, field, rule_items)
        result = SemanticCandidate.from_graph(rule_graph,
                                              confidence=max(rule.confidence, llm.confidence), source="rule+llm")
        result.candidate_key = rule.candidate_key or llm.candidate_key
        return result, audit

    @staticmethod
    def _is_unresolved_generic_entity(graph: SemanticTaskGraph,
                                       local_ref: Optional[str]) -> bool:
        entity = graph.entity(local_ref) if local_ref else None
        if entity is None:
            return False
        category = str(entity.category or "").lower()
        return bool(entity.attributes.get("_grounding_unresolved")) or category in {
            "object", "item", "unknown", "entity",
        }


def fuse_semantic_candidates(rule: SemanticCandidate, llm: Optional[SemanticCandidate], instruction: str = ""):
    return SemanticFusion().fuse(rule, llm, instruction)
