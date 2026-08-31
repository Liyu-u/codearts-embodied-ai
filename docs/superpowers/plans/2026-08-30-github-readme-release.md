# GitHub README Release Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the repository README with verified experiment results and publish the intended current code, tests, documentation, and experiment protocol changes to `origin/main` without committing secrets or generated presentation artifacts.

**Architecture:** Keep runtime code and test changes already present in the working tree intact. Refresh only the result and reproducibility sections of `README.md` using the verified 30-case × 5-repeat V0/V2/V4 report, the separate V1 online result, and the Isaac Sim camera evidence. Stage source, tests, protocol, and operational documentation as one coherent release commit; leave generated PPTX, rendered slides, and ignored runtime reports out of Git.

**Tech Stack:** Python 3 test suite with `unittest`, PowerShell remote runners, JSON experiment manifests/reports, Markdown documentation, Git/GitHub `origin/main`.

**Spec:** `C:/Users/14810/Desktop/最终验收.md`, supplemented by `testdata/benchmark/experiment_protocol_v1.json` and `testdata/benchmark/experiment_run_config_v1.json`.

## Global Constraints

- Use the verified main comparison protocol: 30 cases, 5 repeats, identical Mock backend, same seeds/scene/fault injection, and variants V0/V2/V4.
- Report V1 CodeArts online results separately because it has 3 repeats and is not part of the strict V0/V2/V4 main comparison.
- Keep Isaac Sim results as separate physical-execution evidence; do not merge them with the 30-case Mock success rate.
- Do not commit `.env`, `codearts.env`, `tracecoder_llm.env`, API keys, SSH credentials, raw logs, ignored `reports/` outputs, PPTX files, slide renders, or generated project workspaces.
- Preserve unrelated existing user changes by staging only source, tests, documentation, manifests, and operational scripts intended for the code release.

### Task 1: Audit the release scope and verified evidence

**Files:**
- Read: `C:/Users/14810/Desktop/最终验收.md`
- Read: `README.md`
- Read: `reports/final_full_comparison_v0_v2_v4_20260829.json`
- Read: `reports/final_ppt_experiment_summary_20260829.json`
- Read: `reports/final-isaac-v4-20260829/execution.json`
- Read: `reports/final-isaac-v4-20260829/remote_run.json`
- Read: `.gitignore`

**Interfaces:**
- Consumes the attached acceptance wording and machine-readable experiment reports.
- Produces a staging allowlist that separates publishable source/protocol files from generated artifacts.

- [ ] Confirm V0/V2/V4 are 150 runs each and record the verified metrics: overall pass, valid-task success, recoverable-failure recovery, safe-stop correctness, unsafe execution, false success, and case stability.
- [ ] Confirm the separate V1 online sample count and repetition count.
- [ ] Confirm Isaac Sim evidence is one successful RGB-D/physical execution and record its source and boundary.
- [ ] Confirm no secret/config files are included in the staging allowlist.

### Task 2: Update the README with the verified release status

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes the verified reports from Task 1 and the protocol definitions in `testdata/benchmark/experiment_protocol_v1.json`.
- Produces a README that states the current system boundary, exact V0/V1/V2/V4 results, separate Isaac evidence, reproduction commands, and remaining limitations.

- [ ] Replace stale status wording that says the full test suite is not green with the current verified result: `290 tests`, `OK`, `1 skipped`.
- [ ] Add a clearly labeled main comparison table for V0/V2/V4 using 30 cases × 5 repeats and the verified values `84.62% → 100%` valid-task success, `33.33% → 100%` recoverable-failure recovery, `100%` safe-stop correctness, `0%` unsafe execution, `0%` false success, and `100%` case stability.
- [ ] Add V1 as a separately labeled 30 cases × 3 repeats online auxiliary result, without combining it with the strict main comparison.
- [ ] Add the V4 Isaac Sim evidence as a separate one-task result and explicitly state that it is not a 30-task Isaac aggregate and does not represent physical robot/camera HIL.
- [ ] Update the test and reproduction commands to use `python -m unittest discover -s tests -t . -q` and the established experiment/protocol commands.
- [ ] Keep credentials, personal paths, raw logs, and generated report data out of the README.

### Task 3: Verify documentation and release contents

**Files:**
- Test: `tests/`
- Check: `README.md`
- Check: all staged source/test/doc/manifest files

**Interfaces:**
- Consumes the README update and staging allowlist.
- Produces verified, reviewable release content ready for a Git commit.

- [ ] Run `python -m unittest discover -s tests -t . -q` and record the complete result.
- [ ] Run `python tools/validate_experiment_protocol.py` against the frozen experiment protocol/config if the command interface requires explicit paths; record exit code and output.
- [ ] Run `git diff --check` and inspect `git diff --cached --stat` after staging.
- [ ] Search staged content for secret filenames, API-key assignments, SSH private-key paths, and generated PPTX/report artifacts; remove any accidental matches before commit.

### Task 4: Commit and push the current code release

**Files:**
- Stage: tracked source, tests, README, docs, `experiments/`, protocol/config manifests, and operational scripts from the verified allowlist.
- Exclude: `projects/`, PPTX files, slide-render directories, `.inspect.ndjson`, ignored `reports/`, and local secret/config files.

**Interfaces:**
- Consumes the verified staged tree from Task 3.
- Produces one release commit on the existing `main` branch pushed to `origin/main`.

- [ ] Confirm `git diff --cached --check` is clean and the staged file list matches the allowlist.
- [ ] Create a commit with message `docs: publish verified experiment results and release updates`.
- [ ] Run `git push origin main`.
- [ ] Verify the push with `git status --short --branch` and `git log -1 --oneline`; report any intentionally uncommitted generated artifacts separately.
