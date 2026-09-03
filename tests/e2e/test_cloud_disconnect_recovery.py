from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.cloud_relay_agent import CloudRelayAgent, EventSpool, RelayStateStore, main
from tools.relay.client import RelayHTTPError


def claimed_job(run_id: str = "run-relay-001") -> dict:
    return {
        "job_id": "job-relay-001",
        "run_id": run_id,
        "job_type": "ISAAC_PREPARE_AND_PERCEIVE",
        "payload": {
            "schema_version": "cloud-job.v1",
            "run_id": run_id,
            "case_id": "multi-red-001",
            "scene_id": "multi_object_stacking",
        },
    }


class FakeRelayClient:
    def __init__(self, *, fail_operation: str | None = None) -> None:
        self.fail_operation = fail_operation
        self.claimed = False
        self.calls: list[tuple] = []
        self.relay_id = "relay-windows-001"

    def _record(self, operation: str, *values):
        self.calls.append((operation, *values))
        if self.fail_operation == operation:
            raise RelayHTTPError(0, True, f"{operation} unavailable")

    def register(self, status=None):
        self._record("register", status)
        return {"ok": True}

    def heartbeat(self, status=None):
        self._record("heartbeat", status)
        return {"ok": True}

    def claim(self, *, lease_ms=20_000):
        self._record("claim", lease_ms)
        if self.claimed:
            return {"job": None}
        self.claimed = True
        return {"job": claimed_job()}

    def renew(self, job_id, *, lease_ms=20_000):
        self._record("renew", job_id, lease_ms)
        return {"ok": True}

    def post_events(self, job_id, events):
        self._record("events", job_id, list(events))
        return {"inserted": len(events)}

    def upload_artifact(self, job_id, artifact_name, value):
        self._record("artifact", job_id, artifact_name, value)
        return {"ok": True}

    def complete(self, job_id, *, succeeded, error=None):
        self._record("complete", job_id, succeeded, error)
        return {"ok": True}


class FakeJobRunner:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def run(self, job, emit):
        self.calls += 1
        if self.error is not None:
            raise self.error
        emit({"sequence": 2, "event_id": "evt-2", "type": "WORKING"})
        emit({"sequence": 1, "event_id": "evt-1", "type": "STARTED"})
        return {
            "run_id": job["run_id"],
            "job_type": job["job_type"],
            "artifacts": {"perception.json": {"schema_version": "perception.v1"}},
            "completion": {"status": "SUCCEEDED"},
            "last_sequence": 2,
        }


