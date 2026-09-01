# Final Live Cloud Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Deliver a persistent Huawei Cloud A/B/D orchestrator, durable Windows relay, single-world Persistent Live Isaac Worker, truthful browser UI, and rollback-safe candidate deployment without changing the working WebRTC/OBS Livestream architecture.

**Architecture:** Huawei Cloud owns durable run state, provider gates, browser/relay APIs, evidence, and authorization. A Windows relay polls outbound and exchanges typed files with one long-running Isaac Kit process whose World is also the WebRTC source. Candidate deployment uses port 8876 and versioned releases; Livestream is operator-owned and becomes mandatory only at the final real E2E gate.

**Tech Stack:** Python 3.11, sqlite3, http.server, urllib, threading, Isaac Sim 6.0, OpenSSH/SCP, PowerShell, vanilla HTML/CSS/JavaScript, HLS.js, unittest, pytest, nginx, systemd.

**Spec:** docs/superpowers/specs/2026-09-01-final-live-cloud-closed-loop-design.md

## Global Constraints

- Run local Python and tests through conda run -n huawei; use isaacsim only for explicit local Isaac probes.
- Preserve the dirty original checkout; all work remains on feat/final-live-cloud-closed-loop in the isolated worktree.
- Production A, B, and D are required and fail closed on timeout, fallback, missing request ID, or invalid schema.
- strategy.code is null; C executes only allowlisted primitives and never arbitrary code.
- The executing World and the WebRTC World are the same SimulationApp/Kit process.
- Do not stop or restart MediaMTX, OBS, WebRTC Client, livestream containers, or /live/.
- Livestream absence is not a failure before the final real E2E gate.
- Public writes require authenticated roles; Relay Bearer Token is separate from browser sessions.
- Candidate binds 127.0.0.1:8876; production 8765 remains untouched until all gates pass.
- Never log, commit, upload to browser, or place in fixtures any real secret.

## File Boundaries

- demo/cloud/types.py: run/job enums, transitions, public snapshots.
- demo/cloud/store.py: SQLite transactions, leases, events, artifacts, relay sessions.
- demo/cloud/scenario_registry.py: verified Isaac scene allowlist.
- demo/cloud/orchestrator.py: A/B/D gates and C job lifecycle.
- demo/cloud/service.py: composition, health, browser and relay use cases.
- demo/cloud/auth.py and credentials.py: role sessions and encrypted credential storage primitives.
- demo/server.py: HTTP routing and static serving only.
- tools/relay/client.py: outbound authenticated Cloud client.
- tools/relay/runtime_protocol.py: typed runtime jobs, safe names, digests, atomic files.
- tools/relay/isaac_job.py: SSH/SCP exchange with the persistent worker.
- tools/cloud_relay_agent.py: durable polling, lease renewal, spool and restart recovery.
- tools/live_worker/runtime.py: inbox claim, duplicate handling, events and results.
- tools/run_live_isaac_worker.py: single persistent Kit/World entrypoint.
- demo/frontend/: truthful run UI and same-origin HLS playback.
- deploy/huawei/ and tools/deploy_huawei_cloud.ps1: candidate, cutover and rollback.

---

### Task 1: Latest-main baseline and FINAL GAP AUDIT

**Files:**
- Create: docs/final_deployment_gap_analysis.md
- Read: docs/superpowers/plans/2026-08-31-cloud-web-real-closed-loop.md
- Read: docs/superpowers/plans/2026-08-31-live-intelligent-e2e.md

**Interfaces:**
- Produces: a DONE/PARTIAL/TODO/BROKEN table for every earlier task and a pre-existing-failure baseline.

- [ ] Record branch, HEAD, Conda versions, git status, relevant tree and design/plan task inventory.
- [ ] Run conda run -n huawei python -m unittest discover -v and retain complete exit status plus failing test names.
- [ ] Run conda run -n huawei python -m pytest -q; if pytest is not installed, record that exact pre-existing environment blocker without installing globally.
- [ ] Run targeted import smoke for demo.server, demo.cloud.security, tools.live_intelligent_e2e and tools.run_live_intelligent_bridge.
- [ ] Write the gap document with evidence, files to change, explicit non-goals, Livestream exemption before final E2E, and classification of every existing Cloud plan task.
- [ ] Run git diff --check and commit only the gap document with docs: audit final deployment gaps.

