"""Negation parser with explicit scope and propagation metadata."""

from __future__ import annotations

import re
from typing import List, Optional

from robot_intent_agent.schemas.semantic_task_graph import (
    EvidenceSpan, SemanticEntity, SemanticEvent, SemanticProhibition,
)


class NegationParser:
    NEGATION = r"不要|别|禁止|避免|不能|不可|不许|不想|avoid|don't|do not|must not|never|not"

    def parse(self, instruction: str, entities: List[SemanticEntity],
              events: List[SemanticEvent]) -> List[SemanticProhibition]:
        text = instruction or ""
        result: List[SemanticProhibition] = []
        last_explicit_entity: Optional[str] = None
        for entity in entities:
            if entity.mention and entity.mention in text:
                last_explicit_entity = entity.local_ref

        # State-negated action clauses are safety-critical even when they do
        # not use the ordinary ``不要/禁止`` prefix.  For example,
        # ``拿起杯子，同时保持它没有被拿起`` explicitly requires and forbids
        # the same transition.  Preserve that contradiction as a prohibition
        # on the already parsed theme; the validator will block execution.
        contradictory_action = re.compile(
            r"(?:同时|并且|但|却)?\s*(?:保持|确保|维持|仍然保持)"
            r"[^，。；,;]{0,18}?(?:没有|未|不再|不能)"
            r"[^，。；,;]{0,12}?(?:拿起|抓住|抓取|拿住|握住|夹住|提起|提起来|放下|放置|移动)",
        )
        for index, match in enumerate(contradictory_action.finditer(text)):
            target_ref = next((event.theme_ref for event in events if event.theme_ref), None)
            evidence = EvidenceSpan(
                value=match.group(0), source_text=text, start=match.start(),
                end=match.end(), confidence=0.99, rule_id="negation.action_contradiction",
            )
            result.append(SemanticProhibition(
                prohibition_id=f"contradiction-{index + 1}",
                type="FORBID_ACTION", target_ref=target_ref,
                scope_event_ids=[event.event_id for event in events],
                evidence_span=match.group(0), evidence=[evidence], propagated_to=[],
            ))
        negation_pattern = (
            r"(?:" + self.NEGATION + r")"
            r"(?:拿|取|抓|碰|接触|touch|contact|touching|碰倒)?"
            r"\s*(?:[红蓝绿黄白黑透明]色?的?|[^，。；,;]+?)"
            r"(?=[，。；,;]|$)"
        )
        for index, match in enumerate(re.finditer(negation_pattern, text, re.IGNORECASE)):
            span = match.group(0)
            # ``同类物体/旁边的同类对象`` is a vague peer constraint, not a
            # uniquely bindable entity reference. Keep it as provenance only;
            # explicit named objects continue through the normal safety path.
            if re.search(r"(?:旁边的|附近的|周围的)?(?:同类物体|同类对象|同样的物体|同类目标)", span):
                continue
            if re.search(r"(?:力|力量|抓力|force|velocity|速度|不超过|最多|至少|超过|低于|高于|<=|>=|<|>)\s*\d", span, re.IGNORECASE):
                continue
            target_ref: Optional[str] = None
            # Prefer an entity whose mention appears in the negated clause.
            for entity in entities:
                if entity.mention and entity.mention in span:
                    target_ref = entity.local_ref
                    break
            if target_ref is None:
                # Adjectival targets such as “红色的” may be represented by
                # an attribute-only entity candidate.
                color = next((name for name in ("红色", "蓝色", "绿色", "黄色", "白色", "黑色", "透明") if name in span), None)
                if color:
                    target_ref = next((entity.local_ref for entity in entities
                                       if entity.attributes.get("color") == {
                                           "红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
                                           "白色": "white", "黑色": "black", "透明": "transparent",
                                       }.get(color)), None)
            if target_ref is None and span:
                # The role parser may not have an object noun for an adjective
                # phrase; create no new entity here, but preserve the explicit
                # unresolved target so final validation blocks safely.
                target_ref = next((entity.local_ref for entity in entities
                                   if entity.mention and entity.mention in span), None)
                for entity in entities:
                    if any(token in span for token in entity.evidence_spans):
                        target_ref = entity.local_ref
                        break
            # A pronoun-only prohibition (“不要碰到它”) resolves to the
            # nearest explicit entity only when there is exactly one such
            # antecedent.  Do not guess among multiple candidates.
            if target_ref is None and re.search(r"它|这个|那个", span):
                explicit = [entity.local_ref for entity in entities
                            if entity.mention and entity.mention in text[:match.start()]]
                if len(explicit) == 1:
                    target_ref = explicit[0]
            if target_ref is None:
                # The role parser may intentionally keep the prohibition
                # target outside the main theme role.  Match the noun phrase
                # after the negation verb against all raw entities.
                clause = re.sub(r"^(?:" + self.NEGATION + r")(?:拿|取|抓|碰|接触)?", "", span).strip()
                for entity in entities:
                    if entity.mention and (entity.mention in clause or clause in entity.mention):
                        target_ref = entity.local_ref
                        break
            ptype = "NO_CONTACT" if any(word.lower() in span.lower() for word in ("碰", "接触", "撞", "avoid", "touch", "contact")) else "FORBID_ACTION"
            evidence = EvidenceSpan(value=span, source_text=text, start=match.start(), end=match.end(),
                                    confidence=0.96, rule_id="negation.scope")
            result.append(SemanticProhibition(
                prohibition_id=f"p{index + 1}", type=ptype, target_ref=target_ref,
                scope_event_ids=[event.event_id for event in events], evidence_span=span,
                evidence=[evidence], propagated_to=[],
            ))
        return result
