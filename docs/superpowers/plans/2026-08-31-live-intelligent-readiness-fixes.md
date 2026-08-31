# Live Intelligent Readiness Fixes Implementation Plan

**Goal:** Make real DeepSeek/CodeArts/Isaac acceptance runs fail closed on provider fallback while tolerating bounded retry telemetry and preserving deterministic scene grounding.

**Architecture:** The live runner loads provider configuration before importing cached adapters, blocks any semantic fallback, and records provider retry failures separately. The semantic compiler repairs provider omissions only by copying scene-grounded rule descriptors; it never invents physical IDs.

**Status:** Implemented and smoke-verified on 2026-08-31.

## Findings and fixes

1. **Configuration cache ordering:** the live runner imported the intent adapter before loading `.env`, so cached settings could retain fallback mode. Environment loading and settings-cache clearing now happen before adapter imports.
2. **Silent semantic fallback:** a provider response could be network-successful but contract-invalid and then continue with rules. Live mode now rejects any `engine_trace.fallback_used` result before CodeArts/Isaac.
3. **Provider role omissions:** DeepSeek sometimes emitted `e1/e2` role refs without entity atoms or split GRASP/PLACE fragments. The compiler maps only placeholder refs to same-scene rule entities, normalizes mismatched actions to the evidenced rule action, and collapses duplicate fragments.
4. **Feedback retry accounting:** TraceCoder could make a successful repair after a transient failed retry. Feedback evidence now marks the stage successful when at least one real call succeeds, while preserving `failed_calls` and `total_calls` for failure-rate statistics.

## Verification

- Real DeepSeek + CodeArts smoke on `multi-red-003`: READY, target `red_cube_right`, both providers had network calls and request IDs, no fallback.
- Real TraceCoder smoke with SAFE_STOP execution: required mode, two successful provider calls and one failed retry, no fallback, request IDs present.
- `py_compile` passed for changed modules.
- Existing semantic accuracy unit tests passed under `unittest`.

## Remaining external prerequisites

- Install/enable the repository's pytest environment before running the full test suite.
- Re-run the complete historical, safety, and 240-run matrices; prior fallback-invalid runs remain excluded and must not be overwritten.
