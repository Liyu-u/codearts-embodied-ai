"""Conservative coreference resolution for the supported command domain."""

from __future__ import annotations

import re
from typing import List

from robot_intent_agent.schemas.semantic_task_graph import CoreferenceChain, EvidenceSpan, SemanticEntity


class CoreferenceResolver:
    def resolve(self, instruction: str, entities: List[SemanticEntity]) -> List[CoreferenceChain]:
        text = instruction or ""
        chains: List[CoreferenceChain] = []
        pronouns = list(re.finditer(r"它|这个|那个|该物体|其", text))
        for index, match in enumerate(pronouns):
            # Prefer the nearest explicit mention before the pronoun.  This
            # prevents a later branch object from stealing the antecedent of
            # “拿它，否则拿蓝杯”.
            prior = []
            for entity in entities:
                mention = entity.mention or ""
                position = text.rfind(mention, 0, match.start()) if mention else -1
                if position >= 0:
                    prior.append((position, entity.local_ref))
            antecedent = max(prior, default=(-1, None))[1]
            if antecedent is None and len(entities) == 1:
                antecedent = entities[0].local_ref
            evidence = EvidenceSpan(value=match.group(0), source_text=text,
                                    start=match.start(), end=match.end(), confidence=0.55,
                                    rule_id="coreference.last_mention")
            chains.append(CoreferenceChain(
                chain_id=f"coref-{index + 1}", mention_refs=[match.group(0)],
                antecedent_ref=antecedent, evidence=[evidence], resolved=antecedent is not None,
            ))
        return chains
