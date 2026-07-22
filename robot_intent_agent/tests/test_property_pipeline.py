"""
Property Intelligence Pipeline -- end-to-end tests.

Validates: Observation -> Ontology -> Mapper -> Affordance -> Confidence.
"""

import json
import pytest
from pathlib import Path

from robot_intent_agent.property_inference.observation_schema import (
    ObjectObservation, PerceptionObservation, FORBIDDEN_FIELDS,
)
from robot_intent_agent.property_inference.ontology.ontology_loader import OntologyLoader
from robot_intent_agent.property_inference.property_mapper import PropertyMapper
from robot_intent_agent.property_inference.affordance_engine import AffordanceEngine
from robot_intent_agent.property_inference.fusion_engine import FusionEngine
from robot_intent_agent.property_inference.confidence import (
    compute_overall_confidence, determine_execution_mode, ExecutionMode,
)


class TestObservationSchema:
    """Observation protocol validation."""

    def test_forbidden_fields_rejected(self):
        """Perception MUST NOT contain fragile/graspable/movable/material/max_force."""
        for field in FORBIDDEN_FIELDS:
            bad_data = {
                "object_id": "obj_001",
                "category_candidates": [{"name": "cup", "score": 0.9}],
                "geometry": {"width": 0.05, "height": 0.10, "depth": 0.05},
                field: True,  # FORBIDDEN!
            }
            with pytest.raises(ValueError, match=f"'{field}'"):
                ObjectObservation.from_dict(bad_data)

    def test_valid_observation_accepted(self):
        """Valid observation with only objective data should pass."""
        data = {
            "object_id": "obj_001",
            "category_candidates": [{"name": "cup", "score": 0.93}],
            "geometry": {"width": 0.06, "height": 0.12, "depth": 0.06},
            "pose": {"x": 0.35, "y": 0.12, "z": 0.75},
            "appearance": {"color": "transparent", "shape": "cylinder"},
        }
        obs = ObjectObservation.from_dict(data)
        assert obs.object_id == "obj_001"
        assert obs.top_category().name == "cup"

    def test_simulation_ground_truth_isolated(self):
        """simulation_metadata must NOT affect normal inference path."""
        data = {
            "object_id": "obj_002",
            "category_candidates": [{"name": "cup", "score": 0.9}],
            "geometry": {"width": 0.05, "height": 0.10, "depth": 0.05},
            "simulation_metadata": {
                "ground_truth": {"category": "glass_cup", "material": "glass"}
            },
        }
        obs = ObjectObservation.from_dict(data)
        # ground truth is stored but NOT used for inference
        assert obs.simulation_metadata is not None
        assert obs.top_category().name == "cup"  # uses perception, NOT ground truth


class TestOntologyLoader:
    """Ontology query with match types."""

    @pytest.fixture
    def loader(self):
        return OntologyLoader()

    def test_exact_match(self, loader):
        r = loader.query("glass_cup")
        assert r.match_type == "exact"
        assert r.confidence == 1.0
        assert r.properties.get("material") == "glass"
        assert r.properties["physical"]["fragility_level"] == 3

    def test_alias_match(self, loader):
        r = loader.query("cup")
        assert r.match_type in ("alias", "exact")
        assert r.confidence >= 0.9
        assert r.properties.get("material") == "glass"

    def test_fuzzy_match(self, loader):
        r = loader.query("glass_cu")  # typo of glass_cup -> fuzzy match
        assert r.match_type in ("fuzzy", "exact", "alias")
        if r.match_type == "fuzzy":
            assert 0.5 <= r.confidence < 1.0

    def test_no_match(self, loader):
        r = loader.query("xyz_unknown_thing_42")
        assert r.match_type == "none"
        assert r.confidence == 0.0


