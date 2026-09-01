"""Source-level tests for the Huawei candidate deployment assets (Task 13).

The candidate binds 127.0.0.1:8876; production 8765 and the MediaMTX
Livestream (/live/) must remain untouched. The deploy script must offer
Validate/DeployCandidate/CheckCandidate/Cutover/Rollback and must never
issue commands that stop, restart or rewrite the Livestream.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "huawei"
SERVICE = DEPLOY / "closed-loop-demo.service"
NGINX = DEPLOY / "nginx-closed-loop.conf"
ENV_EXAMPLE = DEPLOY / "closed-loop.env.example"
DEPLOY_PS1 = Path(__file__).resolve().parents[2] / "tools" / "deploy_huawei_cloud.ps1"

FORBIDDEN_SCRIPT_TOKENS = (
    "mediamtx",
    "obs.exe",
    "Start-Process obs",
    "Stop-Process obs",
    "systemctl stop",
    "systemctl restart",
    "rm /live/",
    "rm -r /live/",
    "rm -rf /live/",
)


class HuaweiDeploymentAssetTests(unittest.TestCase):
    def test_service_unit_non_root_with_environment_file_and_restart(self) -> None:
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("EnvironmentFile=", text)
        self.assertIn("Restart=on-failure", text)
        self.assertIn("NoNewPrivileges=true", text)
        self.assertNotRegex(text, r"^User\s*=\s*root\s*$", re.MULTILINE)
        self.assertNotRegex(text, r"^Group\s*=\s*root\s*$", re.MULTILINE)

    def test_service_binds_candidate_port_8876(self) -> None:
        text = SERVICE.read_text(encoding="utf-8")
        self.assertIn("8876", text)

    def test_env_example_uses_placeholders_and_candidate_port(self) -> None:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("CLOUD_BIND_PORT=8876", text)
        self.assertIn("CLOUD_RELAY_TOKEN=change-me-before-production", text)
        self.assertIn("CLOUD_OPERATOR_PASSWORD=", text)
        self.assertNotRegex(
            text, r"(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----)"
        )

    def test_nginx_proxies_api_to_candidate_and_preserves_live(self) -> None:
        text = NGINX.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1:8876", text)
        self.assertIn("/live/", text)
        self.assertIn("127.0.0.1:8888", text)
        for token in ("systemctl", "mediamtx"):
            self.assertNotIn(token, text)

    def test_deploy_script_exposes_all_modes(self) -> None:
        text = DEPLOY_PS1.read_text(encoding="utf-8")
        for mode in (
            "Validate",
            "DeployCandidate",
            "CheckCandidate",
            "Cutover",
            "Rollback",
        ):
            self.assertIn(mode, text)

    def test_deploy_script_never_touches_livestream_lifecycle(self) -> None:
        lowered = DEPLOY_PS1.read_text(encoding="utf-8").lower()
        for token in FORBIDDEN_SCRIPT_TOKENS:
            self.assertNotIn(token, lowered, f"forbidden livestream token: {token}")

    def test_deploy_script_runs_nginx_test_and_probes_hls(self) -> None:
        text = DEPLOY_PS1.read_text(encoding="utf-8")
        self.assertIn("nginx -t", text)
        self.assertIn("curl", text)
        self.assertIn("/live/isaac/index.m3u8", text)

    def test_deploy_script_uses_versioned_release_links(self) -> None:
        text = DEPLOY_PS1.read_text(encoding="utf-8")
        self.assertIn("releases", text)
        self.assertIn("current", text)
        self.assertIn("previous", text)


if __name__ == "__main__":
    unittest.main()
