from __future__ import annotations

import os
import time

from copy import deepcopy
from typing import Any, Callable, Mapping
from uuid import uuid4

from demo.cloud.scenario_registry import get_verified_scenario
from demo.cloud.store import CloudStore, utc_ms
from demo.cloud.types import RunState, public_run_snapshot
from integration.contract_validation import assert_contract
from integration.strategy_policy import DEFAULT_CAPABILITIES, validate_patch, validate_strategy
from tools.live_intelligent_e2e import document_digest, strategy_digest


IntentCall = Callable[[str, dict[str, Any], str], dict[str, Any]]
StrategyCall = Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]]
FeedbackCall = Callable[[dict[str, Any], str], dict[str, Any]]


class EvidenceError(ValueError):
    pass


class CloudOrchestrator:
    def __init__(
        self,
        store: CloudStore,
        *,
        intent_call: IntentCall,
        strategy_call: StrategyCall,
        feedback_call: FeedbackCall,
        run_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.store = store
        self.intent_call = intent_call
        self.strategy_call = strategy_call
        self.feedback_call = feedback_call
        self.run_id_factory = run_id_factory or (lambda: f"run-{uuid4().hex}")

    def _event(
        self,
        run_id: str,
        event_type: str,
        *,
        stage: str | None = None,
        payload: Mapping[str, Any] | None = None,
        now_ms: int | None = None,
    ) -> None:
        self.store.append_events(
            run_id,
            [
                {
                    "event_id": f"evt-{uuid4().hex}",
                    "type": event_type,
                    "stage": stage,
                    "payload": dict(payload or {}),
                    "created_at": utc_ms() if now_ms is None else int(now_ms),
                }
            ],
            now_ms=now_ms,
        )

    def create_run(self, scene_id: str, instruction: str, actor_id: str) -> dict[str, Any]:
        scenario = get_verified_scenario(scene_id)
        if not actor_id:
            raise ValueError("actor_id is required")
        normalized_instruction = instruction.strip()
        if not normalized_instruction:
            raise ValueError("instruction is required")
        run_id = self.run_id_factory()
        self.store.create_run(
            run_id,
            scene_id,
            normalized_instruction,
            metadata={"actor_id": actor_id, "scenario": scenario},
        )
        self._event(run_id, "RUN_CREATED", stage=RunState.CREATED.value)
        self.store.transition_run(run_id, RunState.PREPARING_SCENE)
        self._event(run_id, "SCENE_PREPARATION_QUEUED", stage=RunState.PREPARING_SCENE.value)
        self.store.enqueue_job(
            run_id,
            "ISAAC_PREPARE_AND_PERCEIVE",
            {
                "schema_version": "cloud-job.v1",
                "run_id": run_id,
                "case_id": scenario["case_id"],
                "scene_id": scenario["scene_id"],
                "scene_version": scenario["scene_version"],
                "object_id": scenario["object_id"],
                "destination_id": scenario["destination_id"],
                "initial_scene_poses": scenario.get("initial_scene_poses", {}),
            },
        )
        return public_run_snapshot(self.store.get_run(run_id))

    @staticmethod
    def _require_intent_evidence(task: dict[str, Any], run_id: str) -> None:
        assert_contract(task, "task.v1")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise EvidenceError("A did not return a non-empty task_id")
        if task_id == run_id:
            raise EvidenceError("A task_id must be independent from Cloud run_id")
        if task.get("status") != "READY":
            raise EvidenceError("A did not return one READY task for this run")
        trace = ((task.get("diagnostics") or {}).get("engine_trace") or {})
        if (
            not trace.get("llm_call_succeeded")
            or int(trace.get("llm_network_calls") or 0) < 1
            or not trace.get("llm_request_id")
            or trace.get("fallback_used")
        ):
            raise EvidenceError("A real DeepSeek request evidence is missing or fallback was used")

    @staticmethod
    def _require_strategy_evidence(
        strategy: dict[str, Any], task: dict[str, Any], perception: dict[str, Any]
    ) -> None:
        if strategy.get("blocked") or not strategy.get("success"):
            reasons = []

            for value in strategy.get("blocking_reasons") or []:
                if value:
                    reasons.append(str(value))

            for value in (strategy.get("validation") or {}).get("errors") or []:
                if value:
                    reasons.append(str(value))

            provider_error = strategy.get("provider_error")
            if provider_error:
                reasons.append(str(provider_error))

            reasons = list(dict.fromkeys(reasons))
            detail = "; ".join(reasons) if reasons else "unknown B failure"

            raise EvidenceError("B strategy blocked: " + detail)

        if strategy.get("code") is not None:
            raise EvidenceError("B strategy contains executable code")

        validation = validate_strategy(
            strategy,
            task=task,
            capabilities=DEFAULT_CAPABILITIES,
        )
        if not validation["passed"]:
            raise EvidenceError(
                "B strategy failed validation: "
                + "; ".join(validation["errors"])
            )

        provenance = strategy.get("provenance") or {}
        if (
            not provenance.get("request_id")
            or provenance.get("fallback")
            or provenance.get("source") in {None, "local_rules", "primitive_planner"}
        ):
            raise EvidenceError("B real CodeArts request evidence is missing or fallback was used")
        if strategy.get("input_perception_sha256") != document_digest(perception):
            raise EvidenceError("B strategy perception digest drift")

    def _fail_run(self, run_id: str, state: RunState, code: str, message: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if run["state"] not in {item.value for item in (RunState.SUCCEEDED, RunState.BLOCKED, RunState.FAILED, RunState.SAFE_STOPPED, RunState.CANCELLED)}:
            self.store.transition_run(
                run_id,
                state,
                error_code=code,
                error_message=message,
                audit_eligible=False,
            )
            self._event(run_id, code, stage=state.value, payload={"message": message})
        return public_run_snapshot(self.store.get_run(run_id))

    def _call_intent_with_retry(
        self,
        run: Mapping[str, Any],
        document: dict[str, Any],
        run_id: str,
    ) -> dict[str, Any]:
        retryable_error = (
            "A real DeepSeek request evidence is missing or fallback was used"
        )

        try:
            max_retries = int(
                os.getenv("DEEPSEEK_INTENT_MAX_RETRIES", "2")
            )
        except ValueError:
            max_retries = 2

        max_retries = max(0, min(max_retries, 3))

        try:
            backoff_s = float(
                os.getenv(
                    "DEEPSEEK_INTENT_RETRY_BACKOFF_S",
                    "0.2",
                )
            )
        except ValueError:
            backoff_s = 0.2

        backoff_s = max(0.0, min(backoff_s, 5.0))

        for attempt in range(max_retries + 1):
            task = self.intent_call(
                run["instruction"],
                deepcopy(document),
                run_id,
            )

            try:
                self._require_intent_evidence(task, run_id)
                return task
            except EvidenceError as exc:
                if (
                    str(exc) != retryable_error
                    or attempt >= max_retries
                ):
                    raise

                self._event(
                    run_id,
                    "A_RETRY",
                    stage=RunState.UNDERSTANDING.value,
                    payload={
                        "failed_attempt": attempt + 1,
                        "next_attempt": attempt + 2,
                    },
                )

                if backoff_s > 0:
                    time.sleep(backoff_s)

        raise EvidenceError(retryable_error)

    def handle_perception(self, run_id: str, document: dict[str, Any]) -> dict[str, Any]:
        try:
            run = self.store.get_run(run_id)
            if run["state"] != RunState.PREPARING_SCENE.value:
                raise EvidenceError(f"perception is not allowed in state {run['state']}")
            assert_contract(document, "perception.v1")
            provenance = document.get("provenance") or {}
            if provenance.get("backend") != "isaac_ground_truth":
                raise EvidenceError("perception is not proven as Isaac ground truth")

            self.store.transition_run(run_id, RunState.PERCEIVING)
            self._event(
                run_id,
                "ISAAC_PERCEPTION_ACCEPTED",
                stage=RunState.PERCEIVING.value,
                payload={"sha256": document_digest(document)},
            )
            self.store.transition_run(run_id, RunState.UNDERSTANDING)
            self._event(run_id, "A_STARTED", stage=RunState.UNDERSTANDING.value)
            task = self._call_intent_with_retry(
                run,
                document,
                run_id,
            )
            self._event(
                run_id,
                "A_COMPLETED",
                stage=RunState.UNDERSTANDING.value,
                payload={
                    "request_id": ((task.get("diagnostics") or {}).get("engine_trace") or {}).get("llm_request_id")
                },
            )

            self.store.transition_run(run_id, RunState.PLANNING)
            self._event(run_id, "B_STARTED", stage=RunState.PLANNING.value)
            strategy = self.strategy_call(deepcopy(task), deepcopy(document), run_id)
            self._require_strategy_evidence(strategy, task, document)
            digest = strategy_digest(strategy)
            self._event(
                run_id,
                "B_COMPLETED",
                stage=RunState.PLANNING.value,
                payload={"request_id": (strategy.get("provenance") or {}).get("request_id"), "strategy_sha256": digest},
            )

            self.store.transition_run(run_id, RunState.QUEUED_C)
            self.store.enqueue_job(
                run_id,
                "ISAAC_EXECUTE",
                {
                    "schema_version": "cloud-job.v1",
                    "run_id": run_id,
                    "case_id": run["scene_id"],
                    "task": task,
                    "perception": document,
                    "perception_sha256": document_digest(document),
                    "strategy": strategy,
                    "strategy_sha256": digest,
                    "capabilities": DEFAULT_CAPABILITIES,
                    "repair_attempt": 0,
                },
            )
            self._event(run_id, "C_EXECUTION_QUEUED", stage=RunState.QUEUED_C.value, payload={"strategy_sha256": digest})
            return public_run_snapshot(self.store.get_run(run_id))
        except Exception as exc:
            return self._fail_run(run_id, RunState.BLOCKED, "PROVIDER_OR_PERCEPTION_BLOCKED", str(exc))

    def handle_c_event(
        self,
        job_id: str,
        event: Mapping[str, Any],
        *,
        relay_id: str,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        run = self.store.get_run(job["run_id"])
        if event.get("type") == "EXECUTION_STARTED" and run["state"] == RunState.QUEUED_C.value:
            self.store.transition_run(job["run_id"], RunState.EXECUTING, now_ms=now_ms)
        self.store.append_events(
            job["run_id"],
            [dict(event)],
            job_id=job_id,
            relay_id=relay_id,
            now_ms=now_ms,
        )
        return public_run_snapshot(self.store.get_run(job["run_id"]))

    @staticmethod
    def _successful_action_after_stop(execution: Mapping[str, Any]) -> bool:
        stopped = False
        stop_tokens = ("STOP", "E_STOP", "COLLISION", "TIMEOUT", "LIMIT", "WORKSPACE")
        for step in execution.get("steps") or []:
            status = str(step.get("status") or "").upper()
            reason = str(step.get("reason") or step.get("stop_reason") or "").upper()
            if status in {"FAILED", "SAFE_STOP", "BLOCKED"} and any(token in reason for token in stop_tokens):
                stopped = True
            elif stopped and status == "SUCCESS":
                return True
        return False

    @staticmethod
    def _artifact_values(store: CloudStore, run_id: str) -> dict[str, Any]:
        return {item["artifact_name"]: item["value"] for item in store.list_artifacts(run_id)}

    @staticmethod
    def _require_c_evidence(job: dict[str, Any], artifacts: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        execution = artifacts.get("execution.json")
        final_pose = artifacts.get("final_pose.json")
        if not isinstance(execution, dict):
            raise EvidenceError("execution.json is missing")
        if not isinstance(final_pose, dict):
            raise EvidenceError("final_pose.json is missing")
        assert_contract(execution, "execution.v1")
        payload = job["payload"]
        strategy = payload["strategy"]
        task = payload["task"]
        run_id = job["run_id"]
        task_id = task.get("task_id")
        if execution.get("task_id") != task_id:
            raise EvidenceError("C execution task_id drift")
        if execution.get("input_strategy_sha256") != strategy_digest(strategy):
            raise EvidenceError("C executed strategy digest drift")
        if (execution.get("provenance") or {}).get("backend") != "isaac":
            raise EvidenceError("C execution is not Isaac-backed")
        if final_pose.get("run_id") != run_id or final_pose.get("task_id") != task_id:
            raise EvidenceError("final pose run/task identity drift")
        if (final_pose.get("provenance") or {}).get("backend") != "isaac":
            raise EvidenceError("final pose is not Isaac-backed")
        return execution, final_pose

    @staticmethod
    def _require_feedback_evidence(feedback: dict[str, Any], task_id: str) -> None:
        assert_contract(feedback, "feedback.v1")
        if feedback.get("task_id") != task_id:
            raise EvidenceError("D feedback task_id drift")
        provenance = feedback.get("provenance") or {}
        if (
            not provenance.get("request_id")
            or provenance.get("fallback")
            or provenance.get("source") in {None, "tracecoder_rules", "tracecoder_skipped"}
        ):
            raise EvidenceError("D real request evidence is missing or fallback was used")

    def handle_c_completion(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        run_id = job["run_id"]
        if job["job_type"] != "ISAAC_EXECUTE":
            return self._fail_run(run_id, RunState.FAILED, "WRONG_JOB_TYPE", job["job_type"])
        try:
            run = self.store.get_run(run_id)
            if run["state"] != RunState.EXECUTING.value:
                raise EvidenceError(f"C completion is not allowed in state {run['state']}")
            self.store.transition_run(run_id, RunState.VERIFYING)
            self._event(run_id, "C_EVIDENCE_VERIFYING", stage=RunState.VERIFYING.value)
            artifacts = self._artifact_values(self.store, run_id)
            execution, final_pose = self._require_c_evidence(job, artifacts)
            if self._successful_action_after_stop(execution):
                return self._fail_run(
                    run_id,
                    RunState.SAFE_STOPPED,
                    "ACTION_AFTER_TERMINAL_STOP",
                    "C reported a successful action after a terminal stop",
                )

            payload = job["payload"]
            feedback = self.feedback_call(
                {
                    "run_id": run_id,
                    "task": payload["task"],
                    "strategy": payload["strategy"],
                    "perception": payload["perception"],
                    "execution": execution,
                    "final_pose": final_pose,
                    "capabilities": payload.get("capabilities") or DEFAULT_CAPABILITIES,
                },
                run_id,
            )
            self._require_feedback_evidence(
                feedback,
                str(payload["task"]["task_id"]),
            )
            self._event(
                run_id,
                "D_COMPLETED",
                stage=RunState.VERIFYING.value,
                payload={"request_id": (feedback.get("provenance") or {}).get("request_id")},
            )

            if execution.get("status") == "SUCCEEDED" and final_pose.get("goal_reached") is True:
                self.store.transition_run(
                    run_id,
                    RunState.SUCCEEDED,
                    result={
                        "execution_status": "SUCCEEDED",
                        "goal_reached": True,
                        "strategy_sha256": payload["strategy_sha256"],
                        "world_id": (final_pose.get("provenance") or {}).get("world_id"),
                    },
                    audit_eligible=True,
                )
                self._event(run_id, "RUN_SUCCEEDED", stage=RunState.SUCCEEDED.value)
                return public_run_snapshot(self.store.get_run(run_id))

            if execution.get("status") == "SAFE_STOP":
                return self._fail_run(run_id, RunState.SAFE_STOPPED, "ISAAC_SAFE_STOP", "Isaac reported SAFE_STOP")

            patch = feedback.get("patch")
            if feedback.get("retryable") and isinstance(patch, dict) and int(payload.get("repair_attempt") or 0) < 1:
                validation = validate_patch(
                    patch,
                    current_strategy=payload["strategy"],
                    task=payload["task"],
                    capabilities=payload.get("capabilities") or DEFAULT_CAPABILITIES,
                )
                if not validation["passed"]:
                    raise EvidenceError("D patch failed validation: " + "; ".join(validation["errors"]))
                digest = strategy_digest(patch)
                self.store.transition_run(run_id, RunState.QUEUED_C, repair_attempts=1)
                self.store.enqueue_job(
                    run_id,
                    "ISAAC_EXECUTE",
                    {
                        **payload,
                        "strategy": patch,
                        "strategy_sha256": digest,
                        "repair_attempt": 1,
                        "original_execution_sha256": document_digest(execution),
                    },
                )
                self._event(run_id, "D_REPAIR_QUEUED", stage=RunState.QUEUED_C.value, payload={"strategy_sha256": digest})
                return public_run_snapshot(self.store.get_run(run_id))

            return self._fail_run(run_id, RunState.FAILED, "C_GOAL_NOT_REACHED", "C execution did not reach the verified goal")
        except Exception as exc:
            return self._fail_run(run_id, RunState.FAILED, "EVIDENCE_VERIFICATION_FAILED", str(exc))

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        self.store.transition_run(run_id, RunState.CANCELLED)
        self._event(run_id, "RUN_CANCELLED", stage=RunState.CANCELLED.value)
        return public_run_snapshot(self.store.get_run(run_id))
