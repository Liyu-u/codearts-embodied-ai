"""Unified integration adapter for the intent-understanding module.

The local engine has a richer internal IR than the integration repository's
task.v1 contract.  This file is the only place that translates between them.
It deliberately keeps the default path deterministic and treats incomplete or
unsafe plans as non-executable.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


_MODULE_DIR = Path(__file__).resolve().parent
_CORE_PARENT = str(_MODULE_DIR / "robot_intent_agent")
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from robot_intent_agent.constraint import HybridConstraintCompiler  # noqa: E402
from robot_intent_agent.ir import RobotTaskIRGenerator  # noqa: E402
from robot_intent_agent.planner import LLMPlanner  # noqa: E402
from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder  # noqa: E402
from robot_intent_agent.semantic_compiler import SemanticCompiler  # noqa: E402
from robot_intent_agent.schemas.scene import (  # noqa: E402
    SpatialPredicate,
    SpatialRelation,
)


MODULE_NAME = "intent_understanding"
MODULE_VERSION = "1.0.0"

_ACTION_MAP = {
    "GRASP": "pick",
    "FETCH": "fetch",
    "PLACE": "pick_and_place",
    "HANDOVER": "handover",
    "TRANSFER": "transfer",
    "DYNAMIC_GRASP": "dynamic_grasp",
    "PUSH": "push",
    "POUR": "pour",
    "STACK": "stack",
    "WAIT": "wait",
    "CUSTOM": "custom",
}


def health() -> dict:
    """Return a local, side-effect-free health result."""

    try:
        SemanticSceneBuilder()
        HybridConstraintCompiler()
        RobotTaskIRGenerator()
        return {
            "module": MODULE_NAME,
            "version": MODULE_VERSION,
            "healthy": True,
            "engine": "rule",
            "message": "intent-understanding core loaded",
        }
    except Exception as exc:  # pragma: no cover - defensive boundary
        return {
            "module": MODULE_NAME,
            "version": MODULE_VERSION,
            "healthy": False,
            "engine": "unavailable",
            "message": f"health check failed: {type(exc).__name__}: {exc}",
        }


def run(input_json: dict) -> dict:
    """Run intent understanding and return the repository's task.v1 object."""

    if not isinstance(input_json, dict):
        return _blocked("unknown", ["intent 输入必须是 JSON 对象"])

    instruction = input_json.get("instruction")
    perception = input_json.get("perception")
    if not isinstance(instruction, str) or not instruction.strip():
        return _blocked("unknown", ["instruction 必须是非空字符串"])
    if not isinstance(perception, dict):
        return _blocked("unknown", ["perception 必须是 JSON 对象"])
    if perception.get("schema_version") not in {None, "perception.v1", "1.0.0"}:
        return _blocked(
            str(perception.get("scene_id") or "unknown"),
            ["不支持的 perception schema_version"],
        )

    task_id = str(
        input_json.get("task_id")
        or perception.get("observation_id")
        or perception.get("scene_id")
        or "task-intent"
    )

    try:
        scene = _build_scene(perception)
        engine = _select_engine(input_json)
        # The core compiler already implements rule/LLM/hybrid fusion.  The
        # integration adapter must provide the optional provider explicitly;
        # otherwise a requested hybrid run silently becomes rule-only.
        llm_planner = _build_llm_planner(engine)
        compiled = SemanticCompiler(llm_planner=llm_planner).compile(
            instruction,
            scene=scene,
            mode=engine,
        )
        behavior_tree = compiled.behavior_tree
        target = _first_target_name(scene)
        constraint_graph = HybridConstraintCompiler().compile(
            instruction,
            behavior_tree=behavior_tree,
            scene=scene,
            target=target,
        )
        ir = RobotTaskIRGenerator().generate(
            instruction,
            behavior_tree=behavior_tree,
            constraint_graph=constraint_graph,
            scene=scene,
        )
        return _to_task_v1(
            ir,
            perception,
            task_id,
            engine,
            engine_trace=compiled.engine_trace,
        )
    except Exception as exc:
        return _blocked(
            task_id,
            [f"INTENT_PIPELINE_ERROR:{type(exc).__name__}"],
            diagnostics={"message": str(exc)},
        )


def _select_engine(input_json: dict) -> str:
    requested = str(
        input_json.get("engine")
        or os.getenv("RIA_PLANNER_ENGINE", "rule")
    ).strip().lower()
    if requested not in {"rule", "llm", "hybrid"}:
        return "rule"
    return requested


def _build_llm_planner(engine: str) -> Optional[LLMPlanner]:
    """Create the optional provider used by the selected semantic engine.

    Rule mode remains completely offline.  For ``llm``/``hybrid`` the
    planner is created even when no key is configured so the compiler can
    produce an auditable fallback trace instead of failing the whole task.
    ``LLMPlanner`` performs the actual availability check before any network
    call.
    """

    if engine not in {"llm", "hybrid"}:
        return None
    try:
        return LLMPlanner()
    except Exception:
        # Configuration errors must not make the safe rule fallback
        # unavailable.  The compiler will record the provider as absent.
        return None


