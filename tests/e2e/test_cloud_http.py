from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo.cloud.auth import Role, issue_session
from demo.cloud.orchestrator import CloudOrchestrator
from demo.cloud.service import CloudService, configure_cloud_service
from demo.cloud.security import MAX_JSON_BYTES
from demo.cloud.store import CloudStore
from demo.server import DemoHandler
from tests.unit.test_cloud_orchestrator import (
    feedback_document,
    perception_document,
    strategy_document,
    task_document,
)


class CloudHttpAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CloudStore(Path(self.temp_dir.name) / "cloud.sqlite3")
        self.sessions = {}
        issued = issue_session(
            "operator-001",
            Role.OPERATOR,
            self.sessions,
            ttl_ms=600_000,
            now_ms=1_000,
            https=False,
        )
        type(self).cookie = f"closed_loop_session={issued.token}"

        def intent_call(_instruction, _perception, run_id):
            return task_document(run_id)

        def strategy_call(_task, perception, run_id):
            return strategy_document(run_id, perception)

        def feedback_call(_payload, run_id):
            return feedback_document(run_id)

        orchestrator = CloudOrchestrator(
            self.store,
            intent_call=intent_call,
            strategy_call=strategy_call,
            feedback_call=feedback_call,
            run_id_factory=lambda: "run-http-001",
        )
        service = CloudService(
            self.store,
            orchestrator,
            relay_token="relay-secret-test-value",
            browser_sessions=self.sessions,
            hls_url="/live/isaac/index.m3u8",
            now_ms=lambda: 1_001,
        )
        configure_cloud_service(service)

    def tearDown(self) -> None:
        configure_cloud_service(None)
        self.temp_dir.cleanup()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    @classmethod
    def request(cls, method: str, path: str, payload=None, *, headers=None):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = Request(cls.base_url + path, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    @classmethod
    def browser_headers(cls):
        return {"Cookie": cls.cookie}

    @classmethod
    def relay_headers(cls):
        return {"Authorization": "Bearer relay-secret-test-value"}

    def test_health_scenarios_and_livestream_are_truthful_and_secret_free(self) -> None:
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(set(health["components"]), {"cloud", "relay", "isaac", "providers", "livestream"})
        self.assertEqual(health["components"]["relay"]["status"], "offline")
        self.assertEqual(health["components"]["livestream"]["status"], "not_probed")

        status, scenarios = self.request("GET", "/api/scenarios")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in scenarios["scenarios"]], ["multi-red-001", "multi-green-001", "multi-red-003"])

        status, livestream = self.request("GET", "/api/livestream")
        self.assertEqual(status, 200)
        self.assertEqual(livestream["url"], "/live/isaac/index.m3u8")
        self.assertFalse(livestream["live"])
        serialized = json.dumps({"health": health, "scenarios": scenarios, "livestream": livestream})
        self.assertNotIn("relay-secret-test-value", serialized)

    def test_run_creation_requires_browser_session_and_supports_snapshot_cursor(self) -> None:
        payload = {"scene_id": "multi-red-001", "instruction": "把红色方块放到桌面区域"}
        status, _ = self.request("POST", "/api/runs", payload)
        self.assertEqual(status, 401)

        status, created = self.request("POST", "/api/runs", payload, headers=self.browser_headers())
        self.assertEqual(status, 202)
        self.assertEqual(created["run"]["state"], "PREPARING_SCENE")

        status, snapshot = self.request("GET", "/api/runs/run-http-001", headers=self.browser_headers())
        self.assertEqual(status, 200)
        self.assertEqual(snapshot["run"]["run_id"], "run-http-001")
        self.assertNotIn("metadata", snapshot["run"])

        status, all_events = self.request("GET", "/api/runs/run-http-001/events?after_sequence=0", headers=self.browser_headers())
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(all_events["events"]), 2)
        first_sequence = all_events["events"][0]["sequence"]
        status, later = self.request("GET", f"/api/runs/run-http-001/events?after_sequence={first_sequence}", headers=self.browser_headers())
        self.assertEqual(status, 200)
        self.assertTrue(all(event["sequence"] > first_sequence for event in later["events"]))

    def test_legacy_run_is_gone_and_validation_is_structured(self) -> None:
        status, legacy = self.request("POST", "/api/run", {})
        self.assertEqual(status, 410)
        self.assertIn("/api/runs", legacy["error"])

        status, invalid = self.request("POST", "/api/runs", {"scene_id": "not-verified", "instruction": "执行"}, headers=self.browser_headers())
        self.assertEqual(status, 400)
        self.assertFalse(invalid["ok"])

    def test_login_issues_operator_cookie_and_blocks_bad_credentials(self) -> None:
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"CLOUD_OPERATOR_PASSWORD": "test-operator-pw"}):
            status, ok = self.request(
                "POST",
                "/api/login",
                {"user": "op-001", "password": "test-operator-pw"},
            )
            self.assertEqual(status, 200)
            self.assertEqual(ok["role"], "operator")
            self.assertEqual(ok["user"], "op-001")

            status, bad = self.request(
                "POST", "/api/login", {"user": "op-001", "password": "wrong"}
            )
            self.assertEqual(status, 401)
            self.assertFalse(bad["ok"])

    def test_session_endpoint_reports_anonymous_and_logout_clears(self) -> None:
        status, anonymous = self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertFalse(anonymous["authenticated"])

        status, logged_out = self.request("POST", "/api/logout", {})
        self.assertEqual(status, 200)
        self.assertTrue(logged_out["ok"])

    def test_relay_auth_claim_lease_events_artifacts_and_completion(self) -> None:
        status, _ = self.request(
            "POST",
            "/api/runs",
            {"scene_id": "multi-red-001", "instruction": "把红色方块放到桌面区域"},
            headers=self.browser_headers(),
        )
        self.assertEqual(status, 202)
        status, _ = self.request("POST", "/api/relay/register", {"relay_id": "relay-a"})
        self.assertEqual(status, 401)
        status, registered = self.request("POST", "/api/relay/register", {"relay_id": "relay-a", "status": {"ssh": "ok"}}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        self.assertEqual(registered["relay"]["relay_id"], "relay-a")

        status, claimed = self.request("POST", "/api/relay/jobs/claim", {"relay_id": "relay-a", "lease_ms": 60_000}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        job = claimed["job"]
        self.assertEqual(job["job_type"], "ISAAC_PREPARE_AND_PERCEIVE")

        status, _ = self.request("POST", f"/api/relay/jobs/{job['job_id']}/lease", {"relay_id": "relay-b", "lease_ms": 60_000}, headers=self.relay_headers())
        self.assertEqual(status, 409)

        event = {"event_id": "evt-http-progress", "type": "PERCEPTION_READY", "payload": {"objects": 2}}
        status, first = self.request("POST", f"/api/relay/jobs/{job['job_id']}/events", {"relay_id": "relay-a", "events": [event]}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        self.assertEqual(first["inserted"], 1)
        status, duplicate = self.request("POST", f"/api/relay/jobs/{job['job_id']}/events", {"relay_id": "relay-a", "events": [event]}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["inserted"], 0)

        status, _ = self.request("POST", f"/api/relay/jobs/{job['job_id']}/artifacts", {"relay_id": "relay-a", "artifact_name": "../secret", "value": {}}, headers=self.relay_headers())
        self.assertEqual(status, 400)
        status, uploaded = self.request("POST", f"/api/relay/jobs/{job['job_id']}/artifacts", {"relay_id": "relay-a", "artifact_name": "perception.json", "value": perception_document("run-http-001")}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        self.assertTrue(uploaded["ok"])

        status, completed = self.request("POST", f"/api/relay/jobs/{job['job_id']}/complete", {"relay_id": "relay-a", "succeeded": True}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        self.assertEqual(completed["run"]["state"], "QUEUED_C")

        status, replayed = self.request("POST", f"/api/relay/jobs/{job['job_id']}/complete", {"relay_id": "relay-a", "succeeded": True}, headers=self.relay_headers())
        self.assertEqual(status, 200)
        self.assertEqual(replayed["run"]["state"], "QUEUED_C")
        self.assertEqual(
            len([item for item in self.store.list_jobs(job["run_id"]) if item["job_type"] == "ISAAC_EXECUTE"]),
            1,
        )

    def test_oversized_json_body_returns_413(self) -> None:
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.putrequest("POST", "/api/relay/heartbeat")
        connection.putheader("Authorization", "Bearer relay-secret-test-value")
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(MAX_JSON_BYTES + 1))
        connection.endheaders()
        connection.send(b"{}")
        http_response = connection.getresponse()
        status = http_response.status
        response = json.loads(http_response.read().decode("utf-8"))
        connection.close()
        self.assertEqual(status, 413)
        self.assertFalse(response["ok"])


if __name__ == "__main__":
    unittest.main()
