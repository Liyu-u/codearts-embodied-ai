# Cloud Web Real Closed Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the Huawei Cloud website into a persistent, evidence-backed A/B/C/D orchestrator that sends C jobs through an outbound campus relay to Isaac Sim and presents synchronized real progress with the existing HLS stream.

**Architecture:** Huawei Cloud owns the state machine, A/B/D calls, job queue, evidence and browser APIs. A Windows relay polls Huawei Cloud over HTTPS, reuses the proven SSH/SCP Isaac bridge and posts idempotent events/artifacts; the current RTSP→relay→Huawei Cloud livestream remains independent. The browser renders only server-sourced truth.

**Tech Stack:** Python 3.11 standard library (`http.server`, `sqlite3`, `urllib`, `threading`), existing adapters, Isaac Sim 6.0 Docker runner, SSH/SCP, vanilla HTML/CSS/JS, HLS.js, `unittest`, PowerShell, nginx/systemd.

**Spec:** `docs/superpowers/specs/2026-08-31-cloud-web-real-closed-loop-design.md`

## Global Constraints

- Never change, stop or restart the existing RTSP relay, MediaMTX publisher/path or `/live/` reverse proxy during task-system work.
- A/B/D required-mode failure or fallback terminates the run; Mock/fallback output is never presented as real.
- C success requires `provenance.backend=isaac`, matching hashes/task IDs and an Isaac-sourced final pose.
- The campus relay makes outbound HTTPS connections only and exposes no inbound port.
- Browser responses never contain model keys, SSH keys, relay tokens or publisher credentials.
- The relay accepts typed jobs only; no API transports arbitrary shell commands.
- Writes are idempotent by `event_id` or artifact name and scoped to one `run_id`.
- Preserve unrelated dirty-worktree changes; each commit stages only its task files.

## Planned File Boundaries

- `demo/cloud/types.py`: run/job enums and legal transitions.
- `demo/cloud/store.py`: SQLite runs, jobs, leases, events and artifacts.
- `demo/cloud/scenario_registry.py`: allowlisted Isaac-verified presets.
- `demo/cloud/security.py`: relay authentication, body limits and artifact allowlist.
- `demo/cloud/orchestrator.py`: A/B/D execution and C job lifecycle.
- `demo/cloud/service.py`: process composition and truthful health.
- `demo/server.py`: browser/relay HTTP routes and static serving.
- `tools/relay/client.py`: outbound HTTPS relay client.
- `tools/relay/isaac_job.py`: typed jobs using the proven SSH/Isaac bridge.
- `tools/cloud_relay_agent.py`: durable Windows polling agent.
- `demo/frontend/{index.html,app.js,styles.css}`: focused real-workflow UI.
- `deploy/huawei/*` and `tools/deploy_huawei_cloud.ps1`: staged deployment and rollback.

---

### Task 1: Run state machine and verified scene registry

**Files:** Create `demo/cloud/__init__.py`, `demo/cloud/types.py`, `demo/cloud/scenario_registry.py`; test `tests/unit/test_cloud_types.py`.

**Interfaces:** Produce `RunState`, `JobState`, `assert_transition(current, target)`, `public_run_snapshot(row)`, `list_verified_scenarios()`, `get_verified_scenario(scene_id)`.

- [ ] Write failing tests that accept `CREATED→PREPARING_SCENE→PERCEIVING→UNDERSTANDING→PLANNING→QUEUED_C→EXECUTING→VERIFYING→SUCCEEDED`, reject transitions out of terminal states and expose at least `multi-red-001`, `multi-green-001`, `multi-red-003` with `backend="isaac"`.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_types -v`; expect missing-module failure.
- [ ] Implement string enums, an explicit transition table and public snapshots that omit tokens/internal paths.
- [ ] Implement the three initial presets from `testdata/benchmark/real_isaac_supplement_v2.json`, including `case_id`, example instruction, scene version and `/live/isaac/index.m3u8`.
- [ ] Rerun the test; expect PASS.
- [ ] Commit only these files with `feat: define real cloud run states and scenes`.

### Task 2: Persistent transactional store

**Files:** Create `demo/cloud/store.py`, `tests/unit/test_cloud_store.py`; modify `.gitignore`.

**Interfaces:** Produce `CloudStore(path)` with `create_run`, `transition_run`, `enqueue_job`, `claim_job`, `renew_lease`, `append_events`, `save_artifact`, `get_run`, `list_events`, `recover_expired_jobs`.

- [ ] Write a failing test that inserts the same `event_id` twice and observes one event.
- [ ] Write a failing test that prevents two relays claiming the same job and safely requeues an expired lease.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_store -v`; expect import failure.
- [ ] Implement SQLite tables `runs`, `jobs`, `events`, `artifacts` with WAL, foreign keys, busy timeout, unique `event_id`, unique `(run_id, artifact_name)` and UTC timestamps.
- [ ] Implement claim/renew/write under `BEGIN IMMEDIATE`; reject writes by a relay that does not own the lease.
- [ ] Add `.cloud-runtime/` and `.relay-runtime/` to `.gitignore`.
- [ ] Rerun tests and commit with `feat: persist cloud runs jobs and events`.

