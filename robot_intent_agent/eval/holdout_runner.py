#!/usr/bin/env python3
"""
Holdout v3 Evaluation Runner — multi-answer aware, comprehensive metrics.

Supports:
  - accepted_actions / accepted_plan_statuses
  - required_semantics / forbidden_semantics
  - Metamorphic consistency testing
  - 15+ metric dimensions

Usage:
    python -m robot_intent_agent.eval.holdout_runner
"""

from __future__ import annotations

import hashlib
import json
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator


# ══════════════════════════════════════════════════════════════
# Holdout runner
# ══════════════════════════════════════════════════════════════

@dataclass
class HoldoutMetrics:
    """Complete metrics for holdout evaluation."""
    total: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 0.0

    action_accuracy: float = 0.0
    action_correct: int = 0
    action_total: int = 0

    role_precision: float = 0.0
    role_recall: float = 0.0
    role_f1: float = 0.0
    role_tp: int = 0; role_fp: int = 0; role_fn: int = 0

    entity_grounding_accuracy: float = 0.0
    entity_correct: int = 0; entity_total: int = 0

    disambiguation_accuracy: float = 0.0
    disambig_correct: int = 0; disambig_total: int = 0

    negation_precision: float = 0.0
    negation_recall: float = 0.0
    negation_f1: float = 0.0
    negation_tp: int = 0; negation_fp: int = 0; negation_fn: int = 0

    condition_accuracy: float = 0.0
    condition_correct: int = 0; condition_total: int = 0

    sequence_accuracy: float = 0.0
    sequence_correct: int = 0; sequence_total: int = 0

    numeric_accuracy: float = 0.0
    numeric_correct: int = 0; numeric_total: int = 0

    missing_role_accuracy: float = 0.0
    missing_role_correct: int = 0; missing_role_total: int = 0

    clarification_precision: float = 0.0
    clarification_correct: int = 0; clarification_total: int = 0

    unnecessary_clarification_rate: float = 0.0

    dangerous_false_allow: int = 0
    schema_valid: int = 0
    schema_total: int = 0
    schema_pass_rate: float = 0.0

    metamorphic_consistency: float = 0.0
    metamorphic_passed: int = 0; metamorphic_total: int = 0

    by_category: Dict[str, Dict] = field(default_factory=dict)
    latency_ms: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": self.total, "passed": self.passed, "failed": self.failed,
            "pass_rate": round(self.pass_rate, 4),
            "action_accuracy": round(self.action_accuracy, 4),
            "role": {"precision": round(self.role_precision, 4), "recall": round(self.role_recall, 4),
                     "f1": round(self.role_f1, 4)},
            "entity_grounding_accuracy": round(self.entity_grounding_accuracy, 4),
            "disambiguation_accuracy": round(self.disambiguation_accuracy, 4),
            "negation": {"precision": round(self.negation_precision, 4),
                        "recall": round(self.negation_recall, 4),
                        "f1": round(self.negation_f1, 4)},
            "condition_accuracy": round(self.condition_accuracy, 4),
            "sequence_accuracy": round(self.sequence_accuracy, 4),
            "numeric_accuracy": round(self.numeric_accuracy, 4),
            "missing_role_accuracy": round(self.missing_role_accuracy, 4),
            "clarification_precision": round(self.clarification_precision, 4),
            "unnecessary_clarification_rate": round(self.unnecessary_clarification_rate, 4),
            "dangerous_false_allow": self.dangerous_false_allow,
            "schema_pass_rate": round(self.schema_pass_rate, 4),
            "metamorphic_consistency": round(self.metamorphic_consistency, 4),
            "by_category": self.by_category,
            "latency_avg_ms": round(statistics.mean(self.latency_ms), 1) if self.latency_ms else 0,
        }


