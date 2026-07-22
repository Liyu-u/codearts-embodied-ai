"""
Shared test fixtures for robot_intent_agent tests.

Provides:
    - pipeline: Pipeline instance for end-to-end tests
    - load_reasoning_cases(): loads normal/abnormal/api exception cases from JSON
    - assert_result_matches(): unified assertion helper
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Ensure the package root is on sys.path
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REASONING_CASES_PATH = FIXTURES_DIR / "reasoning_cases.json"


@pytest.fixture(scope="session")
def reasoning_cases_data() -> Dict[str, Any]:
    """Load the full reasoning cases JSON file once per session."""
    with open(REASONING_CASES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_normal_cases() -> List[Dict[str, Any]]:
    """Load all normal (non-abnormal, non-mock) test cases for parametrization."""
    with open(REASONING_CASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("normal_cases", [])


def load_abnormal_cases() -> List[Dict[str, Any]]:
    """Load all abnormal input test cases."""
    with open(REASONING_CASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("abnormal_cases", [])


def load_api_exception_cases() -> List[Dict[str, Any]]:
    """Load all API exception test cases."""
    with open(REASONING_CASES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("api_exception_cases", [])


@pytest.fixture
def pipeline():
    """Return a fresh Pipeline instance for end-to-end tests."""
    from robot_intent_agent.demo.web_ui import Pipeline
    return Pipeline()


def assert_result_matches(result: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    """
    Assert that pipeline result matches expected values.

    Returns a list of failure messages (empty = all passed).
    Does NOT raise — accumulates all failures for comprehensive reporting.
    """
    failures: List[str] = []

    # Check execution_ready
    if "execution_ready" in expected:
        if result.get("execution_ready") != expected["execution_ready"]:
            failures.append(
                f"execution_ready: expected {expected['execution_ready']}, "
                f"got {result.get('execution_ready')}"
            )

    # Check material (from semantic properties)
    if "material" in expected:
        sem_props = result.get("sem_props", [])
        if sem_props:
            actual_material = sem_props[0].material.value
            if actual_material != expected["material"]:
                failures.append(
                    f"material: expected '{expected['material']}', "
                    f"got '{actual_material}'"
                )

    # Check fragility_level
    if "fragility_level" in expected:
        sem_props = result.get("sem_props", [])
        if sem_props:
            actual_fragility = sem_props[0].fragility_level.value
            if actual_fragility != expected["fragility_level"]:
                failures.append(
                    f"fragility_level: expected {expected['fragility_level']}, "
                    f"got {actual_fragility}"
                )

    # Check max_force_n
    if "max_force_n" in expected:
        sem_props = result.get("sem_props", [])
        if sem_props:
            actual_max_f = sem_props[0].max_force_N.value
            if abs(actual_max_f - expected["max_force_n"]) > 0.01:
                failures.append(
                    f"max_force_n: expected {expected['max_force_n']}, "
                    f"got {actual_max_f}"
                )

    # Check requested_force_n
    if "requested_force_n" in expected:
        actual_req = result.get("raw_requested_force")
        if actual_req is not None:
            if abs(actual_req - expected["requested_force_n"]) > 0.01:
                failures.append(
                    f"requested_force_n: expected {expected['requested_force_n']}, "
                    f"got {actual_req}"
                )
        elif expected["requested_force_n"] is not None:
            failures.append(
                f"requested_force_n: expected {expected['requested_force_n']}, "
                f"got None"
            )

    # Check resolved_force_n <= some value
    if "resolved_force_n_le" in expected:
        actual_resolved = result.get("resolved_force")
        if actual_resolved is not None:
            if actual_resolved > expected["resolved_force_n_le"]:
                failures.append(
                    f"resolved_force_n: expected <= {expected['resolved_force_n_le']}, "
                    f"got {actual_resolved}"
                )

    # Check required skills
    if "required_skills" in expected:
        actual_skills = result.get("actions", [])
        for skill in expected["required_skills"]:
            if skill not in actual_skills:
                failures.append(
                    f"required_skill '{skill}' not found in actions: {actual_skills}"
                )

    # Check target_moving
    if "target_moving" in expected:
        if result.get("target_moving") != expected["target_moving"]:
            failures.append(
                f"target_moving: expected {expected['target_moving']}, "
                f"got {result.get('target_moving')}"
            )

    # Check planner name
    if "planner_name_contains" in expected:
        planner = result.get("planner_name", "")
        if expected["planner_name_contains"] not in planner:
            failures.append(
                f"planner_name: expected to contain '{expected['planner_name_contains']}', "
                f"got '{planner}'"
            )

    # Check override ledger for conflict
    if "conflict_detected" in expected and expected["conflict_detected"]:
        force_clamp = result.get("force_clamp", {})
        override = result.get("override", [])
        candidates = force_clamp.get("candidates", [])
        if len(candidates) <= 1 and len(override) == 0:
            failures.append(
                "conflict_detected: expected conflict but no clamping candidates "
                "or override ledger entries found"
            )

    # Check error expected
    if "error_expected" in expected and expected["error_expected"]:
        if not result.get("_error_occurred", False):
            failures.append("error_expected: expected an error but none occurred")

    return failures
