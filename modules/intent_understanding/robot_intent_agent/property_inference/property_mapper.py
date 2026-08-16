"""
Property Mapper -- Raw Object Observation -> SemanticProperty.

Input:  ObjectObservation {name, category, geometry, position}
Output: SemanticProperty with confidence and source annotations.
"""

from typing import Any, Dict, Optional
from .ontology.ontology_loader import OntologyLoader
from .affordance_engine import AffordanceEngine
from .confidence import SemanticProperty, PropertyConfidence


class PropertyMapper:
    """
    Map raw object observations to semantic properties using ontology + affordance engine.
    All output properties carry source and confidence.
    """

    def __init__(self):
        self._ontology = OntologyLoader()
        self._affordance = AffordanceEngine()

    def infer(self, observation: Dict[str, Any]) -> SemanticProperty:
        """
        Infer complete semantic properties from observation.

        observation format:
            {"name": "透明杯", "category": "glass_cup",
             "geometry": {"width": 0.05, "height": 0.12, "depth": 0.05},
             "position": [0.1, 0.2, 0.3]}
        """
        name = observation.get("name", "unknown")
        category = observation.get("category", "unknown")
        geometry = observation.get("geometry", {})
        bbox = (
            float(geometry.get("width", 0.05)),
            float(geometry.get("height", 0.08)),
            float(geometry.get("depth", 0.05)),
        )

        prop = SemanticProperty(name=name, category=category)
        trace = []

        # ── Step 1: Ontology lookup ──
        result = self._ontology.query(category)
        if result.match_type == "none":
            # Try name-based fuzzy fallback
            result = self._ontology.query(name.lower().replace(" ", "_"))

        if result.match_type != "none":
            entry = result.properties
            phys = entry.get("physical", {})
            affs = entry.get("affordance", [])
            risks = entry.get("risk", [])

            prop.material = PropertyConfidence(
                entry.get("material", "unknown"), result.confidence, "ontology",
                result.match_reason
            )
            prop.fragility_level = PropertyConfidence(
                phys.get("fragility_level", 0), result.confidence, "ontology",
                f"Fragility level from {result.matched_category}"
            )
            prop.max_force_N = PropertyConfidence(
                phys.get("max_force_N", 10.0), result.confidence, "ontology",
                f"Force limit from {result.matched_category}"
            )
            prop.max_velocity_ms = PropertyConfidence(
                phys.get("max_velocity_ms", 0.3), result.confidence, "ontology",
                f"Velocity limit from {result.matched_category}"
            )
            prop.risks = list(risks)
            prop.affordances = list(affs)

            trace.append({"step": "ontology_lookup", "result": result.matched_category,
                          "confidence": result.confidence, "match_type": result.match_type})
            trace.append({"step": "fragility_mapping", "result": f"L{phys.get('fragility_level',0)}",
                          "confidence": result.confidence})
            trace.append({"step": "force_limit", "result": f"{phys.get('max_force_N',10)}N",
                          "confidence": result.confidence})
        else:
            # Unknown object -- mark low confidence
            prop.material = PropertyConfidence("unknown", 0.3, "default",
                                               f"No ontology entry for '{category}'")
            prop.fragility_level = PropertyConfidence(0, 0.3, "default")
            prop.max_force_N = PropertyConfidence(10.0, 0.3, "default")
            trace.append({"step": "ontology_lookup", "result": "unknown", "confidence": 0.3})

        # ── Step 2: Affordance computation (geometry-based) ──
        graspable = self._affordance.calculate_graspable(bbox)
        prop.graspable = PropertyConfidence(
            graspable, 0.90, "affordance_engine",
            f"Geometry check: {bbox[0]:.3f}m x {bbox[1]:.3f}m, "
            f"gripper max=0.08m -> {'OK' if graspable else 'TOO_WIDE'}"
        )

        movable = self._affordance.calculate_movable(bbox, prop.material.value)
        prop.movable = PropertyConfidence(
            movable, 0.90, "affordance_engine",
            f"Movable check: {'movable' if movable else 'fixed/too_heavy'}"
        )
        trace.append({"step": "affordance_compute", "result": f"grasp={graspable}, move={movable}", "confidence": 0.90})

        # Override affordance with ontology if available
        if result.match_type != "none":
            onto_affs = result.properties.get("affordance", [])
            prop.graspable.value = "grasp" in onto_affs
            prop.movable.value = "move" in onto_affs

        prop.decision_trace = trace
        return prop
