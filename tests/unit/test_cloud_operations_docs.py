"""Contract tests for the operations manual (Task 14).

The manual must document the exact endpoints, environment variables,
candidate/production ports, same-world evidence IDs, the manual
Livestream gate, relay/worker startup commands and rollback, and must
never embed secret-looking examples.
"""

from __future__ import annotations

import unittest
from pathlib import Path

MANUAL = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "华为云真实闭环部署与Livestream运维手册.md"
)
README = Path(__file__).resolve().parents[2] / "README.md"

SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----",
    r"ghp_[A-Za-z0-9]{20,}",
)


class CloudOperationsDocsTests(unittest.TestCase):
    def test_manual_covers_endpoints(self) -> None:
        text = MANUAL.read_text(encoding="utf-8")
        for endpoint in (
            "/api/runs",
            "/api/runs/{run_id}",
            "after_sequence",
            "/api/livestream",
            "/api/health",
            "/api/login",
        ):
            self.assertIn(endpoint, text, endpoint)

    def test_manual_covers_env_and_ports(self) -> None:
        text = MANUAL.read_text(encoding="utf-8")
        for token in (
            "CLOUD_OPERATOR_PASSWORD",
            "CLOUD_RELAY_TOKEN",
            "CLOUD_BIND_PORT",
            "8876",
            "8765",
            "8888",
            "1935",
        ):
            self.assertIn(token, text, token)

    def test_manual_covers_same_world_ids_and_evidence(self) -> None:
        text = MANUAL.read_text(encoding="utf-8")
        for token in (
            "kit_instance_id",
            "world_id",
            "final_pose.json",
            "execution.json",
            "perception.json",
        ):
            self.assertIn(token, text, token)

    def test_manual_covers_livestream_gate_and_rollback(self) -> None:
        text = MANUAL.read_text(encoding="utf-8")
        self.assertIn("现在需要开启 Livestream", text)
        for token in ("Rollback", "previous", "回滚"):
            self.assertIn(token, text, token)

    def test_manual_covers_relay_and_worker_startup(self) -> None:
        text = MANUAL.read_text(encoding="utf-8")
        for token in (
            "cloud_relay_agent",
            "run_live_isaac_worker",
            "start_cloud_relay",
            "school",
            "5122",
        ):
            self.assertIn(token, text, token)

    def test_manual_covers_https_status(self) -> None:
        text = MANUAL.read_text(encoding="utf-8")
        self.assertIn("WAITING_FOR_DOMAIN", text)
        self.assertIn("HTTPS", text)

    def test_no_secret_looking_examples(self) -> None:
        for path in (MANUAL, README):
            text = path.read_text(encoding="utf-8")
            for pattern in SECRET_PATTERNS:
                self.assertNotRegex(text, pattern, f"{path.name}: {pattern}")

    def test_readme_marks_sync_mock_demo_developer_only(self) -> None:
        text = README.read_text(encoding="utf-8")
        self.assertIn("开发", text)
        self.assertIn("demo", text)


if __name__ == "__main__":
    unittest.main()
