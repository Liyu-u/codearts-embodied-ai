"""
Parameterized end-to-end reasoning test suite.

Covers the FULL pipeline for every test case:
    env JSON → scene parse → NL parse → entity binding →
    property inference → safety constraints → RobotTaskIR →
    action sequence → execution_status

Usage:
    pytest tests/test_reasoning_cases.py -v
    pytest tests/test_reasoning_cases.py -v -k "TC_003"
    pytest tests/test_reasoning_cases.py -v --tb=short
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure package root on sys.path
_PKG_ROOT = Path(__file__).resolve().parent.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

# ── Fixture loading helpers ──
_FIXTURES_PATH = Path(__file__).resolve().parent / "fixtures" / "reasoning_cases.json"

def _load_fixtures():
    with open(_FIXTURES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_normal_cases():
    return _load_fixtures().get("normal_cases", [])

def load_abnormal_cases():
    return _load_fixtures().get("abnormal_cases", [])

def load_api_exception_cases():
    return _load_fixtures().get("api_exception_cases", [])

def assert_result_matches(result, expected):
    """Assert pipeline result matches expected. Returns list of failure messages."""
    failures = []
    if "execution_ready" in expected:
        if result.get("execution_ready") != expected["execution_ready"]:
            failures.append(f"execution_ready: expected {expected['execution_ready']}, got {result.get('execution_ready')}")
    if "material" in expected:
        sps = result.get("sem_props", [])
        if sps:
            am = sps[0].material.value
            if am != expected["material"]:
                failures.append(f"material: expected '{expected['material']}', got '{am}'")
    if "fragility_level" in expected:
        sps = result.get("sem_props", [])
        if sps:
            af = sps[0].fragility_level.value
            if af != expected["fragility_level"]:
                failures.append(f"fragility_level: expected {expected['fragility_level']}, got {af}")
    if "max_force_n" in expected:
        sps = result.get("sem_props", [])
        if sps:
            amf = sps[0].max_force_N.value
            if abs(amf - expected["max_force_n"]) > 0.01:
                failures.append(f"max_force_n: expected {expected['max_force_n']}, got {amf}")
    if "requested_force_n" in expected:
        ar = result.get("raw_requested_force")
        if ar is not None and abs(ar - expected["requested_force_n"]) > 0.01:
            failures.append(f"requested_force_n: expected {expected['requested_force_n']}, got {ar}")
    if "resolved_force_n_le" in expected:
        arf = result.get("resolved_force")
        if arf is not None and arf > expected["resolved_force_n_le"]:
            failures.append(f"resolved_force_n: expected <= {expected['resolved_force_n_le']}, got {arf}")
    if "required_skills" in expected:
        acts = result.get("actions", [])
        for s in expected["required_skills"]:
            if s not in acts:
                failures.append(f"required_skill '{s}' not in {acts}")
    if "target_moving" in expected:
        if result.get("target_moving") != expected["target_moving"]:
            failures.append(f"target_moving: expected {expected['target_moving']}, got {result.get('target_moving')}")
    if "planner_name_contains" in expected:
        pn = result.get("planner_name", "")
        if expected["planner_name_contains"] not in pn:
            failures.append(f"planner_name: expected contains '{expected['planner_name_contains']}', got '{pn}'")
    return failures

# ═══════════════════════════════════════════════════════════════
# Normal Test Cases — Full Pipeline
# ═══════════════════════════════════════════════════════════════

NORMAL_CASES = [
    pytest.param(c, id=c["case_id"])
    for c in load_normal_cases()
]


class TestNormalCases:
    """End-to-end pipeline tests for all normal reasoning scenarios."""

    @pytest.mark.parametrize("case", NORMAL_CASES)
    def test_full_pipeline(self, pipeline, case):
        """Run the full pipeline and verify all expected outcomes."""
        obs = case["observation_json"]
        obs_str = json.dumps(obs, ensure_ascii=False)
        engine = case.get("engine", "纯规则引擎 (极速)")

        result = pipeline.run(
            instruction=case["natural_language_command"],
            obs_json_str=obs_str,
            engine=engine,
            api_key="",
        )

        expected = case.get("expected", {})
        failures = assert_result_matches(result, expected)

        # Build a detailed failure message with context
        if failures:
            failure_msg = (
                f"\n{'='*60}\n"
                f"Case: {case['case_id']} — {case['case_name']}\n"
                f"Instruction: {case['natural_language_command']}\n"
                f"Engine: {engine}\n"
                f"{'='*60}\n"
                f"Failures ({len(failures)}):\n"
                + "\n".join(f"  • {f}" for f in failures)
                + f"\n{'='*60}\n"
            )
            pytest.fail(failure_msg)

    @pytest.mark.parametrize("case", NORMAL_CASES)
    def test_ir_serializable(self, pipeline, case):
        """Verify RobotTaskIR can be serialized to valid JSON."""
        obs_str = json.dumps(case["observation_json"], ensure_ascii=False)
        engine = case.get("engine", "纯规则引擎 (极速)")

        result = pipeline.run(
            instruction=case["natural_language_command"],
            obs_json_str=obs_str,
            engine=engine,
            api_key="",
        )

        ir_raw = result.get("ir_raw", "")
        assert ir_raw, f"IR raw JSON is empty for {case['case_id']}"

        # Must be valid JSON
        try:
            ir_data = json.loads(ir_raw)
        except json.JSONDecodeError as e:
            pytest.fail(f"IR raw is not valid JSON in {case['case_id']}: {e}")

        # Must have required top-level keys
        required_keys = ["ir_version", "task_metadata", "skills"]
        for key in required_keys:
            assert key in ir_data, (
                f"IR missing required key '{key}' in {case['case_id']}"
            )

    @pytest.mark.parametrize("case", NORMAL_CASES)
    def test_action_sequence_not_empty(self, pipeline, case):
        """Verify every case produces a non-empty action sequence."""
        obs_str = json.dumps(case["observation_json"], ensure_ascii=False)
        engine = case.get("engine", "纯规则引擎 (极速)")

        result = pipeline.run(
            instruction=case["natural_language_command"],
            obs_json_str=obs_str,
            engine=engine,
            api_key="",
        )

        actions = result.get("actions", [])
        assert len(actions) > 0, (
            f"Empty action sequence for {case['case_id']}: "
            f"'{case['natural_language_command']}'"
        )


# ═══════════════════════════════════════════════════════════════
# Abnormal Input Test Cases — Error Handling
# ═══════════════════════════════════════════════════════════════

ABNORMAL_CASES = [
    pytest.param(c, id=c["case_id"])
    for c in load_abnormal_cases()
]


class TestAbnormalInputs:
    """Error handling and edge case tests for malformed inputs."""

    @pytest.mark.parametrize("case", ABNORMAL_CASES)
    def test_abnormal_input_handled(self, pipeline, case):
        """Verify abnormal inputs are handled without crashing."""
        obs_str = case["observation_json_str"]
        engine = case.get("engine", "纯规则引擎 (极速)")

        error_occurred = False
        error_message = ""

        try:
            result = pipeline.run(
                instruction=case["natural_language_command"],
                obs_json_str=obs_str,
                engine=engine,
                api_key="",
            )
            # Pipeline didn't raise — check if error was expected
            if case.get("expected", {}).get("error_expected"):
                error_occurred = False
                error_message = (
                    f"Expected error for {case['case_id']} but pipeline "
                    f"completed without raising"
                )
        except Exception as e:
            error_occurred = True
            error_message = str(e)[:200]
            if not case.get("expected", {}).get("error_expected"):
                pytest.fail(
                    f"Unexpected error in {case['case_id']} "
                    f"({case['case_name']}): {error_message}"
                )

        # For cases where error is expected, verify it was caught
        expected = case.get("expected", {})
        if expected.get("error_expected") and not error_occurred:
            pytest.fail(error_message)

        # For cases where we expect graceful handling (no crash)
        if not expected.get("error_expected"):
            # Pipeline finished without crashing — success
            pass


# ═══════════════════════════════════════════════════════════════
# API Exception Test Cases — Mock-based Fallback Tests
# ═══════════════════════════════════════════════════════════════

API_CASES = [
    pytest.param(c, id=c["case_id"])
    for c in load_api_exception_cases()
]


class TestAPIExceptions:
    """Tests for LLM API failure modes and rule-engine fallback."""

    @pytest.mark.parametrize("case", API_CASES)
    def test_empty_api_key_fallback(self, pipeline, case):
        """TC_010_01: Empty API key must fall back to rule engine."""
        if case.get("mock_scenario") != "empty_api_key":
            pytest.skip("Only for empty_api_key scenario")

        obs_str = json.dumps(case["observation_json"], ensure_ascii=False)

        result = pipeline.run(
            instruction=case["natural_language_command"],
            obs_json_str=obs_str,
            engine=case["engine"],
            api_key="",  # Empty key
        )

        planner = result.get("planner_name", "")
        assert "RuleEngine" in planner or "Rule Engine" in planner, (
            f"Expected fallback to Rule Engine, got: '{planner}'"
        )
        expected = case.get("expected", {})
        if "execution_ready" in expected:
            assert result.get("execution_ready") == expected["execution_ready"]

    @pytest.mark.parametrize("case", API_CASES)
    def test_llm_error_fallback(self, pipeline, case):
        """Test that LLM errors trigger rule engine fallback.

        This test verifies the error handling path in Pipeline._plan():
        when LLMPlanner raises an exception, the pipeline catches it
        and falls back to BehaviorTreeGenerator.
        """
        if case.get("mock_scenario") == "empty_api_key":
            pytest.skip("Empty API key tested separately")

        from unittest.mock import patch, MagicMock
        from robot_intent_agent.planner import LLMPlanner

        obs_str = json.dumps(case["observation_json"], ensure_ascii=False)
        scenario = case.get("mock_scenario", "")

        # Map scenarios to corresponding exceptions
        error_map = {
            "http_401": Exception("401 Unauthorized"),
            "http_429": Exception("429 Too Many Requests"),
            "timeout": TimeoutError("Request timed out after 30s"),
            "network_error": ConnectionError("Network unreachable"),
            "empty_response": Exception("Empty response from API"),
        }

        exc = error_map.get(scenario, Exception("Mock error"))

        with patch.object(LLMPlanner, 'plan', side_effect=exc):
            result = pipeline.run(
                instruction=case["natural_language_command"],
                obs_json_str=obs_str,
                engine=case["engine"],
                api_key="sk-mock-key-for-test",
            )

        # Verify fallback to rule engine occurred
        planner = result.get("planner_name", "")
        assert "RuleEngine" in planner or "Rule Engine" in planner or "LLM降级" in planner, (
            f"Expected fallback after {scenario}, got planner: '{planner}'"
        )
        # Pipeline must still produce valid output with actions
        assert len(result.get("actions", [])) > 0
        expected = case.get("expected", {})
        if "execution_ready" in expected:
            assert result.get("execution_ready") == expected["execution_ready"]


# ═══════════════════════════════════════════════════════════════
# Preset Binding Tests
# ═══════════════════════════════════════════════════════════════

class TestPresetBinding:
    """Verify that Gradio preset dropdown correctly binds both inputs."""

    def test_preset_updates_both_inputs(self):
        """Preset selection must update both command and observation JSON."""
        # Simulate what on_case() does in the Gradio UI
        from robot_intent_agent.demo.web_ui import PRESET_CASES

        for preset_name, preset_data in PRESET_CASES.items():
            command = preset_data["command"]
            observation = preset_data["observation_json"]

            assert command, f"Preset '{preset_name}' has empty command"
            assert isinstance(observation, dict), (
                f"Preset '{preset_name}' observation is not a dict: "
                f"{type(observation).__name__}"
            )
            assert "objects" in observation, (
                f"Preset '{preset_name}' observation missing 'objects' key"
            )
            assert len(observation["objects"]) > 0, (
                f"Preset '{preset_name}' has empty objects list"
            )
            # Each object must have required fields
            for obj in observation["objects"]:
                assert "object_id" in obj, (
                    f"Object missing object_id in preset '{preset_name}'"
                )
                assert "category_candidates" in obj, (
                    f"Object missing category_candidates in preset '{preset_name}'"
                )

    def test_all_presets_executable(self, pipeline):
        """Every preset case must execute successfully through the pipeline."""
        from robot_intent_agent.demo.web_ui import PRESET_CASES

        for preset_name, preset_data in PRESET_CASES.items():
            obs_str = json.dumps(preset_data["observation_json"], ensure_ascii=False)
            engine = "纯规则引擎 (极速)"

            result = pipeline.run(
                instruction=preset_data["command"],
                obs_json_str=obs_str,
                engine=engine,
                api_key="",
            )

            # Must complete without error
            assert result.get("elapsed", 0) >= 0, (
                f"Preset '{preset_name}' produced no elapsed time"
            )
            assert len(result.get("actions", [])) > 0, (
                f"Preset '{preset_name}' produced empty action sequence"
            )
            assert result.get("ir_raw"), (
                f"Preset '{preset_name}' produced empty IR"
            )
