"""
Property Fusion Module - Raw Perception -> Semantic Object.

Fuses visual features, knowledge base, and memory experience
into a graded SemanticObject with derived constraints.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from uuid import uuid4

from robot_intent_agent.models.object_semantic_schema import (
    SemanticObject, ConfidentValue,
    FragilityLevel, MobilityType, HazardType, ObjectClass,
)

# ============================================================
# Knowledge Base: material -> fragility
# ============================================================

MATERIAL_FRAGILITY: Dict[str, FragilityLevel] = {
    "steel": FragilityLevel.NORMAL,
    "iron": FragilityLevel.NORMAL,
    "aluminum": FragilityLevel.NORMAL,
    "wood": FragilityLevel.NORMAL,
    "plastic": FragilityLevel.SENSITIVE,
    "acrylic": FragilityLevel.SENSITIVE,
    "ceramic": FragilityLevel.FRAGILE,
    "porcelain": FragilityLevel.FRAGILE,
    "glass": FragilityLevel.FRAGILE,
    "crystal": FragilityLevel.PRECISION,
    "silicon": FragilityLevel.ULTRA_PRECISION,
    "optical_glass": FragilityLevel.PRECISION,
    "quartz": FragilityLevel.PRECISION,
}

# ============================================================
# Knowledge Base: name patterns -> properties
# ============================================================

NAME_PATTERNS: Dict[str, Dict[str, Any]] = {
    # Optical / precision
    "光学": {"fragility": FragilityLevel.PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},
    "镜片": {"fragility": FragilityLevel.PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},
    "透镜": {"fragility": FragilityLevel.PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},
    "晶圆": {"fragility": FragilityLevel.ULTRA_PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},
    "光栅": {"fragility": FragilityLevel.PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},
    "衍射": {"fragility": FragilityLevel.PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},
    "激光": {"fragility": FragilityLevel.PRECISION, "damage_sensitive": True, "object_class": ObjectClass.COMPONENT},

    # Electrical
    "电源": {"electrical_hazard": True, "mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "高压": {"electrical_hazard": True, "mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "电压": {"electrical_hazard": True, "mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "变压器": {"electrical_hazard": True, "mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},

    # Equipment / fixtures
    "箱": {"mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "柜": {"mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "仪": {"mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "台": {"mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},
    "设备": {"mobility": MobilityType.FIXED, "object_class": ObjectClass.FIXTURE},

    # Containers
    "杯": {"container": True, "object_class": ObjectClass.CONTAINER},
    "瓶": {"container": True, "object_class": ObjectClass.CONTAINER},
    "盒": {"container": True, "object_class": ObjectClass.CONTAINER},

    # Stackable
    "块": {"stackable": True, "object_class": ObjectClass.TARGET},
    "积木": {"stackable": True, "object_class": ObjectClass.TARGET},
}

# ============================================================
# Property Fusion Engine
# ============================================================

class PropertyFusion:
    """
    Fuse raw perception data into a SemanticObject.

    Priority: Name pattern KB > Material KB > Raw input > Defaults
    """

    @classmethod
    def fuse(
        cls,
        raw: Dict[str, Any],
        context: Optional[str] = None,
        memory_hints: Optional[Dict[str, Any]] = None,
    ) -> SemanticObject:
        """
        Convert raw perception dict to SemanticObject.

        Args:
            raw: Raw perception data {name, material, color, shape, ...}
            context: Optional context hint (e.g. "optical_lab")
            memory_hints: Optional memory data for override
        """
        name = raw.get("name", "unknown")
        obj_id = raw.get("id", f"obj-{uuid4().hex[:6]}")

        obj = SemanticObject(
            id=obj_id,
            name=name,
            pose=(
                float(raw.get("x", raw.get("pose", {}).get("x", 0))),
                float(raw.get("y", raw.get("pose", {}).get("y", 0))),
                float(raw.get("z", raw.get("pose", {}).get("z", 0.03))),
            ),
            bbox=(
                float(raw.get("width", raw.get("bbox", {}).get("w", 0.05))),
                float(raw.get("height", raw.get("bbox", {}).get("h", 0.05))),
                float(raw.get("depth", raw.get("bbox", {}).get("d", 0.05))),
            ),
        )

        # ── Material ──
        mat = raw.get("material", "unknown")
        mat_conf = raw.get("material_confidence", 0.85)
        obj.material = ConfidentValue(mat, mat_conf, "perception")

        # ── Fragility from material KB ──
        mat_lower = mat.lower()
        if mat_lower in MATERIAL_FRAGILITY:
            obj.fragility_level = MATERIAL_FRAGILITY[mat_lower]
        elif any(kw in mat_lower for kw in ["glass", "crystal", "ceramic", "silicon"]):
            obj.fragility_level = FragilityLevel.FRAGILE

        # ── Name pattern overrides ──
        for pattern, props in NAME_PATTERNS.items():
            if pattern in name:
                if "fragility" in props:
                    obj.fragility_level = max(obj.fragility_level, props["fragility"])
                if "damage_sensitive" in props:
                    obj.damage_sensitive = props["damage_sensitive"]
                if "electrical_hazard" in props:
                    obj.electrical_hazard = props["electrical_hazard"]
                    obj.hazard_type = HazardType.ELECTRICAL
                if "mobility" in props:
                    obj.mobility_type = props["mobility"]
                if "container" in props:
                    obj.container = props["container"]
                if "stackable" in props:
                    obj.stackable = props["stackable"]
                if "object_class" in props:
                    obj.object_class = props["object_class"]

        # ── Context boost ──
        if context and "optical" in context.lower():
            obj.fragility_level = max(obj.fragility_level, FragilityLevel.PRECISION)
            obj.damage_sensitive = True

        # ── Legacy conversion ──
        if raw.get("fragile") or raw.get("extra_attrs", {}).get("fragile"):
            obj.fragility_level = max(obj.fragility_level, FragilityLevel.FRAGILE)
            obj._legacy_fragile = True

        # ── Memory hints ──
        if memory_hints:
            if "fragility" in memory_hints:
                obj.fragility_level = FragilityLevel(memory_hints["fragility"])
            if "force_n" in memory_hints:
                obj.max_grasp_force_n = float(memory_hints["force_n"])

        # ── Derive ──
        obj.derive_constraints()
        obj.derive_risk_tags()
        obj.validate_semantic_consistency()

        return obj

    @classmethod
    def from_scene_object(cls, scene_obj: Any) -> SemanticObject:
        """Convert existing SceneObject to SemanticObject."""
        raw = {
            "name": getattr(scene_obj, "name", "unknown"),
            "id": getattr(scene_obj, "id", ""),
            "x": getattr(scene_obj.position, "x", 0) if hasattr(scene_obj, "position") else 0,
            "y": getattr(scene_obj.position, "y", 0) if hasattr(scene_obj, "position") else 0,
            "z": getattr(scene_obj.position, "z", 0.03) if hasattr(scene_obj, "position") else 0.03,
            "width": getattr(scene_obj.bbox, "width", 0.05) if hasattr(scene_obj, "bbox") else 0.05,
            "height": getattr(scene_obj.bbox, "height", 0.05) if hasattr(scene_obj, "bbox") else 0.05,
            "depth": getattr(scene_obj.bbox, "depth", 0.05) if hasattr(scene_obj, "bbox") else 0.05,
            "material": getattr(scene_obj, "attributes", {}).get("material", "unknown"),
        }
        if hasattr(scene_obj, "attributes"):
            raw.update(scene_obj.attributes)
        if hasattr(scene_obj, "affordances"):
            for a in scene_obj.affordances:
                v = a.value if hasattr(a, "value") else str(a)
                if v == "fragile":
                    raw["fragile"] = True
        return cls.fuse(raw)
