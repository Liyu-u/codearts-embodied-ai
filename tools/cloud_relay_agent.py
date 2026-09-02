from __future__ import annotations

import argparse
import json
import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Protocol

from tools.relay.client import RelayClient, RelayHTTPError
from tools.relay.isaac_job import IsaacJobConfig, IsaacJobRunner, OpenSSHRuntimeRemote
from tools.relay.runtime_protocol import atomic_write_json


class JobRunner(Protocol):
    def run(self, job: dict[str, Any], emit) -> dict[str, Any]: ...


class RelayStateStore:
    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @classmethod
    def empty(cls) -> dict[str, Any]:
        return {
            "version": cls.VERSION,
            "phase": "IDLE",
            "active_job": None,
            "artifacts": {},
            "completion": None,
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.empty()
        with self.path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict) or value.get("version") != self.VERSION:
            raise ValueError("unsupported relay state file")
        return value

    def save(self, value: Mapping[str, Any]) -> dict[str, Any]:
        document = deepcopy(dict(value))
        document["version"] = self.VERSION
        atomic_write_json(self.path, document)
        return document

    def begin(self, job: Mapping[str, Any]) -> dict[str, Any]:
        value = self.load()
        if value.get("active_job") is not None:
            raise RuntimeError("a relay job is already active")
        value.update(
            {
                "phase": "RUNNING",
                "active_job": deepcopy(dict(job)),
                "artifacts": {},
                "completion": None,
            }
        )
        return self.save(value)

    def finish(
        self,
        *,
        artifacts: Mapping[str, Any],
        succeeded: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        value = self.load()
        if value.get("active_job") is None:
            raise RuntimeError("cannot finish without an active relay job")
        completion: dict[str, Any] = {"succeeded": bool(succeeded)}
        if error:
            completion["error"] = error
        value.update(
            {
                "phase": "DELIVERING",
                "artifacts": deepcopy(dict(artifacts)),
                "completion": completion,
            }
        )
        return self.save(value)

    def clear(self) -> dict[str, Any]:
        return self.save(self.empty())


class EventSpool:
    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict) or value.get("version") != self.VERSION:
            raise ValueError("unsupported event spool file")
        events = value.get("events")
        if not isinstance(events, list):
            raise ValueError("event spool events must be a list")
        return [deepcopy(item) for item in events if isinstance(item, dict)]

    def _save(self, events: list[dict[str, Any]]) -> None:
        atomic_write_json(self.path, {"version": self.VERSION, "events": events})

    def append(self, event: Mapping[str, Any]) -> None:
        value = deepcopy(dict(event))
        event_id = value.get("event_id")
        sequence = value.get("sequence")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("spooled event_id is required")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            raise ValueError("spooled event sequence must be a positive integer")
        events = self._load()
        if any(item.get("event_id") == event_id for item in events):
            return
        events.append(value)
        events.sort(key=lambda item: (int(item["sequence"]), str(item["event_id"])))
        self._save(events)

    def pending(self) -> list[dict[str, Any]]:
        return sorted(
            self._load(), key=lambda item: (int(item["sequence"]), str(item["event_id"]))
        )

    def acknowledge(self, event_ids: list[str]) -> None:
        removed = frozenset(event_ids)
        self._save([item for item in self._load() if item.get("event_id") not in removed])

    def clear(self) -> None:
        self._save([])