### Task 2: Run states and verified scene registry

**Files:**
- Create: demo/cloud/__init__.py
- Create: demo/cloud/types.py
- Create: demo/cloud/scenario_registry.py
- Create: tests/unit/test_cloud_types.py

**Interfaces:**
- Produces: RunState, JobState, TERMINAL_RUN_STATES, assert_transition(current, target), public_run_snapshot(row), list_verified_scenarios(), get_verified_scenario(scene_id).

- [ ] Write tests that accept CREATED through SUCCEEDED, include QUEUED_C, and reject every transition out of a terminal state.
- [ ] Write tests that require multi-red-001, multi-green-001 and multi-red-003 with backend isaac, scene version, capabilities and relative livestream URL.
- [ ] Run conda run -n huawei python -m unittest tests.unit.test_cloud_types -v and confirm missing-module failure.
- [ ] Implement string enums and an explicit transition map; public snapshots omit token, credential, database path and remote path fields.
- [ ] Implement immutable scene records derived from testdata/benchmark/real_isaac_supplement_v2.json.
- [ ] Rerun the focused tests and commit with feat: define cloud run states and scenes.

### Task 3: Transactional CloudStore

**Files:**
- Create: demo/cloud/store.py
- Create: tests/unit/test_cloud_store.py
- Modify: .gitignore

**Interfaces:**
- Produces: CloudStore(path, busy_timeout_ms=5000) with create_run, transition_run, enqueue_job, claim_job, renew_lease, append_events, save_artifact, complete_job, get_run, get_job, list_events, recover_expired_jobs and update_relay_session.

- [ ] Write tests for WAL/foreign_keys, duplicate event_id, duplicate artifact name, terminal immutability, lease conflict, lease owner checks, expired lease recovery and process restart.
- [ ] Run the focused suite and confirm import failure.
- [ ] Implement schema creation and one connection per operation with PRAGMA journal_mode=WAL, foreign_keys=ON and busy_timeout.
- [ ] Use BEGIN IMMEDIATE for claim, renew, complete and expired recovery; represent timestamps as UTC milliseconds.
- [ ] Add .cloud-runtime/ and .relay-runtime/ to .gitignore.
- [ ] Rerun focused tests plus tests.unit.test_cloud_security and commit with feat: persist cloud runs jobs and evidence.

### Task 4: Browser roles, sessions and credential boundaries

**Files:**
- Modify: demo/cloud/auth.py
- Modify: demo/cloud/credentials.py
- Create: tests/unit/test_cloud_authz.py

**Interfaces:**
- Produces: Role(viewer, operator, admin), SessionRecord, authorize(role, action), issue_session, validate_session, revoke_session and CredentialCipher-backed encrypted values.

- [ ] Write tests proving viewer is read-only, operator can create runs, admin can update configuration, Relay tokens cannot authenticate browser routes, and expired/revoked sessions fail.
- [ ] Write tests proving public configuration returns configured=true without raw encrypted or decrypted values.
- [ ] Run the focused tests and confirm missing authorization interfaces.
- [ ] Extend existing password/session primitives without changing their verified PBKDF2 and constant-time comparisons.
- [ ] Keep session tokens hashed at rest and set cookie policy metadata to HttpOnly, SameSite=Strict and Secure when HTTPS is enabled.
- [ ] Rerun auth/credentials/security tests and commit with feat: authorize cloud operators safely.

### Task 5: Required-provider CloudOrchestrator

**Files:**
- Create: demo/cloud/orchestrator.py
- Create: tests/unit/test_cloud_orchestrator.py

**Interfaces:**
- Consumes: CloudStore, scene registry, injected intent_call, strategy_call, feedback_call, contract audit and document digests.
- Produces: create_run(scene_id, instruction, actor_id), handle_perception(run_id, document), handle_c_event(job_id, event), handle_c_completion(job_id), cancel_run(run_id).

