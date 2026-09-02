import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from modules.strategy_generation.codearts_agent import (
    CodeArtsStrategyClient,
    OUTPUT_BEGIN,
    OUTPUT_END,
    REVIEW_BEGIN,
    REVIEW_END,
    extract_strategy,
    extract_review,
    extract_provider_error,
    validate_review,
    validate_strategy,
    _build_prompt,
    _build_review_prompt,
    classify_codearts_error,
)


TASK = {
    "schema_version": "task.v1",
    "task_id": "task-codearts-001",
    "action": "pick_and_place",
    "target_ids": ["obj-001"],
    "destination_id": "zone-001",
    "status": "READY",
}


def valid_strategy() -> dict:
    detect_id = "task-codearts-001-detect"
    return {
        "schema_version": "strategy.v1",
        "task_id": "task-codearts-001",
        "steps": [
            {
                "step_id": detect_id,
                "action": "detect_object",
                "arguments": {"object_id": "obj-001"},
            },
            {
                "step_id": "task-codearts-001-approach",
                "action": "move_to_object",
                "arguments": {"object_id": f"${detect_id}.object_id"},
            },
            {
                "step_id": "task-codearts-001-grasp",
                "action": "grasp",
                "arguments": {"object_id": f"${detect_id}.object_id"},
            },
            {
                "step_id": "task-codearts-001-target",
                "action": "move_to_target",
                "arguments": {"destination_id": "zone-001"},
            },
            {
                "step_id": "task-codearts-001-release",
                "action": "release",
                "arguments": {},
            },
        ],
        "code": None,
    }



def task_for_action(action: str) -> dict:
    return {
        "schema_version": "task.v1",
        "task_id": f"task-codearts-{action}",
        "action": action,
        "target_ids": ["obj-001"],
        "destination_id": None if action in {"pick", "grasp"} else "zone-001",
        "status": "READY",
    }


def strategy_for_action(action: str) -> dict:
    task = task_for_action(action)
    task_id = task["task_id"]
    detect_id = f"{task_id}-detect"
    object_reference = f"${detect_id}.object_id"
    steps = [
        {
            "step_id": detect_id,
            "action": "detect_object",
            "arguments": {"object_id": "obj-001"},
        },
        {
            "step_id": f"{task_id}-approach",
            "action": "move_to_object",
            "arguments": {"object_id": object_reference},
        },
        {
            "step_id": f"{task_id}-grasp",
            "action": "grasp",
            "arguments": {"object_id": object_reference},
        },
    ]
    if action not in {"pick", "grasp"}:
        move_arguments = {"destination_id": "zone-001"}
        if action == "stack":
            move_arguments["placement_mode"] = "stack_on"
        steps.extend(
            [
                {
                    "step_id": f"{task_id}-target",
                    "action": "move_to_target",
                    "arguments": move_arguments,
                },
                {
                    "step_id": f"{task_id}-release",
                    "action": "release",
                    "arguments": {},
                },
            ]
        )
    return {
        "schema_version": "strategy.v1",
        "task_id": task_id,
        "steps": steps,
        "code": None,
    }

class CodeArtsOutputTests(unittest.TestCase):
    def test_extracts_strategy_from_json_event_text(self):
        content = (
            f"{OUTPUT_BEGIN}\n"
            + json.dumps(valid_strategy(), ensure_ascii=False)
            + f"\n{OUTPUT_END}"
        )
        stdout = json.dumps({"type": "message", "content": content}, ensure_ascii=False)

        self.assertEqual(extract_strategy(stdout), valid_strategy())

    def test_rejects_unknown_action_and_generated_code(self):
        strategy = valid_strategy()
        strategy["steps"][1]["action"] = "run_shell"
        strategy["code"] = "import os"

        errors = validate_strategy(strategy, TASK)

        self.assertTrue(any("not allowed" in error for error in errors), errors)
        self.assertIn("code must be null", errors)

    def test_ids_in_unrelated_metadata_cannot_bypass_binding_checks(self):
        strategy = valid_strategy()
        strategy["steps"][0]["arguments"] = {"object_id": "invented-object"}
        strategy["steps"][3]["arguments"] = {"destination_id": "invented-zone"}
        strategy["notes"] = "obj-001 zone-001"

        errors = validate_strategy(strategy, TASK)

        self.assertIn("strategy lost the stable target_id", errors)
        self.assertIn("strategy lost the stable destination_id", errors)

    def test_rejects_forward_references_and_unsafe_action_order(self):
        strategy = valid_strategy()
        strategy["steps"][1]["arguments"] = {"object_id": "$future.object_id"}
        strategy["steps"][2], strategy["steps"][3] = (
            strategy["steps"][3],
            strategy["steps"][2],
        )

        errors = validate_strategy(strategy, TASK)

        self.assertTrue(any("forward reference" in error for error in errors), errors)
        self.assertIn("pick_and_place actions are missing or out of safe order", errors)

    def test_extracts_and_validates_review_contract(self):
        review = {"status": "PASS", "issues": [], "risk_level": "LOW"}
        content = f"{REVIEW_BEGIN}\n{json.dumps(review)}\n{REVIEW_END}"
        stdout = json.dumps({"type": "message", "content": content})
        self.assertEqual(extract_review(stdout), review)
        self.assertEqual(validate_review(review), [])
        self.assertTrue(validate_review({"status": "PASS", "issues": ["bad"], "risk_level": "LOW"}))

    def test_extracts_provider_error_from_fenced_json_in_event_text(self):
        stdout = json.dumps(
            {
                "type": "text",
                "part": {
                    "text": (
                        "```json\n"
                        '{"status":"error","code":"MISSING_INPUT",'
                        '"required":["candidate_strategy"]}\n'
                        "```"
                    )
                },
            }
        )
        self.assertEqual(
            extract_provider_error(stdout),
            "MISSING_INPUT required=candidate_strategy",
        )


