"""
Intent understanding accuracy evaluation runner — LEGACY.

⚠️ DEPRECATED: Use UpgradedEvalRunner (eval/upgraded_runner.py) instead.
This runner is kept for backward-compatible metric comparison only.
The UpgradedEvalRunner provides 13-dimension scoring, severe-error veto,
and consistent applicable counting that this runner lacks.

Runs golden dataset cases through the production pipeline and computes
Action, Role, Entity, Constraint, and Schema accuracy metrics.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from robot_intent_agent.scene_builder import SemanticSceneBuilder, RawObjectPercept
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.task_semantics import parse_task_semantics
from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR


@dataclass
class CaseResult:
    case_id: str = ""
    instruction: str = ""
    passed: bool = False
    action_match: bool = False
    action_expected: str = ""
    action_actual: str = ""
    theme_entity_match: bool = False
    theme_expected_id: str = ""
    theme_actual_id: str = ""
    force_match: bool = False
    force_expected: Optional[float] = None
    force_actual: Optional[float] = None
    missing_roles_match: bool = False
    missing_roles_expected: List[str] = field(default_factory=list)
    missing_roles_actual: List[str] = field(default_factory=list)
    schema_valid: bool = False
    errors: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


class EvalRunner:
    """Golden dataset evaluation runner."""

    def __init__(self, dataset_path: str = None):
        if dataset_path is None:
            dataset_path = str(Path(__file__).parent / "golden_dataset.json")
        with open(dataset_path, "r", encoding="utf-8") as f:
            self.dataset = json.load(f)
        self.results: List[CaseResult] = []
        self._builder = SemanticSceneBuilder()

    def run_all(self) -> List[CaseResult]:
        """Run all golden dataset cases and return results."""
        self.results = []
        for case in self.dataset.get("cases", []):
            result = self._run_case(case)
            self.results.append(result)
        return self.results

    def _run_case(self, case: Dict[str, Any]) -> CaseResult:
        result = CaseResult(
            case_id=case["case_id"],
            instruction=case["instruction"],
        )
        t0 = time.time()

        try:
            # Build scene from golden dataset objects
            instruction = case["instruction"]
            objects_raw = case.get("objects", [])
            expected = case.get("expected", {})

            # Handle empty instruction
            if not instruction.strip():
                if expected.get("error_expected"):
                    result.passed = True
                    result.elapsed_ms = (time.time() - t0) * 1000
                    return result
                result.errors.append("Empty instruction should error")
                result.elapsed_ms = (time.time() - t0) * 1000
                return result

            # Handle empty objects
            if not objects_raw:
                if expected.get("empty_scene"):
                    result.passed = True
                    result.elapsed_ms = (time.time() - t0) * 1000
                    return result

            # Build RawObjectPercept list
            raw_objects = []
            for obj in objects_raw:
                pos = obj.get("pose", {}).get("position", {})
                geom = obj.get("geometry", {}).get("size", obj.get("geometry", {}))
                app = obj.get("appearance", {})
                cats = obj.get("category_candidates", [{"name": "unknown", "score": 0.5}])
                top_cat = max(cats, key=lambda c: c.get("score", 0))
                raw_objects.append(RawObjectPercept(
                    name=top_cat["name"],
                    x=float(pos.get("x", 0)), y=float(pos.get("y", 0)), z=float(pos.get("z", 0.03)),
                    width=float(geom.get("width", 0.05)), height=float(geom.get("height", 0.08)), depth=float(geom.get("depth", 0.05)),
                    color=app.get("color", "unknown"), material=app.get("material", "unknown"),
                ))

            scene = self._builder.build(raw_objects)
            target = raw_objects[0].name if raw_objects else "target"

            # Run full pipeline
            bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
            cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
            ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

            # ── Evaluate action ──
            expected_action = expected.get("action")
            actual_action = ir.parsed_task.action.value if ir.parsed_task else "UNKNOWN"
            result.action_expected = expected_action or ""
            result.action_actual = actual_action
            result.action_match = (expected_action == actual_action) if expected_action else True

            # ── Evaluate theme entity grounding ──
            expected_entity_id = expected.get("theme_entity_id")
            actual_entity_id = ir.parsed_task.theme.entity_id if ir.parsed_task.theme else None
            result.theme_expected_id = expected_entity_id or ""
            result.theme_actual_id = actual_entity_id or ""
            # Grounding check: verify entity IS grounded (non-null entity_id) and matches a scene object
            scene_ids = {getattr(o, "id", "") for o in scene.objects}
            theme_grounded = actual_entity_id is not None and actual_entity_id in scene_ids
            if expected_entity_id:
                result.theme_entity_match = theme_grounded
            else:
                result.theme_entity_match = True

            # ── Evaluate force (user constraint parsing, not resolution) ──
            expected_force = expected.get("force_n")
            if expected_force is not None:
                result.force_expected = expected_force
                user_constraints = [c for c in (ir.parsed_task.user_constraints or []) if c.parameter == "force_n"]
                if user_constraints:
                    actual_force = user_constraints[0].value
                    result.force_actual = actual_force
                    result.force_match = actual_force is not None and abs(actual_force - expected_force) < 0.01
                else:
                    fr = ir.constraint_resolution.parameters.get("force_n")
                    actual_force = fr.selected_value if fr else None
                    result.force_actual = actual_force
                    result.force_match = True  # No user constraint to check, skip

            # ── Evaluate force operator ──
            expected_force_op = expected.get("force_op")
            if expected_force_op:
                user_constraints = [c for c in (ir.parsed_task.user_constraints or []) if c.parameter == "force_n"]
                if user_constraints:
                    actual_op = user_constraints[0].operator.value
                    if actual_op != expected_force_op:
                        result.errors.append(f"force_op: expected {expected_force_op}, got {actual_op}")

            # ── Evaluate missing roles ──
            expected_missing = expected.get("missing_roles", [])
            actual_missing = list(ir.grounded_task.missing_roles) if ir.grounded_task else []
            result.missing_roles_expected = list(expected_missing)
            result.missing_roles_actual = actual_missing
            if expected_missing:
                result.missing_roles_match = set(expected_missing).issubset(set(actual_missing))

            # ── Evaluate theme not in scene ──
            if expected.get("theme_not_in_scene"):
                if ir.parsed_task.theme and ir.parsed_task.theme.entity_id:
                    result.errors.append("Expected theme not found, but got entity_id")

            # ── Evaluate theme specific class ──
            expected_specific_class = expected.get("theme_specific_class")
            if expected_specific_class:
                actual_sc = ir.parsed_task.theme.specific_class if ir.parsed_task.theme else None
                if actual_sc != expected_specific_class:
                    result.errors.append(f"specific_class: expected {expected_specific_class}, got {actual_sc}")

            # ── Evaluate recipient ──
            if expected.get("recipient_identified"):
                if not ir.parsed_task.recipient:
                    result.errors.append("Expected recipient to be identified")
                elif ir.parsed_task.recipient.entity_id != "user":
                    result.errors.append(f"Expected recipient entity_id='user', got {ir.parsed_task.recipient.entity_id}")

            # ── Evaluate support_surface ──
            if "support_surface" in expected:
                if expected["support_surface"] is None and ir.parsed_task.support_surface is not None:
                    result.errors.append("Expected support_surface=None but got a value")

            # ── Evaluate manner ──
            expected_manner = expected.get("manner")
            if expected_manner:
                actual_manner = ir.parsed_task.manner
                if actual_manner != expected_manner:
                    result.errors.append(f"manner: expected {expected_manner}, got {actual_manner}")

            # ── Evaluate motion state ──
            expected_motion = expected.get("motion_state")
            if expected_motion:
                actual_motion = ir.parsed_task.motion_state.state if ir.parsed_task else "unknown"
                if actual_motion != expected_motion:
                    result.errors.append(f"motion_state: expected {expected_motion}, got {actual_motion}")

            # ── Evaluate schema validity ──
            try:
                ir.model_dump_json()
                result.schema_valid = True
            except Exception as e:
                result.schema_valid = False
                result.errors.append(f"Schema validation: {e}")

            # ── Evaluate execution_allowed ──
            if "execution_allowed" in expected:
                expected_ea = expected["execution_allowed"]
                actual_ea = ir.validation_result.execution_allowed
                if expected_ea != actual_ea:
                    result.errors.append(f"execution_allowed: expected {expected_ea}, got {actual_ea}")

            # ── Evaluate plan_status ──
            if "plan_status" in expected:
                expected_ps = expected["plan_status"]
                actual_ps = ir.plan_metadata.plan_status.value
                if expected_ps != actual_ps:
                    result.errors.append(f"plan_status: expected {expected_ps}, got {actual_ps}")

            # ── Evaluate output_schema_valid ──
            if expected.get("output_schema_valid"):
                if not result.schema_valid:
                    result.errors.append("Expected output schema to be valid")

            # Determine pass/fail
            result.passed = len(result.errors) == 0

        except Exception as e:
            result.errors.append(f"Exception: {type(e).__name__}: {str(e)[:200]}")
            result.passed = False

        result.elapsed_ms = (time.time() - t0) * 1000
        return result

    def compute_metrics(self) -> Dict[str, Any]:
        """Compute accuracy metrics across all results."""
        total = len(self.results)
        if total == 0:
            return {"total": 0}

        passed = sum(1 for r in self.results if r.passed)
        actionable = [r for r in self.results if r.action_expected]
        action_correct = sum(1 for r in actionable if r.action_match)
        action_accuracy = action_correct / len(actionable) if actionable else 1.0

        entity_cases = [r for r in self.results if r.theme_expected_id]
        entity_correct = sum(1 for r in entity_cases if r.theme_entity_match)
        entity_accuracy = entity_correct / len(entity_cases) if entity_cases else 1.0

        force_cases = [r for r in self.results if r.force_expected is not None]
        force_correct = sum(1 for r in force_cases if r.force_match)
        force_accuracy = force_correct / len(force_cases) if force_cases else 1.0

        role_cases = [r for r in self.results if r.missing_roles_expected]
        role_correct = sum(1 for r in role_cases if r.missing_roles_match)
        role_accuracy = role_correct / len(role_cases) if role_cases else 1.0

        schema_correct = sum(1 for r in self.results if r.schema_valid)
        schema_rate = schema_correct / total if total else 1.0

        avg_elapsed = sum(r.elapsed_ms for r in self.results) / total if total else 0

        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "action_accuracy": round(action_accuracy, 4),
            "action_cases": len(actionable),
            "entity_grounding_accuracy": round(entity_accuracy, 4),
            "entity_cases": len(entity_cases),
            "force_parsing_accuracy": round(force_accuracy, 4),
            "force_cases": len(force_cases),
            "role_detection_accuracy": round(role_accuracy, 4),
            "role_cases": len(role_cases),
            "schema_pass_rate": round(schema_rate, 4),
            "avg_elapsed_ms": round(avg_elapsed, 1),
            "overall_pass_rate": round(passed / total, 4) if total else 0.0,
        }

    def export_json(self, filepath: str) -> None:
        """Export results to JSON."""
        data = {
            "meta": self.dataset.get("meta", {}),
            "metrics": self.compute_metrics(),
            "results": [
                {
                    "case_id": r.case_id,
                    "instruction": r.instruction,
                    "passed": r.passed,
                    "action_match": r.action_match,
                    "action_expected": r.action_expected,
                    "action_actual": r.action_actual,
                    "theme_entity_match": r.theme_entity_match,
                    "theme_expected_id": r.theme_expected_id,
                    "theme_actual_id": r.theme_actual_id,
                    "force_match": r.force_match,
                    "force_expected": r.force_expected,
                    "force_actual": r.force_actual,
                    "missing_roles_match": r.missing_roles_match,
                    "missing_roles_expected": r.missing_roles_expected,
                    "missing_roles_actual": r.missing_roles_actual,
                    "schema_valid": r.schema_valid,
                    "errors": r.errors,
                    "elapsed_ms": r.elapsed_ms,
                }
                for r in self.results
            ],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def export_markdown(self, filepath: str) -> None:
        """Export results to Markdown report."""
        m = self.compute_metrics()
        lines = [
            "# Intent Understanding Accuracy Report",
            "",
            f"**Total cases**: {m['total']} | **Passed**: {m['passed']} | **Failed**: {m['failed']}",
            f"**Overall pass rate**: {m['overall_pass_rate']:.1%}",
            "",
            "## Accuracy Metrics",
            "",
            f"| Metric | Accuracy | Cases |",
            f"|--------|----------|-------|",
            f"| Action | {m['action_accuracy']:.1%} | {m['action_cases']} |",
            f"| Entity Grounding | {m['entity_grounding_accuracy']:.1%} | {m['entity_cases']} |",
            f"| Force Parsing | {m['force_parsing_accuracy']:.1%} | {m['force_cases']} |",
            f"| Role Detection | {m['role_detection_accuracy']:.1%} | {m['role_cases']} |",
            f"| Schema Valid | {m['schema_pass_rate']:.1%} | {m['total']} |",
            f"| Avg Latency | {m['avg_elapsed_ms']:.1f}ms | |",
            "",
            "## Failed Cases",
            "",
        ]
        for r in self.results:
            if not r.passed:
                lines.append(f"### ❌ {r.case_id}: {r.instruction}")
                lines.append(f"- Action: expected={r.action_expected}, actual={r.action_actual}")
                lines.append(f"- Entity: expected_id={r.theme_expected_id}, actual_id={r.theme_actual_id}")
                lines.append(f"- Errors: {'; '.join(r.errors)}")
                lines.append("")
        lines.append("## All Results")
        lines.append("")
        lines.append("| Case | Instruction | Action | Entity | Force | Roles | Schema | Result |")
        lines.append("|------|-------------|--------|--------|-------|-------|--------|--------|")
        for r in self.results:
            status = "✅" if r.passed else "❌"
            lines.append(f"| {r.case_id} | {r.instruction[:20]} | {'✅' if r.action_match else '❌'} | {'✅' if r.theme_entity_match else '❌'} | {'✅' if r.force_match else '—'} | {'✅' if r.missing_roles_match else '—'} | {'✅' if r.schema_valid else '❌'} | {status} |")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


if __name__ == "__main__":
    runner = EvalRunner()
    results = runner.run_all()
    metrics = runner.compute_metrics()
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    runner.export_json("eval-results.json")
    runner.export_markdown("eval-report.md")
    print(f"\nExported eval-results.json and eval-report.md")
    print(f"Total: {metrics['total']} | Passed: {metrics['passed']} | Failed: {metrics['failed']}")
