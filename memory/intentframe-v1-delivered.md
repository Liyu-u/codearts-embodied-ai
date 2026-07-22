---
name: intentframe-v1-delivered
description: IntentFrame v1 strict Pydantic schema delivered in Phase 1
metadata:
  type: project
---

IntentFrame v1 is the single authoritative NL→structured semantics schema at `schemas/intent_frame.py`. Both RuleEngine and DeepSeek must produce or be normalized to an IntentFrame. Key invariants: EntityReference NEVER has entity_id (that's GroundingEngine's job), additionalProperties=false, all fields must be explicit (null not omitted). 44 unit tests at `tests/test_intent_frame_schema.py`.

**Why:** DeepSeek was producing free-form JSON with no schema validation. Prohibitions and conditions were stored in `notes` strings.

**How to apply:** Import `IntentFrame` from `schemas.intent_frame`. Use `model_validate()` for parsing. Use `normalize_intent_frame()` for downstream compatibility.
