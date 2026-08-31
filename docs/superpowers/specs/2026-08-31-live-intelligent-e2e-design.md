# Live Intelligent E2E Acceptance Design

## Goal

Run a fail-closed acceptance matrix in which every intelligent result is backed by fresh DeepSeek, CodeArts and Isaac Sim evidence. Missing or contradictory provenance makes a run ineligible for intelligent-mode statistics.

## Architecture

The local orchestrator owns immutable case/repeat/variant identities and invokes A and B before launching C. V0 uses the declared local-rule strategy. V1, V2 and V4 must use the exact validated CodeArts strategy written by the orchestrator. The remote Isaac entrypoint gains an explicit external-strategy mode; it may enrich only the task ID and failure-recovery policy selected by the variant, and must record the input strategy hash.

A separate evidence auditor validates required files, provider calls, network-call provenance, request identifiers, strategy hashes, Isaac ground-truth provenance, final poses and terminal stop semantics. Only audited runs enter the real-online aggregates. Mock, offline, API failure, safety interception and physical success remain disjoint populations.

## Variants

- `V0_RULE_BASELINE`: local rules, no CodeArts requirement, no diagnosis/repair.
- `V1_CODEARTS_POLICY`: real DeepSeek intent plus real CodeArts strategy, no diagnosis/repair.
- `V2_FULL_NO_D`: real DeepSeek intent plus real CodeArts strategy, diagnosis/repair disabled.
- `V4_FULL`: real DeepSeek intent, real CodeArts strategy and real DeepSeek feedback; at most one bounded recovery attempt.

V1 and V2 remain separately labelled even where their runtime behavior is intentionally equal; the report explains that V2 represents the full pipeline with D disabled.

## Evidence

Each run directory contains `input.json`, `api_calls.json`, `task.json`, `strategy.json`, `perception.json`, `execution.json`, `progress.jsonl`, `container.log`, `final_pose.json`, `remote_run.json`, `audit.json` and `result.json`. Secret values are never serialized. API failures preserve their error class and request metadata and stop before C.

## Safety and Recovery

Safety cases are executed in Isaac Sim when the fault is physical. Ambiguous/missing targets and malformed/missing/timeout CodeArts responses are fail-closed gate cases and are reported separately from physical task success. A terminal safety stop must have no successful action after its stop event. Recovery is successful only when the final physical pose satisfies the task after one bounded retry.

## Reports

The orchestrator writes `reports/live_intelligent_e2e_summary_YYYYMMDD.json` and `.md`. Historical reports are read-only and never used as fresh evidence. Formulas and population denominators are embedded in both reports.

## Verification

Unit tests cover variant definitions, external strategy preservation, hash auditing, evidence eligibility, metric separation and report naming. Integration tests exercise local orchestration with deterministic test providers only; live acceptance itself forbids those providers. Final verification includes unit tests, integration tests and `py_compile`.