### Task 3: Relay and artifact security

**Files:** Create `demo/cloud/security.py`, `tests/unit/test_cloud_security.py`, `deploy/huawei/closed-loop.env.example`.

**Interfaces:** Produce `require_bearer`, `read_json_body`, `validate_artifact`, `MAX_JSON_BYTES=2_000_000`, and `ALLOWED_ARTIFACTS`.

- [ ] Write failing tests for missing/wrong bearer tokens, oversized bodies, path traversal and non-allowlisted artifacts.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_security -v`; expect import failure.
- [ ] Implement constant-time token comparison with `hmac.compare_digest`; refuse startup with an empty production relay token.
- [ ] Allow only `perception.json`, `execution.json`, `final_pose.json`, `progress.jsonl`, and `container_log_summary.json`; validate known schemas through `assert_contract`.
- [ ] Document non-secret environment names, port `8876`, DB path, HLS URL and required A/B/D modes in the example file.
- [ ] Rerun tests and commit with `feat: secure relay and evidence boundaries`.

### Task 4: Cloud A/B/D orchestrator

**Files:** Create `demo/cloud/orchestrator.py`, `tests/unit/test_cloud_orchestrator.py`.

**Interfaces:** Produce `CloudOrchestrator.create_run(scene_id,instruction)`, `handle_perception(run_id,document)`, `handle_c_completion(run_id)`, `cancel_run(run_id)`; consume injected A/B/D callables, `document_digest`, `strategy_digest`, `audit_documents`.

- [ ] Write a failing injected-adapter happy-path test proving A and B consume the same perception and queue one typed C job with a strategy hash.
- [ ] Write fail-closed tests for missing request IDs, A/B/D fallback, invalid strategy, perception/strategy hash drift, non-Isaac C provenance, missing final pose and terminal-event violations.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_orchestrator -v`; expect failure.
- [ ] Implement `create_run` to validate scene/instruction and queue `ISAAC_PREPARE_AND_PERCEIVE` with only allowlisted identifiers.
- [ ] Implement A/B after validated perception; persist task/strategy and queue `ISAAC_EXECUTE` only after request-ID, fallback and contract gates.
- [ ] Implement C evidence verification and D required-mode verification; permit at most one validated repair attempt while preserving original evidence.
- [ ] Rerun tests and commit with `feat: orchestrate provider backed Isaac runs`.

### Task 5: Browser and relay HTTP APIs

**Files:** Create `demo/cloud/service.py`, `tests/e2e/test_cloud_http.py`; modify `demo/server.py`, `tests/e2e/test_demo_http.py`.

**Interfaces:** Browser endpoints: `GET /api/health`, `GET /api/scenarios`, `POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events`, `GET /api/livestream`. Relay endpoints: `register`, `heartbeat`, `jobs/claim`, `jobs/{id}/lease`, `events`, `artifacts`, `complete`.

- [ ] Write failing HTTP tests for asynchronous run creation (`202`), snapshot recovery, ordered `after_sequence` events, truthful split health and absence of secrets.
- [ ] Write failing relay tests for 401 without bearer token, typed claim, idempotent duplicate events, lease conflict and rejected artifact names.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.e2e.test_cloud_http -v`; expect failure.
- [ ] Implement `CloudService` composition after env loading plus a `configure_cloud_service(service)` test hook.
- [ ] Add structured status codes: 202 create, 400 validation, 401 auth, 409 lease/state, 413 body size. Change legacy `POST /api/run` to 410 with migration guidance; never execute Mock silently.
- [ ] Health must separately report cloud, relay, Isaac, providers and livestream; stale relay heartbeat means `degraded`.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.e2e.test_cloud_http tests.e2e.test_demo_http -v` and commit with `feat: expose cloud run and relay APIs`.

### Task 6: Outbound relay HTTPS client

**Files:** Create `tools/relay/__init__.py`, `tools/relay/client.py`, `tests/unit/test_cloud_relay_client.py`.

