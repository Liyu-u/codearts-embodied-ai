from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from http.client import RemoteDisconnected
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class RelayHTTPError(RuntimeError):
    status: int
    retryable: bool
    message: str

    def __str__(self) -> str:
        return self.message


class RelayClient:
    _RETRYABLE_STATUSES = frozenset({502, 503})
    _MAX_RETRIES = 2

    def __init__(
        self,
        base_url: str,
        token: str,
        relay_id: str,
        timeout_s: float = 10,
    ) -> None:
        normalized = base_url.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base_url must be an http(s) URL")
        if not token or not relay_id:
            raise ValueError("token and relay_id are required")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.base_url = normalized
        self._token = token
        self.relay_id = relay_id
        self.timeout_s = float(timeout_s)

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        key = idempotency_key or f"relay-{uuid4().hex}"
        encoded = json.dumps(
            dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        last_error: RelayHTTPError | None = None
        for attempt in range(self._MAX_RETRIES + 1):
            request = Request(
                self.base_url + path,
                data=encoded,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/json",
                    "Idempotency-Key": key,
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout_s) as response:
                    body = response.read()
                    value = json.loads(body.decode("utf-8") or "{}")
                    if not isinstance(value, dict):
                        raise RelayHTTPError(0, False, "relay response was not a JSON object")
                    return value
            except HTTPError as exc:
                exc.close()
                retryable = exc.code in self._RETRYABLE_STATUSES
                last_error = RelayHTTPError(
                    int(exc.code),
                    retryable,
                    f"relay request failed with HTTP {exc.code}",
                )
                if not retryable or attempt >= self._MAX_RETRIES:
                    raise last_error from None
            except RelayHTTPError:
                raise
            except (
                URLError,
                TimeoutError,
                socket.timeout,
                ConnectionResetError,
                ConnectionAbortedError,
                RemoteDisconnected,
            ) as exc:
                last_error = RelayHTTPError(
                    0,
                    True,
                    f"relay transport failed: {type(exc).__name__}",
                )
                if attempt >= self._MAX_RETRIES:
                    raise last_error from None
            if attempt < self._MAX_RETRIES:
                time.sleep(min(0.05 * (2**attempt), 0.1))
        raise last_error or RelayHTTPError(0, True, "relay request failed")

    def register(self, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._post(
            "/api/relay/register",
            {"relay_id": self.relay_id, "status": dict(status or {})},
        )

    def heartbeat(self, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self._post(
            "/api/relay/heartbeat",
            {"relay_id": self.relay_id, "status": dict(status or {})},
        )

    def claim(self, *, lease_ms: int = 20_000) -> dict[str, Any]:
        return self._post(
            "/api/relay/jobs/claim",
            {"relay_id": self.relay_id, "lease_ms": int(lease_ms)},
        )

    def renew(self, job_id: str, *, lease_ms: int = 20_000) -> dict[str, Any]:
        return self._post(
            f"/api/relay/jobs/{job_id}/lease",
            {"relay_id": self.relay_id, "lease_ms": int(lease_ms)},
        )

    def post_events(
        self, job_id: str, events: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        return self._post(
            f"/api/relay/jobs/{job_id}/events",
            {"relay_id": self.relay_id, "events": [dict(event) for event in events]},
        )

    def upload_artifact(
        self, job_id: str, artifact_name: str, value: object
    ) -> dict[str, Any]:
        return self._post(
            f"/api/relay/jobs/{job_id}/artifacts",
            {
                "relay_id": self.relay_id,
                "artifact_name": artifact_name,
                "value": value,
            },
        )

    def complete(
        self,
        job_id: str,
        *,
        succeeded: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "relay_id": self.relay_id,
            "succeeded": bool(succeeded),
        }
        if error:
            payload["error"] = error
        return self._post(f"/api/relay/jobs/{job_id}/complete", payload)
