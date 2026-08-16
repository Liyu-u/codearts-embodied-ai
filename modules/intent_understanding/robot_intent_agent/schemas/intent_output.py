"""Stable external intent contract for the downstream decision agent.

The semantic task graph remains the internal authority.  This model is the
small, versioned projection exposed to the rest of the project and preserves
the field names used by the original interface contract.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class IntentConstraintOutput(BaseModel):
    """A normalized constraint in the public JSON contract."""

    model_config = ConfigDict(extra="forbid")

    constraint_type: str
    operator: Optional[str] = None
    target_entity: Optional[str] = None
    subject_entity: Optional[str] = None
    source_event: Optional[str] = None
    target_event: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    hard: bool = True


class IntentOutput(BaseModel):
    """Versioned JSON consumed by the next decision/planning agent."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "intent_output.v1.1"
    intent_id: str
    observation_id: Optional[str] = None
    scene_id: Optional[str] = None
    action: str
    target_object: Optional[str] = None
    destination: Optional[str] = None
    target_objects: List[str] = Field(default_factory=list)
    reference_object: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    resolved_attributes: Dict[str, Any] = Field(default_factory=dict)
    sort_criterion: Optional[str] = None
    constraints: List[IntentConstraintOutput] = Field(default_factory=list)
    plan_status: Literal["READY", "NEEDS_CLARIFICATION", "BLOCKED"] = "BLOCKED"
    execution_allowed: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification: Optional[str] = None
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    unsupported_capabilities: List[str] = Field(default_factory=list)