- [ ] Write an injected-adapter happy-path test proving A and B consume the same perception and only one typed C job is queued with a strategy digest.
- [ ] Write fail-closed tests for A/B/D fallback, missing request IDs, invalid strategy, non-null strategy.code, digest drift, non-Isaac execution, missing final_pose and action after terminal stop.
- [ ] Run the focused suite and confirm import failure.
- [ ] Implement prepare/perceive job creation, A/B gates, C execute job creation, evidence audit and D verification with at most one validated repair.
- [ ] Persist every stage transition and event before dispatching the next side effect; preserve original failure evidence on repair.
- [ ] Rerun tests and commit with feat: orchestrate required providers and Isaac jobs.

### Task 6: CloudService and HTTP APIs

**Files:**
- Create: demo/cloud/service.py
- Modify: demo/server.py
- Create: tests/e2e/test_cloud_http.py
- Modify: tests/e2e/test_demo_http.py

**Interfaces:**
- Produces browser and relay endpoints from the spec, structured status codes, after_sequence event reads and truthful split health.

- [ ] Write HTTP tests for 202 run creation, snapshot recovery, ordered event polling, 410 legacy /api/run, 400 validation, 401 auth, 409 lease/state, 413 body size and secret-free responses.
- [ ] Write Relay API tests for register, heartbeat, typed claim, renew, duplicate events, allowlisted artifacts and complete.
- [ ] Run focused HTTP tests and confirm failures.
- [ ] Compose Store and Orchestrator only after environment loading; remove any configure_cloud_service call-before-import ordering defect.
- [ ] Keep demo.server routing thin and preserve existing developer Mock routes only where explicitly labelled developer-only.
- [ ] Report Cloud, Relay, Isaac, A/B/D and Livestream independently; stale heartbeat is degraded, not online.
- [ ] Rerun HTTP and existing demo suites and commit with feat: expose persistent cloud and relay APIs.

### Task 7: Outbound Relay client

**Files:**
- Create: tools/relay/__init__.py
- Create: tools/relay/client.py
- Create: tests/unit/test_cloud_relay_client.py

**Interfaces:**
- Produces RelayClient(base_url, token, relay_id, timeout_s) with register, heartbeat, claim, renew, post_events, upload_artifact and complete.

- [ ] Write in-process server tests for Bearer auth, UTF-8 JSON, timeout, idempotency key reuse, retryable 502/503/reset and non-retryable 401/409.
- [ ] Run focused tests and confirm import failure.
- [ ] Implement urllib.request calls without logging token or response secrets; cap transport retries at two.
- [ ] Return typed response dictionaries and explicit RelayHTTPError(status, retryable, message).
- [ ] Rerun focused tests and commit with feat: add outbound cloud relay client.

### Task 8: Runtime protocol and persistent-worker job runner

**Files:**
- Create: tools/relay/runtime_protocol.py
- Create: tools/relay/isaac_job.py
- Modify: tools/run_live_intelligent_bridge.py
- Create: tests/unit/test_live_runtime_protocol.py
- Create: tests/unit/test_cloud_relay_isaac_job.py

**Interfaces:**
- Produces validate_run_id, validate_job, atomic_write_json, atomic_append_event, RuntimeLayout and IsaacJobRunner(config).run(job, emit).

- [ ] Write tests for safe run_id, path traversal, known job kinds, exact strategy digest, task/run continuity and primitive allowlist.
- [ ] Write fake-remote tests for atomic inbox upload, ordered event collection, allowlisted result download, timeout and no broad cleanup.
- [ ] Run focused tests and confirm failures.
- [ ] Extract reusable Remote/bundle/wait behavior from the existing bridge without changing its CLI or historical acceptance behavior.
- [ ] Implement only PREPARE_AND_PERCEIVE and ISAAC_EXECUTE jobs; neither path calls A/B/D locally.
- [ ] Rerun new and existing bridge/experiment tests and commit with refactor: expose persistent Isaac runtime jobs.

### Task 9: Durable Windows Relay Agent

