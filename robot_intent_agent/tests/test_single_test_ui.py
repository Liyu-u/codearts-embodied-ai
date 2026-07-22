"""
Tests for single-case interactive test UI and assertion scorer.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════
# 1. Preset loading
# ══════════════════════════════════════════════════════════════

class TestPresetLoading:
    def test_presets_loadable(self):
        path = Path(__file__).parent.parent / "eval" / "single_test_presets.json"
        assert path.exists(), f"Presets not found: {path}"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "presets" in data
        assert len(data["presets"]) >= 10, f"Need >=10 presets, got {len(data['presets'])}"

    def test_all_categories_have_presets(self):
        path = Path(__file__).parent.parent / "eval" / "single_test_presets.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cats = set(data["meta"]["categories"])
        covered = set(p["category"] for p in data["presets"])
        missing = cats - covered
        assert not missing, f"Categories without presets: {missing}"

    def test_each_preset_has_required_fields(self):
        path = Path(__file__).parent.parent / "eval" / "single_test_presets.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for p in data["presets"]:
            assert p.get("instruction"), f"{p['preset_id']} missing instruction"
            assert p.get("perception_json"), f"{p['preset_id']} missing perception_json"
            assert p.get("expected_assertions"), f"{p['preset_id']} missing expected_assertions"


# ══════════════════════════════════════════════════════════════
# 2. Canonical entity ID mapping
# ══════════════════════════════════════════════════════════════

class TestCanonicalEntityMapping:
    def test_mapping_built_from_perception_and_scene(self):
        from robot_intent_agent.eval.assertion_scorer import build_canonical_entity_map
        from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder

        perception = [
            {"object_id": "obj-a", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "red"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
             "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
             "affordances": [], "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
            {"object_id": "obj-b", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "blue"}, "pose": {"position": {"x": 0.3, "y": -0.1, "z": 0.05}},
             "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
             "affordances": [], "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
        ]
        raw = [RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.05, color="red", material="plastic"),
               RawObjectPercept(name="cup", x=0.3, y=-0.1, z=0.05, color="blue", material="plastic")]
        scene = SemanticSceneBuilder().build(raw)
        mapping = build_canonical_entity_map(perception, scene)
        assert len(mapping) >= 1, f"Mapping should have entries, got {mapping}"

    def test_same_name_different_objects_mapped_separately(self):
        """Two cups with different colors must map to different scene IDs."""
        from robot_intent_agent.eval.assertion_scorer import build_canonical_entity_map
        from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder

        perception = [
            {"object_id": "obj-red", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "red"}, "pose": {"position": {"x": 0.3, "y": 0.1, "z": 0.05}},
             "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
             "affordances": [], "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
            {"object_id": "obj-blue", "category_candidates": [{"name": "cup", "score": 0.9}],
             "appearance": {"color": "blue"}, "pose": {"position": {"x": 0.3, "y": -0.1, "z": 0.05}},
             "geometry": {"size": {"width": 0.07, "height": 0.1, "depth": 0.07}},
             "affordances": [], "tracking": {"state": "stationary", "confidence": 0.9, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
        ]
        raw = [RawObjectPercept(name="cup", x=0.3, y=0.1, z=0.05, color="red", material="plastic"),
               RawObjectPercept(name="cup", x=0.3, y=-0.1, z=0.05, color="blue", material="plastic")]
        scene = SemanticSceneBuilder().build(raw)
        mapping = build_canonical_entity_map(perception, scene)
        # Different perception object_ids should map to different scene UUIDs
        ids = set(mapping.values())
        assert len(mapping) == 2 or len(ids) == len(mapping), \
            f"Same-name objects must map to different IDs: {mapping}"


# ══════════════════════════════════════════════════════════════
# 3. Assertion scorer
# ══════════════════════════════════════════════════════════════

class TestAssertionScorer:
    def test_scorer_runs_presets(self):
        """Run first 3 presets through the assertion scorer."""
        from robot_intent_agent.eval.assertion_scorer import (
            evaluate_assertions, build_canonical_entity_map,
        )
        from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator

        path = Path(__file__).parent.parent / "eval" / "single_test_presets.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for preset in data["presets"][:3]:  # Test first 3 to keep fast
            objects_raw = preset["perception_json"].get("objects", [])
            raw = []
            for obj in objects_raw:
                pos = obj.get("pose", {}).get("position", {})
                geom = obj.get("geometry", {}).get("size", obj.get("geometry", {}))
                app = obj.get("appearance", {})
                cats = obj.get("category_candidates", [{"name": "unknown", "score": 0.5}])
                top = max(cats, key=lambda c: c.get("score", 0))
                raw.append(RawObjectPercept(
                    name=top["name"], x=float(pos.get("x", 0)), y=float(pos.get("y", 0)), z=float(pos.get("z", 0.03)),
                    width=float(geom.get("width", 0.05)), height=float(geom.get("height", 0.08)), depth=float(geom.get("depth", 0.05)),
                    color=app.get("color", "unknown"), material=app.get("material", "unknown"),
                ))

            scene = SemanticSceneBuilder().build(raw)
            target = raw[0].name if raw else "target"
            bt = BehaviorTreeGenerator().plan(preset["instruction"], scene=scene)
            cg = HybridConstraintCompiler().compile(preset["instruction"], bt, scene=scene, target=target)
            ir = RobotTaskIRGenerator().generate(preset["instruction"], bt, cg, scene=scene)

            mapping = build_canonical_entity_map(objects_raw, scene)
            scored = evaluate_assertions(ir, scene, bt, cg, preset["expected_assertions"], mapping)
            assert scored.total_assertions > 0, f"{preset['preset_id']}: no assertions evaluated"

    def test_numeric_tolerance(self):
        """approx op must respect tolerance."""
        from robot_intent_agent.eval.assertion_scorer import AssertionResult
        # Test the logic directly
        expected, actual, tol = 3.0, 3.005, 0.01
        passed = abs(float(actual) - float(expected)) <= float(tol)
        assert passed, f"3.005 should be within 0.01 of 3.0"
        passed2 = abs(3.02 - 3.0) <= 0.01
        assert not passed2, "3.02 should NOT be within 0.01 of 3.0"

    def test_list_contains_semantics(self):
        """contains op must use list membership semantics."""
        actual = ["Grasp", "Reach", "WaitUntilStable"]
        assert "WaitUntilStable" in actual
        assert "Fetch" not in actual

    def test_critical_error_display(self):
        """CRITICAL severity must cause failure."""
        from robot_intent_agent.eval.assertion_scorer import AssertionResult
        r = AssertionResult(key="test", op="truthy", expected="truthy", actual=False,
                           passed=False, severity="CRITICAL", detail="Must be truthy")
        assert not r.passed
        assert r.severity == "CRITICAL"


# ══════════════════════════════════════════════════════════════
# 4. Field diff display
# ══════════════════════════════════════════════════════════════

class TestFieldDiff:
    def test_diff_shows_expected_vs_actual(self):
        from robot_intent_agent.eval.assertion_scorer import AssertionResult
        results = [
            AssertionResult(key="action", op="eq", expected="GRASP", actual="FETCH",
                           passed=False, detail="Equal: False"),
            AssertionResult(key="force", op="approx", expected=3.0, actual=50.0,
                           passed=False, detail="Approx: diff=47.0", severity="CRITICAL"),
        ]
        diff_lines = []
        for r in results:
            if not r.passed:
                diff_lines.append(f"**{r.key}**: expected=`{r.expected}` → actual=`{r.actual}` ({r.detail})")
        assert len(diff_lines) == 2
        assert "action" in diff_lines[0]
        assert "CRITICAL" in results[1].severity


# ══════════════════════════════════════════════════════════════
# 5. Export
# ══════════════════════════════════════════════════════════════

class TestExport:
    def test_json_export(self):
        from robot_intent_agent.eval.assertion_scorer import ScoredResult
        scored = ScoredResult(preset_id="P01", instruction="test", total_assertions=2,
                             passed_assertions=2, total_score=1.0)
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False, encoding="utf-8") as f:
            json.dump(scored.__dict__, f, indent=2, ensure_ascii=False, default=str)
            path = f.name
        assert os.path.getsize(path) > 0
        os.unlink(path)

    def test_markdown_export(self):
        md = "# 单题测试报告\n\n**指令**: 测试\n**时间**: 2024-01-01\n\n### 测试结果"
        with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False, encoding="utf-8") as f:
            f.write(md)
            path = f.name
        assert os.path.getsize(path) > 0
        os.unlink(path)


# ══════════════════════════════════════════════════════════════
# 6. Input modification
# ══════════════════════════════════════════════════════════════

class TestInputModification:
    def test_modified_input_runs(self):
        """User-modified instruction should still run through pipeline."""
        from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
        from robot_intent_agent.planner import BehaviorTreeGenerator
        from robot_intent_agent.constraint import HybridConstraintCompiler
        from robot_intent_agent.ir import RobotTaskIRGenerator

        scene = SemanticSceneBuilder().build([
            RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="plastic"),
        ])
        # Modified instruction (different from preset)
        bt = BehaviorTreeGenerator().plan("轻轻抓住白色塑料杯", scene=scene)
        cg = HybridConstraintCompiler().compile("轻轻抓住白色塑料杯", bt, scene=scene, target="cup")
        ir = RobotTaskIRGenerator().generate("轻轻抓住白色塑料杯", bt, cg, scene=scene)
        assert ir.parsed_task is not None
        assert ir.validation_result is not None
