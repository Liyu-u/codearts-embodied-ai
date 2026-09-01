from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping

from demo.cloud.auth import SessionRecord, authorize, validate_session
from demo.cloud.orchestrator import CloudOrchestrator
from demo.cloud.scenario_registry import list_verified_scenarios
from demo.cloud.security import require_bearer, validate_artifact
from demo.cloud.store import CloudStore
from demo.cloud.types import public_run_snapshot
from tools.live_intelligent_e2e import document_digest


def _utc_ms() -> int:
    return time.time_ns() // 1_000_000


class CloudService:
    def __init__(
        self,
        store: CloudStore,
        orchestrator: CloudOrchestrator,
        *,
        relay_token: str,
        browser_sessions: MutableMapping[bytes, SessionRecord] | None = None,
        hls_url: str = "/live/isaac/index.m3u8",
        now_ms: Callable[[], int] = _utc_ms,
        relay_stale_ms: int = 30_000,
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.relay_token = relay_token
        self.browser_sessions = browser_sessions if browser_sessions is not None else {}
        self.hls_url = hls_url
        self.now_ms = now_ms
        self.relay_stale_ms = relay_stale_ms

    @staticmethod
    def _cookie_value(cookie_header: str | None, name: str) -> str | None:
        for item in (cookie_header or "").split(";"):
            key, separator, value = item.strip().partition("=")
            if separator and key == name:
                return value
        return None

    def authorize_browser(self, cookie_header: str | None, action: str) -> SessionRecord:
        token = self._cookie_value(cookie_header, "closed_loop_session")
        if not token:
            raise PermissionError("browser session is required")
        session = validate_session(token, self.browser_sessions, now_ms=self.now_ms())
        authorize(session.role, action)
        return session

    def require_relay(self, authorization_header: str | None) -> None:
        require_bearer(authorization_header, self.relay_token, production=True)

    def scenarios(self) -> list[dict[str, Any]]:
        return list_verified_scenarios()

    def livestream(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.hls_url),
            "protocol": "HLS",
            "url": self.hls_url,
            "live": False,
            "status": "not_probed",
            "source": "mediamtx" if self.hls_url else None,
        }

    def health(self) -> dict[str, Any]:
        now = self.now_ms()
        relays = self.store.list_relay_sessions()
        latest = relays[0] if relays else None
        if latest is None:
            relay_status = "offline"
            relay_public = {"status": relay_status, "last_seen_at": None}
            isaac = {"status": "offline"}
        else:
            age = max(0, now - int(latest["last_seen_at"]))
            relay_status = "online" if age <= self.relay_stale_ms else "degraded"
            relay_public = {
                "status": relay_status,
                "relay_id": latest["relay_id"],
                "last_seen_at": latest["last_seen_at"],
                "age_ms": age,
            }
            reported = latest.get("status") or {}
            isaac_value = reported.get("isaac") or reported.get("worker") or "unknown"
            isaac = {"status": str(isaac_value)}
        components = {
            "cloud": {"status": "ok", "database": "available"},
            "relay": relay_public,
            "isaac": isaac,
            "providers": {
                "status": "not_probed",
                "A": "required",
                "B": "required",
                "D": "required",
            },
            "livestream": {"status": "not_probed", "required_now": False},
        }
        return {
            "status": "ok" if relay_status == "online" else "degraded",
            "healthy": True,
            "components": components,
        }

    def create_run(self, scene_id: str, instruction: str, actor_id: str) -> dict[str, Any]:
        return self.orchestrator.create_run(scene_id, instruction, actor_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return public_run_snapshot(self.store.get_run(run_id))

    def list_runs(self) -> list[dict[str, Any]]:
        return [public_run_snapshot(row) for row in self.store.list_runs()]

    def get_events(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        self.store.get_run(run_id)
        return self.store.list_events(run_id, after_sequence=after_sequence)

    def relay_register(self, relay_id: str, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.store.update_relay_session(relay_id, status or {}, now_ms=self.now_ms())

    def relay_heartbeat(self, relay_id: str, status: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return self.store.update_relay_session(relay_id, status or {}, now_ms=self.now_ms())

    def relay_claim(self, relay_id: str, lease_ms: int) -> dict[str, Any] | None:
        self.store.recover_expired_jobs(now_ms=self.now_ms())
        return self.store.claim_job(relay_id, lease_ms=lease_ms, now_ms=self.now_ms())

    def relay_renew(self, job_id: str, relay_id: str, lease_ms: int) -> dict[str, Any]:
        return self.store.renew_lease(
            job_id, relay_id, lease_ms=lease_ms, now_ms=self.now_ms()
        )

    def relay_events(
        self, job_id: str, relay_id: str, events: list[Mapping[str, Any]]
    ) -> int:
        job = self.store.get_job(job_id)
        if job["job_type"] == "ISAAC_EXECUTE":
            inserted = 0
            for event in events:
                before = len(self.store.list_events(job["run_id"]))
                self.orchestrator.handle_c_event(
                    job_id,
                    event,
                    relay_id=relay_id,
                    now_ms=self.now_ms(),
                )
                after = len(self.store.list_events(job["run_id"]))
                inserted += after - before
            return inserted
        return self.store.append_events(
            job["run_id"],
            events,
            job_id=job_id,
            relay_id=relay_id,
            now_ms=self.now_ms(),
        )

    def relay_artifact(
        self,
        job_id: str,
        relay_id: str,
        artifact_name: str,
        value: object,
    ) -> None:
        validate_artifact(artifact_name, value)
        job = self.store.get_job(job_id)
        self.store.save_artifact(
            job["run_id"],
            artifact_name,
            value,
            job_id=job_id,
            relay_id=relay_id,
            now_ms=self.now_ms(),
        )

    def relay_complete(
        self,
        job_id: str,
        relay_id: str,
        *,
        succeeded: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        replay = job["state"] in {"SUCCEEDED", "FAILED"}
        self.store.complete_job(
            job_id,
            relay_id,
            succeeded=succeeded,
            error=error,
            now_ms=self.now_ms(),
        )
        if replay:
            return public_run_snapshot(self.store.get_run(job["run_id"]))
        if job["job_type"] == "ISAAC_PREPARE_AND_PERCEIVE":
            artifacts = {
                item["artifact_name"]: item["value"]
                for item in self.store.list_artifacts(job["run_id"])
            }
            perception = artifacts.get("perception.json")
            if not isinstance(perception, dict):
                raise ValueError("prepare job completed without perception.json")
            return self.orchestrator.handle_perception(job["run_id"], perception)
        if job["job_type"] == "ISAAC_EXECUTE":
            return self.orchestrator.handle_c_completion(job_id)
        raise ValueError(f"unsupported job type: {job['job_type']}")


_SERVICE: CloudService | None = None
_UNSET = object()


def _build_default_service() -> CloudService:
    from integration.adapters import intent, strategy, tracecoder
    from integration.strategy_policy import DEFAULT_CAPABILITIES

    def intent_call(instruction: str, perception: dict[str, Any], run_id: str) -> dict[str, Any]:
        return intent.run(
            {
                "instruction": instruction,
                "perception": perception,
                "engine": "llm",
                "correlation_id": run_id,
            }
        )

    def strategy_call(task: dict[str, Any], perception: dict[str, Any], _run_id: str) -> dict[str, Any]:
        output = strategy.run({**task, "capabilities": DEFAULT_CAPABILITIES})
        if isinstance(output, dict):
            output["input_perception_sha256"] = document_digest(perception)
        return output

    def feedback_call(payload: dict[str, Any], _run_id: str) -> dict[str, Any]:
        return tracecoder.run(payload)

    db_path = Path(os.getenv("CLOUD_DB_PATH", ".cloud-runtime/cloud.sqlite3"))
    store = CloudStore(db_path)
    orchestrator = CloudOrchestrator(
        store,
        intent_call=intent_call,
        strategy_call=strategy_call,
        feedback_call=feedback_call,
    )
    return CloudService(
        store,
        orchestrator,
        relay_token=os.getenv("CLOUD_RELAY_TOKEN", ""),
        hls_url=os.getenv("CLOUD_HLS_URL", "/live/isaac/index.m3u8"),
    )


def configure_cloud_service(service: CloudService | None | object = _UNSET) -> CloudService | None:
    global _SERVICE
    if service is _UNSET:
        _SERVICE = _build_default_service()
    else:
        _SERVICE = service if isinstance(service, CloudService) else None
    return _SERVICE


def get_cloud_service() -> CloudService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = _build_default_service()
    return _SERVICE