class CodeArtsClientTests(unittest.TestCase):
    def test_open_actions_use_action_specific_prompt_and_contract(self):
        actions = ["pick", "grasp", "pick_and_place", "place", "transfer", "fetch", "stack"]
        for action in actions:
            with self.subTest(action=action):
                task = task_for_action(action)
                prompt = _build_prompt(task)
                self.assertIn(f"任务动作={action}", prompt)
                expected = [step["action"] for step in strategy_for_action(action)["steps"]]
                self.assertIn(str(expected), prompt)
                result = CodeArtsStrategyClient(
                    executable="codearts",
                    runner=lambda command, **kwargs: SimpleNamespace(
                        returncode=0,
                        stdout=f"{OUTPUT_BEGIN}\n{json.dumps(strategy_for_action(action))}\n{OUTPUT_END}",
                        stderr="",
                    ),
                    which=lambda _: "C:\\Tools\\codearts.exe",
                ).generate(task)
                self.assertTrue(result["success"], result)
    def test_binds_only_task_id_mismatch_before_accepting(self):
        calls = []
        mismatched = valid_strategy()
        mismatched["task_id"] = "task-from-example"

        def runner(command, **kwargs):
            calls.append(command)
            content = (
                f"{OUTPUT_BEGIN}\n"
                + json.dumps(mismatched, ensure_ascii=False)
                + f"\n{OUTPUT_END}"
            )
            return SimpleNamespace(returncode=0, stdout=content, stderr="")

        client = CodeArtsStrategyClient(
            executable="codearts",
            runner=runner,
            which=lambda _: "C:\\Tools\\codearts.exe",
        )

        result = client.generate(TASK)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["strategy"]["task_id"], TASK["task_id"])
        self.assertEqual(len(calls), 1)
        self.assertTrue(result["trace"]["task_id_bound_locally"])

    def test_binds_id_before_retrying_a_combined_empty_strategy(self):
        calls = []
        empty = {
            "schema_version": "strategy.v1",
            "task_id": "task-from-example",
            "steps": [],
            "code": None,
        }
        valid = valid_strategy()

        def runner(command, **kwargs):
            calls.append(command)
            strategy = empty if len(calls) == 1 else valid
            content = f"{OUTPUT_BEGIN}\n{json.dumps(strategy)}\n{OUTPUT_END}"
            return SimpleNamespace(returncode=0, stdout=content, stderr="")

        with patch.dict(os.environ, {"CODEARTS_STRATEGY_RETRY_BACKOFF_S": "0"}):
            client = CodeArtsStrategyClient(
                executable="codearts",
                runner=runner,
                which=lambda _: "C:\\Tools\\codearts.exe",
            )
            result = client.generate(TASK)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["strategy"]["task_id"], TASK["task_id"])
        self.assertTrue(result["trace"]["task_id_bound_locally"])
        self.assertEqual(result["trace"]["validation_retry_count"], 1)

    def test_two_empty_strategies_can_recover_on_third_attempt(self):
        calls = []
        empty = {
            "schema_version": "strategy.v1",
            "task_id": "task-from-example",
            "steps": [],
            "code": None,
        }
        valid = valid_strategy()

        def runner(command, **kwargs):
            calls.append(command)
            strategy = empty if len(calls) <= 2 else valid
            content = f"{OUTPUT_BEGIN}\n{json.dumps(strategy)}\n{OUTPUT_END}"
            return SimpleNamespace(
                returncode=0,
                stdout=content,
                stderr="",
            )

        with patch.dict(
            os.environ,
            {
                "CODEARTS_STRATEGY_MAX_RETRIES": "2",
                "CODEARTS_STRATEGY_RETRY_BACKOFF_S": "0",
            },
        ):
            client = CodeArtsStrategyClient(
                executable="codearts",
                runner=runner,
                which=lambda _: r"C:\Tools\codearts.exe",
            )
            result = client.generate(TASK)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(calls), 3)

        titles = [
            command[command.index("--title") + 1]
            for command in calls
        ]

        self.assertEqual(len(set(titles)), 3)

        self.assertEqual(
            result["trace"]["validation_retry_count"],
            2,
        )

    def test_persistent_empty_strategy_is_rejected_and_never_sent_forward(self):
        empty = {
            "schema_version": "strategy.v1",
            "task_id": "task-from-example",
            "steps": [],
            "code": None,
        }

        def runner(command, **kwargs):
            content = f"{OUTPUT_BEGIN}\n{json.dumps(empty)}\n{OUTPUT_END}"
            return SimpleNamespace(returncode=0, stdout=content, stderr="")

        with patch.dict(os.environ, {"CODEARTS_STRATEGY_RETRY_BACKOFF_S": "0"}):
            client = CodeArtsStrategyClient(
                executable="codearts",
                runner=runner,
                which=lambda _: "C:\\Tools\\codearts.exe",
            )
            result = client.generate(TASK)

        self.assertFalse(result["success"])
        self.assertIsNone(result["strategy"])
        self.assertEqual(
            result["error"],
            "CODEARTS_STRATEGY_REJECTED:steps must be a non-empty array",
        )
        self.assertEqual(result["trace"]["validation_retry_count"], 1)

    def test_invokes_official_cli_and_returns_provenance(self):
        calls = []
        content = (
            f"{OUTPUT_BEGIN}\n"
            + json.dumps(valid_strategy(), ensure_ascii=False)
            + f"\n{OUTPUT_END}"
        )

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"content": content}, ensure_ascii=False),
                stderr="",
            )

        client = CodeArtsStrategyClient(
            executable="codearts",
            agent="robot-strategy-agent",
            model="huaweicloud-maas/test-model",
            runner=runner,
            which=lambda _: "C:\\Tools\\codearts.exe",
        )

        with patch.dict(os.environ, {"CODEARTS_CLI_PURE": "0"}):
            result = client.generate(TASK)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["strategy"]["mode"], "codearts_agent")
        self.assertIsNone(result["strategy"]["code"])
        command = calls[0][0]
        self.assertEqual(command[:4], ["C:\\Tools\\codearts.exe", "run", "--format", "json"])
        self.assertIn("--agent", command)
        self.assertIn("--model", command)
        self.assertIn("STRATEGY_JSON_BEGIN", command[-1])
        self.assertFalse(calls[0][1].get("shell", False))

    def test_reports_missing_cli_without_faking_agent_participation(self):
        client = CodeArtsStrategyClient(which=lambda _: None)

        result = client.generate(TASK)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "CODEARTS_CLI_NOT_FOUND")

    def test_timeout_is_reported(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        client = CodeArtsStrategyClient(runner=runner, which=lambda _: "codearts")

        result = client.generate(TASK)

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "CODEARTS_CLI_TIMEOUT")

    def test_provider_balance_failure_is_classified_without_changing_error_text(self):
        self.assertEqual(
            classify_codearts_error("CODEARTS_PROVIDER_ERROR:Insufficient Balance"),
            "provider_balance",
        )

    def test_required_failure_matrix_keeps_structured_provenance(self):
        cases = {
            "illegal_json": "not-json",
            "unknown_action": f"{OUTPUT_BEGIN}\n" + json.dumps({
                **valid_strategy(),
                "steps": [{**valid_strategy()["steps"][0], "action": "hack_robot"}],
            }) + f"\n{OUTPUT_END}",
            "wrong_entity_id": f"{OUTPUT_BEGIN}\n" + json.dumps({
                **valid_strategy(),
                "steps": [{
                    **valid_strategy()["steps"][0],
                    "arguments": {"object_id": "unknown-object"},
                }],
            }) + f"\n{OUTPUT_END}",
        }
        for name, stdout in cases.items():
            with self.subTest(name=name):
                client = CodeArtsStrategyClient(
                    runner=lambda command, **kwargs: SimpleNamespace(
                        returncode=0, stdout=stdout, stderr=""
                    ),
                    which=lambda _: "codearts",
                )
                result = client.generate(TASK)
                self.assertFalse(result["success"])
                trace = result["trace"]
                self.assertTrue(trace["request_id"])
                self.assertIn("latency_ms", trace)
                self.assertFalse(trace["validation"]["passed"])

    def test_retries_transient_cli_failure_and_normalizes_grasp_recovery(self):
        calls = []
        content = (
            f"{OUTPUT_BEGIN}\n"
            + json.dumps(valid_strategy(), ensure_ascii=False)
            + f"\n{OUTPUT_END}"
        )

        def runner(command, **kwargs):
            calls.append(command)
            if len(calls) == 1:
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="temporarily unavailable",
                )
            return SimpleNamespace(returncode=0, stdout=content, stderr="")

        with patch.dict(
            os.environ,
            {
                "CODEARTS_STRATEGY_MAX_RETRIES": "1",
                "CODEARTS_STRATEGY_RETRY_BACKOFF_S": "0",
            },
        ):
            client = CodeArtsStrategyClient(
                executable="codearts",
                runner=runner,
                which=lambda _: r"C:\Tools\codearts.exe",
            )
            result = client.generate(TASK)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["trace"]["attempt_count"], 2)
        self.assertEqual(result["trace"]["retry_count"], 1)
        grasp = next(
            step for step in result["strategy"]["steps"]
            if step["action"] == "grasp"
        )
        self.assertEqual(grasp["on_failure"]["max_attempts"], 1)
        self.assertEqual(grasp["on_failure"]["on_exhausted"], "stop")
        self.assertTrue(result["trace"]["recovery_normalized"])

    def test_review_uses_separate_critic_prompt_and_requires_pass(self):
        calls = []
        strategy = valid_strategy()
        strategy["steps"][2]["on_failure"] = {
            "max_attempts": 1,
            "steps": [
                {
                    "step_id": "retry-grasp",
                    "action": "grasp",
                    "arguments": {"object_id": "$task-codearts-001-detect.object_id"},
                }
            ],
            "on_exhausted": "stop",
        }

        def runner(command, **kwargs):
            calls.append(command)
            review = {"status": "PASS", "issues": [], "risk_level": "LOW"}
            content = f"{REVIEW_BEGIN}\n{json.dumps(review)}\n{REVIEW_END}"
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"content": content}),
                stderr="",
            )

        client = CodeArtsStrategyClient(
            executable="codearts",
            runner=runner,
            which=lambda _: "C:\\Tools\\codearts.exe",
        )
        result = client.review(TASK, strategy)

        self.assertTrue(result["success"], result)
        self.assertEqual(result["review"]["status"], "PASS")
        self.assertEqual(result["trace"]["role"], "critic")
        self.assertIn('"candidate_strategy"', calls[0][-1])
        self.assertIn("STRATEGY_REVIEW_BEGIN", calls[0][-1])
        self.assertNotIn('"provenance"', calls[0][-1])
        self.assertNotIn('"on_failure"', calls[0][-1])
        self.assertNotIn("\n", calls[0][-1])

    def test_review_surfaces_structured_provider_error(self):
        def runner(command, **kwargs):
            error = {
                "type": "text",
                "part": {
                    "text": (
                        "```json\n"
                        '{"status":"error","code":"MISSING_INPUT",'
                        '"required":["candidate_strategy"]}\n'
                        "```"
                    )
                },
            }
            return SimpleNamespace(returncode=0, stdout=json.dumps(error), stderr="")

        client = CodeArtsStrategyClient(
            executable="codearts",
            runner=runner,
            which=lambda _: "C:\\Tools\\codearts.exe",
        )
        result = client.review(TASK, valid_strategy())

        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"],
            "CODEARTS_PROVIDER_ERROR:MISSING_INPUT required=candidate_strategy",
        )

    def test_review_preserves_block_reason(self):
        def runner(command, **kwargs):
            review = {
                "status": "BLOCK",
                "issues": ["recovery branch is not supported"],
                "risk_level": "HIGH",
            }
            content = f"{REVIEW_BEGIN}\n{json.dumps(review)}\n{REVIEW_END}"
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"content": content}),
                stderr="",
            )

        client = CodeArtsStrategyClient(
            executable="codearts",
            runner=runner,
            which=lambda _: "C:\\Tools\\codearts.exe",
        )
        result = client.review(TASK, valid_strategy())

        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"],
            "CODEARTS_REVIEW_REJECTED:BLOCK:recovery branch is not supported",
        )


if __name__ == "__main__":
    unittest.main()