class TestPropertyMapper:
    """PropertyMapper: Observation -> SemanticProperty."""

    @pytest.fixture
    def mapper(self):
        return PropertyMapper()

    def test_glass_cup_inference(self, mapper):
        """glass cup -> fragility=3, force<=2N, graspable=true"""
        obs = {
            "name": "透明杯", "category": "glass_cup",
            "geometry": {"width": 0.06, "height": 0.12, "depth": 0.06},
            "position": [0.35, 0.12, 0.75],
        }
        prop = mapper.infer(obs)
        assert prop.material.value == "glass"
        assert prop.fragility_level.value == 3
        assert prop.max_force_N.value <= 2.0
        assert prop.graspable.value is True
        assert prop.material.source == "ontology"
        assert len(prop.decision_trace) >= 2

    def test_power_supply_inference(self, mapper):
        """power_supply -> NOT fragile, NOT graspable, electrical hazard"""
        obs = {
            "name": "高压电源箱", "category": "power_supply",
            "geometry": {"width": 0.20, "height": 0.40, "depth": 0.20},
            "position": [0.08, 0.03, 0.06],
        }
        prop = mapper.infer(obs)
        assert prop.fragility_level.value <= 1  # NOT fragile
        assert "electrical_hazard" in prop.risks or prop.max_force_N.value >= 30
        # geometry check: 0.20m > 0.08m gripper -> not graspable
        assert prop.graspable.value is False

    def test_wafer_box_inference(self, mapper):
        """wafer_box -> fragility=4, force<=1.5N"""
        obs = {
            "name": "8寸晶圆盒", "category": "wafer_box",
            "geometry": {"width": 0.20, "height": 0.02, "depth": 0.20},
            "position": [0.15, 0.05, 0.03],
        }
        prop = mapper.infer(obs)
        assert prop.fragility_level.value == 4
        assert prop.max_force_N.value <= 1.5
        assert any(r in prop.risks for r in ["precision_equipment", "vibration_sensitive", "static_sensitive"])

    def test_unknown_object(self, mapper):
        """Unknown category -> low confidence, no fabricated material"""
        obs = {
            "name": "奇怪物体", "category": "xyz_strange_thing",
            "geometry": {"width": 0.10, "height": 0.10, "depth": 0.10},
            "position": [0.0, 0.0, 0.03],
        }
        prop = mapper.infer(obs)
        overall = compute_overall_confidence(prop)
        assert overall < 0.7, f"Unknown object confidence too high: {overall}"


class TestAffordanceEngine:
    """Geometry-based affordance computation."""

    def test_graspable_within_range(self):
        engine = AffordanceEngine()
        assert engine.calculate_graspable((0.06, 0.08, 0.06)) is True

    def test_not_graspable_too_wide(self):
        engine = AffordanceEngine()
        assert engine.calculate_graspable((0.20, 0.04, 0.20)) is False

    def test_movable_light_object(self):
        engine = AffordanceEngine()
        assert engine.calculate_movable((0.05, 0.08, 0.05), "plastic") is True

    def test_not_movable_heavy(self):
        engine = AffordanceEngine()
        # 0.4*0.4*0.4 = 0.064 m3 * 7800 = 499 kg -> far exceeds 3kg payload
        assert engine.calculate_movable((0.4, 0.4, 0.4), "steel") is False


class TestFusionEngine:
    """Conflict resolution."""

    def test_stricter_force_wins(self):
        candidates = [
            {"value": 10.0, "source": "default", "confidence": 0.3},
            {"value": 2.0, "source": "ontology", "confidence": 0.98},
        ]
        result = FusionEngine.resolve_safety_limit(candidates, "force_N", 10.0)
        assert result["value"] == 2.0
        assert len(result["conflicts"]) == 1  # default was overridden

    def test_conservative_boolean(self):
        candidates = [
            {"value": True, "source": "vlm", "confidence": 0.6},
            {"value": False, "source": "sensor", "confidence": 0.8},
        ]
        result = FusionEngine.resolve_boolean(candidates, "graspable")
        assert result["value"] is False  # conservative wins
        assert result["source"] == "sensor"


class TestConfidenceModes:
    """Execution mode determination."""

    def test_normal_mode(self):
        assert determine_execution_mode(0.90) == ExecutionMode.NORMAL

    def test_cautious_mode(self):
        assert determine_execution_mode(0.60) == ExecutionMode.CAUTIOUS

    def test_inspect_mode(self):
        assert determine_execution_mode(0.30) == ExecutionMode.INSPECT

    def test_cautious_multiplier(self):
        from robot_intent_agent.property_inference.confidence import get_cautious_multiplier
        assert get_cautious_multiplier(ExecutionMode.NORMAL) == 1.0
        assert get_cautious_multiplier(ExecutionMode.CAUTIOUS) == 0.7
        assert get_cautious_multiplier(ExecutionMode.INSPECT) == 0.0