**Interfaces:** Produce `RelayClient(base_url,token,relay_id,timeout_s)` with `heartbeat`, `claim`, `renew`, `post_events`, `upload_artifact`, `complete`.

- [ ] Write failing in-process server tests for bearer auth, relay ID, UTF-8, timeout, idempotency key and retry classification.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_relay_client -v`; expect failure.
- [ ] Implement with `urllib.request`; never log the token. Retry connection resets and 502/503 at most twice; never retry 401/409. Reuse one idempotency key across a logical retry.
- [ ] Rerun and commit with `feat: add outbound campus relay client`.

### Task 7: Typed Isaac job runner

**Files:** Create `tools/relay/isaac_job.py`, `tests/unit/test_cloud_relay_isaac_job.py`; modify `tools/run_live_intelligent_bridge.py` only to extract reusable SSH/SCP helpers without changing its CLI.

**Interfaces:** Produce `IsaacJobRunner(config).run(job,emit)`; accept only `ISAAC_PREPARE_AND_PERCEIVE` and `ISAAC_EXECUTE`.

- [ ] Write failing tests for strict run/case/GPU/manifest validation, strategy digest equality and shell-safe remote names.
- [ ] Write fake-remote tests that convert `progress.jsonl` to ordered C events, collect only allowed evidence and always clean the task container on success/error/timeout.
- [ ] Assert cleanup never targets the livestream container or any name not derived from the current job.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_relay_isaac_job -v`; expect failure.
- [ ] Extract the proven Base64 SSH command, SCP, bundle and wait behavior; retain `BatchMode=yes`, explicit key and timeouts.
- [ ] Implement prepare/perceive and execute paths. Neither path may call A/B/D locally.
- [ ] Run the new suite plus `tests.unit.test_live_intelligent_bridge` and `tests.unit.test_real_isaac_experiment`; commit with `refactor: expose typed Isaac relay jobs`.

### Task 8: Durable Windows relay agent

**Files:** Create `tools/cloud_relay_agent.py`, `tools/start_cloud_relay.ps1`, `tests/e2e/test_cloud_disconnect_recovery.py`.

**Interfaces:** CLI requires cloud URL, relay ID/token env, SSH key, school server/port/user, GPU, polling interval and state directory.

- [ ] Write a failing restart test: persist active job/lease/last uploaded sequence; restart and reconcile the same job without duplicate execution/events.
- [ ] Write network-loss tests: spool events locally, resume ordered uploads, return explicit C failure on SSH loss and never discard terminal evidence.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.e2e.test_cloud_disconnect_recovery -v`; expect failure.
- [ ] Implement 10-second heartbeat, 20-second long claim, 20-second lease renewal, one job at a time, atomic JSON spool and cloud backoff capped at 30 seconds.
- [ ] Implement PowerShell validation/launcher; it must not set or modify livestream variables.
- [ ] Run recovery tests and `\.venv\Scripts\python.exe tools/cloud_relay_agent.py --help`; commit with `feat: run durable campus Isaac relay`.

### Task 9: Truthful real-workflow frontend

**Files:** Replace `demo/frontend/index.html`, `demo/frontend/app.js`; modify `demo/frontend/styles.css`; create `tests/e2e/test_cloud_frontend.py`.

**Interfaces:** Required DOM: `#systemHealth`, `#scenarioList`, `#instruction`, `#run`, `#livestream`, `[data-stage=A|B|C|D]`, `#currentAction`, `#eventTimeline`, `#resultSummary`.

- [ ] Write failing truthfulness tests requiring real selectors and `/api/runs`, while rejecting fixed IP/AUBO/joint/CPU/memory values, voice/2D/collision placeholders, fake dataset/model counts and fake robot command APIs.
- [ ] Write failing behavior tests for scene selection, non-empty instruction gate, run creation, session `run_id` recovery, `after_sequence` polling and terminal polling stop.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.e2e.test_cloud_frontend -v`; expect failure.
- [ ] Build one responsive page: four health chips, verified scenarios, task input, HLS video, A/B/C/D stages, C action progress, result/safety summary and event timeline.
- [ ] Render only API data; missing data displays “未连接/无数据”, never generated defaults or guessed percentages.
- [ ] Preserve existing `/api/livestream` and HLS.js behavior. Show LIVE/RECONNECTING/OFFLINE from media events; never label an SVG/cache as real-time.
- [ ] Run frontend/cloud HTTP tests and commit with `feat: show the real cloud Isaac workflow`.

### Task 10: Safe staged Huawei deployment

**Files:** Create `deploy/huawei/closed-loop-demo.service`, `deploy/huawei/nginx-closed-loop.conf`, `tools/deploy_huawei_cloud.ps1`, `tests/unit/test_huawei_deployment_assets.py`.

**Interfaces:** Candidate service binds `127.0.0.1:8876`; deployment CLI accepts server, user, SSH key, remote root, health URL, HLS URL and dry-run.

- [ ] Write failing tests that require non-root systemd, protected env file, restart-on-failure, `/api/` proxy, no `/live/` override, `nginx -t`, HLS before/after checks and rollback.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_huawei_deployment_assets -v`; expect failure.
- [ ] Implement systemd/nginx assets while leaving the current `/live/` configuration authoritative.
- [ ] Implement versioned upload including `contracts`, remote `py_compile`/smoke, candidate start, atomic `current` switch and previous-release rollback. The script must contain no MediaMTX/livestream stop command.
- [ ] Run tests and PowerShell dry run; commit with `feat: stage Huawei Cloud deployment safely`.

