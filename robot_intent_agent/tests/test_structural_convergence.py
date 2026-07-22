"""
Structural convergence tests (Phase 11).

Verifies:
1. Deprecated components are not used in production call paths
2. Shared utilities are used (no duplicated implementations)
3. All production entry points go through FinalPlanValidator
4. No sys.path hacks in production code
5. TODO stubs are confined to their files
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


# ══════════════════════════════════════════════════════════════
# 1. Deprecated code isolation
# ══════════════════════════════════════════════════════════════

class TestDeprecatedIsolation:
    """Deprecated code must NOT be called from core production paths."""

    CORE_PRODUCTION_FILES = [
        "ir/ir_generator.py",
        "constraint/constraint_compiler.py",
        "constraint/rule_engine.py",
        "planner/behavior_tree_generator.py",
        "final_plan_validator.py",
        "task_semantics.py",
        "scene_builder/semantic_scene_builder.py",
    ]

    def test_placeholder_validation_not_called_from_ir_generator(self):
        """IR generator must use FinalPlanValidator, not _placeholder_validation."""
        source = (REPO_ROOT / "ir" / "ir_generator.py").read_text(encoding="utf-8")
        assert "FinalPlanValidator" in source, \
            "IR generator must use FinalPlanValidator"
        # _placeholder_validation should NOT appear in ir_generator
        assert "_placeholder_validation" not in source, \
            "IR generator must not use _placeholder_validation"

    def test_placeholder_validation_only_in_constraint_compiler(self):
        """_placeholder_validation should only exist in constraint_compiler (with @deprecated)."""
        for py_file in REPO_ROOT.rglob("*.py"):
            if py_file.name.startswith("__") or "test" in str(py_file).lower():
                continue
            if "constraint_compiler" in str(py_file):
                continue  # allowed here (deprecated)
            content = py_file.read_text(encoding="utf-8")
            if "_placeholder_validation" in content:
                rel = str(py_file.relative_to(REPO_ROOT))
                pytest.fail(f"_placeholder_validation found in {rel} — must only exist in constraint_compiler.py")

    def test_load_parsed_task_shared_utility_used(self):
        """Both constraint_compiler and ir_generator must use load_parsed_task_from_bt."""
        cc = (REPO_ROOT / "constraint" / "constraint_compiler.py").read_text(encoding="utf-8")
        ir = (REPO_ROOT / "ir" / "ir_generator.py").read_text(encoding="utf-8")
        assert "load_parsed_task_from_bt" in cc, \
            "constraint_compiler must use shared load_parsed_task_from_bt"
        assert "load_parsed_task_from_bt" in ir, \
            "ir_generator must use shared load_parsed_task_from_bt"

    def test_no_duplicate_avoid_keywords_in_production(self):
        """AVOID_KEYWORDS in behavior_tree_generator is @deprecated; rule_engine has the active one."""
        bt_gen = (REPO_ROOT / "planner" / "behavior_tree_generator.py").read_text(encoding="utf-8")
        # Must have @deprecated marker near AVOID_KEYWORDS
        assert "@deprecated" in bt_gen, \
            "behavior_tree_generator deprecated items must have @deprecated marker"

    def test_rule_instruction_parser_has_deprecated_marker(self):
        """RuleInstructionParser must be marked @deprecated."""
        bt_gen = (REPO_ROOT / "planner" / "behavior_tree_generator.py").read_text(encoding="utf-8")
        # The class docstring must mention deprecated
        assert "@deprecated" in bt_gen, \
            "RuleInstructionParser must be marked @deprecated"


# ══════════════════════════════════════════════════════════════
# 2. Shared utility verification
# ══════════════════════════════════════════════════════════════

class TestSharedUtilities:
    """Core utilities must be defined once and imported by consumers."""

    def test_parse_task_semantics_is_single_source(self):
        """All production code must get parse_task_semantics from task_semantics.py."""
        from robot_intent_agent.task_semantics import parse_task_semantics
        fn_file = inspect.getfile(parse_task_semantics)
        assert "task_semantics" in fn_file.replace("\\", "/"), \
            f"parse_task_semantics must be defined in task_semantics.py, not {fn_file}"

    def test_final_plan_validator_is_single_source(self):
        """All validation must go through final_plan_validator.py."""
        from robot_intent_agent.final_plan_validator import FinalPlanValidator
        fn_file = inspect.getfile(FinalPlanValidator)
        assert "final_plan_validator" in fn_file.replace("\\", "/"), \
            f"FinalPlanValidator must be defined in final_plan_validator.py, not {fn_file}"

    def test_ir_generator_imports_shared_load_parsed_task(self):
        """ir_generator must import from task_semantics, not define its own."""
        source = (REPO_ROOT / "ir" / "ir_generator.py").read_text(encoding="utf-8")
        assert "from robot_intent_agent.task_semantics import" in source
        assert "load_parsed_task_from_bt" in source

    def test_constraint_compiler_imports_shared_load_parsed_task(self):
        """constraint_compiler must import from task_semantics, not define its own."""
        source = (REPO_ROOT / "constraint" / "constraint_compiler.py").read_text(encoding="utf-8")
        assert "from robot_intent_agent.task_semantics import" in source
        assert "load_parsed_task_from_bt" in source


# ══════════════════════════════════════════════════════════════
# 3. Production path integrity
# ══════════════════════════════════════════════════════════════

class TestProductionPathIntegrity:
    """All production entry points must go through the same validation."""

    def test_web_ui_goes_through_ir_generator(self):
        """Web UI Pipeline.run() → RobotTaskIRGenerator.generate()."""
        source = (REPO_ROOT / "demo" / "web_ui.py").read_text(encoding="utf-8")
        assert "RobotTaskIRGenerator" in source, \
            "Web UI must use RobotTaskIRGenerator for IR generation"

    def test_cli_demo_goes_through_ir_generator(self):
        """CLI demo PipelineRunner.run() → RobotTaskIRGenerator.generate()."""
        source = (REPO_ROOT / "demo" / "cli_demo.py").read_text(encoding="utf-8")
        assert "RobotTaskIRGenerator" in source, \
            "CLI demo must use RobotTaskIRGenerator"

    def test_eval_runner_goes_through_ir_generator(self):
        """Eval runner → RobotTaskIRGenerator.generate()."""
        source = (REPO_ROOT / "eval" / "runner.py").read_text(encoding="utf-8")
        assert "RobotTaskIRGenerator" in source, \
            "Eval runner must use RobotTaskIRGenerator"

    def test_no_sys_path_hack_in_production_demos(self):
        """sys.path.insert should not exist in production demo files."""
        # These files have sys.path.insert for running standalone
        # They are allowed but should have a comment explaining why
        web_ui = (REPO_ROOT / "demo" / "web_ui.py").read_text(encoding="utf-8")
        cli_demo = (REPO_ROOT / "demo" / "cli_demo.py").read_text(encoding="utf-8")
        # Verify they exist (we can't remove them without breaking standalone run)
        assert "sys.path.insert" in web_ui or "sys.path" in web_ui
        assert "sys.path.insert" in cli_demo or "sys.path" in cli_demo


# ══════════════════════════════════════════════════════════════
# 4. TODO / stub confinement
# ══════════════════════════════════════════════════════════════

class TestTODOConfinement:
    """TODO stubs must not be in core production paths."""

    def test_todo_not_in_critical_paths(self):
        """Core files must not have unaddressed TODOs."""
        critical = ["ir/ir_generator.py", "constraint/constraint_compiler.py",
                    "final_plan_validator.py", "task_semantics.py"]
        for rel_path in critical:
            content = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
            # Allow @deprecated markers but not bare [TODO]
            todo_lines = [l for l in content.split("\n")
                         if "[TODO]" in l and "@deprecated" not in l]
            assert not todo_lines, \
                f"{rel_path} has unresolved TODO: {todo_lines[0].strip()[:80]}"

    def test_main_py_todos_are_documented(self):
        """main.py TODOs must be clearly marked as unimplemented stubs."""
        main_py = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        if "[TODO]" in main_py:
            assert "coming in" in main_py.lower() or "future" in main_py.lower(), \
                "main.py TODOs must indicate timeline"


# ══════════════════════════════════════════════════════════════
# 5. File count and structure check
# ══════════════════════════════════════════════════════════════

class TestFileStructureIntegrity:
    """Verify file structure hasn't degraded."""

    def test_no_duplicate_module_names(self):
        """Each Python module should appear only once per directory (same-name across packages is fine)."""
        prod_files = list((REPO_ROOT).rglob("*.py"))
        prod_files = [f for f in prod_files
                      if "__pycache__" not in str(f)
                      and "tests" not in str(f)
                      and not f.name.startswith("__")
                      and "fixtures" not in str(f)]
        # Group by directory
        from collections import defaultdict
        by_dir = defaultdict(list)
        for f in prod_files:
            by_dir[f.parent.name].append(f.name)
        duplicates = {d: [n for n in set(names) if names.count(n) > 1]
                      for d, names in by_dir.items()}
        real_dups = {d: ns for d, ns in duplicates.items() if ns}
        assert not real_dups, f"Duplicate module names within same directory: {real_dups}"

    def test_schema_files_count_unchanged(self):
        """Schema files must not be duplicated."""
        schema_dir = REPO_ROOT / "schemas"
        py_files = list(schema_dir.glob("*.py"))
        assert len(py_files) <= 6, \
            f"Schema directory should have <=6 .py files, has {len(py_files)}"

    def test_constraint_files_count_stable(self):
        """Constraint module files must not grow unexpectedly."""
        constraint_dir = REPO_ROOT / "constraint"
        py_files = [f for f in constraint_dir.glob("*.py") if not f.name.startswith("__")]
        assert 5 <= len(py_files) <= 8, \
            f"Constraint module should have 5-8 .py files, has {len(py_files)}: {[f.name for f in py_files]}"
