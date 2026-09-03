"""HTTP and static-frontend smoke acceptance for the local Demo server."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo.cloud.orchestrator import CloudOrchestrator
from demo.cloud.service import CloudService, configure_cloud_service
from demo.cloud.store import CloudStore
from demo.server import DemoHandler


class DemoHttpAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory()
        store = CloudStore(Path(cls.temp_dir.name) / "cloud.sqlite3")

        def unused(*_args, **_kwargs):
            raise AssertionError("public smoke test must not invoke providers")

        orchestrator = CloudOrchestrator(
            store,
            intent_call=unused,
            strategy_call=unused,
            feedback_call=unused,
        )
        configure_cloud_service(
            CloudService(
                store,
                orchestrator,
                relay_token="demo-http-relay-token",
                hls_url="/live/isaac/index.m3u8",
            )
        )
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        configure_cloud_service(None)
        cls.temp_dir.cleanup()

    @classmethod
    def get(cls, path: str):
        with urlopen(cls.base_url + path, timeout=5) as response:
            return response.status, response.headers.get_content_type(), response.read()

    @classmethod
    def post_json(cls, path: str, payload: dict):
        request = Request(
            cls.base_url + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_health_and_static_frontend_are_available(self):
        status, content_type, body = self.get("/api/health")
        health = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "application/json")
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(
            set(health["components"]),
            {"cloud", "relay", "isaac", "providers", "livestream"},
        )

        index_status, index_type, index_body = self.get("/")
        self.assertEqual(index_status, 200)
        self.assertEqual(index_type, "text/html")
        self.assertIn(b"A/B/C/D", index_body)

        js_status, js_type, js_body = self.get("/app.js")
        self.assertEqual(js_status, 200)
        self.assertIn(js_type, {"text/javascript", "application/javascript"})
        self.assertIn(b"apiBase", js_body)
        self.assertIn(b"/api/livestream", js_body)

    def test_frontend_cockpit_contract_is_present(self):
        _, _, body = self.get("/")
        html = body.decode("utf-8")
        for value in (
            'data-view="home"',
            'data-view="config"',
            'data-view="records"',
            'data-view="user"',
            'id="homeView"',
            'id="configView"',
            'id="recordsView"',
            'id="userView"',
            'id="stageAState"',
            'id="stageBState"',
            'id="stageCState"',
            'id="stageDState"',
            'id="modelConfigForm"',
            'id="recordsList"',
            'id="userName"',
            'id="userRole"',
        ):
            self.assertIn(value, html)

        _, _, js_body = self.get("/app.js")
        javascript = js_body.decode("utf-8")
        for value in (
            'data-config-secret',
            'data-config-field',
            'data-credential-status',
            'id + "." + key',
            '["A", "B", "D"]',
            'credential === "ak" ? 0 : 1',
        ):
            self.assertIn(value, javascript)

    def test_home_hides_redundant_page_titles(self):
        _, _, body = self.get("/")
        html = body.decode("utf-8")
        self.assertNotIn("<h1>具身智能执行平台</h1>", html)
        self.assertNotIn('id="homeTitle"', html)

    def test_scenario_catalog_exposes_only_verified_isaac_presets(self):
        status, _, body = self.get("/api/scenarios")
        catalog = json.loads(body.decode("utf-8"))["scenarios"]
        self.assertEqual(status, 200)
        self.assertEqual(
            [item["id"] for item in catalog],
            ["multi-red-001", "multi-green-001", "multi-red-003"],
        )
        self.assertTrue(all(item["backend"] == "isaac" for item in catalog))

    def test_http_rejects_invalid_requests_and_path_traversal(self):
        with self.assertRaises(HTTPError) as legacy_error:
            self.post_json("/api/run", {"scene_id": "single_red_cube", "instruction": ""})
        self.assertEqual(legacy_error.exception.code, 410)

        with self.assertRaises(HTTPError) as anonymous_error:
            self.post_json(
                "/api/runs",
                {"scene_id": "multi-red-001", "instruction": "执行任务"},
            )
        self.assertEqual(anonymous_error.exception.code, 401)

        with self.assertRaises(HTTPError) as missing_error:
            self.get("/not-found")
        self.assertEqual(missing_error.exception.code, 404)

        with self.assertRaises(HTTPError) as traversal_error:
            self.get("/../server.py")
        self.assertIn(traversal_error.exception.code, {403, 404})


if __name__ == "__main__":
    unittest.main()
