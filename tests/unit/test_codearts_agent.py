import json
import subprocess
import unittest
from types import SimpleNamespace

from modules.strategy_generation.codearts_agent import (
    CodeArtsStrategyClient,
    OUTPUT_BEGIN,
    OUTPUT_END,
    extract_strategy,
    validate_strategy,
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
                "arguments": {"object_name": "obj-001"},
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
                "arguments": {"target": "zone-001"},
            },
            {
                "step_id": "task-codearts-001-release",
                "action": "release",
                "arguments": {},
            },
        ],
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
        strategy["steps"][0]["arguments"] = {"object_name": "invented-object"}
        strategy["steps"][3]["arguments"] = {"target": "invented-zone"}
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


class CodeArtsClientTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
