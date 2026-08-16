"""Orchestration boundary for deterministic semantic candidate generation."""

from __future__ import annotations

from .rule_semantic_parser import RuleSemanticParser


class SemanticPipeline:
    def __init__(self, rule_parser: RuleSemanticParser | None = None):
        self.rule_parser = rule_parser or RuleSemanticParser()

    def parse_rule(self, instruction: str, scene=None):
        return self.rule_parser.parse(instruction, scene=scene)

    def diagnostics(self, candidate, scene=None) -> dict:
        graph = candidate.graph
        action_complete = bool(graph.events) and all(event.action != "CUSTOM" for event in graph.events)
        roles_complete = True
        from robot_intent_agent.domain.action_schemas import get_action_schema
        for event in graph.events:
            schema = get_action_schema(event.action)
            roles = {name for name, ref in {
                "theme": event.theme_ref, "destination": event.destination_ref,
                "source": event.source_ref, "recipient": event.recipient_ref,
            }.items() if ref}
            roles_complete = roles_complete and not schema.missing_roles(roles)
        return {
            "action_complete": action_complete,
            "roles_complete": roles_complete,
            "negation_complete": not graph.prohibitions or all(p.target_ref for p in graph.prohibitions),
            "sequence_complete": len(graph.events) <= 1 or len(graph.relations) >= len(graph.events) - 1,
            "grounding_unique": all(entity.entity_id for entity in graph.entities if entity.candidate_key),
            "semantic_conflicts": graph.validate_local_references(),
        }
