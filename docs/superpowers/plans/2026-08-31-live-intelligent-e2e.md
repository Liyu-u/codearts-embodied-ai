# Live Intelligent E2E Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, evidence-audited runner for the requested real DeepSeek + CodeArts + Isaac Sim acceptance matrix.

**Architecture:** Preserve externally generated strategies through the remote boundary, audit every real-provider claim, then aggregate only eligible runs into disjoint metrics. Existing historical reports remain untouched.

**Tech Stack:** Python 3.11, unittest, PowerShell, OpenSSH, Docker, Isaac Sim 6.0.

**Spec:** `docs/superpowers/specs/2026-08-31-live-intelligent-e2e-design.md`

## Global Constraints

- No Mock or pre-generated strategy may count as a real intelligent run.
- Secrets are read only from environment-backed local configuration and never logged.
- Missing real API or Isaac evidence stops or disqualifies the run.
- Existing historical failure reports are immutable.

---

### Task 1: Variant and external-strategy contract

**Files:**
- Modify: `tools/real_isaac_experiment.py`
- Modify: `tools/run_ground_truth_executor_acceptance.py`
- Test: `tests/unit/test_real_isaac_experiment.py`

- [ ] Add failing tests for V1 and exact external strategy preservation.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Add V1 and an explicit external-strategy selection function.
- [ ] Run the focused tests to green.

### Task 2: Evidence auditor and metrics

**Files:**
- Create: `tools/live_intelligent_e2e.py`
- Create: `tests/unit/test_live_intelligent_e2e.py`

- [ ] Add failing tests for required evidence, provider provenance, strategy hashes, stop semantics and disjoint metrics.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement the minimal pure audit and aggregation functions.
- [ ] Run the focused tests to green.

### Task 3: Live orchestration and reports

**Files:**
- Modify: `tools/live_intelligent_e2e.py`
- Modify: `tools/run_remote_ground_truth_acceptance_final.ps1`
- Test: `tests/unit/test_live_intelligent_e2e.py`

- [ ] Add failing command/manifest/report tests.
- [ ] Run the focused tests and confirm the expected failures.
- [ ] Implement preflight, A/B, remote C, optional D, resumable matrix and report writers.
- [ ] Run the focused tests to green.

### Task 4: Verification and live execution

**Files:**
- Create: `reports/live_intelligent_e2e_summary_20260831.json`
- Create: `reports/live_intelligent_e2e_summary_20260831.md`

- [ ] Run focused unit and integration tests.
- [ ] Run `py_compile` for changed Python files.
- [ ] Run one fresh live canary and audit all evidence.
- [ ] Run the full matrix only after the canary passes and GPU contention clears.
- [ ] Generate the final reports and state completion only if every required real evidence gate passes.