def _build_scene(perception: dict):
    raw_objects: List[RawObjectPercept] = []
    for index, item in enumerate(perception.get("objects") or []):
        if not isinstance(item, dict):
            continue
        position = _position(item.get("pose"))
        dimensions = _dimensions(item.get("dimensions") or item.get("geometry"))
        category = str(item.get("category") or item.get("name") or "unknown")
        attributes = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
        appearance = item.get("appearance") if isinstance(item.get("appearance"), dict) else {}
        color = attributes.get("color") or appearance.get("color")
        material = attributes.get("material") or item.get("material")
        object_id = str(item.get("id") or item.get("object_id") or f"obj-{index + 1:03d}")
        raw_objects.append(
            RawObjectPercept(
                name=category,
                x=position[0],
                y=position[1],
                z=position[2],
                width=dimensions[0],
                height=dimensions[1],
                depth=dimensions[2],
                color=str(color) if color is not None else None,
                material=str(material) if material is not None else None,
                object_id=object_id,
                extra_attrs={
                    "_integration_category": category,
                    "_integration_attributes": attributes,
                },
            )
        )

    scene = SemanticSceneBuilder().build(raw_objects)
    # SemanticSceneBuilder creates UUIDs by default.  The integration contract
    # requires perception-owned IDs to survive unchanged, so remap the scene
    # objects and any already-inferred relations before semantic grounding.
    generated_to_perception: Dict[str, str] = {}
    for obj in scene.objects:
        attrs = getattr(obj, "attributes", {}) or {}
        perception_id = attrs.get("_perception_object_id")
        if perception_id:
            generated_to_perception[str(obj.id)] = str(perception_id)
            obj.id = str(perception_id)
    for relation in scene.relations:
        relation.subject = generated_to_perception.get(str(relation.subject), str(relation.subject))
        relation.object = generated_to_perception.get(str(relation.object), str(relation.object))

    known_ids = {getattr(obj, "id", None) for obj in scene.objects}
    existing = {
        (relation.subject, relation.predicate.value, relation.object)
        for relation in scene.relations
    }
    for relation in perception.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        subject = relation.get("subject")
        predicate = relation.get("predicate")
        object_id = relation.get("object")
        if subject not in known_ids or object_id not in known_ids:
            continue
        try:
            predicate_enum = SpatialPredicate(str(predicate))
        except (TypeError, ValueError):
            continue
        key = (subject, predicate_enum.value, object_id)
        if key in existing:
            continue
        scene.relations.append(
            SpatialRelation(
                subject=subject,
                predicate=predicate_enum,
                object=object_id,
                confidence=float(relation.get("confidence", 1.0)),
                metadata=relation.get("metadata", {})
                if isinstance(relation.get("metadata", {}), dict)
                else {},
            )
        )
        existing.add(key)
    return scene


def _position(raw: Any) -> Tuple[float, float, float]:
    if isinstance(raw, dict):
        if isinstance(raw.get("position"), dict):
            raw = raw["position"]
        values = (raw.get("x", 0.0), raw.get("y", 0.0), raw.get("z", 0.03))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 3:
        values = raw[:3]
    else:
        values = (0.0, 0.0, 0.03)
    return tuple(_finite_number(value, default) for value, default in zip(values, (0.0, 0.0, 0.03)))


def _dimensions(raw: Any) -> Tuple[float, float, float]:
    if isinstance(raw, dict) and isinstance(raw.get("size"), dict):
        raw = raw["size"]
    if isinstance(raw, dict):
        values = (raw.get("width", 0.05), raw.get("height", 0.10), raw.get("depth", 0.05))
    elif isinstance(raw, (list, tuple)) and len(raw) >= 3:
        values = raw[:3]
    else:
        values = (0.05, 0.10, 0.05)
    return tuple(max(0.001, _finite_number(value, default)) for value, default in zip(values, (0.05, 0.10, 0.05)))


def _finite_number(value: Any, default: float) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def _first_target_name(scene: Any) -> str:
    objects = list(getattr(scene, "objects", []) or [])
    return str(getattr(objects[0], "name", "target")) if objects else "target"


