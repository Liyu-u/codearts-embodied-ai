# Phase 2 Report: Object ID/UUID/Name Tracking Fix

**Run ID**: `phase2-20260721`
**Git Commit**: `c229b54`

## Changes

### File 1: `scene_builder/semantic_scene_builder.py`
- Added `object_id: Optional[str] = None` to `RawObjectPercept` dataclass
- In `to_scene_object()`: store original `object_id` in `attributes["_perception_object_id"]`

### File 2: `eval/upgraded_runner.py`
- In `_build_raw_objects()`: pass `object_id=obj.get("object_id")` to RawObjectPercept
- Added `_build_scene_id_map()` static method: builds `{dataset_object_id: scene_uuid}` mapping

### File 3: `tests/test_scene_builder.py`
- Added `TestObjectIdPreservation` class with 6 tests

## Test Results

```
472 passed, 3 failed, 6 skipped
New Phase 2 tests: 6 passed
Regressions: 0
```

## Metrics (unchanged — scoring logic not modified)

| Dataset | Pass Rate | CRITICAL | HIGH | MEDIUM |
|---------|-----------|----------|------|--------|
| Golden (28) | 89.3% (25/28) | 2 | 1 | 0 |
| Blind (110) | 70.9% (78/110) | 24 | 18 | 3 |

## Key Achievement
- Dataset `object_id` now preserved through the full scene builder pipeline
- `_build_scene_id_map()` enables precise entity identity verification in evaluators
- No production scoring logic changed; backward compatible