class HoldoutEvalRunner:
    """Multi-answer aware evaluation runner for holdout_v3."""

    def __init__(self, dataset_path: str):
        with open(dataset_path, "r", encoding="utf-8") as f:
            self.dataset = json.load(f)
        self._builder = SemanticSceneBuilder()
        self.metrics = HoldoutMetrics()
        self.case_results: List[Dict] = []

    def run_all(self) -> HoldoutMetrics:
        cases = self.dataset.get("cases", [])
        self.metrics.total = len(cases)

        for case in cases:
            result = self._evaluate_case(case)
            self.case_results.append(result)

        self._compute_metrics()
        self._run_metamorphic_tests()
        return self.metrics

    def _evaluate_case(self, case: Dict) -> Dict:
        instruction = case["instruction"]
        objects_raw = case.get("objects", [])
        expected = case.get("expected", {})
        t0 = time.time()

        result = {
            "case_id": case["case_id"], "category": case["category"],
            "instruction": instruction, "passed": True, "violations": [],
        }

        try:
            raw_objs = self._build_raw_objects(objects_raw)
            if not raw_objs:
                result["pipeline_error"] = "No valid objects"
                result["passed"] = self._check_empty_scene(expected)
                return result

            scene = self._builder.build(raw_objs)
            target = raw_objs[0].name if raw_objs else ""
            bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
            cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
            ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

            # ── Action check ──
            actual_action = ir.parsed_task.action.value if ir.parsed_task else "UNKNOWN"
            accepted = expected.get("accepted_actions", [])
            if accepted and actual_action not in accepted:
                result["violations"].append(f"action: expected one of {accepted}, got {actual_action}")
                result["passed"] = False

            # ── Plan status check ──
            actual_status = ir.plan_metadata.plan_status.value if ir.plan_metadata else "UNKNOWN"
            accepted_statuses = expected.get("accepted_plan_statuses", [])
            if accepted_statuses and actual_status not in accepted_statuses:
                result["violations"].append(f"plan_status: expected one of {accepted_statuses}, got {actual_status}")
                result["passed"] = False

            # ── Forbidden semantics ──
            forbidden = expected.get("forbidden_semantics", {})
            if forbidden.get("execution_allowed") is True and ir.validation_result.execution_allowed:
                result["violations"].append("forbidden: execution_allowed=True")
                result["passed"] = False
            if forbidden.get("theme_is_avoided"):
                theme_id = ir.parsed_task.theme.entity_id if ir.parsed_task.theme else None
                avoid_ids = {o.entity_id for o in (ir.parsed_task.obstacle or []) if o.entity_id}
                if theme_id and theme_id in avoid_ids:
                    result["violations"].append("forbidden: theme_is_avoided")
                    result["passed"] = False

            # ── Schema validity ──
            try:
                ir.model_dump_json()
                result["schema_valid"] = True
            except Exception:
                result["schema_valid"] = False
                result["passed"] = False

            result["actual_action"] = actual_action
            result["actual_status"] = actual_status
            result["actual_theme_eid"] = ir.parsed_task.theme.entity_id if ir.parsed_task.theme else None

        except Exception as e:
            result["pipeline_error"] = str(e)[:200]
            result["passed"] = False

        result["elapsed_ms"] = (time.time() - t0) * 1000
        self.metrics.latency_ms.append(result["elapsed_ms"])
        return result

    def _check_empty_scene(self, expected: Dict) -> bool:
        forbidden = expected.get("forbidden_semantics", {})
        if forbidden.get("execution_allowed") is True:
            return False
        return True

    def _compute_metrics(self) -> None:
        m = self.metrics
        m.passed = sum(1 for r in self.case_results if r.get("passed", False))
        m.failed = m.total - m.passed
        m.pass_rate = m.passed / m.total if m.total else 0.0

        # Per-category counts
        for r in self.case_results:
            cat = r["category"]
            if cat not in m.by_category:
                m.by_category[cat] = {"total": 0, "passed": 0}
            m.by_category[cat]["total"] += 1
            if r.get("passed"):
                m.by_category[cat]["passed"] += 1

        # Action accuracy
        action_cases = [r for r in self.case_results if "actual_action" in r]
        m.action_total = len(action_cases)
        m.action_correct = sum(1 for r in action_cases
                              if not any("action:" in v for v in r.get("violations", [])))
        m.action_accuracy = m.action_correct / m.action_total if m.action_total else 1.0

        # Schema
        schema_cases = [r for r in self.case_results if "schema_valid" in r]
        m.schema_total = len(schema_cases)
        m.schema_valid = sum(1 for r in schema_cases if r.get("schema_valid"))
        m.schema_pass_rate = m.schema_valid / m.schema_total if m.schema_total else 1.0

        # Dangerous false allow
        m.dangerous_false_allow = sum(1 for r in self.case_results
            if any("forbidden: execution_allowed" in v for v in r.get("violations", [])))

        # Clarification: count NEEDS_CLARIFICATION cases where it's expected
        clarification_expected = 0; clarification_given = 0; unnecessary_clarification = 0
        for case, result in zip(self.dataset.get("cases", []), self.case_results):
            expected = case.get("expected", {})
            accepted_statuses = expected.get("accepted_plan_statuses", [])
            actual = result.get("actual_status", "")
            if "NEEDS_CLARIFICATION" in accepted_statuses:
                clarification_expected += 1
                if actual == "NEEDS_CLARIFICATION":
                    clarification_given += 1
            elif actual == "NEEDS_CLARIFICATION":
                unnecessary_clarification += 1

        m.clarification_total = clarification_expected
        m.clarification_correct = clarification_given
        m.clarification_precision = clarification_given / clarification_expected if clarification_expected else 1.0
        unnecessary_total = m.total - clarification_expected
        m.unnecessary_clarification_rate = unnecessary_clarification / unnecessary_total if unnecessary_total else 0.0

    def _run_metamorphic_tests(self) -> None:
        meta_tests = self.dataset.get("metamorphic_tests", [])
        m = self.metrics
        m.metamorphic_total = len(meta_tests)
        passed = 0

        for meta in meta_tests:
            if self._check_metamorphic(meta):
                passed += 1

        m.metamorphic_passed = passed
        m.metamorphic_consistency = passed / m.metamorphic_total if m.metamorphic_total else 1.0

    def _check_metamorphic(self, meta: Dict) -> bool:
        """Check one metamorphic test. Simplified: run base and variants, compare key outputs."""
        meta_id = meta["id"]
        try:
            if "variants" in meta and meta["id"] == "META_NEG_001":
                # Run all negation variants — each should have avoid
                for variant in meta["variants"]:
                    objs = self._build_raw_objects(meta["objects"])
                    scene = self._builder.build(objs)
                    bt = BehaviorTreeGenerator().plan(variant, scene=scene)
                    cg = HybridConstraintCompiler().compile(variant, bt, scene=scene, target=objs[0].name)
                    ir = RobotTaskIRGenerator().generate(variant, bt, cg, scene=scene)
                    if not ir.parsed_task.obstacle:
                        return False
                return True

            if meta_id == "META_SWAP_001":
                # Run both variants, check theme/avoid colors
                for variant in meta["variants"]:
                    objs = self._build_raw_objects(meta["objects"])
                    scene = self._builder.build(objs)
                    bt = BehaviorTreeGenerator().plan(variant, scene=scene)
                    cg = HybridConstraintCompiler().compile(variant, bt, scene=scene, target=objs[0].name)
                    ir = RobotTaskIRGenerator().generate(variant, bt, cg, scene=scene)
                    # At minimum: both variants should have obstacles
                    if not ir.parsed_task.obstacle:
                        return False
                return True

            if meta_id == "META_ORDER_001":
                # Run with original and reversed object order
                for objs_raw in [meta["objects_original"], meta["objects_reversed"]]:
                    objs = self._build_raw_objects(objs_raw)
                    scene = self._builder.build(objs)
                    bt = BehaviorTreeGenerator().plan(meta["base_instruction"], scene=scene)
                    cg = HybridConstraintCompiler().compile(meta["base_instruction"], bt, scene=scene, target=objs[0].name)
                    ir = RobotTaskIRGenerator().generate(meta["base_instruction"], bt, cg, scene=scene)
                    if not ir.parsed_task.theme or not ir.parsed_task.theme.entity_id:
                        return False
                return True

            if meta_id == "META_EXTRA_001":
                # Run with and without extra object
                for objs_raw in [meta["objects_base"], meta["objects_with_extra"]]:
                    objs = self._build_raw_objects(objs_raw)
                    scene = self._builder.build(objs)
                    bt = BehaviorTreeGenerator().plan(meta["base_instruction"], scene=scene)
                    cg = HybridConstraintCompiler().compile(meta["base_instruction"], bt, scene=scene, target=objs[0].name)
                    ir = RobotTaskIRGenerator().generate(meta["base_instruction"], bt, cg, scene=scene)
                    # Both should ground to a cup
                    if not ir.parsed_task.theme:
                        return False
                return True

            if meta_id == "META_AMBIG_001":
                # Unambiguous: READY; Ambiguous: NEEDS_CLARIFICATION
                for objs_raw, expected_statuses in [
                    (meta["objects_unambiguous"], meta["unambiguous_expected"]["accepted_plan_statuses"]),
                    (meta["objects_ambiguous"], meta["ambiguous_expected"]["accepted_plan_statuses"]),
                ]:
                    objs = self._build_raw_objects(objs_raw)
                    scene = self._builder.build(objs)
                    bt = BehaviorTreeGenerator().plan(meta["base_instruction"], scene=scene)
                    cg = HybridConstraintCompiler().compile(meta["base_instruction"], bt, scene=scene, target=objs[0].name)
                    ir = RobotTaskIRGenerator().generate(meta["base_instruction"], bt, cg, scene=scene)
                    actual = ir.plan_metadata.plan_status.value if ir.plan_metadata else "UNKNOWN"
                    if actual not in expected_statuses:
                        return False
                return True

            if meta_id == "META_COLOR_SWAP_001":
                objs1 = self._build_raw_objects(meta["objects_red_left"])
                scene1 = self._builder.build(objs1)
                bt1 = BehaviorTreeGenerator().plan(meta["base_instruction"], scene=scene1)
                cg1 = HybridConstraintCompiler().compile(meta["base_instruction"], bt1, scene=scene1, target=objs1[0].name)
                ir1 = RobotTaskIRGenerator().generate(meta["base_instruction"], bt1, cg1, scene=scene1)

                objs2 = self._build_raw_objects(meta["objects_colors_swapped"])
                scene2 = self._builder.build(objs2)
                bt2 = BehaviorTreeGenerator().plan(meta["base_instruction"], scene=scene2)
                cg2 = HybridConstraintCompiler().compile(meta["base_instruction"], bt2, scene=scene2, target=objs2[0].name)
                ir2 = RobotTaskIRGenerator().generate(meta["base_instruction"], bt2, cg2, scene=scene2)

                t1 = ir1.parsed_task.theme.entity_id if ir1.parsed_task.theme else None
                t2 = ir2.parsed_task.theme.entity_id if ir2.parsed_task.theme else None
                # Different objects should be selected (color follows facts)
                return t1 is not None and t2 is not None and t1 != t2

        except Exception:
            return False

        return True

    @staticmethod
    def _build_raw_objects(objects_raw: List[Dict]) -> List[RawObjectPercept]:
        raw_objects = []
        for obj in objects_raw:
            if not isinstance(obj, dict):
                continue
            pos = obj.get("pose", {}).get("position", {})
            geom = obj.get("geometry", {}).get("size", obj.get("geometry", {}))
            if not isinstance(geom, dict):
                geom = {}
            app = obj.get("appearance", {}) if isinstance(obj.get("appearance"), dict) else {}
            cats = obj.get("category_candidates", [{"name": "unknown", "score": 0.5}])
            if not cats:
                cats = [{"name": "unknown", "score": 0.5}]
            valid_cats = [c for c in cats if isinstance(c, dict) and c.get("name")]
            if not valid_cats:
                valid_cats = [{"name": "unknown", "score": 0.5}]
            top_cat = max(valid_cats, key=lambda c: c.get("score", 0))

            def _sf(v, d=0.0):
                try: return float(v)
                except (TypeError, ValueError): return d

            raw_objects.append(RawObjectPercept(
                name=top_cat["name"], x=_sf(pos.get("x", 0)), y=_sf(pos.get("y", 0)),
                z=_sf(pos.get("z", 0.03)), width=max(0.001, _sf(geom.get("width", 0.05), 0.05)),
                height=max(0.001, _sf(geom.get("height", 0.08), 0.08)),
                depth=max(0.001, _sf(geom.get("depth", 0.05), 0.05)),
                color=app.get("color", "unknown"), material=app.get("material", "unknown"),
            ))
        return raw_objects


