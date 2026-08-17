"""Data-driven acceptance tests for the P/A/B/C/D closed loop.

Each JSON file under ``testdata/acceptance/cases`` is an executable
acceptance question: scene + natural-language instruction + expected protocol
and safety outcomes.  The suite deliberately keeps the deterministic B
primitive plan and Mock C backend for CI.  The separate TraceCoder fixture
case covers the D -> C repair retry path already used by the integration
tests.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from integration.adapters import intent, perception, strategy, tracecoder
from integration.adapters.executor import ExecutorAdapter
from integration.contract_validation import assert_contract
from integration.pipeline import run_pipeline
from modules.executor.mock_backend import MockBackend
from tests.helpers.tracecoder_fixtures import (
    DEMO_STRATEGY_V1,
    DEMO_TASK_V1,
    mock_executor_run,
)


ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_ROOT = ROOT / "testdata" / "acceptance"
CASES_ROOT = ACCEPTANCE_ROOT / "cases"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_cases() -> list[dict]:
    cases = [_load_json(path) for path in sorted(CASES_ROOT.glob("*.json"))]
    if not cases:
        raise AssertionError(f"no acceptance cases found in {CASES_ROOT}")
    return cases


def _load_scene(case: dict) -> dict:
    source = case["scene"]
    if source["source"] == "perception_adapter":
        return perception.run(
            {
                "scene_id": source["scene_id"],
                "backend": source.get("backend", "mock"),
            }
        )
    if source["source"] == "file":
        scene = _load_json(ACCEPTANCE_ROOT / source["path"])
        assert_contract(scene, "perception.v1")
        return scene
    raise AssertionError(f"unsupported acceptance scene source: {source}")


class _MockAdapter:
    def __init__(self, fn):
        self._fn = fn

    def run(self, *args, **kwargs):
        return self._fn(*args, **kwargs)

    def health(self):
        return {"status": "ok"}


def _tracecoder_fixture_adapters() -> dict:
    """Build the deterministic D-repair fixture used by the acceptance set."""

    def intent_run(_input_json):
        return DEMO_TASK_V1

    def strategy_run(_task):
        return DEMO_STRATEGY_V1

    return {
        "intent": _MockAdapter(intent_run),
        "strategy": _MockAdapter(strategy_run),
        "executor": _MockAdapter(
            lambda strategy_v1: mock_executor_run(strategy_v1, DEMO_TASK_V1)
        ),
        "tracecoder": _MockAdapter(tracecoder.run),
    }


def _run_case(case: dict) -> tuple[dict, MockBackend | None]:
    if case.get("mode") == "tracecoder_fixture":
        perception_input = {
            "schema_version": "perception.v1",
            "scene_id": "acceptance_tracecoder_fixture",
            "objects": [],
        }
        return (
            run_pipeline(
                perception_input,
                case["instruction"],
                _tracecoder_fixture_adapters(),
            ),
            None,
        )

    scene = _load_scene(case)
    failures = (case.get("executor") or {}).get("failures")
    backend = MockBackend.from_perception(scene, failures=failures)
    adapters = {
        "intent": intent,
        "strategy": strategy,
        "executor": ExecutorAdapter(backend),
        "tracecoder": tracecoder,
    }
    return (
        run_pipeline(
            scene,
            case["instruction"],
            adapters,
            engine=case.get("engine", "rule"),
        ),
        backend,
    )


class ClosedLoopAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _load_cases()

    def test_acceptance_cases(self):
        # B's CodeArts provider is intentionally disabled here: acceptance
        # tests should exercise the adapter and the deterministic public
        # strategy contract, not depend on a local CLI or network service.
        with patch.dict(os.environ, {"CODEARTS_STRATEGY_MODE": "off"}):
            for case in self.cases:
                with self.subTest(case_id=case["case_id"]):
                    output, backend = _run_case(case)
                    self._assert_expected(case, output, backend)

    def _assert_expected(
        self,
        case: dict,
        output: dict,
        backend: MockBackend | None,
    ) -> None:
        expected = case["expected"]
        self.assertEqual(output["status"], expected["pipeline_status"])

        task = output["task"]
        if "task_status" in expected:
            self.assertEqual(task["status"], expected["task_status"], task)
        if "action" in expected:
            self.assertEqual(task["action"], expected["action"])
        if "target_ids" in expected:
            self.assertEqual(task["target_ids"], expected["target_ids"])
        if "destination_id" in expected:
            self.assertEqual(task["destination_id"], expected["destination_id"])
        if expected.get("blocking_reasons_non_empty"):
            self.assertTrue(task.get("blocking_reasons"), task)

        if expected.get("no_execution"):
            self.assertNotIn("execution", output)
            self.assertNotIn("feedback", output)

        if expected.get("strategy_blocked") is not None:
            self.assertEqual(
                output["strategy"]["blocked"],
                expected["strategy_blocked"],
                output.get("strategy"),
            )
        if "strategy_blocking_reasons_contains" in expected:
            self.assertIn(
                expected["strategy_blocking_reasons_contains"],
                output["strategy"].get("blocking_reasons", []),
            )
        if "strategy_actions" in expected:
            self.assertEqual(
                [step["action"] for step in output["strategy"]["steps"]],
                expected["strategy_actions"],
            )
        if "strategy_code" in expected:
            self.assertEqual(output["strategy"].get("code"), expected["strategy_code"])

        if "execution_status" in expected:
            self.assertEqual(output["execution"]["status"], expected["execution_status"])
        if "safety_event_types_contains" in expected:
            safety_types = {
                event.get("type") for event in output["execution"].get("safety_events", [])
            }
            self.assertIn(expected["safety_event_types_contains"], safety_types)

        if "feedback_schema_version" in expected:
            feedback = output.get("feedback")
            self.assertIsNotNone(feedback)
            self.assertEqual(feedback["schema_version"], expected["feedback_schema_version"])
            self.assertEqual(feedback["task_id"], output["task"]["task_id"])
            if "feedback_retryable" in expected:
                self.assertEqual(feedback["retryable"], expected["feedback_retryable"])
            if "diagnosis" in expected:
                diagnosis = json.loads(feedback["diagnosis"])
                for key, value in expected["diagnosis"].items():
                    self.assertEqual(diagnosis[key], value, diagnosis)

        if "retry_count" in expected:
            self.assertEqual(output["retry_count"], expected["retry_count"])
        if "attempt_count" in expected:
            self.assertEqual(len(output["attempts"]), expected["attempt_count"])
        if "stop_reason" in expected:
            self.assertEqual(output["stop_reason"], expected["stop_reason"])

        if expected.get("final_strategy_grasp_has_recovery"):
            grasp = next(
                step for step in output["strategy"]["steps"] if step["action"] == "grasp"
            )
            self.assertEqual(grasp["on_failure"]["on_exhausted"], "stop")

        placement = expected.get("final_placement")
        if placement:
            self.assertIsNotNone(backend)
            snapshot = backend.snapshot()
            self.assertEqual(
                snapshot["objects"][placement["object_id"]]["pose"],
                snapshot["objects"][placement["destination_id"]]["pose"],
            )


if __name__ == "__main__":
    unittest.main()
