"""Input contract helpers for the upstream perception observation.

The model is intentionally permissive about detector-specific fields, while
the production boundary explicitly strips evaluation-only ground truth before
the observation reaches inference code.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class PerceptionObservation(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0.0"
    message_type: str = "perception_observation"
    observation_id: Optional[str] = None
    scene_id: Optional[str] = None
    timestamp: Any = None
    clock_domain: Optional[str] = None
    coordinate_system: Optional[str] = None
    source: Dict[str, Any] = Field(default_factory=dict)
    objects: List[Dict[str, Any]] = Field(default_factory=list)
    relations: List[Dict[str, Any]] = Field(default_factory=list)
    robot_state: Dict[str, Any] = Field(default_factory=dict)


def inference_observation(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy safe for inference, excluding evaluation-only truth."""

    payload = deepcopy(raw if isinstance(raw, dict) else {})

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: scrub(item)
                for key, item in value.items()
                if key not in {
                    "simulation_metadata", "ground_truth_objects", "ground_truth",
                    "evaluation", "evaluation_only",
                }
            }
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(payload)
