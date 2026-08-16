"""Unified intermediate semantic representation.

The graph is deliberately independent from scene IDs.  Parsers may produce
local references and evidence, while the grounding stage is the only stage
allowed to populate ``entity_id`` from perception.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ConfigDict


class EvidenceSpan(BaseModel):
    value: Any = None
    source_text: str = ""
    start: int = -1
    end: int = -1
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rule_id: str = ""


class SpatialConstraint(BaseModel):
    relation: str
    reference: Optional[str] = None
    tolerance: Optional[float] = None
    evidence: List[str] = Field(default_factory=list)


class SemanticEntity(BaseModel):
    local_ref: str
    mention: str
    category: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    spatial_constraints: List[SpatialConstraint] = Field(default_factory=list)
    entity_id: Optional[str] = None
    evidence_spans: List[str] = Field(default_factory=list)
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    candidate_key: Optional[str] = None
    affordances: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class SemanticEvent(BaseModel):
    event_id: str
    action: str
    theme_ref: Optional[str] = None
    destination_ref: Optional[str] = None
    source_ref: Optional[str] = None
    recipient_ref: Optional[str] = None
    obstacle_refs: List[str] = Field(default_factory=list)
    condition_refs: List[str] = Field(default_factory=list)
    evidence_span: str = ""
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    sequence_index: int = 0
    parameters: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class SemanticRelation(BaseModel):
    type: str
    source_event: Optional[str] = None
    target_event: Optional[str] = None
    source_ref: Optional[str] = None
    target_ref: Optional[str] = None
    evidence: List[EvidenceSpan] = Field(default_factory=list)


class SemanticCondition(BaseModel):
    condition_id: str
    predicate: str
    subject_ref: Optional[str] = None
    operator: Optional[str] = None
    value: Any = None
    on_true_event_ids: List[str] = Field(default_factory=list)
    on_false_event_ids: List[str] = Field(default_factory=list)
    on_true_action: Optional[str] = None
    on_false_action: Optional[str] = None
    on_true_text: str = ""
    on_false_text: str = ""
    evidence_span: str = ""
    evidence: List[EvidenceSpan] = Field(default_factory=list)


class SemanticConstraint(BaseModel):
    constraint_id: str
    parameter: str
    operator: str
    value: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    unit: str = ""
    evidence_span: str = ""
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    hard: bool = True


class SemanticProhibition(BaseModel):
    prohibition_id: str
    type: str
    target_ref: Optional[str] = None
    scope_event_ids: List[str] = Field(default_factory=list)
    evidence_span: str = ""
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    propagated_to: List[str] = Field(default_factory=list)


class CoreferenceChain(BaseModel):
    chain_id: str
    mention_refs: List[str] = Field(default_factory=list)
    antecedent_ref: Optional[str] = None
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    resolved: bool = False


class AmbiguityRecord(BaseModel):
    ambiguity_id: str
    type: str
    candidates: List[str] = Field(default_factory=list)
    resolution: Optional[str] = None
    status: str = "UNRESOLVED"
    evidence: List[EvidenceSpan] = Field(default_factory=list)
    clarification: Optional[str] = None


class SemanticTaskGraph(BaseModel):
    """Single semantic source consumed by grounding, validation and BT code."""

    schema_version: str = "semantic-task-graph-1.0"
    instruction: str = ""
    entities: List[SemanticEntity] = Field(default_factory=list)
    events: List[SemanticEvent] = Field(default_factory=list)
    relations: List[SemanticRelation] = Field(default_factory=list)
    conditions: List[SemanticCondition] = Field(default_factory=list)
    constraints: List[SemanticConstraint] = Field(default_factory=list)
    prohibitions: List[SemanticProhibition] = Field(default_factory=list)
    coreference_chains: List[CoreferenceChain] = Field(default_factory=list)
    ambiguities: List[AmbiguityRecord] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def entity(self, local_ref: str) -> Optional[SemanticEntity]:
        return next((item for item in self.entities if item.local_ref == local_ref), None)

    def event(self, event_id: str) -> Optional[SemanticEvent]:
        return next((item for item in self.events if item.event_id == event_id), None)

    def actions(self) -> List[str]:
        return [event.action for event in self.events]

    def all_evidence(self) -> List[EvidenceSpan]:
        result: List[EvidenceSpan] = []
        for collection in (self.entities, self.events, self.conditions, self.constraints,
                           self.prohibitions, self.coreference_chains):
            for item in collection:
                result.extend(getattr(item, "evidence", []) or [])
        return result

    def validate_local_references(self) -> List[str]:
        known_entities = {entity.local_ref for entity in self.entities}
        known_events = {event.event_id for event in self.events}
        errors: List[str] = []
        for event in self.events:
            for ref in (event.theme_ref, event.destination_ref, event.source_ref, event.recipient_ref,
                        *event.obstacle_refs):
                if ref and ref not in known_entities:
                    errors.append(f"UNKNOWN_ENTITY_REF:{event.event_id}:{ref}")
        for relation in self.relations:
            for event_id in (relation.source_event, relation.target_event):
                if event_id and event_id not in known_events:
                    errors.append(f"UNKNOWN_EVENT_REF:{event_id}")
            if relation.type not in {"IF_TRUE", "IF_FALSE"}:
                for local_ref in (relation.source_ref, relation.target_ref):
                    if local_ref and local_ref not in known_entities:
                        errors.append(f"UNKNOWN_ENTITY_REF:relation:{local_ref}")
        for prohibition in self.prohibitions:
            if prohibition.target_ref and prohibition.target_ref not in known_entities:
                errors.append(f"UNKNOWN_ENTITY_REF:prohibition:{prohibition.target_ref}")
            for event_id in prohibition.scope_event_ids:
                if event_id not in known_events:
                    errors.append(f"UNKNOWN_EVENT_REF:prohibition:{event_id}")
        for condition in self.conditions:
            if condition.subject_ref and condition.subject_ref not in known_entities:
                errors.append(f"UNKNOWN_ENTITY_REF:condition:{condition.subject_ref}")
            for event_id in (*condition.on_true_event_ids, *condition.on_false_event_ids):
                if event_id not in known_events:
                    errors.append(f"UNKNOWN_EVENT_REF:condition:{event_id}")
        return errors


class SemanticCandidate(BaseModel):
    """Evidence-bearing candidate emitted by either rules or LLM."""

    schema_version: str = "semantic-candidate-1.0"
    graph: SemanticTaskGraph = Field(default_factory=SemanticTaskGraph)
    evidence_spans: List[EvidenceSpan] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = "rule"
    candidate_key: str = ""

    @classmethod
    def from_graph(cls, graph: SemanticTaskGraph, confidence: float = 0.0,
                   source: str = "rule") -> "SemanticCandidate":
        return cls(graph=graph, evidence_spans=graph.all_evidence(),
                   confidence=confidence, source=source)
