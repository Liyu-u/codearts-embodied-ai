from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import RemoteDisconnected
from unittest.mock import patch

from tools.relay.client import RelayClient, RelayHTTPError


class _RelayTestHandler(BaseHTTPRequestHandler):
    responses: list[int | str] = []
    requests: list[dict] = []
    delay_s = 0.0

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "idempotency_key": self.headers.get("Idempotency-Key"),
                "body": json.loads(body.decode("utf-8")),
            }
        )
        if type(self).delay_s:
            time.sleep(type(self).delay_s)
        outcome = type(self).responses.pop(0) if type(self).responses else 200
        if outcome == "reset":
            self.close_connection = True
            return
        payload = {"ok": int(outcome) < 400, "job": None, "message": "响应"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(outcome))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, _format, *_args):
        return


class RelayClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _RelayTestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        _RelayTestHandler.responses = []
        _RelayTestHandler.requests = []
        _RelayTestHandler.delay_s = 0.0
        self.client = RelayClient(
            self.base_url,
            token="relay-secret-value",
            relay_id="windows-relay-01",
            timeout_s=1,
        )

    def test_register_sends_bearer_relay_id_and_utf8_json(self) -> None:
        response = self.client.register({"message": "校园服务器可连接"})

        self.assertTrue(response["ok"])
        request = _RelayTestHandler.requests[0]
        self.assertEqual(request["path"], "/api/relay/register")
        self.assertEqual(request["authorization"], "Bearer relay-secret-value")
        self.assertEqual(request["body"]["relay_id"], "windows-relay-01")
        self.assertEqual(request["body"]["status"]["message"], "校园服务器可连接")

    def test_retryable_statuses_reuse_one_idempotency_key_and_stop_after_two_retries(self) -> None:
        _RelayTestHandler.responses = [503, 502, 200]

        response = self.client.heartbeat({"isaac": "ready"})

        self.assertTrue(response["ok"])
        self.assertEqual(len(_RelayTestHandler.requests), 3)
        keys = {request["idempotency_key"] for request in _RelayTestHandler.requests}
        self.assertEqual(len(keys), 1)
        self.assertNotIn(None, keys)

    def test_connection_reset_is_retryable(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true,"job":null}'

        with patch(
            "tools.relay.client.urlopen",
            side_effect=[RemoteDisconnected("reset"), Response()],
        ) as mocked_open:
            response = self.client.claim(lease_ms=20_000)

        self.assertTrue(response["ok"])
        self.assertEqual(mocked_open.call_count, 2)

    def test_401_and_409_are_not_retried_or_leak_token(self) -> None:
        for status in (401, 409):
            with self.subTest(status=status):
                _RelayTestHandler.requests = []
                _RelayTestHandler.responses = [status, 200]
                with self.assertRaises(RelayHTTPError) as raised:
                    self.client.renew("job-001", lease_ms=20_000)
                self.assertEqual(raised.exception.status, status)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(len(_RelayTestHandler.requests), 1)
                self.assertNotIn("relay-secret-value", str(raised.exception))

    def test_timeout_is_bounded_and_reported_as_retryable_without_secret(self) -> None:
        client = RelayClient(
            self.base_url,
            token="relay-secret-value",
            relay_id="windows-relay-01",
            timeout_s=0.02,
        )

        with patch(
            "tools.relay.client.urlopen",
            side_effect=[TimeoutError("slow"), TimeoutError("slow"), TimeoutError("slow")],
        ) as mocked_open:
            with self.assertRaises(RelayHTTPError) as raised:
                client.heartbeat({"isaac": "slow"})

        self.assertEqual(raised.exception.status, 0)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(mocked_open.call_count, 3)
        self.assertNotIn("relay-secret-value", str(raised.exception))

    def test_typed_methods_use_expected_routes(self) -> None:
        self.client.post_events("job-001", [{"event_id": "evt-1", "type": "ACTION"}])
        self.client.upload_artifact("job-001", "execution.json", {"schema_version": "execution.v1"})
        self.client.complete("job-001", succeeded=False, error="ssh lost")

        self.assertEqual(
            [request["path"] for request in _RelayTestHandler.requests],
            [
                "/api/relay/jobs/job-001/events",
                "/api/relay/jobs/job-001/artifacts",
                "/api/relay/jobs/job-001/complete",
            ],
        )


if __name__ == "__main__":
    unittest.main()
