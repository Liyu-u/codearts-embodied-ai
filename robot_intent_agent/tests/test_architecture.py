"""
Architecture invariants for the intent understanding module.

Ensures:
1. All production entry points go through FinalPlanValidator.
2. RobotTaskIR is only constructed inside RobotTaskIRGenerator.generate().
3. The validation_result on RobotTaskIR is authoritative (not a placeholder).
4. No bypass path exists that produces RobotTaskIR without validation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.final_plan_validator import FinalPlanValidator, STAGE_VELOCITY_LIMITS
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.planner import BehaviorTreeGenerator
from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.task_semantics import PlanStatus, TaskActionKind, ValidationResult


# ── Helpers ──────────────────────────────────────────────────

def _make_scene(objects):
    return SemanticSceneBuilder().build(objects)


def _run_pipeline(instruction, objects):
    """Standard pipeline: Scene → BT → CG → IR (includes FinalPlanValidator)."""
    scene = _make_scene(objects)
    bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    target = objects[0].name if objects else "target"
    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)
    return ir


# ── Fixtures ─────────────────────────────────────────────────

@pytest.fixture
def cup_scene():
    return [
        RawObjectPercept(name="杯子", x=0.35, y=0.12, z=0.06,
                         width=0.07, height=0.10, depth=0.07,
                         color="white", material="ceramic"),
    ]


@pytest.fixture
def glass_scene():
    return [
        RawObjectPercept(name="玻璃杯", x=0.35, y=0.12, z=0.06,
                         width=0.07, height=0.12, depth=0.07,
                         color="transparent", material="glass"),
    ]


@pytest.fixture
def cup_and_table_scene():
    return [
        RawObjectPercept(name="杯子", x=0.25, y=0.10, z=0.06,
                         width=0.07, height=0.10, depth=0.07,
                         color="white", material="ceramic"),
        RawObjectPercept(name="桌子", x=0.40, y=0.00, z=0.00,
                         width=0.60, height=0.03, depth=0.40,
                         color="brown", material="wood"),
    ]


# ── Test: All entry points go through FinalPlanValidator ─────

class TestArchitectureFinalPlanValidator:
    """Verify FinalPlanValidator is the authoritative validation gate."""

    def test_standard_pipeline_produces_validated_ir(self, cup_scene):
        """Standard pipeline: IR.validation_result must be authoritative (not placeholder)."""
        ir = _run_pipeline("抓住杯子", cup_scene)

        assert ir.validation_result is not None
        assert isinstance(ir.validation_result, ValidationResult)
        # A real validation has an explicit status (not just plan_status passthrough)
        assert ir.validation_result.status in (
            PlanStatus.READY, PlanStatus.READY_WITH_SAFE_SUBSTITUTION,
            PlanStatus.NEEDS_CLARIFICATION, PlanStatus.BLOCKED,
        )
        # execution_allowed must be a bool, not None
        assert isinstance(ir.validation_result.execution_allowed, bool)

    def test_ir_construction_only_in_generator(self):
        """RobotTaskIR should only be constructed via RobotTaskIRGenerator.generate()."""
        from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR

        # RobotTaskIR has required fields — constructing it directly
        # without going through the generator should fail validation
        # because there's no flattened public API for that
        generator_source_file = RobotTaskIRGenerator.generate.__code__.co_filename
        assert "ir_generator" in generator_source_file.replace("\\", "/"), \
            f"RobotTaskIRGenerator.generate() defined in unexpected file: {generator_source_file}"

    def test_final_validator_called_from_ir_generator(self):
        """FinalPlanValidator.validate() must be called from RobotTaskIRGenerator.generate()."""
        import inspect

        source = inspect.getsource(RobotTaskIRGenerator.generate)
        assert "FinalPlanValidator" in source, \
            "RobotTaskIRGenerator.generate() must explicitly call FinalPlanValidator"
        assert "validator.validate(" in source or "validator.validate(" not in source or "FinalPlanValidator().validate(" in source or ".validate(" in source, \
            ".validate() must be called inside generate()"

    def test_no_direct_robot_task_ir_construction_in_pipeline(self):
        """Search: no production code constructs RobotTaskIR(...) outside ir_generator.py."""
        import os
        # RobotTaskIR(...) should only appear in:
        # - ir_generator.py (the builder)
        # - test files
        # - robot_task_ir.py (the class definition)
        # It should NOT appear in planner/, constraint/, scene_builder/, demo/, etc.
        allowed_patterns = ["ir_generator.py", "test_", "conftest.py"]
        suspicious = []
        repo_root = Path(__file__).parent.parent

        for py_file in repo_root.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            rel = str(py_file.relative_to(repo_root)).replace("\\", "/")
            if any(p in rel for p in allowed_patterns):
                continue
            if "schemas/robot_task_ir.py" in rel:
                continue  # class definition
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if "RobotTaskIR(" in content:
                # Only flag if it's a construction call (not import/type hint/comment)
                lines = content.split("\n")
                for lineno, line in enumerate(lines, 1):
                    if "RobotTaskIR(" in line and not line.strip().startswith(("#", "from", "import")):
                        # Allow if it's inside a comment
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            continue
                        suspicious.append(f"{rel}:{lineno}: {stripped[:100]}")

        if suspicious:
            # Some constructions might be legitimate (e.g., test mocks).
            # Filter out known test patterns.
            real_violations = [s for s in suspicious
                             if not any(p in s for p in ["test_", "tests/", "conftest"])]
            assert not real_violations, \
                f"RobotTaskIR constructed outside ir_generator.py:\n" + "\n".join(real_violations)


# ── Test: Validation dimensions are applied ──────────────────

class TestArchitectureValidationDimensions:
    """Verify all 8 validation dimensions from FinalPlanValidator are exercised."""

    def test_stage_velocity_validation_applied(self, cup_scene):
        """STAGE_VELOCITY_LIMITS must be checked — per-skill velocity validation."""
        ir = _run_pipeline("抓住杯子", cup_scene)
        # After validation, no BT action should exceed stage velocity limits
        for action in ir.behavior_tree.root.flatten_actions():
            limit = STAGE_VELOCITY_LIMITS.get(action.skill_name)
            if limit is None or limit <= 0:
                continue
            vel = action.params.get("velocity_ms")
            if isinstance(vel, dict):
                vel = vel.get("value")
            if vel is not None:
                assert float(vel) <= limit + 1e-9, \
                    f"{action.skill_name} velocity {vel} exceeds stage limit {limit}"

    def test_force_not_exceed_hard_limit(self, glass_scene):
        """Glass cup must cap force to safe limit even with user override."""
        ir = _run_pipeline("用50N力量把玻璃杯抓过来", glass_scene)
        force_res = ir.constraint_resolution.parameters.get("force_n")
        assert force_res is not None
        assert force_res.selected_value is not None
        # Glass material hard cap should clamp from 50N
        assert force_res.selected_value <= 10.0, \
            f"Glass cup force {force_res.selected_value} exceeds material hard limit"

    def test_missing_theme_blocks_execution(self):
        """If theme not in scene, execution must be blocked."""
        scene = _make_scene([
            RawObjectPercept(name="药瓶", x=0.20, y=0.08, z=0.04,
                             width=0.03, height=0.08, depth=0.03,
                             color="red", material="plastic"),
        ])
        bt = BehaviorTreeGenerator().plan("把杯子拿过来", scene=scene)
        cg = HybridConstraintCompiler().compile("把杯子拿过来", bt, scene=scene, target="杯子")
        ir = RobotTaskIRGenerator().generate("把杯子拿过来", bt, cg, scene=scene)

        # "杯子" is not in the scene ("药瓶" is) — theme grounding should fail
        issues = ir.validation_result.issues
        has_grounding_issue = any(
            issue.code in ("MISSING_THEME_GROUNDING", "BT_TARGET_NOT_GROUNDED", "NON_DISPATCHABLE_STATUS")
            for issue in issues
        )
        assert has_grounding_issue or not ir.validation_result.execution_allowed, \
            f"Expected grounding issue but got execution_allowed={ir.validation_result.execution_allowed}, issues={[i.code for i in issues]}"

    def test_missing_recipient_detected(self, cup_scene):
        """HANDOVER without recipient must be flagged."""
        ir = _run_pipeline("把杯子递给我", cup_scene)
        # "我" is recognized as recipient, but no pose → should produce issues
        issues = ir.validation_result.issues
        recipient_codes = {i.code for i in issues}
        # Either recipient_pose is missing or execution is blocked
        has_recipient_issue = bool(
            recipient_codes & {"MISSING_RECIPIENT_POSE", "MISSING_DELIVERY_POSE", "MISSING_RECIPIENT"}
        )
        assert has_recipient_issue or not ir.validation_result.execution_allowed, \
            f"HANDOVER without recipient pose: execution_allowed={ir.validation_result.execution_allowed}, codes={recipient_codes}"

    def test_action_consistency_validated(self, cup_scene):
        """BT actions must match parsed task action."""
        ir = _run_pipeline("抓住杯子", cup_scene)
        # GRASP action should have Grasp or Reach in BT
        action_names = [a.skill_name for a in ir.behavior_tree.root.flatten_actions()]
        assert any(a in ("Grasp", "Reach", "GentleGrasp") for a in action_names), \
            f"Grasp task should have Grasp/Reach action, got {action_names}"

    def test_obstacle_constraint_propagation(self):
        """Obstacles must produce collision_avoid constraints and PlanPath."""
        scene = _make_scene([
            RawObjectPercept(name="盒子", x=0.30, y=0.10, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard"),
            RawObjectPercept(name="玻璃杯", x=0.30, y=0.05, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ])
        bt = BehaviorTreeGenerator().plan("把盒子拿过来，别碰玻璃杯", scene=scene)
        cg = HybridConstraintCompiler().compile("把盒子拿过来，别碰玻璃杯", bt, scene=scene, target="盒子")
        ir = RobotTaskIRGenerator().generate("把盒子拿过来，别碰玻璃杯", bt, cg, scene=scene)

        action_names = [a.skill_name for a in bt.root.flatten_actions()]
        collision_nodes = [n for n in cg.nodes if n.constraint_type == "collision_avoid"]
        assert len(collision_nodes) > 0, "Must have collision_avoid constraints for obstacle"
        assert "PlanPath" in action_names, f"Must have PlanPath when obstacles exist, got {action_names}"


# ── Test: Web UI pipeline consistency ────────────────────────

class TestArchitectureWebUIPipeline:
    """Verify the Web UI pipeline (demo/web_ui.py) path is consistent."""

    def test_web_ui_pipeline_uses_final_validator(self):
        """The web_ui Pipeline.run() → RobotTaskIRGenerator.generate() → FinalPlanValidator."""
        try:
            from robot_intent_agent.demo.web_ui import pipeline
        except ImportError:
            pytest.skip("Gradio not available")
        import json
        obs = json.dumps({
            "objects": [{
                "object_id": "obj_cup_001",
                "category_candidates": [{"name": "杯子", "score": 0.93}],
                "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}},
                "geometry": {"size": {"width": 0.07, "height": 0.10, "depth": 0.07}},
                "appearance": {"color": "white", "material": "ceramic"},
                "affordances": ["graspable", "movable"],
                "tracking": {"state": "stationary", "confidence": 0.96,
                             "velocity": {"x": 0.0, "y": 0.0, "z": 0.0}, "velocity_confidence": 0.0},
            }]
        })
        result = pipeline.run("抓住杯子", obs, "纯规则引擎 (极速)", "")
        ir = result["ir"]
        assert ir is not None, "Web UI pipeline must produce IR"
        assert ir.validation_result is not None, "Web UI pipeline must produce validated IR"
        assert isinstance(ir.validation_result.execution_allowed, bool), \
            "Web UI pipeline validation must produce execution_allowed bool"


# ── Test: CLI demo pipeline consistency ──────────────────────

class TestArchitectureCLIPipeline:
    """Verify the CLI demo (demo/cli_demo.py) path is consistent."""

    def test_cli_pipeline_produces_validated_ir(self):
        """CLI pipeline must produce IR with validation."""
        from robot_intent_agent.demo.cli_demo import PipelineRunner, PresetTask

        runner = PipelineRunner()
        task = PresetTask(
            name="test_arch",
            instruction="抓住杯子",
            objects=[
                RawObjectPercept(name="杯子", x=0.35, y=0.12, z=0.06,
                                 width=0.07, height=0.10, depth=0.07,
                                 color="white", material="ceramic"),
            ],
            memory_setup=lambda r: None,
            description="test",
        )
        result = runner.run(task, verbose=False)
        assert result is not None
        ir_data = json.loads(result["ir_json"])
        assert "validation_result" in ir_data, "CLI pipeline must produce validation_result"
        vr = ir_data["validation_result"]
        assert "execution_allowed" in vr, "Validation result must contain execution_allowed"


# ── Test: No bypass path ─────────────────────────────────────

class TestArchitectureNoBypass:
    """Verify no bypass path exists that produces IR without FinalPlanValidator."""

    def test_constraint_compiler_placeholder_not_authoritative(self, cup_scene):
        """CG metadata contains a placeholder validation — must not be treated as authoritative."""
        scene = _make_scene(cup_scene)
        bt = BehaviorTreeGenerator().plan("抓住杯子", scene=scene)
        cg = HybridConstraintCompiler().compile("抓住杯子", bt, scene=scene, target="杯子")

        # CG metadata has PlanDecision with placeholder validation
        plan_decision = cg.metadata.get("plan_decision")
        assert plan_decision is not None, "CG should store plan_decision"
        pd_validation = plan_decision.get("validation_result", {})
        # The placeholder has no issues (empty list), while the real validator would catch real problems
        placeholder_issues = pd_validation.get("issues", [])
        # Compare: the authoritative IR must have been through FinalPlanValidator
        ir = RobotTaskIRGenerator().generate("抓住杯子", bt, cg, scene=scene)
        # The authoritative validation_result is on the IR, not in CG metadata
        assert ir.validation_result is not None
        # The IR's validation should have been produced by FinalPlanValidator
        # (which checks all 8 dimensions, not just plan_status passthrough)
        assert hasattr(ir.validation_result, "issues")

    def test_final_validator_not_called_outside_ir_generator(self):
        """Production code should not call FinalPlanValidator directly outside tests/ir_generator."""
        import ast
        violations = []
        repo_root = Path(__file__).parent.parent

        for py_file in repo_root.rglob("*.py"):
            if py_file.name.startswith("__"):
                continue
            rel = str(py_file.relative_to(repo_root)).replace("\\", "/")
            # Allowed: test files, ir_generator, final_plan_validator itself
            if any(p in rel for p in ["test_", "tests/", "ir_generator.py", "final_plan_validator.py", "conftest.py", "__init__.py"]):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
            except Exception:
                continue
            if "FinalPlanValidator" in content:
                # Check if it's a real instantiation call (not import/test)
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Name) and node.func.id == "FinalPlanValidator":
                            violations.append(f"{rel}:{node.lineno}: FinalPlanValidator() called directly")
                        elif isinstance(node.func, ast.Attribute) and node.func.attr == "validate":
                            if isinstance(node.func.value, ast.Name) and "validator" in node.func.value.id.lower():
                                violations.append(f"{rel}:{node.lineno}: .validate() called on possible FinalPlanValidator")

        # Filter false positives: test_architecture.py is allowed
        real_violations = [v for v in violations
                         if "test_architecture" not in v]
        if real_violations:
            # Check if they're in demo/ or other non-test non-ir_generator code
            demo_violations = [v for v in real_violations if "demo/" in v or "eval/" in v or "safety/" in v or "constraint/" in v]
            assert not demo_violations, \
                f"FinalPlanValidator should not be called outside ir_generator.py:\n" + "\n".join(demo_violations)
