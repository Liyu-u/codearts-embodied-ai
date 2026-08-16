"""Projection from the authoritative semantic/IR layers to the public JSON.

This adapter is deliberately one-way.  It never scans the original command
and it never consumes simulation ground truth.  All executable values come
from the grounded ParsedTask, semantic graph, validation result, and the
object IDs exposed by perception.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from robot_intent_agent.schemas.intent_output import (
    IntentConstraintOutput,
    IntentOutput,
)
from robot_intent_agent.schemas.perception_observation import inference_observation
from robot_intent_agent.task_semantics import PlanStatus, TaskActionKind


ACTION_OUTPUT_MAP = {
    TaskActionKind.GRASP.value: "grasp",
    TaskActionKind.DYNAMIC_GRASP.value: "dynamic_grasp",
    TaskActionKind.PLACE.value: "pick_and_place",
    TaskActionKind.FETCH.value: "fetch",
    TaskActionKind.TRANSFER.value: "transfer",
    TaskActionKind.HANDOVER.value: "handover",
    TaskActionKind.PUSH.value: "push",
    TaskActionKind.POUR.value: "pour",
    TaskActionKind.STACK.value: "stack",
    TaskActionKind.WAIT.value: "wait",
    TaskActionKind.CUSTOM.value: "unsupported",
}


def _model_dump(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value or {})


def _known_entity_ids(observation: Optional[Dict[str, Any]], scene: Any = None) -> Set[str]:
    known: Set[str] = set()
    clean = inference_observation(observation or {})
    for item in clean.get("objects", []) or []:
        if isinstance(item, dict) and item.get("object_id"):
            known.add(str(item["object_id"]))
    for item in clean.get("regions", []) or []:
        if isinstance(item, dict) and item.get("region_id"):
            known.add(str(item["region_id"]))
    if scene is not None:
        for item in getattr(scene, "objects", []) or []:
            if getattr(item, "id", None):
                known.add(str(item.id))
    known.update({"user", "operator"})
    return known


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    if raw == PlanStatus.READY_WITH_SAFE_SUBSTITUTION.value:
        return "READY"
    if raw in {"READY", "NEEDS_CLARIFICATION", "BLOCKED"}:
        return str(raw)
    return "BLOCKED"


def _action(parsed_task: Any) -> str:
    raw = getattr(getattr(parsed_task, "action", None), "value", "CUSTOM")
    return ACTION_OUTPUT_MAP.get(raw, "unsupported")


def _graph(parsed_task: Any) -> Dict[str, Any]:
    graph = getattr(parsed_task, "semantic_task_graph", None)
    return graph if isinstance(graph, dict) else {}


def _entity_id(ref: Any) -> Optional[str]:
    value = getattr(ref, "entity_id", None) if ref is not None else None
    return str(value) if value else None


def _resolved_attributes(scene: Any, entity_id: Optional[str]) -> Dict[str, Any]:
    """Expose only perception-derived attributes for a grounded entity.

    Private scene-builder bookkeeping (keys beginning with ``_``) is not a
    downstream fact and must not leak into the public contract.  In
    particular, this function can only see the sanitized perception scene,
    so evaluation-only material/category facts cannot be copied here.
    """

    if not scene or not entity_id:
        return {}
    obj = next(
        (item for item in getattr(scene, "objects", []) or []
         if str(getattr(item, "id", "")) == str(entity_id)),
        None,
    )
    if obj is None:
        return {}
    result: Dict[str, Any] = {}
    for key, value in (getattr(obj, "attributes", {}) or {}).items():
        if not str(key).startswith("_") and value is not None:
            result[str(key)] = value
    for key in ("specific_class", "parent_class", "label"):
        value = getattr(obj, key, None)
        if value and key not in result:
            result[key] = value
    return result


def _target_ids(parsed_task: Any, graph: Dict[str, Any], ambiguity: bool) -> List[str]:
    if ambiguity:
        return []
    values: List[str] = []
    for event in graph.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        ref = event.get("theme_ref")
        entity = next((item for item in graph.get("entities", []) or []
                       if isinstance(item, dict) and item.get("local_ref") == ref), None)
        if entity and entity.get("entity_id"):
            value = str(entity["entity_id"])
            if value not in values:
                values.append(value)
    theme_id = _entity_id(getattr(parsed_task, "theme", None))
    if theme_id and theme_id not in values:
        values.insert(0, theme_id)
    return values


def _constraint_outputs(parsed_task: Any, graph: Dict[str, Any]) -> List[IntentConstraintOutput]:
    result: List[IntentConstraintOutput] = []
    for item in graph.get("constraints", []) or []:
        if not isinstance(item, dict):
            continue
        parameter = item.get("parameter")
        ctype = "force_limit" if parameter == "force_n" else (
            "velocity_limit" if parameter == "velocity_ms" else "numeric_limit"
        )
        result.append(IntentConstraintOutput(
            constraint_type=ctype,
            operator=str(item.get("operator", "")).lower() or None,
            min_value=item.get("min_value"),
            max_value=item.get("max_value"),
            value=item.get("value"),
            unit=item.get("unit") or None,
            hard=bool(item.get("hard", True)),
        ))
    seen = {(c.constraint_type, c.operator, c.target_entity, c.min_value, c.max_value, c.value)
            for c in result}
    for item in graph.get("prohibitions", []) or []:
        if not isinstance(item, dict):
            continue
        target_ref = item.get("target_ref")
        entity = next((entity for entity in graph.get("entities", []) or []
                       if isinstance(entity, dict) and entity.get("local_ref") == target_ref), None)
        target_id = entity.get("entity_id") if entity else None
        if item.get("type") == "NO_CONTACT":
            candidate = IntentConstraintOutput(
                constraint_type="no_contact", target_entity=target_id, hard=True
            )
        elif item.get("type") == "FORBID_ACTION":
            candidate = IntentConstraintOutput(
                constraint_type="forbid_action", target_entity=target_id, hard=True
            )
        else:
            continue
        key = (candidate.constraint_type, candidate.operator, candidate.target_entity,
               candidate.min_value, candidate.max_value, candidate.value)
        if key not in seen:
            result.append(candidate)
            seen.add(key)
    return result


class IntentOutputAdapter:
    """Build and validate the stable downstream intent JSON."""

    def build(
        self,
        ir: Any,
        observation: Optional[Dict[str, Any]] = None,
        *,
        observation_id: Optional[str] = None,
        scene_id: Optional[str] = None,
        status_override: Optional[str] = None,
        execution_allowed_override: Optional[bool] = None,
        intent_id_override: Optional[str] = None,
    ) -> IntentOutput:
        parsed_task = getattr(ir, "parsed_task", None)
        graph = _graph(parsed_task)
        validation = getattr(ir, "validation_result", None)
        ambiguity_records = getattr(parsed_task, "ambiguity_resolution", []) or []
        ambiguous = bool(ambiguity_records) or bool(graph.get("ambiguities"))
        plan_status = _status(status_override or getattr(getattr(ir, "plan_metadata", None), "plan_status", None))
        execution_allowed = bool(
            execution_allowed_override
            if execution_allowed_override is not None
            else getattr(validation, "execution_allowed", False)
        )
        if plan_status != "READY":
            execution_allowed = False

        theme = getattr(parsed_task, "theme", None)
        destination = getattr(parsed_task, "destination", None)
        recipient = getattr(parsed_task, "recipient", None)
        target_id = None if ambiguous else _entity_id(theme)
        destination_id = None if ambiguous else (_entity_id(destination) or (
            _entity_id(recipient) if str(getattr(getattr(parsed_task, "action", None), "value", "")) == "FETCH" else None
        ))
        target_ids = _target_ids(parsed_task, graph, ambiguous)

        metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
        reference_ref = metadata.get("reference_ref")
        reference_entity = next((item for item in graph.get("entities", []) or []
                                 if isinstance(item, dict) and item.get("local_ref") == reference_ref), None)
        reference_id = None if ambiguous else (
            str(reference_entity.get("entity_id"))
            if reference_entity and reference_entity.get("entity_id") else None
        )
        sort_criterion = metadata.get("sort_criterion")

        attributes: Dict[str, Any] = {}
        theme_ref = graph.get("events", [{}])[-1].get("theme_ref") if graph.get("events") else None
        theme_entity = next((item for item in graph.get("entities", []) or []
                             if isinstance(item, dict) and item.get("local_ref") == theme_ref), None)
        if theme_entity:
            attributes = dict(theme_entity.get("attributes") or {})

        errors: List[Dict[str, Any]] = []
        for issue in getattr(validation, "issues", []) or []:
            errors.append({
                "code": getattr(issue, "code", "VALIDATION_ERROR"),
                "message": getattr(issue, "message", str(issue)),
                "severity": getattr(issue, "severity", "error"),
            })
        unsupported: List[str] = []
        if _action(parsed_task) == "unsupported":
            unsupported.append(str(getattr(getattr(parsed_task, "action", None), "value", "CUSTOM")))

        known_ids = _known_entity_ids(observation, getattr(ir, "scene", None))
        for field_name, value in (("target_object", target_id), ("destination", destination_id),
                                  ("reference_object", reference_id)):
            if value and value not in known_ids:
                errors.append({"code": "OUTPUT_UNKNOWN_ENTITY_ID", "field": field_name,
                               "entity_id": value, "severity": "error"})
                plan_status = "BLOCKED"
                execution_allowed = False
        for item in target_ids:
            if item not in known_ids:
                errors.append({"code": "OUTPUT_UNKNOWN_ENTITY_ID", "field": "target_objects",
                               "entity_id": item, "severity": "error"})
                plan_status = "BLOCKED"
                execution_allowed = False
        for constraint in _constraint_outputs(parsed_task, graph):
            for field_name, value in (("target_entity", constraint.target_entity),
                                      ("subject_entity", constraint.subject_entity)):
                if value and value not in known_ids:
                    errors.append({"code": "OUTPUT_UNKNOWN_ENTITY_ID", "field": f"constraints.{field_name}",
                                   "entity_id": value, "severity": "error"})
                    plan_status = "BLOCKED"
                    execution_allowed = False

        # READY is never allowed to leave the adapter without the required
        # entity binding for the supported task shape.
        raw_action = getattr(getattr(parsed_task, "action", None), "value", "CUSTOM")
        if plan_status == "READY" and raw_action in {"GRASP", "FETCH", "PLACE", "TRANSFER",
                                                      "HANDOVER", "PUSH", "POUR", "STACK", "DYNAMIC_GRASP"} and not target_id:
            errors.append({"code": "OUTPUT_MISSING_TARGET_ID", "severity": "error"})
            plan_status = "BLOCKED"
            execution_allowed = False
        if plan_status == "READY" and raw_action in {"PLACE", "TRANSFER", "POUR", "STACK"} and not destination_id:
            errors.append({"code": "OUTPUT_MISSING_DESTINATION_ID", "severity": "error"})
            plan_status = "BLOCKED"
            execution_allowed = False

        confidence = float(getattr(ir, "overall_confidence", 0.0) or 0.0)
        return IntentOutput(
            intent_id=str(intent_id_override or getattr(getattr(ir, "task_metadata", None), "task_id", "intent-unknown")),
            observation_id=observation_id,
            scene_id=scene_id,
            action=_action(parsed_task),
            target_object=target_id,
            destination=destination_id,
            target_objects=target_ids,
            reference_object=reference_id,
            attributes=attributes,
            resolved_attributes=_resolved_attributes(getattr(ir, "scene", None), target_id),
            sort_criterion=str(sort_criterion) if sort_criterion else None,
            constraints=_constraint_outputs(parsed_task, graph),
            plan_status=plan_status, execution_allowed=execution_allowed,
            confidence=max(0.0, min(1.0, confidence)),
            clarification=getattr(parsed_task, "clarification", None),
            errors=errors, unsupported_capabilities=unsupported,
        )


def build_intent_output(ir: Any, observation: Optional[Dict[str, Any]] = None, **kwargs: Any) -> IntentOutput:
    return IntentOutputAdapter().build(ir, observation, **kwargs)
