---
name: role-aware-grounding-reground
description: Phase 4 fix — _reground_llm_parsed_task() forces GroundingEngine to re-assign entity_id for LLM-provided parsed_task
metadata:
  type: project
---

The critical fix: `load_parsed_task_from_bt()` in `task_semantics.py` now calls `_reground_llm_parsed_task()` whenever an LLM-provided parsed_task is loaded with a scene. This ensures GroundingEngine independently grounds every entity reference (theme, destination, support_surface, obstacles) against the scene. The LLM provides semantic descriptors; GroundingEngine assigns entity_id.

**Why:** Previously, when LLM `parsed_task` validation succeeded, the GroundingEngine was never run. LLM could directly set entity_id, violating the architectural invariant.

**How to apply:** `_reground_llm_parsed_task()` is called automatically inside `load_parsed_task_from_bt()`. No external changes needed.