### Task 11: Operations and recording documentation

**Files:** Create `docs/华为云真实闭环部署与录制手册.md`, `tests/unit/test_cloud_operations_docs.py`; modify `README.md`.

- [ ] Write a failing documentation contract test requiring exact browser/relay endpoints, relay launcher, HLS pre/post check, systemd/nginx checks, evidence files and rollback commands; reject credential-looking values.
- [ ] Run `\.venv\Scripts\python.exe -m unittest tests.unit.test_cloud_operations_docs -v`; expect failure.
- [ ] Document secret placement, staged cloud deploy, relay start, unchanged RTSP/OBS start, health interpretation, one real run, evidence checks, recording order, restart recovery and rollback.
- [ ] Update README so the real cloud flow is primary and the synchronous Mock demo is explicitly developer-only.
- [ ] Rerun and commit with `docs: operate and record the real cloud loop`.

### Task 12: Local integration and full regression

**Files:** Modify only newly introduced files if a verified defect is found.

- [ ] Run `\.venv\Scripts\python.exe -m unittest discover -v`; require zero failures.
- [ ] Start an isolated cloud server on port 8877 with a temporary DB and test relay token; verify truthful relay-offline and independent livestream status.
- [ ] Run a fake-Isaac relay end to end; require ordered A/B/C/D events, allowed artifacts and terminal `SUCCEEDED`.
- [ ] Kill/restart the fake relay during a second run; require recovery without duplicate events/execution.
- [ ] Probe the configured HLS manifest before and after; any regression blocks progress.
- [ ] If a defect appears, add a failing regression test, implement one fix, rerun full tests and commit only that fix; otherwise create no empty commit.

### Task 13: Staged real deployment and campus connection

**Files:** Runtime only; never commit secrets.

- [ ] Capture current website headers, health, HLS manifest/segment advancement, service names and nginx checksum.
- [ ] Deploy candidate cloud service to the staged port; keep the public root unchanged.
- [ ] Start the campus relay with the operator-provided HTTPS token, existing school SSH key, `10.16.0.40:5122`, `stu_01` and an available GPU.
- [ ] Verify relay heartbeat separately reports SSH, assets, GPU and Isaac capability.
- [ ] Verify the existing RTSP relay/OBS publisher remains running and staged frontend plays HLS. On regression, roll back web/API only.
- [ ] Expose a temporary test URL only after all gates pass.

### Task 14: Real acceptance and public switch

**Files:** Runtime evidence under ignored `reports/cloud-live-<timestamp>/`.

- [ ] Run three normal presets three times each. Every run requires A/B/D real request IDs without fallback, C Isaac provenance, matching hashes, correct actions/final pose, audit eligibility and matching UI events.
- [ ] Run one ambiguous-target, one invalid-strategy and one collision/safety case; require boundary block or safe stop with no successful action after stop.
- [ ] Run one bounded D recovery; require a real D request ID, one validated patch, no more than one visible retry and preserved original evidence.
- [ ] Refresh the browser during execution and restart the relay between jobs; require state recovery and no duplicate C execution.
- [ ] Record a known object movement; require selected scenario, C events and visible livestream action to agree, with continuous HLS playback.
- [ ] Produce GO only when every required run is eligible, all safety gates pass and HLS remains continuous. Otherwise retain the old public release and report the failed gate.
- [ ] Back up and atomically switch the public web/API release, then execute one fresh public real run. Roll back web/API immediately on failure without touching livestream.

## Final Verification

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
git diff --check
```

Unit tests alone cannot authorize the public switch. Final approval requires fresh real A/B/C/D artifacts, Isaac final-pose evidence and a live HLS continuity observation.