class CloudDisconnectRecoveryTests(unittest.TestCase):
    def build_agent(self, directory, client, runner):
        state = RelayStateStore(Path(directory) / "relay-state.json")
        spool = EventSpool(Path(directory) / "event-spool.json")
        return CloudRelayAgent(
            client,
            runner,
            state,
            spool,
            lease_ms=20_000,
            renew_interval_s=60,
        )

    def test_status_provider_adds_worker_health_without_overriding_agent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeRelayClient()
            runner = FakeJobRunner()
            state = RelayStateStore(Path(directory) / "relay-state.json")
            spool = EventSpool(Path(directory) / "event-spool.json")
            agent = CloudRelayAgent(
                client,
                runner,
                state,
                spool,
                status_provider=lambda: {
                    "agent": "should-not-override",
                    "worker": "online",
                },
            )

            self.assertEqual(
                agent._status_payload(),
                {"agent": "online", "worker": "online"},
            )

    def test_claim_is_persisted_before_execution_and_token_is_never_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeRelayClient(fail_operation="renew")
            runner = FakeJobRunner()
            agent = self.build_agent(directory, client, runner)

            outcome = agent.run_once()

            saved = json.loads((Path(directory) / "relay-state.json").read_text(encoding="utf-8"))
            disk_text = "\n".join(path.read_text(encoding="utf-8") for path in Path(directory).glob("*.json"))
        self.assertEqual(outcome["status"], "WAITING_FOR_CLOUD")
        self.assertEqual(saved["active_job"]["job_id"], "job-relay-001")
        self.assertEqual(runner.calls, 0)
        self.assertNotIn("relay-secret", disk_text)

    def test_event_spool_deduplicates_and_replays_in_sequence_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            spool = EventSpool(Path(directory) / "events.json")
            spool.append({"sequence": 2, "event_id": "evt-2", "type": "SECOND"})
            spool.append({"sequence": 1, "event_id": "evt-1", "type": "FIRST"})
            spool.append({"sequence": 1, "event_id": "evt-1", "type": "FIRST"})
            restored = EventSpool(Path(directory) / "events.json")

            self.assertEqual([item["sequence"] for item in restored.pending()], [1, 2])
            restored.acknowledge(["evt-1"])
            self.assertEqual([item["event_id"] for item in restored.pending()], ["evt-2"])

    def test_network_loss_preserves_terminal_evidence_and_restart_does_not_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeJobRunner()
            first_client = FakeRelayClient(fail_operation="events")
            first = self.build_agent(directory, first_client, runner)

            first_outcome = first.run_once()
            persisted = RelayStateStore(Path(directory) / "relay-state.json").load()

            second_client = FakeRelayClient()
            second_client.claimed = True
            second = self.build_agent(directory, second_client, runner)
            second_outcome = second.run_once()

            self.assertEqual(first_outcome["status"], "WAITING_FOR_CLOUD")
            self.assertEqual(persisted["phase"], "DELIVERING")
            self.assertTrue(persisted["completion"]["succeeded"])
            self.assertEqual(runner.calls, 1)
            self.assertEqual(second_outcome["status"], "COMPLETED")
            self.assertNotIn("claim", [call[0] for call in second_client.calls])
            self.assertEqual(
                [event["sequence"] for event in next(call[2] for call in second_client.calls if call[0] == "events")],
                [1, 2],
            )
            self.assertEqual(RelayStateStore(Path(directory) / "relay-state.json").load()["phase"], "IDLE")

    def test_restart_reconciles_running_job_without_duplicate_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RelayStateStore(Path(directory) / "relay-state.json")
            store.begin(claimed_job())
            runner = FakeJobRunner()
            client = FakeRelayClient()
            client.claimed = True

            outcome = self.build_agent(directory, client, runner).run_once()

            self.assertEqual(outcome["status"], "COMPLETED")
            self.assertEqual(runner.calls, 1)
            self.assertNotIn("claim", [call[0] for call in client.calls])

    def test_ssh_loss_becomes_explicit_failed_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeRelayClient()
            runner = FakeJobRunner(error=ConnectionError("ssh transport lost"))

            outcome = self.build_agent(directory, client, runner).run_once()

            failure_event = next(call for call in client.calls if call[0] == "events")[2][0]
            completed = next(call for call in client.calls if call[0] == "complete")
            self.assertEqual(outcome["status"], "FAILED")
            self.assertEqual(failure_event["type"], "RELAY_JOB_FAILED")
            self.assertIn("ConnectionError", failure_event["payload"]["error"])
            self.assertFalse(completed[2])
            self.assertIn("ConnectionError", completed[3])

    def test_expired_lease_blocks_execution_and_is_retried_from_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeJobRunner()
            unavailable = FakeRelayClient(fail_operation="renew")
            first = self.build_agent(directory, unavailable, runner).run_once()
            recovered_client = FakeRelayClient()
            recovered_client.claimed = True

            second = self.build_agent(directory, recovered_client, runner).run_once()

            self.assertEqual(first["status"], "WAITING_FOR_CLOUD")
            self.assertEqual(runner.calls, 1)
            self.assertEqual(second["status"], "COMPLETED")
            self.assertNotIn("claim", [call[0] for call in recovered_client.calls])

    def test_cli_check_config_builds_real_ssh_runner_without_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "id_ed25519"
            known_hosts = root / "known_hosts"
            key.write_text("test-key", encoding="utf-8")
            known_hosts.write_text("10.16.0.40 ssh-ed25519 test", encoding="utf-8")
            with patch.dict(os.environ, {"CLOUD_RELAY_TOKEN": "relay-secret"}, clear=False):
                exit_code = main(
                    [
                        "--cloud-url",
                        "https://cloud.example.test",
                        "--server",
                        "10.16.0.40",
                        "--ssh-key",
                        str(key),
                        "--known-hosts",
                        str(known_hosts),
                        "--state-dir",
                        str(root / "state"),
                        "--check-config",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertFalse((root / "state" / "relay-state.json").exists())


if __name__ == "__main__":
    unittest.main()
