"""Single deterministic grounding authority."""

from __future__ import annotations

from typing import Any, Dict

from .candidate_retriever import CandidateRetriever
from .joint_assignment import JointGroundingSolver


class GroundingEngine:
    def __init__(self):
        self.retriever = CandidateRetriever()
        self.solver = JointGroundingSolver()

    def ground(self, role_queries: Dict[str, Dict[str, Any]], scene: Any, action: str = "CUSTOM"):
        candidates = {}
        for role, query in role_queries.items():
            candidates[role] = self.retriever.retrieve(
                scene, category=query.get("category"), attributes=query.get("attributes"),
                mention=query.get("mention"),
                exclude_ids=set(query.get("exclude_ids", [])),
            )
        # The perception contract uses recipient/robot_receive_zone as
        # semantic affordances in addition to the closed enum. Candidate
        # retrieval is broad, then the action contract narrows this role.
        return self.solver.solve(candidates, action=action, role_queries=role_queries)

    def ground_graph(self, graph, scene: Any):
        """Bind graph local references to scene IDs and return audit decisions."""
        if not graph.events:
            return graph, {}
        queries: Dict[str, Dict[str, Any]] = {}
        role_refs: Dict[str, str] = {}
        # Collect role bindings across every event.  The previous first-event
        # shortcut dropped later sequence/condition roles and made the graph
        # appear grounded while its BT was not.
        for event in graph.events:
            for role, local_ref in {
                "theme": event.theme_ref,
                "destination": event.destination_ref,
                "source": event.source_ref,
                "recipient": event.recipient_ref,
            }.items():
                if not local_ref or role in role_refs:
                    continue
                entity = graph.entity(local_ref)
                if entity is None:
                    continue
                role_refs[role] = local_ref
                queries[role] = {"category": entity.category, "attributes": entity.attributes,
                                 "mention": entity.mention}
            for local_ref in event.obstacle_refs or []:
                entity = graph.entity(local_ref)
                if entity is not None and "obstacle" not in role_refs:
                    role_refs["obstacle"] = local_ref
                    queries["obstacle"] = {
                        "category": entity.category,
                        "attributes": entity.attributes,
                        "mention": entity.mention,
                    }
        action = graph.events[0].action if graph.events else "CUSTOM"
        primary_queries = {role: query for role, query in queries.items() if role != "obstacle"}
        decisions = self.ground(primary_queries, scene, action=action) if primary_queries else {}
        excluded = {
            item.selected_entity_id for role, item in decisions.items()
            if role in {"theme", "destination", "source"} and item.selected_entity_id
        }
        # Scene-derived blocking is always part of the grounded graph.  It
        # does not require an NL avoidance verb: the spatial scene graph is
        # already the authoritative evidence for path safety.
        theme_decision = decisions.get("theme")
        if theme_decision and theme_decision.selected_entity_id:
            blocker_ids = list(getattr(scene, "blocking_objects", lambda _id: [])(
                theme_decision.selected_entity_id
            ))
            # Some perception feeds encode a fixture/obstacle as a fixed
            # object but omit an explicit BLOCKING relation.  It is still a
            # collision-relevant scene entity for manipulation safety.  Use
            # this conservative fallback only for obstacle-like categories;
            # ordinary tables and trays remain valid destinations.
            if not blocker_ids:
                obstacle_like = {"fixture", "obstacle", "table_edge", "hot_surface", "barrier"}
                blocker_ids = [
                    getattr(item, "id", "") for item in getattr(scene, "objects", []) or []
                    if getattr(item, "id", "") != theme_decision.selected_entity_id
                    and str(getattr(item, "specific_class", None)
                            or getattr(item, "label", None) or "").lower() in obstacle_like
                ]
            for blocker_id in blocker_ids:
                if blocker_id not in excluded:
                    blocker = scene.find_object(blocker_id) if hasattr(scene, "find_object") else None
                    if blocker is not None:
                        # An explicit NL obstacle already owns this role.  Do
                        # not append a second scene-derived entity for the
                        # same blocker: that duplicate local ref can remain
                        # ungrounded while the scene-derived ref is grounded.
                        # Keep the explicit query so its mention/category is
                        # resolved against the scene-owned object ID.
                        if role_refs.get("obstacle"):
                            break
                        local_ref = f"scene-obstacle-{blocker_id}"
                        if graph.entity(local_ref) is None:
                            from robot_intent_agent.schemas.semantic_task_graph import EvidenceSpan, SemanticEntity
                            graph.entities.append(SemanticEntity(
                                local_ref=local_ref, mention=blocker.name,
                                category=blocker.specific_class or blocker.label or blocker.name,
                                attributes={"scene_derived": True}, entity_id=blocker.id,
                                evidence_spans=[blocker.name],
                                evidence=[EvidenceSpan(value=blocker.name, source_text=graph.instruction,
                                                        confidence=0.95, rule_id="scene.blocking")],
                            ))
                        for event in graph.events:
                            if event.theme_ref == role_refs.get("theme") and local_ref not in event.obstacle_refs:
                                event.obstacle_refs.append(local_ref)
                        queries["obstacle"] = {"category": blocker.specific_class or blocker.label,
                                               "attributes": {"scene_derived": True},
                                               "mention": blocker.name, "exclude_ids": excluded}
                        role_refs["obstacle"] = local_ref
                        break
        if "obstacle" in queries:
            obstacle_query = dict(queries["obstacle"])
            obstacle_query["exclude_ids"] = excluded
            obstacle_entity = graph.entity(role_refs.get("obstacle"))
            spatial_relation = ((obstacle_entity.attributes or {}).get("spatial_relation")
                                if obstacle_entity else None)
            if spatial_relation and decisions.get("theme") and decisions["theme"].selected_entity_id:
                theme_id = decisions["theme"].selected_entity_id
                wanted = {spatial_relation, str(spatial_relation).lower()}
                related = {
                    rel.object for rel in getattr(scene, "relations", [])
                    if getattr(rel, "subject", None) == theme_id
                    and str(getattr(getattr(rel, "predicate", None), "value",
                                    getattr(rel, "predicate", ""))) in wanted
                }
                if related:
                    obstacle_query["exclude_ids"] = {
                        getattr(obj, "id", "") for obj in getattr(scene, "objects", [])
                        if getattr(obj, "id", "") not in related
                    }
            # This is an obstacle-only subproblem.  Reusing PLACE/FETCH here
            # would make the solver append missing required theme/destination
            # decisions and overwrite already-resolved primary roles.
            obstacle_decisions = self.ground({"obstacle": obstacle_query}, scene, action="CUSTOM")
            decisions["obstacle"] = obstacle_decisions.get("obstacle")
        for role, decision in decisions.items():
            local_ref = role_refs.get(role)
            entity = graph.entity(local_ref) if local_ref else None
            if entity is not None and decision.selected_entity_id:
                entity.entity_id = decision.selected_entity_id
        # Apply role binding to every occurrence, including later conditional
        # events that reference the same local entity.
        for entity in graph.entities:
            if not entity.entity_id:
                for role, local_ref in role_refs.items():
                    if entity.local_ref == local_ref and decisions.get(role):
                        entity.entity_id = decisions[role].selected_entity_id
        return graph, decisions
