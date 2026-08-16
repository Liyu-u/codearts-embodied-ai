
"""
Object Semantic Schema v1.0 -- Embodied AI object ontology.

Replaces flat booleans (fragile=true) with graded semantic profiles.
All physical constraints MUST be derived from SemanticObject, not raw attributes.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple


# ============================================================
# Ontology Enums
# ============================================================

class FragilityLevel(IntEnum):
    """Standard fragility taxonomy (replaces fragile=true/false)."""
    NORMAL = 0           # generic plastic/wood
    SENSITIVE = 1         # thin glass, lightweight
    FRAGILE = 2           # standard glass/ceramic
    PRECISION = 3         # optical components, lenses
    ULTRA_PRECISION = 4   # semiconductor wafers, MEMS


class MobilityType(str):
    FIXED = "fixed"
    MOVABLE_ROBOT = "movable_robot"
    MOVABLE_HUMAN = "movable_human"
    UNKNOWN = "unknown"


class HazardType(str):
    NONE = "none"
    ELECTRICAL = "electrical"
    CHEMICAL = "chemical"
    THERMAL = "thermal"
    MECHANICAL = "mechanical"
    BIOLOGICAL = "biological"


class ObjectClass(str):
    """Top-level object classification."""
    CONTAINER = "container"
    TOOL = "tool"
    COMPONENT = "component"
    OBSTACLE = "obstacle"
    TARGET = "target"
    FIXTURE = "fixture"
    UNKNOWN = "unknown"


# ============================================================
# Fragility -> Force mapping (used by Constraint Generator)
# ============================================================

FRAGILITY_FORCE_MAP: Dict[FragilityLevel, Tuple[float, float, float]] = {
    FragilityLevel.NORMAL:          (0.1, 10.0, 0.3),   # (min_N, max_N, max_velocity_ms)
    FragilityLevel.SENSITIVE:       (0.1, 5.0, 0.2),
    FragilityLevel.FRAGILE:         (0.1, 3.0, 0.15),
    FragilityLevel.PRECISION:       (0.1, 2.0, 0.10),
    FragilityLevel.ULTRA_PRECISION: (0.05, 1.0, 0.05),
}


# ============================================================
# Confidence wrapper
# ============================================================

@dataclass
class ConfidentValue:
    value: Any
    confidence: float = 1.0
    source: str = "unknown"


# ============================================================
# SemanticObject
# ============================================================

@dataclass
class SemanticObject:
    """
    Semantic Object -- intermediate layer between Raw Perception and Intent Reasoner.

    All downstream modules (Constraint Compiler, Planner, IR Generator)
    MUST read from this model, NOT from raw affordance flags.
    """
    id: str
    name: str
    object_class: ObjectClass = ObjectClass.UNKNOWN

    # Pose + geometry
    pose: Tuple[float, float, float] = (0, 0, 0)
    bbox: Tuple[float, float, float] = (0.05, 0.05, 0.05)

    # Physical
    material: ConfidentValue = field(default_factory=lambda: ConfidentValue("unknown", 0.5))
    mass_estimate_kg: ConfidentValue = field(default_factory=lambda: ConfidentValue(0.1, 0.5))

    # Risk
    fragility_level: FragilityLevel = FragilityLevel.NORMAL
    damage_sensitive: bool = False
    electrical_hazard: bool = False
    hazard_type: HazardType = HazardType.NONE
    hazard_level: str = "low"  # low | medium | high | critical

    # Affordance
    graspable: bool = True
    grasp_types: List[str] = field(default_factory=lambda: ["top_down"])
    force_range_n: Tuple[float, float] = (0.1, 10.0)
    mobility_type: MobilityType = MobilityType.MOVABLE_ROBOT
    stackable: bool = False
    container: bool = False

    # Manipulation constraints (derived from fragility + affordance)
    max_grasp_force_n: float = 10.0
    max_velocity_ms: float = 0.3
    preferred_strategy: str = "standard"

    # Risk tags for explain report
    risk_tags: List[str] = field(default_factory=list)
    semantic_warnings: List[str] = field(default_factory=list)

    # Backward compat
    schema_version: str = "1.0"
    _legacy_fragile: bool = False  # for v1.0 conversion

    # ============================================================
    # Derivation (called after all fields set)
    # ============================================================

    def derive_constraints(self) -> None:
        """Derive manipulation constraints from fragility level."""
        min_f, max_f, max_v = FRAGILITY_FORCE_MAP[self.fragility_level]
        self.max_grasp_force_n = max_f
        self.max_velocity_ms = max_v
        self.force_range_n = (min_f, max_f)
        self.preferred_strategy = (
            "gentle_grasp" if self.fragility_level >= FragilityLevel.FRAGILE
            else "standard"
        )

    def derive_risk_tags(self) -> None:
        """Auto-generate risk tags."""
        tags = []
        if self.fragility_level >= FragilityLevel.FRAGILE:
            tags.append("fragile")
        if self.fragility_level >= FragilityLevel.PRECISION:
            tags.append("force_sensitive")
        if self.damage_sensitive:
            tags.append("damage_sensitive")
        if self.electrical_hazard:
            tags.append("electrical_hazard")
        if self.mobility_type == MobilityType.FIXED:
            tags.append("blocking")
        self.risk_tags = tags

    def validate_semantic_consistency(self) -> List[str]:
        """Check for semantic inconsistencies and return warnings."""
        warnings = []
        name_lower = self.name.lower()

        # Electrical equipment detection
        elec_keywords = ["电源", "电", "变压器", "配电", "高压", "电压"]
        if any(kw in name_lower for kw in elec_keywords):
            if not self.electrical_hazard:
                warnings.append(
                    f"Object '{self.name}': name suggests electrical equipment "
                    f"but electrical_hazard=False. Auto-setting."
                )
                self.electrical_hazard = True
                self.hazard_type = HazardType.ELECTRICAL

        # Fixed equipment detection
        fixed_keywords = ["箱", "柜", "台", "机", "仪", "设备", "站"]
        if any(kw in name_lower for kw in fixed_keywords):
            if self.mobility_type == MobilityType.MOVABLE_ROBOT:
                warnings.append(
                    f"Object '{self.name}': name suggests fixed equipment "
                    f"but mobility=movable_robot. Setting to 'fixed'."
                )
                self.mobility_type = MobilityType.FIXED

        # Optical/precision detection
        precision_keywords = ["光学", "镜片", "透镜", "晶圆", "光栅", "衍射", "激光", "半导体"]
        if any(kw in name_lower for kw in precision_keywords):
            if self.fragility_level < FragilityLevel.PRECISION:
                warnings.append(
                    f"Object '{self.name}': precision component detected, "
                    f"upgrading fragility from {self.fragility_level} to PRECISION(3)."
                )
                self.fragility_level = FragilityLevel.PRECISION
                self.damage_sensitive = True

        self.semantic_warnings = warnings
        return warnings

    def to_legacy_dict(self) -> Dict[str, Any]:
        """Convert back to v1.0 compatible dict for backward compat."""
        return {
            "fragile": self.fragility_level >= FragilityLevel.FRAGILE,
            "fragility_level": int(self.fragility_level),
            "graspable": self.graspable,
            "movable": self.mobility_type != MobilityType.FIXED,
            "container": self.container,
            "max_force_n": self.max_grasp_force_n,
            "max_velocity_ms": self.max_velocity_ms,
            "risk_tags": self.risk_tags,
            "hazard_type": self.hazard_type.value if isinstance(self.hazard_type, HazardType) else str(self.hazard_type),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "object_class": self.object_class.value if isinstance(self.object_class, ObjectClass) else str(self.object_class),
            "schema_version": self.schema_version,
            "pose": list(self.pose),
            "bbox": list(self.bbox),
            "physical_properties": {
                "material": {"value": self.material.value, "confidence": self.material.confidence},
                "mass_estimate_kg": {"value": self.mass_estimate_kg.value, "confidence": self.mass_estimate_kg.confidence},
            },
            "risk_profile": {
                "fragility_level": int(self.fragility_level),
                "fragility_label": self.fragility_level.name,
                "damage_sensitive": self.damage_sensitive,
                "electrical_hazard": self.electrical_hazard,
                "hazard_type": self.hazard_type.value if isinstance(self.hazard_type, HazardType) else str(self.hazard_type),
                "hazard_level": self.hazard_level,
            },
            "affordance_profile": {
                "grasp": {
                    "available": self.graspable,
                    "grasp_types": self.grasp_types,
                    "force_range_n": list(self.force_range_n),
                },
                "mobility": {"type": self.mobility_type.value if isinstance(self.mobility_type, MobilityType) else str(self.mobility_type)},
                "stackable": self.stackable,
                "container": self.container,
            },
            "manipulation_constraints": {
                "max_grasp_force_n": self.max_grasp_force_n,
                "max_velocity_ms": self.max_velocity_ms,
                "preferred_strategy": self.preferred_strategy,
            },
            "risk_tags": self.risk_tags,
            "semantic_warnings": self.semantic_warnings,
        }