def _to_task_v1(
    ir: Any,
    perception: dict,
    task_id: str,
    engine: str,
    engine_trace: Optional[dict] = None,
) -> dict:
    parsed = getattr(ir, "parsed_task", None)
    metadata = getattr(ir, "plan_metadata", None)
    validation = getattr(ir, "validation_result", None)
    action_value = str(getattr(getattr(parsed, "action", None), "value", "CUSTOM"))
    action = _ACTION_MAP.get(action_value, action_value.lower())
    theme = getattr(parsed, "theme", None)
    destination = getattr(parsed, "destination", None)
    support_surface = getattr(parsed, "support_surface", None)
    recipient = getattr(parsed, "recipient", None)
    destination_ref = destination or support_surface
    if destination_ref is None and action_value == "FETCH":
        destination_ref = recipient

    status_value = str(getattr(getattr(metadata, "plan_status", None), "value", "BLOCKED"))
    status = {
        "READY_WITH_SAFE_SUBSTITUTION": "READY",
        "READY": "READY",
        "NEEDS_CLARIFICATION": "NEEDS_CLARIFICATION",
        "BLOCKED": "BLOCKED",
    }.get(status_value, "BLOCKED")
    execution_allowed = bool(getattr(validation, "execution_allowed", False))
    if status != "READY" or not execution_allowed:
        if status == "READY":
            status = "BLOCKED"
        execution_allowed = False

    blocking_reasons: List[str] = []
    issues = list(getattr(validation, "issues", []) or [])
    blocking_reasons.extend(
        str(getattr(issue, "code", "VALIDATION_ERROR")) for issue in issues
    )
    blocking_reasons.extend(str(item) for item in (getattr(parsed, "unmet_roles", []) or []))
    if getattr(parsed, "clarification", None):
        blocking_reasons.append("CLARIFICATION_REQUIRED")
    blocking_reasons = list(dict.fromkeys(item for item in blocking_reasons if item))

    target_id = _entity_id(theme)
    destination_id = _entity_id(destination_ref)
    scene = getattr(ir, "scene", None)
    destination_pose = _scene_pose(scene, destination_id)
    constraints = [_constraint_dump(item) for item in (getattr(parsed, "user_constraints", []) or [])]
    diagnostics = {
        "engine": engine,
        "requested_engine": engine,
        "actual_engine": (engine_trace or {}).get("actual_engine", "RuleEngine"),
        "engine_trace": dict(engine_trace or {}),
        "execution_allowed": execution_allowed,
        "confidence": {
            "parse": float(getattr(parsed, "parse_confidence", 0.0) or 0.0),
            "grounding": float(getattr(parsed, "grounding_confidence", 0.0) or 0.0),
            "constraint": float(getattr(parsed, "constraint_confidence", 0.0) or 0.0),
        },
        "issues": [
            {
                "code": str(getattr(issue, "code", "VALIDATION_ERROR")),
                "message": str(getattr(issue, "message", issue)),
                "severity": str(getattr(issue, "severity", "error")),
            }
            for issue in issues
        ],
    }
    output = {
        "schema_version": "task.v1",
        "task_id": task_id,
        "action": action,
        "target_ids": [target_id] if target_id else [],
        "target_object": getattr(theme, "mention", None),
        "destination_id": destination_id,
        "constraints": constraints,
        "status": status,
        "blocking_reasons": blocking_reasons,
        "execution_allowed": execution_allowed,
        "clarification": getattr(parsed, "clarification", None),
        "diagnostics": diagnostics,
    }
    if destination_pose is not None:
        output["destination"] = destination_pose
    return output


def _entity_id(entity: Any) -> Optional[str]:
    value = getattr(entity, "entity_id", None)
    return str(value) if value else None


def _scene_pose(scene: Any, entity_id: Optional[str]) -> Optional[dict]:
    if not entity_id or scene is None:
        return None
    for obj in getattr(scene, "objects", []) or []:
        if str(getattr(obj, "id", "")) != entity_id:
            continue
        position = getattr(obj, "position", None)
        return {
            "x": float(getattr(position, "x", 0.0)),
            "y": float(getattr(position, "y", 0.0)),
            "z": max(0.02, float(getattr(position, "z", 0.03))),
        }
    return None


def _constraint_dump(constraint: Any) -> dict:
    operator = getattr(getattr(constraint, "operator", None), "value", None)
    return {
        "parameter": str(getattr(constraint, "parameter", "")),
        "operator": operator or str(getattr(constraint, "operator", "")),
        "value": getattr(constraint, "value", None),
        "min_value": getattr(constraint, "min_value", None),
        "max_value": getattr(constraint, "max_value", None),
        "unit": str(getattr(constraint, "unit", "")),
        "hard": bool(getattr(constraint, "is_hard", True)),
    }


def _blocked(task_id: str, reasons: Iterable[str], diagnostics: Optional[dict] = None) -> dict:
    return {
        "schema_version": "task.v1",
        "task_id": task_id or "unknown",
        "action": "custom",
        "target_ids": [],
        "destination_id": None,
        "constraints": [],
        "status": "BLOCKED",
        "blocking_reasons": list(dict.fromkeys(str(reason) for reason in reasons if reason)),
        "execution_allowed": False,
        "diagnostics": diagnostics or {},
    }
