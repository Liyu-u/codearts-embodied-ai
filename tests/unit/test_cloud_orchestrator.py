from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from demo.cloud.orchestrator import CloudOrchestrator
from demo.cloud.store import CloudStore
from tools.live_intelligent_e2e import document_digest, strategy_digest


def task_id_for(run_id: str) -> str:
    return f"task-{run_id}"


def perception_document(run_id: str = "run-test") -> dict:
    return {
        "schema_version": "perception.v1",
        "scene_id": "multi_object_stacking",
        "objects": [
            {
                "id": "red_cube",
                "category": "cube",
                "pose": {"x": 0.60, "y": -0.14, "z": 0.0258},
            },
            {
                "id": "zone_unstack_target",
                "category": "target_zone",
                "pose": {"x": 0.45, "y": 0.10, "z": 0.02575},
            },
        ],
        "provenance": {"backend": "isaac_ground_truth", "run_id": run_id},
    }


def task_document(run_id: str) -> dict:
    return {
        "schema_version": "task.v1",
        "task_id": task_id_for(run_id),
        "action": "pick_and_place",
        "target_ids": ["red_cube"],
        "destination_id": "zone_unstack_target",
        "constraints": [],
        "status": "READY",
        "blocking_reasons": [],
        "execution_allowed": True,
        "clarification": None,
        "diagnostics": {
            "engine_trace": {
                "llm_network_calls": 1,
                "llm_call_succeeded": True,
                "llm_request_id": "deepseek-request-001",
                "fallback_used": False,
            }
        },
    }


def strategy_document(run_id: str, perception: dict) -> dict:
    return {
        "schema_version": "strategy.v1",
        "task_id": task_id_for(run_id),
        "steps": [
            {"step_id": "detect", "action": "detect_object", "arguments": {"object_id": "red_cube"}},
            {"step_id": "move-object", "action": "move_to_object", "arguments": {"object_id": "$detect.object_id"}},
            {"step_id": "grasp", "action": "grasp", "arguments": {"object_id": "$detect.object_id"}},
            {"step_id": "move-target", "action": "move_to_target", "arguments": {"destination_id": "zone_unstack_target"}},
            {"step_id": "release", "action": "release", "arguments": {}},
        ],
        "code": None,
        "success": True,
        "blocked": False,
        "mode": "codearts",
        "provenance": {
            "source": "codearts_agent",
            "request_id": "codearts-request-001",
            "fallback": False,
        },
        "validation": {"passed": True, "errors": []},
        "input_perception_sha256": document_digest(perception),
    }


def feedback_document(run_id: str, *, fallback: bool = False, request_id: str | None = "deepseek-d-001") -> dict:
    return {
        "schema_version": "feedback.v1",
        "task_id": task_id_for(run_id),
        "diagnosis": "真实执行证据已复核",
        "retryable": False,
        "patch": None,
        "provenance": {
            "source": "tracecoder_llm",
            "request_id": request_id,
            "fallback": fallback,
        },
    }


class CloudOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CloudStore(Path(self.temp_dir.name) / "cloud.sqlite3")
        self.intent_inputs: list[dict] = []
        self.strategy_inputs: list[dict] = []
        self.feedback_inputs: list[dict] = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def build_orchestrator(self, *, intent=None, strategy=None, feedback=None) -> CloudOrchestrator:
        def intent_call(instruction: str, perception: dict, run_id: str) -> dict:
            self.intent_inputs.append(deepcopy(perception))
            return task_document(run_id)

        def strategy_call(task: dict, perception: dict, run_id: str) -> dict:
            self.strategy_inputs.append(deepcopy(perception))
            return strategy_document(run_id, perception)

        def feedback_call(payload: dict, run_id: str) -> dict:
            self.feedback_inputs.append(deepcopy(payload))
            return feedback_document(run_id)

        return CloudOrchestrator(
            self.store,
            intent_call=intent or intent_call,
            strategy_call=strategy or strategy_call,
            feedback_call=feedback or feedback_call,
            run_id_factory=lambda: "run-test",
        )

    def prepare_execution(self, orchestrator: CloudOrchestrator) -> tuple[dict, dict]:
        orchestrator.create_run("multi-red-001", "把红色方块放到桌面区域", "operator-1")
        orchestrator.handle_perception("run-test", perception_document())
        jobs = self.store.list_jobs("run-test")
        execute_job = next(job for job in jobs if job["job_type"] == "ISAAC_EXECUTE")
        claimed = self.store.claim_job("relay-a", lease_ms=1000, now_ms=100)
        if claimed["job_id"] != execute_job["job_id"]:
            self.store.complete_job(claimed["job_id"], "relay-a", succeeded=True, now_ms=101)
            claimed = self.store.claim_job("relay-a", lease_ms=1000, now_ms=102)
        orchestrator.handle_c_event(
            execute_job["job_id"],
            {"event_id": "evt-start", "type": "EXECUTION_STARTED", "payload": {}},
            relay_id="relay-a",
            now_ms=103,
        )
        return execute_job, execute_job["payload"]["strategy"]

    def upload_success_evidence(self, job: dict, strategy: dict, *, backend: str = "isaac") -> None:
        execution = {
            "schema_version": "execution.v1",
            "task_id": task_id_for("run-test"),
            "status": "SUCCEEDED",
            "steps": [{"step_id": "release", "action": "release", "status": "SUCCESS"}],
            "safety_events": [],
            "input_strategy_sha256": strategy_digest(strategy),
            "provenance": {"backend": backend, "run_id": "run-test"},
        }
        final_pose = {
            "run_id": "run-test",
            "task_id": task_id_for("run-test"),
            "object_id": "red_cube",
            "destination_id": "zone_unstack_target",
            "goal_reached": True,
            "provenance": {"backend": backend, "world_id": "persistent-world-1"},
        }
        self.store.save_artifact("run-test", "execution.json", execution, job_id=job["job_id"], relay_id="relay-a", now_ms=104)
        self.store.save_artifact("run-test", "final_pose.json", final_pose, job_id=job["job_id"], relay_id="relay-a", now_ms=105)
        self.store.complete_job(job["job_id"], "relay-a", succeeded=True, now_ms=106)

    def test_happy_path_uses_same_perception_and_queues_one_digest_bound_execute_job(self) -> None:
        orchestrator = self.build_orchestrator()
        created = orchestrator.create_run("multi-red-001", "把红色方块放到桌面区域", "operator-1")
        self.assertEqual(created["state"], "PREPARING_SCENE")

        queued = orchestrator.handle_perception("run-test", perception_document())

        self.assertEqual(queued["state"], "QUEUED_C")
        self.assertEqual(self.intent_inputs, self.strategy_inputs)
        execute_jobs = [job for job in self.store.list_jobs("run-test") if job["job_type"] == "ISAAC_EXECUTE"]
        self.assertEqual(len(execute_jobs), 1)
        payload = execute_jobs[0]["payload"]
        self.assertEqual(payload["strategy_sha256"], strategy_digest(payload["strategy"]))
        self.assertEqual(payload["perception_sha256"], document_digest(perception_document()))

        job, strategy = self.prepare_execution(orchestrator) if False else (execute_jobs[0], payload["strategy"])
        prepare = next(job for job in self.store.list_jobs("run-test") if job["job_type"] == "ISAAC_PREPARE_AND_PERCEIVE")
        claimed_prepare = self.store.claim_job("relay-a", lease_ms=1000, now_ms=90)
        self.assertEqual(claimed_prepare["job_id"], prepare["job_id"])
        self.store.complete_job(prepare["job_id"], "relay-a", succeeded=True, now_ms=91)
        claimed_execute = self.store.claim_job("relay-a", lease_ms=1000, now_ms=92)
        self.assertEqual(claimed_execute["job_id"], job["job_id"])
        orchestrator.handle_c_event(job["job_id"], {"event_id": "evt-start", "type": "EXECUTION_STARTED", "payload": {}}, relay_id="relay-a", now_ms=93)
        self.upload_success_evidence(job, strategy)

        finished = orchestrator.handle_c_completion(job["job_id"])

        self.assertEqual(finished["state"], "SUCCEEDED")
        self.assertTrue(finished["audit_eligible"])
        self.assertEqual(len(self.feedback_inputs), 1)

    def test_a_or_b_provider_fallback_fails_closed_before_execute_job(self) -> None:
        def bad_intent(_instruction, _perception, run_id):
            task = task_document(run_id)
            task["diagnostics"]["engine_trace"]["fallback_used"] = True
            return task

        orchestrator = self.build_orchestrator(intent=bad_intent)
        orchestrator.create_run("multi-red-001", "把红色方块放到桌面区域", "operator-1")

        blocked = orchestrator.handle_perception("run-test", perception_document())

        self.assertEqual(blocked["state"], "BLOCKED")
        self.assertFalse(any(job["job_type"] == "ISAAC_EXECUTE" for job in self.store.list_jobs("run-test")))

        self.temp_dir.cleanup()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CloudStore(Path(self.temp_dir.name) / "cloud.sqlite3")

        def bad_strategy(_task, perception, run_id):
            value = strategy_document(run_id, perception)
            value["code"] = "print('unsafe')"
            return value

        orchestrator = self.build_orchestrator(strategy=bad_strategy)
        orchestrator.create_run("multi-red-001", "把红色方块放到桌面区域", "operator-1")
        blocked = orchestrator.handle_perception("run-test", perception_document())
        self.assertEqual(blocked["state"], "BLOCKED")

    def test_c_digest_backend_final_pose_and_d_evidence_fail_closed(self) -> None:
        cases = ("digest", "backend", "final_pose", "d_fallback")
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as directory:
                    self.store = CloudStore(Path(directory) / "cloud.sqlite3")
                    feedback = None
                    if case == "d_fallback":
                        feedback = lambda _payload, run_id: feedback_document(run_id, fallback=True)
                    orchestrator = self.build_orchestrator(feedback=feedback)
                    job, strategy = self.prepare_execution(orchestrator)
                    backend = "mock" if case == "backend" else "isaac"
                    self.upload_success_evidence(job, strategy, backend=backend)
                    if case == "digest":
                        connection = sqlite3.connect(self.store.path)
                        try:
                            execution = dict(self.store.list_artifacts("run-test")[0]["value"])
                            execution["input_strategy_sha256"] = "drift"
                            connection.execute("UPDATE artifacts SET value_json=? WHERE artifact_name='execution.json'", (json.dumps(execution),))
                            connection.commit()
                        finally:
                            connection.close()
                    if case == "final_pose":
                        connection = sqlite3.connect(self.store.path)
                        try:
                            connection.execute("DELETE FROM artifacts WHERE artifact_name='final_pose.json'")
                            connection.commit()
                        finally:
                            connection.close()

                    result = orchestrator.handle_c_completion(job["job_id"])
                    self.assertIn(result["state"], {"FAILED", "BLOCKED"})
                    self.assertFalse(result["audit_eligible"])

    def test_success_after_safe_stop_is_rejected_without_calling_d(self) -> None:
        orchestrator = self.build_orchestrator()
        job, strategy = self.prepare_execution(orchestrator)
        execution = {
            "schema_version": "execution.v1",
            "task_id": task_id_for("run-test"),
            "status": "SAFE_STOP",
            "steps": [
                {"step_id": "move", "action": "move_to_target", "status": "FAILED", "reason": "COLLISION_DETECTED"},
                {"step_id": "release", "action": "release", "status": "SUCCESS"},
            ],
            "safety_events": [{"type": "COLLISION_DETECTED"}],
            "input_strategy_sha256": strategy_digest(strategy),
            "provenance": {"backend": "isaac", "run_id": "run-test"},
        }
        self.store.save_artifact("run-test", "execution.json", execution, job_id=job["job_id"], relay_id="relay-a", now_ms=104)
        self.store.save_artifact("run-test", "final_pose.json", {"run_id": "run-test", "task_id": task_id_for("run-test"), "goal_reached": False, "provenance": {"backend": "isaac"}}, job_id=job["job_id"], relay_id="relay-a", now_ms=105)
        self.store.complete_job(job["job_id"], "relay-a", succeeded=False, now_ms=106)

        stopped = orchestrator.handle_c_completion(job["job_id"])

        self.assertEqual(stopped["state"], "SAFE_STOPPED")
        self.assertEqual(self.feedback_inputs, [])


if __name__ == "__main__":
    unittest.main()
