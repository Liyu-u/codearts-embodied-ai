"""Authoritative domain-restricted semantic compiler.

This module is the production boundary between natural language and the
execution stack.  Providers only emit semantic candidates; this compiler
owns fusion, scene grounding, compatibility projection and deterministic
BT generation.  Downstream consumers receive the same graph-derived
``ParsedTask`` and must not re-parse the instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Dict, List, Optional

from robot_intent_agent.domain.action_schemas import get_action_schema, normalize_action
from robot_intent_agent.grounding.grounding_engine import GroundingEngine
from robot_intent_agent.schemas.semantic_task_graph import (
    AmbiguityRecord,
    EvidenceSpan,
    SemanticCandidate,
    SemanticEntity,
    SemanticTaskGraph,
)
from robot_intent_agent.semantic_parser.rule_semantic_parser import RuleSemanticParser
from robot_intent_agent.semantic_reasoner.semantic_fusion import (
    FusionAuditRecord,
    SemanticFusion,
)
from robot_intent_agent.task_semantics import (
    ConstraintOperator,
    ConstraintSourceKind,
    MotionState,
    ParsedConstraint,
    ParsedTask,
    PlanStatus,
    SemanticEntityRef,
    TaskActionKind,
    build_grounded_task,
)


@dataclass
class SemanticCompilationResult:
    """All authoritative artifacts produced for one instruction."""

    graph: SemanticTaskGraph
    rule_candidate: SemanticCandidate
    llm_candidates: List[SemanticCandidate] = field(default_factory=list)
    fused_candidate: Optional[SemanticCandidate] = None
    fusion_trace: List[FusionAuditRecord] = field(default_factory=list)
    grounding_decisions: List[Dict[str, Any]] = field(default_factory=list)
    parsed_task: Optional[ParsedTask] = None
    grounded_task: Any = None
    behavior_tree: Any = None
    engine_trace: Dict[str, Any] = field(default_factory=dict)


class SemanticCompiler:
    """Compile candidates into one grounded semantic execution contract."""

    def __init__(self, llm_planner: Any = None, strict_llm: bool = False):
        self.rule_parser = RuleSemanticParser()
        self.fusion = SemanticFusion()
        self.grounder = GroundingEngine()
        self.llm_planner = llm_planner
        # Formal LLM mode can fail closed on provider/configuration errors.
        # Candidate contract rejection still follows the safety fallback path
        # below, so malformed model output never becomes executable.
        self.strict_llm = bool(strict_llm)

    def compile(
        self,
        instruction: str,
        scene: Any = None,
        memory_context: Optional[List[Dict[str, Any]]] = None,
        mode: str = "rule",
        request_llm: Optional[bool] = None,
    ) -> SemanticCompilationResult:
        # The rule parser may use scene affordances only to complete a generic
        # semantic role (for example, the sole movable object monitored by
        # WAIT).  It still never receives or assigns physical IDs; grounding
        # remains the responsibility of GroundingEngine below.
        rule_candidate = self.rule_parser.parse(instruction, scene=scene)
        graph = rule_candidate.graph
        diagnostics = self._diagnostics(rule_candidate)
        should_call_llm = bool(request_llm) if request_llm is not None else mode in {"llm", "hybrid"}
        if mode == "hybrid" and request_llm is None:
            # Hybrid is an independent language-understanding path.  The
            # deterministic candidate is evidence and a safety witness, not
            # a gate that decides whether unfamiliar wording may reach the
            # semantic model.
            should_call_llm = True

        llm_candidates: List[SemanticCandidate] = []
        engine_trace: Dict[str, Any] = {
            "requested_engine": mode,
            "actual_engine": "RuleEngine",
            "llm_call_attempted": False,
            "llm_call_succeeded": False,
            "response_schema_valid": False,
            "fallback_used": False,
            "fallback_reason": None,
            "final_semantics_source": "rule_candidate",
            "llm_transport_succeeded": False,
            "llm_json_parsed": False,
            "llm_candidate_valid": False,
            "llm_candidate_partially_repaired": False,
            "llm_cache_hit": False,
            "llm_network_calls": 0,
            "llm_candidate_selection": [],
        }
        if should_call_llm and self.llm_planner is not None and self.llm_planner.is_available:
            engine_trace["llm_call_attempted"] = True
            try:
                llm_candidates = self.llm_planner.semantic_candidates(
                    instruction, scene=scene, memory_context=memory_context
                )
                if not llm_candidates:
                    raise ValueError("no valid semantic candidates")
                engine_trace["llm_call_succeeded"] = True
                engine_trace["response_schema_valid"] = True
            except Exception as exc:
                engine_trace["fallback_used"] = True
                engine_trace["fallback_reason"] = f"{type(exc).__name__}: {exc}"
                if self.strict_llm:
                    raise RuntimeError(
                        "LLM_REQUIRED_FAILED: semantic provider call failed: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
            provider_trace = getattr(self.llm_planner, "last_call_metadata", {}) or {}
            engine_trace.update({
                "llm_transport_succeeded": bool(provider_trace.get("transport_succeeded")),
                "llm_json_parsed": bool(provider_trace.get("json_parsed")),
                "llm_candidate_valid": bool(provider_trace.get("candidate_valid")) or bool(llm_candidates),
                "llm_cache_hit": bool(provider_trace.get("cache_hit")),
                "llm_network_calls": int(bool(provider_trace.get("network_call"))),
                "llm_attempt_count": int(provider_trace.get("attempt_count", 0) or 0),
                "llm_error_class": provider_trace.get("error_class"),
                "llm_status_code": provider_trace.get("status_code"),
                "llm_request_id": provider_trace.get("request_id"),
                "llm_request_id_source": provider_trace.get("request_id_source"),
            })
        elif should_call_llm:
            engine_trace["fallback_used"] = True
            engine_trace["fallback_reason"] = "llm_unavailable"
            if self.strict_llm:
                raise RuntimeError(
                    "LLM_REQUIRED_FAILED: semantic provider unavailable or API key missing"
                )

        llm_candidate, llm_candidates, selection_trace = self._select_llm_candidate(
            llm_candidates, instruction
        )
        engine_trace["llm_candidate_selection"] = selection_trace
        if selection_trace and llm_candidate is None:
            engine_trace["fallback_used"] = True
            engine_trace["fallback_reason"] = "all_llm_candidates_rejected"
            engine_trace["llm_candidate_rejected"] = True
            engine_trace["llm_candidate_rejection_reasons"] = [
                "NO_VALID_SEMANTIC_CANDIDATE"
            ]
        engine_trace["llm_candidate_valid"] = bool(llm_candidate)
        # Enforce the domain boundary before provider output reaches repair or
        # fusion.  An out-of-contract process verb must become a blocked
        # CUSTOM request; allowing a malformed provider answer to enter the
        # fusion path can turn a safe rejection into an exception or an
        # invented manipulation action.
        unsupported_evidence = (rule_candidate.graph.metadata or {}).get(
            "unsupported_action_evidence"
        )
        if unsupported_evidence and llm_candidate is not None:
            engine_trace["fallback_used"] = True
            engine_trace["fallback_reason"] = (
                f"unsupported_domain_action:{unsupported_evidence}"
            )
            engine_trace["llm_candidate_rejected"] = True
            engine_trace["llm_candidate_rejection_reasons"] = [
                "UNSUPPORTED_DOMAIN_ACTION"
            ]
            llm_candidates = []
            llm_candidate = None
        if llm_candidate is not None:
            candidate_errors = self._validate_llm_candidate_contract(llm_candidate)
            if not candidate_errors:
                self._repair_scene_candidate_refs(rule_candidate.graph, llm_candidate.graph, scene)
                candidate_errors = llm_candidate.graph.validate_local_references()
            # Provider candidate_key values are only accepted when they are
            # backed by entity atoms.  A candidate that refers to
            # scene-object-* without declaring those local refs would erase
            # the rule graph's grounded roles during fusion.
            if candidate_errors:
                engine_trace["fallback_used"] = True
                engine_trace["fallback_reason"] = "llm_candidate_reference_invalid: " + ";".join(candidate_errors[:4])
                engine_trace["llm_candidate_rejected"] = True
                engine_trace["llm_candidate_rejection_reasons"] = candidate_errors
                llm_candidates = []
                llm_candidate = None
        fused, audit = self.fusion.fuse(rule_candidate, llm_candidate, instruction)
        if llm_candidate is not None:
            regressions = self._fused_graph_regressions(rule_candidate.graph, fused.graph)
            if not regressions and scene is not None:
                protected_roles = self._protect_rule_grounded_roles(
                    rule_candidate.graph, fused.graph, scene
                )
                if protected_roles:
                    engine_trace["llm_grounded_role_protection"] = protected_roles
                regressions = self._grounded_graph_regressions(
                    rule_candidate.graph, fused.graph, scene
                )
            if regressions:
                engine_trace["fallback_used"] = True
                engine_trace["fallback_reason"] = "llm_semantic_regression: " + ";".join(regressions[:6])
                engine_trace["llm_candidate_rejected"] = True
                engine_trace["llm_candidate_rejection_reasons"] = regressions
                llm_candidates = []
                llm_candidate = None
                fused = rule_candidate
                audit.append(FusionAuditRecord(
                    "candidate", fused.graph.model_dump(mode="json"),
                    None, fused.graph.model_dump(mode="json"),
                    "REJECT_LLM_SEMANTIC_REGRESSION", ";".join(regressions[:6])))
        graph = fused.graph

        # Domain boundary is a hard safety invariant.  If the deterministic
        # parser witnessed a process/sensing verb outside the ten supported
        # actions, an LLM answer that invents a supported manipulation is not
        # a useful correction.  Keep the rule graph's CUSTOM result so the
        # final adapter reports a blocked unsupported request.
        if unsupported_evidence and any(
                normalize_action(event.action) != "CUSTOM" for event in graph.events
        ):
            engine_trace["fallback_used"] = True
            engine_trace["fallback_reason"] = (
                f"unsupported_domain_action:{unsupported_evidence}"
            )
            engine_trace["llm_candidate_rejected"] = True
            engine_trace["llm_candidate_rejection_reasons"] = [
                "UNSUPPORTED_DOMAIN_ACTION"
            ]
            llm_candidates = []
            llm_candidate = None
            fused = rule_candidate
            graph = fused.graph

        if scene is not None:
            self._normalize_fetch_receive_role(graph)
            self._reject_generic_fetch_destination(graph)
            # A FETCH destination can be expressed as a receive-zone noun
            # (收取区/回收位置/指定接收处) or as a deictic endpoint (拿到我这
            # 里/带回机器人身边).  Complete it only when the perception
            # scene exposes exactly one valid receive surface; the scene,
            # never the language model, owns the physical object id.
            self._complete_fetch_destination_from_scene(graph, scene, instruction)
            # A nominal placement request such as “完成绿色方块的放置”
            # omits the destination. Complete it only when perception exposes
            # exactly one valid support surface; otherwise keep the request
            # unresolved and do not choose an arbitrary table or tray.
            self._complete_place_destination_from_scene(graph, scene)
            graph, decisions = self.grounder.ground_graph(graph, scene)
            # Rule-parser candidate keys are deterministic scene-selection
            # witnesses and are retained for compatibility with the existing
            # rule path.  Provider-only candidate keys remain mere retrieval
            # hints: they are deliberately excluded by ``rule_local_refs``
            # and can never bypass grounding or resolve an LLM ambiguity.
            rule_local_refs = {item.local_ref for item in rule_candidate.graph.entities}
            scene_ids = {str(getattr(item, "id", ""))
                         for item in getattr(scene, "objects", []) or []}
            for entity in graph.entities:
                candidate_key = str(entity.candidate_key or "")
                if (entity.local_ref not in rule_local_refs or
                        not candidate_key or candidate_key not in scene_ids or
                        entity.entity_id):
                    continue
                role = next((role for event in graph.events for role, ref in {
                    "theme": event.theme_ref, "destination": event.destination_ref,
                    "source": event.source_ref, "recipient": event.recipient_ref,
                }.items() if ref == entity.local_ref), None)
                if not role:
                    continue
                entity.entity_id = candidate_key
                decision = next((item for item in decisions.values()
                                 if item.role == role), None)
                if decision is not None and decision.selected_entity_id is None:
                    decision.selected_entity_id = candidate_key
                    decision.decision = "RESOLVED_RULE_WITNESS"

            # Human recipients are symbolic contract entities, not objects
            # that must be found in the perception list. Normalize all known
            # operator/staff surfaces to the stable ``operator`` identity so
            # HANDOVER can be dispatched without inventing a scene object.
            recipient_words = ("操作员", "操作人员", "工作人员", "现场操作员", "接收者", "人手", "用户", "对方")
            for event in graph.events:
                if event.action != "HANDOVER" or not event.recipient_ref:
                    continue
                recipient = graph.entity(event.recipient_ref)
                if recipient is not None and any(word in str(recipient.mention or "") for word in recipient_words):
                    recipient.category = "operator"
                    recipient.entity_id = "operator"
                    recipient.attributes = dict(recipient.attributes or {})
                    recipient.attributes["symbolic_recipient"] = True
                    # A handover destination is represented by the recipient
                    # role; do not retain a physical-zone interpretation from
                    # an embedded phrase such as "操作人员面前".
                    event.destination_ref = None
            grounding_decisions = [self._decision_dict(item) for item in decisions.values()]

            # A non-resolved required role is an ambiguity/clarification
            # record, not permission to use the first ranked object.  This
            # converts grounding's decision into the graph-level status that
            # all downstream validators consume.
            for event in graph.events:
                action = normalize_action(event.action)
                if action == "WAIT":
                    # WAIT is condition-only; its schema's symbolic
                    # condition role is not a scene-grounding role.
                    continue
                schema = get_action_schema(action)
                role_values = {
                    "theme": event.theme_ref,
                    "destination": event.destination_ref,
                    "source": event.source_ref,
                    "recipient": event.recipient_ref,
                }
                required_groups = [tuple(schema.required_roles), *schema.required_any_roles]
                for group in required_groups:
                    decisions_for_group = []
                    if any(role in role_values and role_values[role] for role in group):
                        decisions_for_group = [
                            decisions.get(role) for role in group if decisions.get(role) is not None
                        ]
                        if any(getattr(item, "decision", "") == "RESOLVED"
                               for item in decisions_for_group):
                            continue
                    elif group:
                        decisions_for_group = []
                    unresolved = [
                        item for item in decisions_for_group
                        if getattr(item, "decision", "") != "RESOLVED"
                    ]
                    if not unresolved and not decisions_for_group and not any(
                            role_values.get(role) for role in group
                    ):
                        unresolved = [None]
                    if unresolved:
                        ambiguity_id = f"grounding-{event.event_id}-{'-'.join(group)}"
                        if not any(item.ambiguity_id == ambiguity_id
                                   for item in graph.ambiguities):
                            graph.ambiguities.append(AmbiguityRecord(
                                ambiguity_id=ambiguity_id,
                                type="GROUNDING_AMBIGUITY",
                                candidates=[
                                    entity_id for role in group
                                    for entity_id in getattr(decisions.get(role), "candidate_ids", [])
                                ],
                                status="UNRESOLVED",
                                clarification=(
                                    f"请明确{group[0]}对应的唯一对象。"
                                ),
                            ))

            # A role may have been created by a parser-side scene selector but
            # still carry no entity_id when the selector was ambiguous. Keep
            # that unresolved state explicit; never let a local mention become
            # an executable entity merely because its category is valid.
            for event in graph.events:
                for role, local_ref in {
                    "theme": event.theme_ref,
                    "destination": event.destination_ref,
                    "source": event.source_ref,
                    "recipient": event.recipient_ref,
                }.items():
                    if not local_ref:
                        continue
                    entity = graph.entity(local_ref)
                    decision = next((item for item in decisions.values() if item.role == role), None)
                    if entity is not None and not entity.entity_id and decision is not None:
                        entity.attributes = dict(entity.attributes or {})
                        entity.attributes["_grounding_unresolved"] = True

        # Negation and event obstacles can be represented by separate local
        # entity records when the same mention is extracted twice.  Rebind an
        # unresolved prohibition to the already grounded equivalent before
        # projecting ParsedTask; otherwise the final adapter sees a fake
        # ungrounded obstacle and blocks an otherwise valid WAIT/NO_CONTACT
        # contract.
        if scene is not None:
            for prohibition in graph.prohibitions:
                target = graph.entity(prohibition.target_ref) if prohibition.target_ref else None
                if target is None or target.entity_id:
                    continue
                equivalent = next((item for item in graph.entities
                                   if item.entity_id and item.local_ref != target.local_ref
                                   and item.category == target.category
                                   and item.mention == target.mention), None)
                if equivalent is not None:
                    prohibition.target_ref = equivalent.local_ref
        else:
            grounding_decisions = []

        # Explicit safety prohibitions are preserved even when the provider
        # omitted the corresponding obstacle role.  Resolve their local refs
        # against the same scene-owned binding map used by action roles.
        if scene is not None:
            for prohibition in graph.prohibitions:
                entity = graph.entity(prohibition.target_ref) if prohibition.target_ref else None
                if entity is not None and not entity.entity_id:
                    query = {"category": entity.category, "attributes": entity.attributes,
                             "mention": entity.mention}
                    occupied = {
                        item.entity_id for item in graph.entities
                        if item.entity_id and item.local_ref != prohibition.target_ref
                    }
                    query["exclude_ids"] = occupied
                    decision_map = self.grounder.ground(
                        {"obstacle": query}, scene, action="CUSTOM"
                    )
                    decision = decision_map.get("obstacle")
                    if decision and decision.selected_entity_id:
                        entity.entity_id = decision.selected_entity_id
                        grounding_decisions.append(self._decision_dict(decision))

        graph.metadata = dict(graph.metadata or {})
        graph.metadata.update({
            "compiler": "SemanticCompiler",
            "compiler_version": "2.0.0",
            "grounded": scene is not None,
            "llm_candidate_count": len(llm_candidates),
            "fusion_trace": [item.model_dump() for item in audit],
            "grounding_decisions": grounding_decisions,
        })
        parsed_task = parsed_task_from_graph(
            graph,
            instruction,
            scene=scene,
            grounding_decisions=grounding_decisions,
            fusion_trace=[item.model_dump() for item in audit],
        )
        grounded_task = build_grounded_task(parsed_task, scene=scene)

        if unsupported_evidence:
            parsed_task.notes.append(
                f"unsupported_capability:{unsupported_evidence}"
            )
            if "unsupported_capability" not in parsed_task.unmet_roles:
                parsed_task.unmet_roles.append("unsupported_capability")

        # Reject a graph that still contains unknown local references before
        # any executable artifact is considered valid.
        local_reference_errors = graph.validate_local_references()
        if local_reference_errors:
            engine_trace["graph_reference_errors"] = local_reference_errors
            parsed_task.notes.extend(f"semantic_graph_error:{item}" for item in local_reference_errors)
            parsed_task.unmet_roles.append("semantic_graph_reference")

        from robot_intent_agent.planner.behavior_tree_generator import BehaviorTreeGenerator
        behavior_tree = BehaviorTreeGenerator().generate_from_graph(
            graph, scene=scene, instruction=instruction
        )
        behavior_tree.task_id = f"task-{parsed_task.action.value.lower()}"
        behavior_tree.metadata.update({
            "semantic_authority": "SemanticCompiler",
            "semantic_graph_stage": "grounded" if scene is not None else "fused",
            "semantic_task_graph": graph.model_dump(mode="json"),
            "parsed_task": parsed_task.model_dump(mode="json"),
            "grounded_task": grounded_task.model_dump(mode="json"),
            "fusion_trace": [item.model_dump() for item in audit],
            "grounding_decisions": grounding_decisions,
            "plan_status": self._initial_status(parsed_task, grounded_task).value,
            "graph_reference_errors": local_reference_errors,
        })
        accepted_fields = [item.field for item in audit if item.decision in {
            "ACCEPT_LLM_DELTA", "MERGE_RULE_WITH_LLM_DELTA"
        }] if llm_candidates else []
        engine_trace.update({
            "actual_engine": "HybridSemanticCompiler" if accepted_fields else "RuleEngine",
            "semantic_engine": "HybridSemanticCompiler" if llm_candidates else "RuleSemanticCompiler",
            "semantic_candidate_contract": "semantic-candidate-1.0",
            "final_semantics_source": "fused_grounded_graph" if llm_candidates else "grounded_rule_graph",
            "llm_fusion": {
                "accepted_fields": accepted_fields,
                "protected_fields": sorted(SemanticFusion.PROTECTED_FIELDS),
                "final_authority": "grounded_semantic_graph",
            },
        })
        engine_trace["llm_candidate_accepted"] = bool(llm_candidates)
        engine_trace["llm_effective"] = bool(engine_trace["llm_fusion"]["accepted_fields"])
        engine_trace["llm_fields_changed"] = list(engine_trace["llm_fusion"]["accepted_fields"])
        engine_trace["llm_no_effect"] = bool(
            engine_trace.get("llm_call_attempted") and not engine_trace["llm_effective"]
        )
        engine_trace["llm_candidate_partially_repaired"] = bool(
            any(
                int((candidate.graph.metadata or {}).get("provider_repair_count", 0)) > 0
                for candidate in llm_candidates
            )
        )
        if llm_candidates and not engine_trace["llm_effective"]:
            engine_trace["final_semantics_source"] = "grounded_rule_graph"
        behavior_tree.metadata["engine_trace"] = engine_trace

        return SemanticCompilationResult(
            graph=graph,
            rule_candidate=rule_candidate,
            llm_candidates=llm_candidates,
            fused_candidate=fused,
            fusion_trace=audit,
            grounding_decisions=grounding_decisions,
            parsed_task=parsed_task,
            grounded_task=grounded_task,
            behavior_tree=behavior_tree,
            engine_trace=engine_trace,
        )

    @classmethod
    def _select_llm_candidate(
        cls,
        candidates: List[SemanticCandidate],
        instruction: str,
    ) -> tuple[Optional[SemanticCandidate], List[SemanticCandidate], List[Dict[str, Any]]]:
        """Select the strongest provider candidate deterministically.

        Providers may return up to three alternatives.  Taking index zero
        makes answer quality depend on serialization order.  Contract-invalid
        candidates are removed before fusion; the remaining candidates are
        ranked by the provider confidence first, then by evidence coverage,
        role completeness, ambiguity and repair penalties.  The original
        order wins every exact tie.
        """
        if not candidates:
            return None, [], []

        ranked = []
        trace: List[Dict[str, Any]] = []
        for index, candidate in enumerate(candidates):
            contract_errors = cls._validate_llm_candidate_contract(candidate)
            graph = candidate.graph
            evidence_count = sum(
                1 for event in graph.events
                if event.evidence_span and event.evidence_span in instruction
            ) + sum(
                1 for entity in graph.entities
                if entity.mention and (
                    entity.mention in instruction or
                    any(span and span in instruction for span in entity.evidence_spans)
                )
            )
            missing_roles = 0
            for event in graph.events:
                action = normalize_action(event.action)
                if action == "CUSTOM":
                    continue
                schema = get_action_schema(action)
                refs = {
                    "theme": event.theme_ref,
                    "destination": event.destination_ref,
                    "source": event.source_ref,
                    "recipient": event.recipient_ref,
                }
                missing_roles += len(schema.missing_roles(
                    name for name, value in refs.items() if value
                ))
            unresolved = sum(
                1 for item in graph.ambiguities
                if str(item.status or "").upper() == "UNRESOLVED"
            )
            repairs = int((graph.metadata or {}).get("provider_repair_count", 0) or 0)
            item_trace = {
                "index": index,
                "accepted": not bool(contract_errors),
                "confidence": round(float(candidate.confidence), 4),
                "evidence_count": evidence_count,
                "missing_roles": missing_roles,
                "unresolved_ambiguities": unresolved,
                "provider_repairs": repairs,
            }
            if contract_errors:
                item_trace["rejection_reasons"] = contract_errors[:4]
                trace.append(item_trace)
                continue
            # Keep confidence as the dominant signal.  Secondary signals are
            # deterministic tie-breakers that prefer grounded, complete
            # candidates without overriding a materially more confident one.
            score = (
                round(float(candidate.confidence), 6),
                evidence_count,
                -missing_roles,
                -unresolved,
                -repairs,
                -index,
            )
            trace.append(item_trace)
            ranked.append((score, index, candidate, item_trace))

        if not ranked:
            return None, [], trace

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = ranked[0][2]
        selected_index = ranked[0][1]
        for item in trace:
            item["selected"] = item["index"] == selected_index
        ordered = [item[2] for item in ranked]
        return selected, ordered, trace

    @staticmethod
    def _validate_llm_candidate_contract(candidate: SemanticCandidate) -> List[str]:
        """Validate the provider boundary before any fusion or grounding.

        The provider is allowed to describe meaning, but it is not allowed to
        smuggle execution state or physical identity into the semantic graph.
        Pydantic models intentionally remain permissive for backward
        compatibility, so this explicit gate is the authoritative check for
        candidates supplied by external providers and test doubles.
        """
        errors: List[str] = []
        data = candidate.graph.model_dump(mode="json")
        forbidden = {
            "entity_id", "object_id", "target_entity_id", "destination_entity_id",
            "plan_status", "execution_allowed", "behavior_tree", "robot_coordinates",
        }

        def walk(value: Any, path: str = "") -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in forbidden and item not in (None, "", [], {}):
                        errors.append(f"FORBIDDEN_PROVIDER_FIELD:{path}.{key}" if path else f"FORBIDDEN_PROVIDER_FIELD:{key}")
                    walk(item, f"{path}.{key}" if path else key)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{path}[{index}]")

        walk(data)
        for entity in candidate.graph.entities:
            if entity.entity_id:
                errors.append(f"PROVIDER_ENTITY_ID:{entity.local_ref}")
            if entity.candidate_key and not re.fullmatch(r"scene-object-\d+", str(entity.candidate_key)):
                errors.append(f"INVALID_CANDIDATE_KEY:{entity.local_ref}:{entity.candidate_key}")
        for event in candidate.graph.events:
            action = normalize_action(event.action)
            if action not in {"GRASP", "DYNAMIC_GRASP", "FETCH", "PLACE", "TRANSFER",
                              "HANDOVER", "PUSH", "POUR", "STACK", "WAIT", "CUSTOM"}:
                errors.append(f"UNSUPPORTED_ACTION:{event.action}")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _reject_generic_fetch_destination(graph: SemanticTaskGraph) -> None:
        """Do not bind an underspecified FETCH destination to any object.

        A phrase such as "取到手边" describes an unmodelled receive pose,
        not an arbitrary scene object.  Grounding a generic ``object`` or
        ``item`` here would fabricate a destination and make an incomplete
        FETCH look executable.  Explicit scene categories (table, tray,
        container, receive zone, etc.) remain eligible for normal grounding.
        """
        for event in graph.events:
            if normalize_action(event.action) != "FETCH" or not event.destination_ref:
                continue
            entity = graph.entity(event.destination_ref)
            if entity is None:
                continue
            if str(entity.category or "").lower() in {"object", "item", "unknown", "entity"}:
                entity.attributes = dict(entity.attributes or {})
                entity.attributes["_grounding_unresolved"] = True
                event.destination_ref = None

    @staticmethod
    def _normalize_fetch_receive_role(graph: SemanticTaskGraph) -> None:
        """Normalize a physical FETCH recipient into its destination role.

        ``recipient`` is the symbolic/user endpoint in the contract.  Some
        providers use it for a tray or receive zone because the sentence says
        "带到收取区".  A non-human FETCH recipient is therefore a physical
        destination description, while human/operator recipients remain
        symbolic and unchanged.
        """
        for event in graph.events:
            if normalize_action(event.action) != "FETCH" or event.destination_ref:
                continue
            recipient = graph.entity(event.recipient_ref) if event.recipient_ref else None
            if recipient is None:
                continue
            category = str(recipient.category or "").lower()
            if category not in {"human", "operator", "user"}:
                event.destination_ref = event.recipient_ref
                event.recipient_ref = None

    @staticmethod
    def _complete_fetch_destination_from_scene(graph: SemanticTaskGraph, scene: Any,
                                               instruction: str = "") -> None:
        """Complete FETCH with a unique receive zone exposed by perception.

        This is a capability-level rule, not a sentence patch: FETCH requires
        a destination or recipient. When perception declares exactly one
        ``robot_receive_zone`` and language omits the location, that scene
        role is the only safe completion. Otherwise the graph stays incomplete
        and validation requests clarification.
        """
        if not any(normalize_action(event.action) == "FETCH" for event in graph.events):
            return
        text = str(instruction or "")
        endpoint_language = bool(re.search(
            r"收取|接收|回收|机器人身边|机器人接收区|指定接收|到我这|到这边|到手边|回到机器人",
            text,
        ))
        declared_receive_zones = []
        surface_zones = []
        for obj in getattr(scene, "objects", []) or []:
            attrs = getattr(obj, "attributes", {}) or {}
            upstream = attrs.get("_upstream_affordances", []) or []
            if isinstance(upstream, str):
                upstream = [upstream]
            category = str(
                getattr(obj, "specific_class", None) or
                getattr(obj, "label", None) or ""
            ).lower()
            is_surface = any(str(item) in {
                "robot_receive_zone", "receive_zone", "support_surface",
                "fixed", "container",
            } for item in upstream) or category in {
                "tray", "bin", "parts_bin", "table", "workbench",
                "platform", "receive_zone", "inspection_zone",
            }
            if is_surface:
                surface_zones.append(obj)
                if any(str(item) in {"robot_receive_zone", "receive_zone"}
                       for item in upstream):
                    declared_receive_zones.append(obj)
        # A perception-declared receive zone is stronger than generic fixed
        # surfaces. Prefer it whenever present; otherwise an endpoint phrase
        # may use a unique fixed/container/support surface. This prevents a
        # fixture and a tray from making one receive zone appear ambiguous.
        receive_zones = declared_receive_zones or (surface_zones if endpoint_language else [])
        if len(receive_zones) != 1:
            return
        zone = receive_zones[0]
        local_ref = "scene-fetch-destination"
        if graph.entity(local_ref) is None:
            zone_name = getattr(zone, "name", "robot receive zone")
            graph.entities.append(SemanticEntity(
                local_ref=local_ref,
                mention=zone_name,
                category=getattr(zone, "specific_class", None) or getattr(zone, "label", None),
                # Provenance is carried by evidence; query attributes must
                # stay empty so they cannot filter the real scene object.
                attributes={},
                evidence_spans=[zone_name],
                evidence=[EvidenceSpan(value=zone_name, source_text=graph.instruction,
                                       confidence=0.95, rule_id="scene.fetch_receive_zone")],
            ))
        for event in graph.events:
            if event.action != "FETCH" or event.recipient_ref:
                continue
            current = graph.entity(event.destination_ref) if event.destination_ref else None
            # A language-only surface such as "收取区/接收位/回收位置" is a
            # description, not an executable scene binding. If perception
            # exposes exactly one receive zone, replace that unresolved local
            # ref with the scene-derived role. Explicitly grounded locations
            # remain untouched.
            if not event.destination_ref or current is None or not current.entity_id:
                event.destination_ref = local_ref

    @staticmethod
    def _complete_place_destination_from_scene(graph: SemanticTaskGraph, scene: Any) -> None:
        """Fill an omitted PLACE destination only when it is unique and safe."""

        if not any(normalize_action(event.action) == "PLACE" for event in graph.events):
            return
        surfaces = []
        for obj in getattr(scene, "objects", []) or []:
            attrs = getattr(obj, "attributes", {}) or {}
            upstream = attrs.get("_upstream_affordances", []) or []
            if isinstance(upstream, str):
                upstream = [upstream]
            affordances = {
                str(item.value if hasattr(item, "value") else item).lower()
                for item in (getattr(obj, "affordances", []) or [])
            }
            affordances.update(str(item).lower() for item in upstream)
            category = str(
                getattr(obj, "specific_class", None)
                or getattr(obj, "label", None)
                or ""
            ).lower()
            execution = attrs.get("_integration_execution", {})
            if not isinstance(execution, dict):
                execution = {}
            is_surface = bool({"support_surface", "fixed", "container"} & affordances) or category in {
                "table", "tray", "container", "bin", "platform", "workbench",
            }
            if is_surface and execution.get("valid_destination") is not False:
                surfaces.append(obj)
        if len(surfaces) != 1:
            return
        surface = surfaces[0]
        local_ref = "scene-place-destination"
        if graph.entity(local_ref) is None:
            name = getattr(surface, "name", "support surface")
            graph.entities.append(SemanticEntity(
                local_ref=local_ref,
                mention=name,
                category=getattr(surface, "specific_class", None) or getattr(surface, "label", None),
                attributes={},
                evidence_spans=[name],
                evidence=[EvidenceSpan(
                    value=name,
                    source_text=graph.instruction,
                    confidence=0.95,
                    rule_id="scene.unique_place_surface",
                )],
            ))
        for event in graph.events:
            if normalize_action(event.action) != "PLACE":
                continue
            current = graph.entity(event.destination_ref) if event.destination_ref else None
            # An explicit destination mention must remain explicit even before
            # grounding assigns its scene ID.  Replacing every unresolved
            # mention with the only visible table turns commands such as
            # “put the red block on the green block” into a different, unsafe
            # table-placement task.  Only an omitted destination may be
            # completed from the unique scene surface.
            if not event.destination_ref:
                event.destination_ref = local_ref

    @staticmethod
    def _uses_implicit_fetch_receive_zone(instruction: str,
                                          graph: SemanticTaskGraph) -> bool:
        """Recognize the generic FETCH family that implies a receive zone.

        This is intentionally a semantic family check, not a per-case rule.
        Explicit destinations remain untouched, while ``拿来/带回/取回``
        forms can use one unique perception-declared receive zone.
        """
        if not any(normalize_action(event.action) == "FETCH" for event in graph.events):
            return False
        text = str(instruction or "")
        return bool(re.search(
            r"(?:从(?:现场|原处|那里)\s*)?(?:拿来|带来|取回|取回来|带回|拿回来)"
            r"(?:\s*(?:给我|到我这|到这边|到手边|回到机器人身边))?",
            text,
        ))

    @staticmethod
    def _diagnostics(candidate: SemanticCandidate) -> Dict[str, Any]:
        graph = candidate.graph
        action_complete = bool(graph.events) and all(
            event.action not in {"CUSTOM", ""} for event in graph.events
        )
        roles_complete = True
        for event in graph.events:
            schema = get_action_schema(event.action)
            roles = {name for name, ref in {
                "theme": event.theme_ref,
                "destination": event.destination_ref,
                "source": event.source_ref,
                "recipient": event.recipient_ref,
            }.items() if ref}
            roles_complete = roles_complete and not schema.missing_roles(roles)
        action_candidates = graph.metadata.get("action_candidates", []) if isinstance(graph.metadata, dict) else []
        action_values = list(dict.fromkeys(
            item.get("value") for item in action_candidates
            if isinstance(item, dict) and item.get("value")
        ))
        action_ambiguity = len(action_values) > 1
        high_conflict = bool(set(action_values) & {"GRASP", "PLACE"}) and bool(
            set(action_values) & {"POUR", "STACK", "HANDOVER", "TRANSFER", "FETCH", "DYNAMIC_GRASP"}
        )
        explicit_complexity = bool(
            graph.conditions or len(graph.events) > 1 or graph.prohibitions or
            graph.constraints or any(event.action == "CUSTOM" for event in graph.events) or
            action_ambiguity or high_conflict
        )
        return {
            "action_complete": action_complete,
            "roles_complete": roles_complete,
            "action_ambiguity": action_ambiguity,
            "high_conflict": high_conflict,
            "force_llm": bool(explicit_complexity or not action_complete or not roles_complete),
        }

    @staticmethod
    def _fused_graph_regressions(rule_graph: SemanticTaskGraph,
                                  fused_graph: SemanticTaskGraph) -> List[str]:
        """Reject a provider candidate that degrades verified rule semantics.

        This is intentionally evaluated before scene grounding.  Physical IDs
        remain owned by GroundingEngine, while local role references and action
        completeness are still comparable at this stage.
        """
        errors = list(fused_graph.validate_local_references())
        rule_events = {event.sequence_index: event for event in rule_graph.events}
        fused_events = {event.sequence_index: event for event in fused_graph.events}
        # The deterministic graph owns event cardinality for the current
        # contract.  A provider often expands surface phrases such as
        # "完成后再释放夹爪" into a new GRASP/RELEASE event even though that
        # phrase is execution narration, not a second user task.  Keeping
        # that extra event would let downstream projection select the wrong
        # final action or status.  LLM action recovery remains possible when
        # the rule path has no event at all (CUSTOM/empty graph); once the
        # rule path has established a sequence, the provider may enrich those
        # events but may not append another executable event.
        if rule_events and len(fused_events) > len(rule_events):
            errors.append("EXTRA_LLM_EVENT_BEYOND_RULE_SEQUENCE")
        specialized = {"DYNAMIC_GRASP", "HANDOVER", "TRANSFER", "FETCH", "POUR", "STACK"}
        generic = {"GRASP", "PLACE"}
        for index, rule_event in rule_events.items():
            event = fused_events.get(index)
            if event is None:
                errors.append(f"DROPPED_RULE_EVENT:{index}")
                continue
            rule_action = normalize_action(rule_event.action)
            final_action = normalize_action(event.action)
            if rule_action in specialized and final_action in generic:
                errors.append(f"ACTION_DOWNGRADE:{rule_action}->{final_action}:{index}")
            if rule_action != "CUSTOM" and final_action == "CUSTOM":
                errors.append(f"ACTION_REGRESSION:{rule_action}->CUSTOM:{index}")
            schema = get_action_schema(final_action)
            rule_schema = get_action_schema(rule_action)
            final_roles = {
                name for name, ref in {
                    "theme": event.theme_ref,
                    "destination": event.destination_ref,
                    "source": event.source_ref,
                    "recipient": event.recipient_ref,
                }.items() if ref
            }
            # A specialized LLM action must not replace a complete rule
            # action while leaving its own required roles unresolved.  This
            # prevents words such as "提离/拿走" from being promoted to FETCH
            # without a delivery role, while still allowing an LLM action to
            # add a genuinely evidenced role that the rule path missed.
            if (rule_action != final_action and not rule_schema.missing_roles(
                    {name for name, ref in {
                        "theme": rule_event.theme_ref,
                        "destination": rule_event.destination_ref,
                        "source": rule_event.source_ref,
                        "recipient": rule_event.recipient_ref,
                    }.items() if ref}) and schema.missing_roles(final_roles)):
                errors.append(f"INCOMPLETE_LLM_ACTION:{rule_action}->{final_action}:{index}")
            for role in schema.missing_roles(final_roles):
                rule_ref = {
                    "theme": rule_event.theme_ref,
                    "destination": rule_event.destination_ref,
                    "source": rule_event.source_ref,
                    "recipient": rule_event.recipient_ref,
                }.get(role)
                if rule_ref:
                    errors.append(f"DROPPED_RULE_ROLE:{index}:{role}")
        return errors

    def _grounded_graph_regressions(self, rule_graph: SemanticTaskGraph,
                                    fused_graph: SemanticTaskGraph,
                                    scene: Any) -> List[str]:
        """Reject an LLM action change that loses a grounded required role.

        Before grounding, a provider can make an action look complete merely
        by inventing a local destination atom.  Compare grounded copies of
        the rule and fused graphs so an action upgrade is accepted only when
        its required physical roles actually resolve in the perception scene.
        """
        rule_grounded, _ = self.grounder.ground_graph(
            rule_graph.model_copy(deep=True), scene
        )
        fused_grounded, _ = self.grounder.ground_graph(
            fused_graph.model_copy(deep=True), scene
        )
        errors: List[str] = []
        rule_events = {event.sequence_index: event for event in rule_grounded.events}
        fused_events = {event.sequence_index: event for event in fused_grounded.events}
        for index, rule_event in rule_events.items():
            fused_event = fused_events.get(index)
            if fused_event is None:
                continue
            rule_action = normalize_action(rule_event.action)
            final_action = normalize_action(fused_event.action)
            schema = get_action_schema(final_action)
            rule_schema = get_action_schema(rule_action)
            # Even when the top-level action is unchanged, a provider can
            # erase a required grounded role (the observed failure was a
            # valid TRANSFER losing its tray destination).  Preserve every
            # rule role that was already physically resolved; only missing or
            # unresolved rule roles are eligible for provider repair.
            rule_refs = {
                "theme": rule_event.theme_ref,
                "destination": rule_event.destination_ref,
                "source": rule_event.source_ref,
                "recipient": rule_event.recipient_ref,
            }
            fused_refs = {
                "theme": fused_event.theme_ref,
                "destination": fused_event.destination_ref,
                "source": fused_event.source_ref,
                "recipient": fused_event.recipient_ref,
            }
            role_groups = [tuple(rule_schema.required_roles), *rule_schema.required_any_roles]
            for group in role_groups:
                resolved_rule_roles = [
                    role for role in group
                    if (rule_grounded.entity(rule_refs.get(role)) is not None and
                        bool(rule_grounded.entity(rule_refs.get(role)).entity_id))
                ]
                if not resolved_rule_roles:
                    continue
                resolved_fused_roles = [
                    role for role in group
                    if (fused_grounded.entity(fused_refs.get(role)) is not None and
                        bool(fused_grounded.entity(fused_refs.get(role)).entity_id))
                ]
                if not resolved_fused_roles:
                    errors.append(
                        f"GROUNDED_RULE_ROLE_LOST:{index}:{'/'.join(group)}"
                    )
            # An unresolved rule role is still a constraint witness.  The
            # provider may help locate it, but it may not replace an explicit
            # user attribute (for example purple -> visible red) merely
            # because another object is available.  This check is deliberately
            # independent of whether the rule candidate found a physical ID;
            # otherwise an absent target could be converted into an executable
            # target during LLM fusion.
            for role, rule_ref in rule_refs.items():
                rule_entity = rule_graph.entity(rule_ref) if rule_ref else None
                fused_ref = fused_refs.get(role)
                # Use the grounded copy here.  The original fused graph is
                # intentionally still ID-free at this stage; checking it
                # would miss exactly the provider-created red binding.
                fused_entity = fused_grounded.entity(fused_ref) if fused_ref else None
                if rule_entity is None or fused_entity is None or not fused_entity.entity_id:
                    continue
                # A known parser failure can copy the destination mention into
                # the theme role (for example both roles become "table" in a
                # composite fetch-and-place sentence).  That inferred
                # category is not an explicit target constraint and must not
                # block an evidenced provider correction.  Explicit
                # attributes such as color remain protected even in this
                # situation.
                duplicate_role_descriptor = any(
                    other_ref and other_ref != rule_ref
                    and (other_entity := rule_graph.entity(other_ref)) is not None
                    and str(other_entity.mention or "") == str(rule_entity.mention or "")
                    and str(other_entity.category or "") == str(rule_entity.category or "")
                    for other_ref in rule_refs.values()
                )
                if role == "theme" and not (rule_entity.attributes or {}):
                    # Also cover multi-event parses where event 1 has only a
                    # theme and event 2 introduces the destination.  If the
                    # parser gave both roles the same descriptor, the theme
                    # category came from the duplicated destination mention.
                    duplicate_role_descriptor = duplicate_role_descriptor or any(
                        (destination_entity := rule_graph.entity(event.destination_ref)) is not None
                        and destination_entity.local_ref != rule_ref
                        and str(destination_entity.mention or "") == str(rule_entity.mention or "")
                        and str(destination_entity.category or "") == str(rule_entity.category or "")
                        for event in rule_graph.events
                        if event.destination_ref
                    )
                if duplicate_role_descriptor and not (rule_entity.attributes or {}):
                    continue
                mismatch = self._explicit_entity_constraint_mismatch(
                    scene, fused_entity.entity_id, rule_entity
                )
                if mismatch:
                    errors.append(
                        f"EXPLICIT_RULE_CONSTRAINT_CHANGED:{index}:{role}:{mismatch}"
                    )
            if rule_action == final_action:
                continue
            final_refs = {
                "theme": fused_event.theme_ref,
                "destination": fused_event.destination_ref,
                "source": fused_event.source_ref,
                "recipient": fused_event.recipient_ref,
            }
            if schema.missing_roles(name for name, ref in final_refs.items() if ref):
                errors.append(f"GROUNDED_INCOMPLETE_ACTION:{index}:{rule_action}->{final_action}")
                continue
            for role in schema.required_roles:
                ref = final_refs.get(role)
                entity = fused_grounded.entity(ref) if ref else None
                if entity is None:
                    errors.append(f"GROUNDED_MISSING_ROLE:{index}:{role}")
                    continue
                # Human recipients are symbolic entities and are normalized
                # to operator after this comparison.
                symbolic = role == "recipient" and str(entity.category).lower() in {"human", "operator", "user"}
                if not entity.entity_id and not symbolic:
                    errors.append(f"GROUNDED_UNRESOLVED_ROLE:{index}:{role}")
            if final_action in {"PLACE", "FETCH", "TRANSFER", "POUR", "STACK"}:
                theme = fused_grounded.entity(fused_event.theme_ref) if fused_event.theme_ref else None
                destination = fused_grounded.entity(fused_event.destination_ref) if fused_event.destination_ref else None
                if theme is not None and destination is not None and theme.entity_id and theme.entity_id == destination.entity_id:
                    errors.append(f"GROUNDED_SELF_DESTINATION:{index}:{final_action}")
            # A newly introduced optional role must not resolve to the same
            # physical entity as an existing role.  This is usually a
            # provider role-label error (for example recipient=the tray that
            # is already the destination), not a legitimate extra semantic
            # fact.  Reject the fused candidate so the valid rule graph is
            # retained instead of allowing the duplicate binding to poison
            # status validation.
            rule_refs = {
                "theme": rule_event.theme_ref,
                "destination": rule_event.destination_ref,
                "source": rule_event.source_ref,
                "recipient": rule_event.recipient_ref,
            }
            fused_refs = {
                "theme": fused_event.theme_ref,
                "destination": fused_event.destination_ref,
                "source": fused_event.source_ref,
                "recipient": fused_event.recipient_ref,
            }
            resolved_ids: Dict[str, str] = {}
            for role, ref in fused_refs.items():
                entity = fused_grounded.entity(ref) if ref else None
                if entity is not None and entity.entity_id:
                    resolved_ids[role] = str(entity.entity_id)
            for left, left_id in resolved_ids.items():
                for right, right_id in resolved_ids.items():
                    if left >= right or left_id != right_id:
                        continue
                    if not rule_refs.get(left) or not rule_refs.get(right):
                        errors.append(f"GROUNDED_ROLE_COLLISION:{index}:{left}={right}:{left_id}")
        return list(dict.fromkeys(errors))

    @staticmethod
    def _explicit_entity_constraint_mismatch(
        scene: Any, entity_id: str, rule_entity: SemanticEntity
    ) -> Optional[str]:
        """Return the first explicit rule constraint violated by a scene object.

        Rule parsing owns what the user explicitly said; perception owns the
        observed object.  A provider can fill an unresolved role only when
        both agree.  Missing evidence is treated as a mismatch for explicit
        attributes, which keeps an unverified object from becoming executable.
        Internal bookkeeping attributes and spatial wording are not identity
        constraints and are intentionally excluded here.
        """
        if scene is None or not entity_id or rule_entity is None:
            return None
        finder = getattr(scene, "find_object", None)
        obj = finder(entity_id) if callable(finder) else next(
            (item for item in getattr(scene, "objects", []) or []
             if str(getattr(item, "id", "")) == str(entity_id)),
            None,
        )
        if obj is None:
            return None

        aliases = {
            "红": "red", "红色": "red", "蓝": "blue", "蓝色": "blue",
            "绿": "green", "绿色": "green", "黄": "yellow", "黄色": "yellow",
            "紫": "purple", "紫色": "purple", "白": "white", "白色": "white",
            "黑": "black", "黑色": "black",
        }

        def norm(value: Any) -> str:
            text = str(value or "").strip().lower()
            return aliases.get(text, text)

        def values(value: Any) -> set[str]:
            if isinstance(value, (list, tuple, set)):
                return {norm(item) for item in value if item is not None}
            return {norm(value)} if value is not None else set()

        explicit_attributes = {
            str(key): value for key, value in (rule_entity.attributes or {}).items()
            if value is not None and not str(key).startswith("_")
            and str(key) not in {"spatial_relation", "scene_derived"}
        }
        observed_attributes = dict(getattr(obj, "attributes", {}) or {})
        names = {
            getattr(obj, "name", ""),
            getattr(obj, "original_mention", ""),
            getattr(obj, "label", ""),
            getattr(obj, "specific_class", ""),
        }
        for key, expected in explicit_attributes.items():
            actual = observed_attributes.get(key)
            actual_values = values(actual)
            if not actual_values or actual_values == {"unknown", "none", ""}:
                # Color is commonly present only in a detector label.  Use
                # the observed names as a second, read-only evidence source.
                if key == "color":
                    actual_values = {
                        norm(token)
                        for token in names
                        for token in str(token).replace("-", "_").split("_")
                        if token
                    }
                if not actual_values:
                    return f"{key}=UNVERIFIED"
            expected_values = values(expected)
            if expected_values and not (expected_values & actual_values):
                return f"{key}:{expected}->{actual or 'unknown'}"

        generic_categories = {
            "object", "item", "unknown", "entity", "container", "material"
        }
        category = norm(rule_entity.category)
        if category and category not in generic_categories:
            observed_categories = {
                norm(getattr(obj, "specific_class", None)),
                norm(getattr(obj, "label", None)),
                norm(getattr(obj, "parent_class", None)),
                *(norm(item) for item in (getattr(obj, "parent_classes", []) or [])),
            }
            observed_categories.discard("")
            if category not in observed_categories:
                return f"category:{rule_entity.category}->{getattr(obj, 'specific_class', None) or getattr(obj, 'label', None) or 'unknown'}"
        return None

    def _protect_rule_grounded_roles(self, rule_graph: SemanticTaskGraph,
                                     fused_graph: SemanticTaskGraph,
                                     scene: Any) -> List[str]:
        """Keep uniquely grounded rule roles from being replaced by a provider.

        The LLM may repair a missing or ambiguous role, but it must not replace
        a role that the deterministic grounding authority has already resolved
        to a unique perception entity.  This is the post-grounding form of
        "fill, don't replace" and still lets the provider repair FETCH-style
        missing destinations.
        """
        rule_grounded, _ = self.grounder.ground_graph(
            rule_graph.model_copy(deep=True), scene
        )
        protected: List[str] = []
        role_names = ("theme", "destination", "source", "recipient")
        rule_events = {event.sequence_index: event for event in rule_grounded.events}
        fused_events = {event.sequence_index: event for event in fused_graph.events}
        for index, rule_event in rule_events.items():
            fused_event = fused_events.get(index)
            if fused_event is None:
                continue
            action_schema = get_action_schema(rule_event.action)
            for role in role_names:
                rule_ref = getattr(rule_event, f"{role}_ref", None)
                rule_entity = rule_grounded.entity(rule_ref) if rule_ref else None
                if rule_entity is None or not rule_entity.entity_id:
                    continue
                # A generic FETCH destination is deliberately marked as
                # unresolved before grounding.  Grounding it to the only
                # tray in a scene would turn a language-only placeholder
                # into a false rule authority and suppress a provider's
                # evidenced receive-zone description.  Let the FETCH role
                # normalizer/scene completion handle that case instead.
                if (normalize_action(rule_event.action) == "FETCH" and
                        role == "destination" and
                        SemanticFusion._is_unresolved_generic_entity(
                            rule_graph, rule_ref
                        )):
                    continue
                # A unique scene match is not enough to make the rule role
                # authoritative: the parser may have selected a uniquely
                # named but physically incompatible object (for example the
                # word "夹具" before the actual red cup in a GRASP clause).
                # Let an evidenced provider role repair that semantic error;
                # the final grounding/affordance validator still owns safety.
                required_affordances = action_schema.required_affordances_for(role)
                if required_affordances and not self._scene_role_affordance_ok(
                        scene, rule_entity.entity_id, required_affordances):
                    continue
                field = f"{role}_ref"
                current_ref = getattr(fused_event, field, None)
                if current_ref == rule_ref:
                    continue
                if fused_graph.entity(rule_ref) is None:
                    fused_graph.entities.append(rule_entity.model_copy(deep=True))
                setattr(fused_event, field, rule_ref)
                protected.append(f"event[{index}].{role}:{rule_entity.entity_id}")
        return protected

    @staticmethod
    def _scene_role_affordance_ok(scene: Any, entity_id: str,
                                  required_affordances: Any) -> bool:
        if scene is None or not entity_id:
            return True
        obj = scene.find_object(entity_id) if hasattr(scene, "find_object") else None
        if obj is None:
            return True
        affordances = {
            str(getattr(item, "value", item)).lower()
            for item in (getattr(obj, "affordances", []) or [])
        }
        return any(str(item).lower() in affordances for item in required_affordances)

    @staticmethod
    def _repair_scene_candidate_refs(rule_graph: SemanticTaskGraph,
                                      llm_graph: SemanticTaskGraph,
                                      scene: Any = None) -> None:
        """Map only explicit scene candidate keys back to rule-local refs.

        ``scene-object-N`` is a read-only prompt alias, not a graph reference.
        It is accepted only when N identifies an actual scene object and that
        object can be matched to one deterministic rule entity by category and
        attributes.  Arbitrary provider IDs remain invalid and are rejected by
        ``validate_local_references``.
        """
        if scene is None or not getattr(scene, "objects", None):
            return
        scene_objects = list(scene.objects)
        aliases: Dict[str, str] = {}
        # Some providers emit a role reference (e1/e2) but omit the matching
        # entity atom entirely.  The event still carries the provider's
        # semantic decision; recover only the corresponding role from the
        # deterministic, scene-grounded rule event at the same sequence
        # position.  This keeps provider output from inventing a physical ID
        # while preventing a formatting omission from forcing a full
        # fallback.
        rule_events = sorted(rule_graph.events, key=lambda item: item.sequence_index)
        candidate_events = sorted(llm_graph.events, key=lambda item: item.sequence_index)
        for incoming in candidate_events:
            rule_event = next((item for item in rule_events
                               if item.sequence_index == incoming.sequence_index), None)
            if rule_event is None and rule_events:
                # Providers may split one grounded operation into GRASP then
                # PLACE events.  Reconcile those fragments against the
                # single evidenced rule event instead of rejecting an
                # otherwise recoverable candidate for an omitted entity atom.
                rule_event = next((item for item in rule_events
                                   if normalize_action(item.action) == normalize_action(incoming.action)), None)
                rule_event = rule_event or rule_events[0]
            if rule_event is None:
                continue
            # Preserve the deterministic action contract when a provider
            # collapses a placement sentence into GRASP/FETCH.  The provider
            # still contributes target semantics, while action/destination
            # requirements remain anchored to the evidenced rule parse.
            if (normalize_action(incoming.action) != normalize_action(rule_event.action)
                    and normalize_action(rule_event.action) != "CUSTOM"):
                incoming.action = rule_event.action
            for role in ("theme", "destination", "source", "recipient"):
                incoming_ref = getattr(incoming, f"{role}_ref", None)
                rule_ref = getattr(rule_event, f"{role}_ref", None)
                incoming_entity = llm_graph.entity(incoming_ref) if incoming_ref else None
                placeholder = bool(incoming_entity and
                                   (incoming_entity.attributes or {}).get("_llm_placeholder"))
                if incoming_ref and (incoming_entity is None or placeholder) and rule_ref:
                    aliases[str(incoming_ref)] = rule_ref
                    if llm_graph.entity(rule_ref) is None:
                        rule_entity = rule_graph.entity(rule_ref)
                        if rule_entity is not None:
                            # Copy only the semantic descriptor.  Preserve an
                            # unresolved entity_id as None; final grounding
                            # remains owned by the deterministic scene graph.
                            descriptor = rule_entity.model_copy(deep=True)
                            descriptor.entity_id = None
                            llm_graph.entities.append(descriptor)
        # Complete only the semantic category for a provider entity that uses
        # a read-only scene alias.  Never copy scene attributes into an LLM
        # atom: doing so turns the provider's physical hint into a hidden
        # unique-ID decision and can resolve an actually ambiguous scene.
        for entity in llm_graph.entities:
            candidate_key = str(entity.candidate_key or "")
            if not re.fullmatch(r"scene-object-\d+", candidate_key):
                continue
            index = int(candidate_key.rsplit("-", 1)[-1]) - 1
            if index < 0 or index >= len(scene_objects):
                continue
            obj = scene_objects[index]
            category = getattr(obj, "specific_class", None) or getattr(obj, "label", None)
            if not entity.category and category:
                entity.category = category
            if not entity.mention:
                entity.mention = category or candidate_key
        for event in llm_graph.events:
            refs = [event.theme_ref, event.destination_ref, event.source_ref,
                    event.recipient_ref, *event.obstacle_refs]
            for ref in refs:
                if not ref or not re.fullmatch(r"scene-object-\d+", str(ref)):
                    continue
                index = int(str(ref).rsplit("-", 1)[-1]) - 1
                if index < 0 or index >= len(scene_objects):
                    continue
                # Do not map a provider scene alias onto the first compatible
                # rule entity.  Category-only compatibility is not identity;
                # the deterministic grounder must still see all matching
                # scene objects and either resolve uniquely or clarify.

        def resolve(value: Optional[str]) -> Optional[str]:
            return aliases.get(value, value) if value else value

        for event in llm_graph.events:
            event.theme_ref = resolve(event.theme_ref)
            event.destination_ref = resolve(event.destination_ref)
            event.source_ref = resolve(event.source_ref)
            event.recipient_ref = resolve(event.recipient_ref)
            event.obstacle_refs = [resolve(item) or item for item in event.obstacle_refs]

        # Collapse provider fragments that now describe the same grounded
        # action.  Prefer the fragment carrying the most role information
        # (typically the PLACE event with a destination) and preserve its
        # evidence/parameters.
        deduped: list[Any] = []
        for event in sorted(llm_graph.events, key=lambda item: item.sequence_index):
            signature = (
                normalize_action(event.action), event.theme_ref,
                event.destination_ref, event.source_ref, event.recipient_ref,
            )
            existing = next((item for item in deduped if (
                normalize_action(item.action), item.theme_ref,
                item.destination_ref, item.source_ref, item.recipient_ref,
            ) == signature), None)
            if existing is None:
                deduped.append(event)
            else:
                score = lambda item: sum(bool(getattr(item, field, None))
                                         for field in ("theme_ref", "destination_ref", "source_ref", "recipient_ref"))
                if score(event) > score(existing):
                    deduped[deduped.index(existing)] = event
        llm_graph.events = deduped

        # Providers frequently serialize event references as event-1,
        # event-place-1, evt-stack-1, or a numeric index.  First normalize
        # them to the candidate's own event IDs.  The fusion layer performs a
        # second, separate mapping onto rule event IDs after this candidate
        # has passed its own local-reference validation.
        candidate_events = sorted(llm_graph.events, key=lambda item: item.sequence_index)
        event_aliases: Dict[str, str] = {}
        for index, incoming in enumerate(candidate_events):
            target_id = incoming.event_id
            if target_id:
                event_aliases[target_id] = target_id
                event_aliases[str(index)] = target_id
                event_aliases[str(index + 1)] = target_id
                event_aliases[f"e{index + 1}"] = target_id
                event_aliases[f"ev{index + 1}"] = target_id
                event_aliases[f"event_{index + 1}"] = target_id
                event_aliases[f"event-{index}"] = target_id
                event_aliases[f"event-{index + 1}"] = target_id
                event_aliases[f"evt-{index}"] = target_id
                event_aliases[f"evt-{index + 1}"] = target_id
                action_slug = str(incoming.action or "").lower()
                if action_slug:
                    event_aliases[f"event-{action_slug}-{index + 1}"] = target_id
                    event_aliases[f"evt-{action_slug}-{index + 1}"] = target_id

        known_event_ids = {item.event_id for item in candidate_events}

        def event_ref(value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            value = str(value)
            if value in known_event_ids:
                return value
            return event_aliases.get(value, value)

        for item in llm_graph.relations:
            item.source_event = event_ref(item.source_event)
            item.target_event = event_ref(item.target_event)
        for item in llm_graph.conditions:
            item.on_true_event_ids = [event_ref(value) or value for value in item.on_true_event_ids]
            item.on_false_event_ids = [event_ref(value) or value for value in item.on_false_event_ids]
        for item in llm_graph.prohibitions:
            item.scope_event_ids = [event_ref(value) or value for value in item.scope_event_ids]

    @staticmethod
    def _decision_dict(decision: Any) -> Dict[str, Any]:
        if hasattr(decision, "model_dump"):
            return decision.model_dump(mode="json")
        if hasattr(decision, "__dict__"):
            return dict(decision.__dict__)
        return dict(decision)

    @staticmethod
    def _initial_status(parsed_task: ParsedTask, grounded_task: Any) -> PlanStatus:
        if "unsupported_capability" in parsed_task.unmet_roles:
            return PlanStatus.BLOCKED
        if parsed_task.unmet_roles or grounded_task.required_clarifications:
            return PlanStatus.NEEDS_CLARIFICATION
        if parsed_task.ambiguity_resolution:
            return PlanStatus.NEEDS_CLARIFICATION
        return PlanStatus.READY


def _entity_ref(
    graph: SemanticTaskGraph,
    local_ref: Optional[str],
    role: str,
    scene: Any = None,
) -> Optional[SemanticEntityRef]:
    if not local_ref:
        return None
    entity = graph.entity(local_ref)
    if entity is None:
        return None
    attrs = dict(entity.attributes or {})
    if scene is not None and entity.entity_id and hasattr(scene, "find_object"):
        scene_obj = scene.find_object(entity.entity_id)
        if scene_obj is not None:
            attrs.setdefault("parent_class", getattr(scene_obj, "parent_class", None))
    symbolic_user = role == "recipient" and entity.category in {"human", "operator"} and entity.mention in {"我", "用户", "me", "user"}
    if symbolic_user:
        attrs.setdefault("parent_class", "agent")
    return SemanticEntityRef(
        mention=entity.mention,
        specific_class=entity.category,
        parent_class=attrs.get("parent_class"),
        entity_id="user" if symbolic_user else entity.entity_id,
        role=role,
        text_span=(entity.evidence_spans[0] if entity.evidence_spans else entity.mention),
        grounding_confidence=1.0 if entity.entity_id else 0.0,
        source="scene" if entity.entity_id else "nl",
        ontology_path=[item for item in (entity.category, attrs.get("parent_class")) if item],
        match_evidence=[item.source_text for item in entity.evidence if item.source_text],
    )


def _constraint_from_graph(item: Any) -> ParsedConstraint:
    operator = str(item.operator or "exact").lower()
    try:
        op = ConstraintOperator(operator)
    except ValueError:
        op = ConstraintOperator.EXACT
    source_kind = {
        "exact": ConstraintSourceKind.USER_EXACT,
        "min": ConstraintSourceKind.USER_MIN,
        "max": ConstraintSourceKind.USER_MAX,
        "range": ConstraintSourceKind.USER_RANGE,
    }.get(op.value, ConstraintSourceKind.USER_EXACT)
    return ParsedConstraint(
        constraint_id=item.constraint_id,
        parameter=item.parameter,
        operator=op,
        source="user",
        source_kind=source_kind,
        text_span=item.evidence_span,
        unit=item.unit or "",
        value=item.value,
        min_value=item.min_value,
        max_value=item.max_value,
        normalized_value=item.value if item.value is not None else item.max_value,
        confidence=1.0,
        is_hard=bool(item.hard),
        provenance=["semantic_graph"],
    )


def parsed_task_from_graph(
    graph: SemanticTaskGraph,
    instruction: str,
    scene: Any = None,
    grounding_decisions: Optional[List[Dict[str, Any]]] = None,
    fusion_trace: Optional[List[Dict[str, Any]]] = None,
) -> ParsedTask:
    """One-way compatibility projection from the final graph.

    This function never scans the instruction.  All semantic values come from
    graph atoms; the original instruction is copied only as provenance.
    """
    events = sorted(graph.events, key=lambda item: item.sequence_index)
    # The final manipulation event is the task summary for a linear command.
    # Earlier events remain executable steps, but selecting the first event
    # made "grasp then place" look like GRASP and broke the PLACE contract.
    primary = next((item for item in reversed(events) if item.action != "WAIT"),
                   events[0] if events else None)
    action_value = normalize_action(primary.action if primary else "CUSTOM")
    try:
        action = TaskActionKind(action_value)
    except ValueError:
        action = TaskActionKind.CUSTOM

    theme = _entity_ref(graph, primary.theme_ref if primary else None, "theme", scene=scene)
    source = _entity_ref(graph, primary.source_ref if primary else None, "source", scene=scene)
    destination = _entity_ref(graph, primary.destination_ref if primary else None, "destination", scene=scene)
    recipient = _entity_ref(graph, primary.recipient_ref if primary else None, "recipient", scene=scene)
    if action == TaskActionKind.WAIT:
        # WAIT has no public manipulation target even if a provider included a
        # monitoring noun as an event role.
        theme = None
        source = None
        destination = None
        recipient = None
    support_surface = destination if action == TaskActionKind.PLACE else None

    obstacle_refs: List[str] = []
    for event in events:
        obstacle_refs.extend(event.obstacle_refs)
    # Only contact/avoid prohibitions are collision obstacles.  A
    # FORBID_ACTION prohibition may intentionally target the same object as a
    # positive event (the correct result is BLOCKED, not “avoid the target”).
    obstacle_refs.extend(
        item.target_ref for item in graph.prohibitions
        if item.target_ref and item.type == "NO_CONTACT"
    )
    obstacles: List[SemanticEntityRef] = []
    seen_obstacles = set()
    for local_ref in obstacle_refs:
        if local_ref in seen_obstacles:
            continue
        obstacle_atom = graph.entity(local_ref)
        # Scene-derived blockers are still real safety obstacles.  They remain
        # distinct from language-mentioned prohibitions in the graph, but the
        # public task must expose their scene IDs so the downstream planner
        # cannot silently lose collision avoidance information.
        ref = _entity_ref(graph, local_ref, "obstacle", scene=scene)
        if ref is not None:
            obstacles.append(ref)
            seen_obstacles.add(local_ref)

    user_constraints = [_constraint_from_graph(item) for item in graph.constraints]
    steps = [
        {
            "step_index": event.sequence_index,
            "action": event.action,
            "theme_mention": graph.entity(event.theme_ref).mention
            if graph.entity(event.theme_ref) else None,
            "destination_mention": graph.entity(event.destination_ref).mention
            if graph.entity(event.destination_ref) else None,
            "event_id": event.event_id,
        }
        for event in events
    ]
    if any(item.action == "WAIT" for item in events) and action == TaskActionKind.CUSTOM:
        action = TaskActionKind.WAIT
    schema = get_action_schema(action.value)
    present_roles = {
        name for name, value in {
            "theme": theme,
            "source": source,
            "destination": destination,
            "recipient": recipient,
            "condition": graph.conditions[0] if graph.conditions else None,
        }.items() if value is not None
    }
    unmet_roles = schema.missing_roles(present_roles)
    if action == TaskActionKind.WAIT and not graph.conditions:
        unmet_roles.append("condition")
    notes: List[str] = []
    unsupported_evidence = (graph.metadata or {}).get("unsupported_action_evidence")
    if unsupported_evidence:
        notes.append(f"unsupported_capability:{unsupported_evidence}")
        unmet_roles.append("unsupported_capability")
    if graph.ambiguities:
        notes.extend(f"ambiguity:{item.type}:{item.clarification or item.status}" for item in graph.ambiguities)
    if not events:
        notes.append("semantic_graph:no_events")

    graph_dict = graph.model_dump(mode="json")
    return ParsedTask(
        instruction=instruction,
        action=action,
        theme=theme,
        source=source,
        destination=destination,
        recipient=recipient,
        obstacle=obstacles,
        support_surface=support_surface,
        manner=(graph.metadata or {}).get("manner"),
        motion_state=MotionState(
            state=(graph.metadata or {}).get("motion_state", "static")
            if isinstance((graph.metadata or {}).get("motion_state", "static"), str)
            else "static",
            confidence=0.8,
        ),
        user_constraints=user_constraints,
        raw_mentions=list(dict.fromkeys([
            item.mention for item in (theme, source, destination, recipient, *obstacles)
            if item is not None
        ])),
        unmet_roles=list(dict.fromkeys(unmet_roles)),
        parse_confidence=0.9 if events else 0.2,
        grounding_confidence=(
            sum(item.grounding_confidence for item in (theme, destination, source, recipient) if item)
            / max(1, len([item for item in (theme, destination, source, recipient) if item]))
        ),
        constraint_confidence=0.95 if user_constraints else 0.5,
        notes=notes,
        clarification=(graph.ambiguities[0].clarification if graph.ambiguities else None),
        steps=steps,
        conditions=[item.model_dump(mode="json") for item in graph.conditions],
        prohibitions=[item.model_dump(mode="json") for item in graph.prohibitions],
        semantic_task_graph=graph_dict,
        grounding_decisions=list(grounding_decisions or []),
        ambiguity_resolution=[item.model_dump(mode="json") for item in graph.ambiguities],
        fusion_trace=list(fusion_trace or []),
        execution_contract={
            "semantic_authority": "SemanticCompiler",
            "entity_ids_verified": all(
                not entity.entity_id or scene is None or any(
                    getattr(obj, "id", None) == entity.entity_id
                    for obj in getattr(scene, "objects", []) or []
                ) for entity in graph.entities
            ),
        },
    )
