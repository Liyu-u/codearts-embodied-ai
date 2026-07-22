"""
Confidence model v2.0 -- defines execution modes based on inference confidence.

Modes:
    normal   (conf > 0.85):  allow standard planning
    cautious (0.5-0.85):     reduce speed/force, may request re-observation
    inspect  (< 0.5):        prohibit direct grasp, require rescan/clarification
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class ExecutionMode(str, Enum):
    NORMAL = "normal"
    CAUTIOUS = "cautious"
    INSPECT = "inspect"
    BLOCKED = "blocked"


@dataclass
class PropertyConfidence:
    value: Any
    confidence: float = 1.0
    source: str = "unknown"
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "reasoning": self.reasoning,
        }


@dataclass
class SemanticProperty:
    """Complete semantic property set for a single object."""
    name: str
    category: str = "unknown"
    material: PropertyConfidence = field(default_factory=lambda: PropertyConfidence("unknown", 0.3, "default"))
    fragility_level: PropertyConfidence = field(default_factory=lambda: PropertyConfidence(0, 0.3, "default"))
    max_force_N: PropertyConfidence = field(default_factory=lambda: PropertyConfidence(10.0, 0.3, "default"))
    max_velocity_ms: PropertyConfidence = field(default_factory=lambda: PropertyConfidence(0.3, 0.3, "default"))
    graspable: PropertyConfidence = field(default_factory=lambda: PropertyConfidence(False, 0.3, "default"))
    movable: PropertyConfidence = field(default_factory=lambda: PropertyConfidence(False, 0.3, "default"))
    risks: List[str] = field(default_factory=list)
    affordances: List[str] = field(default_factory=list)
    decision_trace: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "category": self.category,
            "material": self.material.to_dict(),
            "fragility_level": self.fragility_level.to_dict(),
            "max_force_N": self.max_force_N.to_dict(),
            "max_velocity_ms": self.max_velocity_ms.to_dict(),
            "graspable": self.graspable.to_dict(),
            "movable": self.movable.to_dict(),
            "risks": self.risks, "affordances": self.affordances,
            "decision_trace": self.decision_trace,
        }


# ============================================================
# Confidence Mode Decision
# ============================================================

def compute_overall_confidence(prop: SemanticProperty) -> float:
    """Compute overall confidence from all property confidences."""
    key_props = [prop.material, prop.fragility_level, prop.max_force_N, prop.graspable, prop.movable]
    confs = [p.confidence for p in key_props]
    return sum(confs) / len(confs) if confs else 0.0


def determine_execution_mode(overall_confidence: float) -> ExecutionMode:
    """
    Map overall confidence to execution mode.

    > 0.85  -> NORMAL
    0.5-0.85 -> CAUTIOUS (reduce speed/force, may re-observe)
    < 0.5   -> INSPECT (prohibit direct grasp, require rescan/clarification)
    """
    if overall_confidence > 0.85:
        return ExecutionMode.NORMAL
    elif overall_confidence >= 0.5:
        return ExecutionMode.CAUTIOUS
    else:
        return ExecutionMode.INSPECT


def get_cautious_multiplier(mode: ExecutionMode) -> float:
    """Get force/velocity reduction multiplier for cautious/inspect modes."""
    if mode == ExecutionMode.CAUTIOUS:
        return 0.7
    elif mode == ExecutionMode.INSPECT:
        return 0.0  # blocked
    return 1.0