**Files:**
- Create: tools/cloud_relay_agent.py
- Create: tools/start_cloud_relay.ps1
- Create: tests/e2e/test_cloud_disconnect_recovery.py

**Interfaces:**
- Produces RelayStateStore, EventSpool and CloudRelayAgent.run_once/run_forever.

- [ ] Write tests for active job persistence, sequence persistence, ordered spool replay, restart reconciliation, duplicate claim, lost network, lost SSH and lease expiry.
- [ ] Run focused tests and confirm failures.
- [ ] Implement atomic local state, 10-second heartbeat, 20-second claim, 20-second renewal, one job thread and backoff capped at 30 seconds.
- [ ] Complete Cloud only after all terminal evidence uploads succeed; never discard a terminal spool record.
- [ ] Implement a PowerShell launcher that reads token and SSH key from environment/config and never changes Livestream settings.
- [ ] Rerun recovery tests and CLI --help smoke; commit with feat: run durable Windows Isaac relay.

### Task 10: Persistent Live Isaac Worker core

**Files:**
- Create: tools/live_worker/__init__.py
- Create: tools/live_worker/runtime.py
- Create: tests/unit/test_live_worker_runtime.py

**Interfaces:**
- Produces LiveRuntimeWorker(layout, execute, prepare, reset), claim_next_job(), process_once() and recover_active_job().

- [ ] Write pure filesystem tests for atomic claim, one active job, duplicate completion, invalid digest, crash recovery, event sequence and atomic result files.
- [ ] Run focused tests and confirm import failure.
- [ ] Implement runtime logic with injected prepare/execute/reset callables so unit tests do not start Isaac or require Livestream.
- [ ] Reject symlinks, traversal, unknown files, unknown job kinds and non-null strategy.code before calling C.
- [ ] Persist worker instance ID and world/session ID in every event and result.
- [ ] Rerun focused tests and commit with feat: process persistent live Isaac jobs safely.

### Task 11: Single-Kit Isaac integration

**Files:**
- Create: tools/run_live_isaac_worker.py
- Modify only if required: modules/executor/isaac_backend.py
- Modify only if required: modules/executor/isaac_driver.py
- Create: tests/unit/test_live_isaac_worker_entrypoint.py

**Interfaces:**
- Produces build_live_world(config), run_worker_loop(app, world, runtime_worker) and evidence carrying kit_instance_id/world_id.

- [ ] Write import/static tests proving one SimulationApp construction, no per-job app.close, no subprocess/docker launch per task, fixed runtime root and reusable existing Driver/Backend.
- [ ] Run focused tests and confirm entrypoint absence.
- [ ] Implement standalone SimulationApp plus WebRTC extension using Isaac Sim 6.0 APIs already present in the image; initialize Stage/World/Franka once.
- [ ] Inject prepare/perceive/execute/reset callbacks into LiveRuntimeWorker and keep app.update/physics stepping in the same process.
- [ ] Emit kit_instance_id/world_id into perception, progress, execution and final_pose evidence.
- [ ] Run static/unit tests locally; defer real Kit/WebRTC validation to campus canary and commit with feat: run one persistent WebRTC Isaac world.

### Task 12: Truthful browser UI and HLS reliability

**Files:**
- Modify: demo/frontend/index.html
- Modify: demo/frontend/app.js
- Modify: demo/frontend/styles.css
- Create: demo/frontend/vendor/hls.min.js
- Create: tests/e2e/test_cloud_frontend.py

**Interfaces:**
- Required DOM: systemHealth, scenarioList, instruction, run, livestream, A/B/C/D stage nodes, currentAction, eventTimeline and resultSummary.

- [ ] Write source tests requiring /api/runs, run_id session recovery, after_sequence polling and relative /live/isaac/index.m3u8.
- [ ] Reject fixed CPU/memory/IP/robot/joint/load/safety values, fake counts, developer jargon and non-functional controls.
- [ ] Run focused tests and confirm failures.
- [ ] Implement API-only rendering, explicit no-data states, terminal polling stop and refresh recovery.
- [ ] Vendor the pinned HLS.js distribution with its license; use native HLS fallback and media-progress-based LIVE/CONNECTING/RECONNECTING/OFFLINE.
- [ ] Rerun frontend/cloud HTTP tests and commit with feat: show truthful cloud Isaac execution.