class CloudRelayAgent:
    def __init__(
        self,
        client: RelayClient,
        runner: JobRunner,
        state: RelayStateStore,
        spool: EventSpool,
        *,
        lease_ms: int = 20_000,
        renew_interval_s: float = 20.0,
        heartbeat_interval_s: float = 10.0,
    ) -> None:
        if lease_ms <= 0 or renew_interval_s <= 0 or heartbeat_interval_s <= 0:
            raise ValueError("relay intervals must be positive")
        self.client = client
        self.runner = runner
        self.state = state
        self.spool = spool
        self.lease_ms = int(lease_ms)
        self.renew_interval_s = float(renew_interval_s)
        self.heartbeat_interval_s = float(heartbeat_interval_s)

    @staticmethod
    def _runtime_job(claimed: Mapping[str, Any]) -> dict[str, Any]:
        payload = claimed.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError("claimed job payload must be an object")
        job = deepcopy(dict(payload))
        job["job_type"] = claimed.get("job_type")
        job["run_id"] = claimed.get("run_id")
        job["job_id"] = claimed.get("job_id")
        return job

    def _renew_while_running(self, job_id: str, stopped: threading.Event) -> None:
        while not stopped.wait(self.renew_interval_s):
            try:
                self.client.renew(job_id, lease_ms=self.lease_ms)
            except RelayHTTPError:
                return

    def _deliver(self, value: Mapping[str, Any]) -> dict[str, Any]:
        job = value.get("active_job")
        completion = value.get("completion")
        if not isinstance(job, Mapping) or not isinstance(completion, Mapping):
            raise ValueError("delivery state is incomplete")
        job_id = str(job.get("job_id") or "")
        if not job_id:
            raise ValueError("active job_id is missing")
        try:
            events = self.spool.pending()
            if events:
                self.client.post_events(job_id, events)
            artifacts = value.get("artifacts") or {}
            if not isinstance(artifacts, Mapping):
                raise ValueError("relay artifacts must be an object")
            for name in sorted(artifacts):
                self.client.upload_artifact(job_id, str(name), artifacts[name])
            succeeded = bool(completion.get("succeeded"))
            self.client.complete(
                job_id,
                succeeded=succeeded,
                error=str(completion.get("error")) if completion.get("error") else None,
            )
        except RelayHTTPError as exc:
            return {"status": "WAITING_FOR_CLOUD", "error": str(exc), "job_id": job_id}
        self.spool.clear()
        self.state.clear()
        return {"status": "COMPLETED" if succeeded else "FAILED", "job_id": job_id}

    def run_once(self) -> dict[str, Any]:
        value = self.state.load()
        if value.get("phase") == "DELIVERING":
            return self._deliver(value)

        active = value.get("active_job")
        if active is None:
            try:
                response = self.client.claim(lease_ms=self.lease_ms)
            except RelayHTTPError as exc:
                return {"status": "WAITING_FOR_CLOUD", "error": str(exc)}
            active = response.get("job") if isinstance(response, Mapping) else None
            if active is None:
                return {"status": "IDLE"}
            if not isinstance(active, Mapping):
                raise ValueError("claimed job must be an object")
            value = self.state.begin(active)
            active = value["active_job"]

        job_id = str(active.get("job_id") or "")
        if not job_id:
            raise ValueError("claimed job_id is missing")
        try:
            self.client.renew(job_id, lease_ms=self.lease_ms)
        except RelayHTTPError as exc:
            return {"status": "WAITING_FOR_CLOUD", "error": str(exc), "job_id": job_id}

        stop_renewal = threading.Event()
        renewer = threading.Thread(
            target=self._renew_while_running,
            args=(job_id, stop_renewal),
            name=f"relay-lease-{job_id}",
            daemon=True,
        )
        renewer.start()
        try:
            result = self.runner.run(self._runtime_job(active), self.spool.append)
            artifacts = result.get("artifacts") if isinstance(result, Mapping) else None
            if not isinstance(artifacts, Mapping):
                raise ValueError("Isaac runner did not return artifacts")
            value = self.state.finish(artifacts=artifacts, succeeded=True)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            pending = self.spool.pending()
            next_sequence = max((int(item["sequence"]) for item in pending), default=0) + 1
            self.spool.append(
                {
                    "sequence": next_sequence,
                    "event_id": f"relay-{job_id}-failed",
                    "type": "RELAY_JOB_FAILED",
                    "stage": "C",
                    "payload": {"error": error},
                }
            )
            value = self.state.finish(artifacts={}, succeeded=False, error=error)
        finally:
            stop_renewal.set()
            renewer.join(timeout=1.0)
        return self._deliver(value)

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stopped = stop_event or threading.Event()
        backoff_s = 1.0
        last_heartbeat = 0.0
        registered = False
        while not stopped.is_set():
            now = time.monotonic()
            if not registered:
                try:
                    self.client.register({"agent": "online"})
                    registered = True
                except RelayHTTPError:
                    stopped.wait(backoff_s)
                    backoff_s = min(backoff_s * 2, 30.0)
                    continue
            if now - last_heartbeat >= self.heartbeat_interval_s:
                try:
                    self.client.heartbeat({"agent": "online"})
                    last_heartbeat = now
                except RelayHTTPError:
                    pass
            outcome = self.run_once()
            if outcome["status"] == "WAITING_FOR_CLOUD":
                stopped.wait(backoff_s)
                backoff_s = min(backoff_s * 2, 30.0)
            else:
                backoff_s = 1.0
                stopped.wait(1.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Durable Windows to campus Isaac relay")
    parser.add_argument("--cloud-url", required=True)
    parser.add_argument("--relay-id", default="windows-campus-relay")
    parser.add_argument("--state-dir", type=Path, default=Path(".relay-runtime"))
    parser.add_argument("--server", required=True)
    parser.add_argument("--port", type=int, default=5122)
    parser.add_argument("--user", default="stu_01")
    parser.add_argument("--ssh-key", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument(
        "--remote-root", default="/data/stu_01/workspace/live-runtime"
    )
    parser.add_argument("--cloud-timeout-s", type=float, default=300.0)
    parser.add_argument("--job-timeout-s", type=float, default=900.0)
    parser.add_argument("--poll-interval-s", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--check-config", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token = os.environ.get("CLOUD_RELAY_TOKEN", "")
    if not token:
        parser.error("CLOUD_RELAY_TOKEN is required")
    remote = OpenSSHRuntimeRemote(
        server=args.server,
        port=args.port,
        user=args.user,
        ssh_key=args.ssh_key,
        known_hosts=args.known_hosts,
        remote_root=args.remote_root,
    )
    client = RelayClient(
        args.cloud_url,
        token,
        args.relay_id,
        timeout_s=args.cloud_timeout_s,
    )
    state_dir = args.state_dir.resolve()
    runner = IsaacJobRunner(
        IsaacJobConfig(
            remote=remote,
            remote_root=args.remote_root,
            local_result_root=state_dir / "artifacts",
            timeout_s=args.job_timeout_s,
            poll_interval_s=args.poll_interval_s,
        )
    )
    agent = CloudRelayAgent(
        client,
        runner,
        RelayStateStore(state_dir / "relay-state.json"),
        EventSpool(state_dir / "event-spool.json"),
    )
    if args.check_config:
        print(json.dumps({"ok": True, "relay_id": args.relay_id, "network_accessed": False}))
        return 0
    if args.once:
        print(json.dumps(agent.run_once(), ensure_ascii=False, sort_keys=True))
        return 0
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
