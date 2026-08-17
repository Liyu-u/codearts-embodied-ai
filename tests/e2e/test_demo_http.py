"""HTTP and static-frontend smoke acceptance for the local Demo server."""

from __future__ import annotations

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from demo.scenarios import list_scenarios
from demo.server import DemoHandler


class DemoHttpAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

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
        self.assertEqual(health["status"], "ok")
        required_modules = {"perception", "intent", "strategy", "tracecoder"}
        self.assertTrue(required_modules.issubset(set(health["modules"])))
        self.assertTrue(all(item.get("status") == "ok" for item in health["modules"].values()))

        index_status, index_type, index_body = self.get("/")
        self.assertEqual(index_status, 200)
        self.assertEqual(index_type, "text/html")
        self.assertIn(b"A/B/C/D", index_body)

        js_status, js_type, js_body = self.get("/app.js")
        self.assertEqual(js_status, 200)
        self.assertIn(js_type, {"text/javascript", "application/javascript"})
        self.assertIn(b"/api/run", js_body)

    def test_scenario_catalog_and_http_runs_match_actual_pipeline(self):
        status, _, body = self.get("/api/scenarios")
        catalog = json.loads(body.decode("utf-8"))["scenarios"]
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in catalog], [item["id"] for item in list_scenarios()])

        for scenario in catalog:
            with self.subTest(scene_id=scenario["id"]):
                response_status, response = self.post_json(
                    "/api/run",
                    {
                        "scene_id": scenario["id"],
                        "instruction": scenario["instruction"],
                        "engine": "rule",
                        "request_id": f"http-{scenario['id']}",
                    },
                )
                self.assertEqual(response_status, 200)
                self.assertTrue(response["ok"])
                self.assertEqual(response["request_id"], f"http-{scenario['id']}")
                self.assertEqual(response["scenario"]["id"], scenario["id"])
                self.assertEqual(response["result"]["status"], scenario["expected"])
                self.assertTrue(response["acceptance"]["passed"])
                self.assertEqual(response["acceptance"]["expected_status"], scenario["expected"])
                self.assertEqual(response["acceptance"]["actual_status"], scenario["expected"])

    def test_http_rejects_invalid_requests_and_path_traversal(self):
        with self.assertRaises(HTTPError) as empty_error:
            self.post_json("/api/run", {"scene_id": "single_red_cube", "instruction": ""})
        self.assertEqual(empty_error.exception.code, 400)

        with self.assertRaises(HTTPError) as unknown_error:
            self.post_json(
                "/api/run",
                {"scene_id": "not_a_real_scene", "instruction": "执行任务"},
            )
        self.assertEqual(unknown_error.exception.code, 400)

        with self.assertRaises(HTTPError) as missing_error:
            self.get("/not-found")
        self.assertEqual(missing_error.exception.code, 404)

        with self.assertRaises(HTTPError) as traversal_error:
            self.get("/../server.py")
        self.assertIn(traversal_error.exception.code, {403, 404})


if __name__ == "__main__":
    unittest.main()