### Task 13: Candidate deployment and rollback assets

**Files:**
- Create: deploy/huawei/closed-loop-demo.service
- Create: deploy/huawei/nginx-closed-loop.conf
- Modify: deploy/huawei/closed-loop.env.example
- Create: tools/deploy_huawei_cloud.ps1
- Create: tests/unit/test_huawei_deployment_assets.py

**Interfaces:**
- Candidate binds 127.0.0.1:8876; deployment supports Validate, DeployCandidate, CheckCandidate, Cutover and Rollback modes.

- [ ] Write tests requiring non-root service, EnvironmentFile, restart-on-failure, versioned release/current/previous links, /api proxy, no /live override, nginx -t, HLS pre/post probe and rollback.
- [ ] Run focused tests and confirm missing assets.
- [ ] Implement source bundle upload without local secrets/reports, remote venv reuse, py_compile, candidate service and health gate.
- [ ] Implement atomic nginx/static switch only after explicit Cutover mode and keep previous config/release for rollback.
- [ ] Staticaly reject commands that stop/restart MediaMTX, OBS, livestream or modify /live/.
- [ ] Run tests and PowerShell -Mode Validate dry run; commit with feat: deploy Huawei candidate with rollback.

### Task 14: Operations documentation and full local regression

**Files:**
- Create: docs/华为云真实闭环部署与Livestream运维手册.md
- Create: tests/unit/test_cloud_operations_docs.py
- Modify: README.md

**Interfaces:**
- Produces exact local, Huawei candidate, Relay, school worker, HLS check, cutover and rollback commands without secrets.

- [ ] Write documentation contract tests for endpoints, environment variable names, service/ports, evidence, same-world IDs, Livestream manual gate and rollback.
- [ ] Write secret-pattern tests that reject credential-looking examples.
- [ ] Run focused tests and confirm failure.
- [ ] Write the operations manual and mark the synchronous Mock demo developer-only in README.
- [ ] Run all focused suites, conda run -n huawei python -m unittest discover -v, conda run -n huawei python -m pytest -q, py_compile for changed Python and git diff --check.
- [ ] Record results and commit with docs: operate final live cloud loop.

### Task 15: Candidate, manual Livestream gate and real E2E

**Files:**
- Runtime evidence only under ignored reports/cloud-live-<timestamp>/.

**Interfaces:**
- Produces candidate health, Relay/Worker evidence, real A/B/C/D evidence, HLS continuity observations, cutover or rollback decision.

- [ ] Read-only capture current Huawei service, nginx checksum, public headers and /live routing without printing secrets.
- [ ] Deploy 8876 candidate and validate Cloud/Relay/fake-worker while leaving 8765 and /live unchanged.
- [ ] Install/start the persistent school Worker and start Windows Relay; verify matching worker/world IDs without requiring HLS.
- [ ] Stop and display exactly “现在需要开启 Livestream”; wait for operator confirmation.
- [ ] After confirmation, verify WebRTC Client/OBS/HLS, run one canary and prove the moving robot and evidence share kit_instance_id/world_id.
- [ ] Run three normal scenes three times plus ambiguity, invalid strategy, safety stop, bounded repair, disconnect/restart and refresh recovery.
- [ ] Cut over only on complete GO evidence; run one fresh public task. On any gate failure, retain or restore the old web/API and do not touch Livestream.
- [ ] Produce final delivery report with files, diff, tests, commands, HTTPS status, evidence, commit SHA, unresolved blockers and rollback status.

## Plan Self-Review Result

- Every design section maps to at least one task.
- Cloud, Relay, Worker, frontend, deployment and real acceptance have separate test cycles.
- Livestream is exempt before Task 15 and mandatory only after the explicit operator gate.
- The executing Isaac World and WebRTC World are linked by persisted kit_instance_id/world_id evidence.
- No task transports arbitrary code, embeds secrets, opens new public campus ports or owns Livestream lifecycle.