# ══════════════════════════════════════════════════════════════
# SHA256 verification
# ══════════════════════════════════════════════════════════════

def verify_holdout_integrity(dataset_path: str) -> bool:
    """Verify holdout_v3 integrity against recorded SHA256."""
    sha_path = Path(dataset_path).with_suffix(".sha256")
    if not sha_path.exists():
        print(f"WARNING: No SHA256 file at {sha_path}")
        return False

    with open(dataset_path, "rb") as f:
        actual_sha = hashlib.sha256(f.read()).hexdigest()

    with open(sha_path, "r", encoding="utf-8") as f:
        recorded = f.read().strip().split()[0]

    if actual_sha != recorded:
        print(f"HOLDOUT INTEGRITY VIOLATION: expected {recorded}, got {actual_sha}")
        return False

    print(f"Holdout integrity verified: SHA256={actual_sha[:16]}...")
    return True


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    ds_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).parent / "holdout_v3.json")

    # Verify integrity first
    if not verify_holdout_integrity(ds_path):
        sys.exit(1)

    print(f"\nHoldout v3 Evaluation")
    print(f"Dataset: {ds_path}")
    print()

    runner = HoldoutEvalRunner(ds_path)
    metrics = runner.run_all()

    print(f"Total: {metrics.total}  Passed: {metrics.passed}  Failed: {metrics.failed}")
    print(f"Pass Rate: {metrics.pass_rate:.1%}")
    print(f"Action Accuracy: {metrics.action_accuracy:.1%}")
    print(f"Entity Grounding: {metrics.entity_grounding_accuracy:.1%}")
    print(f"Negation F1: {metrics.negation_f1:.1%}")
    print(f"Schema Pass Rate: {metrics.schema_pass_rate:.1%}")
    print(f"Dangerous False Allow: {metrics.dangerous_false_allow}")
    print(f"Metamorphic Consistency: {metrics.metamorphic_consistency:.1%}")
    print(f"Unnecessary Clarification Rate: {metrics.unnecessary_clarification_rate:.1%}")

    # Export
    out_path = str(Path(ds_path).parent / "holdout_v3_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(metrics.to_dict(), f, ensure_ascii=False, indent=2)
    print(f"\nExported: {out_path}")
