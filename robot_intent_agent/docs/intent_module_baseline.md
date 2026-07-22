# Intent Understanding Module Baseline

> **Date**: 2026-07-20  
> **Author**: Automated baseline analysis  
> **IR Version**: 3.0.0  
> **Phase**: Phase 1 — Baseline & Call Chain Audit

---

## 1. Test Results Summary

### Unit + Integration Tests (pytest)

```
275 passed, 6 skipped in 9.84s
```

- All unit tests under `robot_intent_agent/tests/` pass.
- All integration tests under `robot_intent_agent/integration_tests/` pass.
- 6 skipped: LLM API key dependent tests (TC_010_01–TC_010_06).

### Evaluation (Golden Dataset)

```
Total: 28 | Passed: 28 | Failed: 0
Action Accuracy:        100.0% (23 cases)
Entity Grounding:       100.0% (22 cases)
Force Parsing:          100.0% (7 cases)
Role Detection:         100.0% (10 cases)
Schema Pass Rate:       92.86%
Overall Pass Rate:      100.0%
Avg Latency:            4.1ms
```

Results exported to: `eval-results.json`, `eval-report.md`

---

## 2. Real Call Chains

### 2.1 Web UI (`demo/web_ui.py`) — Primary Entry

```
Pipeline.run(instruction, obs_json, engine, api_key)
  │
  ├─ [1] json.loads(obs_json)                          → Parse perception JSON
  ├─ [2] PropertyMapper.infer(obs_input)               → Object-level semantic properties
  ├─ [3] RawObjectPercept.to_scene_object()            → Convert to scene objects
  ├─ [4] SemanticSceneBuilder.build(scene_objects)      → Build SemanticSceneGraph
  │       └─ SpatialReasoner.infer_relations()          → Spatial relations (blocking/near/etc.)
  │
  ├─ [5] BehaviorTreeGenerator.plan(instruction, scene)  ─┐
  │       OR LLMPlanner.plan(instruction, scene)           │
  │       OR HybridRouter.plan(instruction, scene)         │
  │       └─ parse_task_semantics(instruction, scene)  ←──┘ (task_semantics.py)
  │           └─ build_grounded_task(parsed_task, scene)
  │
  ├─ [6] HybridConstraintCompiler.compile(instruction, bt, scene, target)
  │       ├─ SafetyConstraint.mandatory_set(target)    → Safety redlines
  │       ├─ ConstraintRuleEngine.extract(...)         → Rule-based constraints
  │       ├─ _inject_user_requests(graph, parsed_task) → User constraint injection
  │       ├─ _align_with_bt(graph, bt)                 → BT binding
  │       ├─ _deduplicate(graph)                       → Dedup
  │       ├─ _resolve_conflicts(graph, ...)            → Conflict detection
  │       ├─ _resolve_constraints(graph, ...)          → Domain-based resolution
  │       │   └─ _resolve_numeric_parameter() × 2      → force_n + velocity_ms
  │       └─ _apply_resolution_to_bt(bt, resolution)   → Write back to BT
  │
  ├─ [7] RobotTaskIRGenerator.generate(instruction, bt, cg, scene)
  │       ├─ _load_parsed_task()                       → From BT metadata or parse
  │       ├─ build_grounded_task()                     → Grounded task
  │       ├─ _load_constraint_resolution()             → From CG metadata
  │       ├─ **FinalPlanValidator.validate()**         → AUTHORITATIVE VALIDATION GATE
  │       │   ├─ _validate_grounding_invariants()
  │       │   ├─ _validate_required_roles()
  │       │   ├─ _validate_action_consistency()
  │       │   ├─ _validate_numeric_constraints()
  │       │   ├─ _validate_per_skill_velocity()
  │       │   ├─ _validate_dynamic_behavior()
  │       │   ├─ _validate_obstacle_passing()
  │       │   └─ _validate_consistency()
  │       ├─ _build_skills()                           → Skills map
  │       ├─ _build_decision_trace()                   → Decision DAG
  │       ├─ _build_task_intent()                      → Structured intent
  │       ├─ _build_explain_report()                   → Explainability report
  │       └─ Assembles RobotTaskIR                     → FINAL OUTPUT
  │
  └─ [8] Web UI renders 7 panels from IR fields
```

### 2.2 CLI Demo (`demo/cli_demo.py`) — Secondary Entry

```
PipelineRunner.run(task)
  ├─ MemoryRetriever.search(instruction)
  ├─ SemanticSceneBuilder.build(objects)
  ├─ RuleInstructionParser.extract_target()            → Legacy regex target extraction
  ├─ BehaviorTreeGenerator.plan(instruction, scene, memory)
  │   └─ (same as web UI path)
  ├─ HybridConstraintCompiler.compile(instruction, bt, scene, memory, target)
  │   └─ (same as web UI path)
  └─ RobotTaskIRGenerator.generate(instruction, bt, cg, scene, memory)
      └─ (same as web UI path, including FinalPlanValidator)
```

**Key difference from Web UI**: CLI uses `BehaviorTreeGenerator` only, with memory context. No LLM path.

### 2.3 Eval Runner (`eval/runner.py`) — Evaluation Entry

```
EvalRunner.run_all()
  ├─ SemanticSceneBuilder.build(raw_objects)
  ├─ BehaviorTreeGenerator.plan(instruction, scene)
  ├─ HybridConstraintCompiler.compile(instruction, bt, scene, target)
  └─ RobotTaskIRGenerator.generate(instruction, bt, cg, scene)
      └─ (same as web UI path, including FinalPlanValidator)
```

### 2.4 Fault Injection (`safety/fault_injection.py`) — Test Entry

```
FaultInjectionRunner.run_scenario()
  ├─ SemanticSceneBuilder.build(objects)
  ├─ BehaviorTreeGenerator.plan(instruction, scene)
  ├─ HybridConstraintCompiler.compile(instruction, bt, scene, target)
  ├─ RobotTaskIRGenerator.generate(instruction, bt, cg, scene)
  │   └─ (includes FinalPlanValidator)
  ├─ [Injection modifies IR/BT/Scene]
  └─ PreExecutionValidator.validate(ir, bt, current_scene, ...)  → Runtime revalidation
      └─ check_all_invariants(ir, bt, scene, ...)                → 20 invariants
```

---

## 3. Production Files by Responsibility

### 3.1 Scene JSON Parsing
| File | Class/Function | Role |
|------|---------------|------|
| `scene_builder/semantic_scene_builder.py:367` | `SemanticSceneBuilder` | Builds `SemanticSceneGraph` from `RawObjectPercept` list |
| `scene_builder/semantic_scene_builder.py:101` | `RawObjectPercept` | Raw perception → `SceneObject` with affordances + class hierarchy |
| `scene_builder/semantic_scene_builder.py:181` | `SpatialReasoner` | Geometric spatial relations (left_of, blocking, near, supporting, etc.) |
| `schemas/scene.py` | `SemanticSceneGraph`, `SceneObject`, etc. | Scene data model Pydantic schemas |

### 3.2 Natural Language Parsing
| File | Class/Function | Role |
|------|---------------|------|
| `task_semantics.py:535` | `parse_task_semantics()` | **AUTHORITATIVE** NL → `ParsedTask` (action, theme, roles, constraints) |
| `task_semantics.py:464` | `_classify_action()` | Keyword-based action classification |
| `task_semantics.py:341` | `_ground_entity_from_text()` | NL mention → `SemanticEntityRef` with scene lookup |
| `task_semantics.py:376` | `_extract_numeric_constraints()` | Force/velocity constraint regex extraction |
| `task_semantics.py:457` | `_extract_manner()` | Manner extraction (gentle/fast/careful) |
| `task_semantics.py:712` | `build_grounded_task()` | `ParsedTask` → `GroundedTask` with role validation |
| `planner/behavior_tree_generator.py:98` | `RuleInstructionParser` | **LEGACY** regex parser (extract_target, classify_action, extract_destination) |

### 3.3 Action Recognition
| File | Class/Function | Role |
|------|---------------|------|
| `task_semantics.py:464` | `_classify_action()` | `_ACTION_PATTERNS` map → `TaskActionKind` |
| `task_semantics.py:276` | `_ACTION_PATTERNS` | Ordered regex patterns for DYNAMIC_GRASP→PLACE→HANDOVER→TRANSFER→FETCH→GRASP |
| `planner/behavior_tree_generator.py:73` | `ACTION_KEYWORDS` | Legacy action keyword dict (used as fallback) |

### 3.4 Role Extraction
| File | Class/Function | Role |
|------|---------------|------|
| `task_semantics.py:341` | `_ground_entity_from_text()` | Grounds destination, recipient, support_surface from NL text |
| `task_semantics.py:535` | `parse_task_semantics()` | Assembles all roles: theme, source, destination, recipient, obstacle, support_surface |
| `task_semantics.py:712` | `build_grounded_task()` | Validates critical roles per action type, detects missing roles |
| `task_semantics.py:292` | `_OBJECT_PATTERNS` | Pattern table for table/tray/user/我 grounding |

### 3.5 Entity Grounding
| File | Class/Function | Role |
|------|---------------|------|
| `task_semantics.py:341` | `_ground_entity_from_text()` | Scene lookup + hardcoded pattern fallback |
| `task_semantics.py:535` | `parse_task_semantics()` | Scene object name/label/specific_class matching + cross-language aliases |
| `task_semantics.py:319` | `_infer_specific_class()` | Name → (specific_class, parent_class, ontology_path) mapping |
| `task_semantics.py:69` | `SemanticEntityRef.from_scene_object()` | Scene object → SemanticEntityRef adapter |
| `scene_builder/semantic_scene_builder.py:160` | `RawObjectPercept._infer_class_hierarchy()` | Scene builder class hierarchy inference |

### 3.6 Force/Velocity/Condition/Negation Parsing
| File | Class/Function | Role |
|------|---------------|------|
| `task_semantics.py:376` | `_extract_numeric_constraints()` | Regex patterns: 不超过N/至少N/N到N/用N (force) + m/s (velocity) |
| `constraint/rule_engine.py:30` | `MODIFIER_TO_CONSTRAINT` | Modifier → constraint mapping (轻一点→3N, 慢一点→0.1m/s, etc.) |
| `constraint/constraint_compiler.py:465` | `_resolve_numeric_parameter()` | Domain clamping with EXACT/MAX/MIN/RANGE semantic resolution |
| `task_semantics.py:482` | `_extract_obstacles()` | Negation keywords (别碰/不要碰/避开) → obstacle list |
| `planner/behavior_tree_generator.py:81` | `AVOID_KEYWORDS` | Regex for obstacle avoidance keywords |

### 3.7 BT Generation
| File | Class/Function | Role |
|------|---------------|------|
| `planner/behavior_tree_generator.py:183` | `BehaviorTreeGenerator.plan()` | Rule-based BT: parse→pipeline→params→BT nodes |
| `planner/behavior_tree_generator.py:62` | `SEMANTIC_PIPELINES` | `TaskActionKind` → skill sequence mapping |
| `planner/behavior_tree_generator.py:51` | `ACTION_PIPELINE` | Legacy action → skill sequence (fallback) |
| `planner/skill_catalog.py` | `SkillCatalog` | Skill definitions (Reach, Grasp, Fetch, Place, Handover, etc.) |
| `planner/llm_planner.py:167` | `LLMPlanner` | DeepSeek LLM-based planner |
| `planner/llm_planner.py:639` | `HybridRouter` | RuleEngine + LLM hybrid routing |

### 3.8 Constraint Arbitration
| File | Class/Function | Role |
|------|---------------|------|
| `constraint/constraint_compiler.py:71` | `HybridConstraintCompiler.compile()` | Master compiler: safety→rules→inject→align→dedup→resolve |
| `constraint/constraint_compiler.py:465` | `_resolve_numeric_parameter()` | Domain-based numeric constraint resolution with substitution |
| `constraint/constraint_compiler.py:672` | `_safe_substitute()` | Clamp requested value to feasible domain |
| `constraint/rule_engine.py:49` | `ConstraintRuleEngine.extract()` | NL modifiers → scene → object → memory constraints |
| `constraint/safety_constraint.py` | `SafetyConstraint.mandatory_set()` | Non-bypassable safety redlines |
| `constraint/spatial_constraint.py` | `SpatialConstraint` | Collision avoid, z-axis floor constraints |
| `constraint/physical_constraint.py` | `PhysicalConstraint` | Force/velocity limit factory methods |

### 3.9 IR Generation
| File | Class/Function | Role |
|------|---------------|------|
| `ir/ir_generator.py:53` | `RobotTaskIRGenerator.generate()` | **AUTHORITATIVE** — all BT/CG/Scene → `RobotTaskIR` |
| `ir/ir_generator.py:182` | `_load_parsed_task()` | Loads or re-parses ParsedTask |
| `ir/ir_generator.py:205` | `_build_skills()` | BT actions + CG bindings → skills map |
| `ir/ir_generator.py:538` | `_build_decision_trace()` | Read-only DAG aggregator (no inference) |
| `ir/ir_generator.py:747` | `_build_explain_report()` | Markdown + Mermaid explainability report |
| `ir/ir_generator.py:1016` | `generate_robot_task_ir()` | Convenience one-liner |
| `schemas/robot_task_ir.py` | `RobotTaskIR`, `TaskMetadata`, etc. | Pydantic output schemas |

### 3.10 Final Validation
| File | Class/Function | Role |
|------|---------------|------|
| `final_plan_validator.py:46` | `FinalPlanValidator.validate()` | **AUTHORITATIVE GATE** — 8 validation dimensions |
| `final_plan_validator.py:24` | `STAGE_VELOCITY_LIMITS` | Per-skill velocity hard limits |
| `safety/pre_execution_validator.py:44` | `PreExecutionValidator.validate()` | **RUNTIME** revalidation (plan age, scene revision, guard) |
| `safety/fault_injection.py:219` | `check_all_invariants()` | 20 safety invariants for fault injection testing |

---

## 4. Identified Issues

### 4.1 Duplicate/Bypass Paths

#### A. Placeholder Validation in Constraint Compiler (`constraint_compiler.py:456`)

`HybridConstraintCompiler._resolve_constraints()` creates a `PlanDecision` with a `_placeholder_validation()` — a fake `ValidationResult` that only checks `plan_status in {READY, READY_WITH_SAFE_SUBSTITUTION}` but performs **none of the 8 real validation checks**. The real validation only happens later in `RobotTaskIRGenerator.generate()` which calls `FinalPlanValidator.validate()`.

**Impact**: The CG metadata stores a PlanDecision with a weak placeholder. If any consumer reads `plan_decision.validation_result` from CG metadata instead of from the IR, they get a bypassed validator.

**Files involved**:
- `constraint/constraint_compiler.py:456` — `_placeholder_validation()`
- `constraint/constraint_compiler.py:184` — where it's called

#### B. Dual Parsing Systems

The codebase has two NL parsing systems:
1. **`task_semantics.parse_task_semantics()`** (authoritative) — structured `ParsedTask` with all roles, constraints, and grounding
2. **`RuleInstructionParser`** in `behavior_tree_generator.py` (legacy) — regex-based `extract_target()`, `classify_action()`, `extract_destination()`, `extract_avoid_objects()`, `extract_modifiers()`

The `BehaviorTreeGenerator.plan()` uses the authoritative parser first, then **falls back** to legacy methods:
- `behavior_tree_generator.py:233` — `target = parsed_task.theme.mention if parsed_task.theme else self.parser.extract_target(instruction)`
- `behavior_tree_generator.py:234` — `destination = parsed_task.destination.mention if parsed_task.destination else self.parser.extract_destination(instruction)`

The CLI demo uses `RuleInstructionParser.extract_target()` **before** the planner is called (`cli_demo.py:141`).

**Impact**: Two parsing paths exist. Changes to one may not propagate to the other. The legacy regex parser has different behavior and coverage.

#### C. Multiple `_load_parsed_task()` Implementations

Three places independently deserialize or re-parse `ParsedTask`:
1. `ir/ir_generator.py:182` — `RobotTaskIRGenerator._load_parsed_task()` — from BT metadata or `parse_task_semantics()`
2. `constraint/constraint_compiler.py:205` — `HybridConstraintCompiler._load_parsed_task()` — from BT metadata or `parse_task_semantics()`
3. `web_ui.py:452` — Uses `ir.parsed_task` (post-generation, authoritative)

**Impact**: If BT metadata serialization fails (exception in `model_validate`), constraint compiler and IR generator could re-parse and produce slightly different ParsedTasks. This is caught silently (`except Exception: pass`).

### 4.2 sys.path Hacks

- `demo/web_ui.py:9` — `sys.path.insert(0, str(Path(__file__).parent.parent.parent))`
- `demo/cli_demo.py:23` — `sys.path.insert(0, str(Path(__file__).parent.parent.parent))`
- `tests/conftest.py:22` — `sys.path.insert(0, str(_PKG_ROOT))`
- `tests/test_reasoning_cases.py:26` — `sys.path.insert(0, str(_PKG_ROOT))`

These would be unnecessary with proper package installation (`pip install -e .`).

### 4.3 TODO Stubs in Main CLI

`robot_intent_agent/main.py:49-63` — The `--api` and `--instruction` modes are stubs:
```python
print("[TODO] FastAPI service mode — coming in future sprint")
print("[TODO] Pipeline execution — coming in subsequent steps")
```
The CLI CLI does not actually run the pipeline — only the Web UI and `cli_demo.py` do.

### 4.4 Duplicate `PreExecutionValidator` in Safety Module

`PreExecutionValidator` (`safety/pre_execution_validator.py`) is a **separate** runtime validator used in fault injection testing. It is NOT a bypass of `FinalPlanValidator` — it serves a distinct purpose (runtime revalidation checking plan age, scene revision matches, perception staleness, guard availability). However, it re-checks some of the same things (numeric consistency, BT entity validity) which could diverge.

---

## 5. All Entry Points → FinalPlanValidator Audit

| Entry Point | Path | FinalPlanValidator called? | How |
|-------------|------|---------------------------|-----|
| Web UI | `Pipeline.run()` → `RobotTaskIRGenerator.generate()` | ✅ Yes | `ir_generator.py:97-104` |
| CLI Demo | `PipelineRunner.run()` → `RobotTaskIRGenerator.generate()` | ✅ Yes | `ir_generator.py:97-104` |
| Eval Runner | `EvalRunner._run_case()` → `RobotTaskIRGenerator.generate()` | ✅ Yes | `ir_generator.py:97-104` |
| Fault Injection | `FaultInjectionRunner.run_scenario()` → `RobotTaskIRGenerator.generate()` | ✅ Yes | `ir_generator.py:97-104` |
| Integration Tests | Various → `RobotTaskIRGenerator.generate()` | ✅ Yes | `ir_generator.py:97-104` |
| `main.py` CLI | `[TODO]` stub | ❌ No | Not implemented |
| Constraint Compiler (standalone) | `_resolve_constraints()` → `_placeholder_validation()` | ❌ No | Uses weak placeholder |

**Result**: All currently functional entry points (Web UI, CLI Demo, Eval, Fault Injection, all tests) go through `RobotTaskIRGenerator.generate()` which always calls `FinalPlanValidator.validate()`. The only gap is the `[TODO]` stub in `main.py` (not yet implemented) and the `_placeholder_validation()` in the constraint compiler (which gets superseded by the real validation in IR generation).

---

## 6. Production File Inventory

### Core Pipeline (must not duplicate)

| File | LOC | Status |
|------|-----|--------|
| `task_semantics.py` | ~815 | **AUTHORITATIVE** NL parsing + semantics |
| `scene_builder/semantic_scene_builder.py` | ~436 | **AUTHORITATIVE** scene building |
| `planner/behavior_tree_generator.py` | ~570 | **AUTHORITATIVE** rule-based BT generation |
| `planner/llm_planner.py` | ~650 | LLM-based planner (DeepSeek) |
| `constraint/constraint_compiler.py` | ~782 | **AUTHORITATIVE** constraint compilation |
| `constraint/rule_engine.py` | ~329 | Rule-based constraint extraction |
| `ir/ir_generator.py` | ~1031 | **AUTHORITATIVE** IR generation |
| `final_plan_validator.py` | ~315 | **AUTHORITATIVE** validation gate |
| `safety/pre_execution_validator.py` | ~? | Runtime revalidation (distinct purpose) |
| `safety/fault_injection.py` | ~724 | Fault injection framework |
| `eval/runner.py` | ~372 | Evaluation runner |

### Schemas (Pydantic models)

| File | Role |
|------|------|
| `schemas/scene.py` | Scene graph models |
| `schemas/behavior_tree.py` | BT models |
| `schemas/robot_task_ir.py` | RobotTaskIR output models |
| `schemas/constraint.py` | Constraint Pydantic models |
| `task_semantics.py` | `ParsedTask`, `ParsedConstraint`, `ConstraintResolution`, etc. |

### Demo / CLI

| File | Status |
|------|--------|
| `demo/web_ui.py` | ✅ Functional Gradio UI (8 panels) |
| `demo/cli_demo.py` | ✅ Functional CLI with 3 preset tasks |
| `main.py` (root) | ⚠️ Version stub only |
| `main.py` (robot_intent_agent) | ⚠️ `[TODO]` stubs for --api and --instruction |

### Other Modules

| Directory | Role |
|-----------|------|
| `memory/` | Memory retrieval (user preferences, skill experiences) |
| `property_inference/` | Object property inference (material, fragility, force/velocity limits) |
| `semantic_reasoner/` | Property fusion across modalities |
| `models/` | Object semantic schema (v3.0) |
| `prompts/` | LLM prompt templates |
| `config/` | Settings management |

---

## 7. Architecture Test Recommendation

An architecture test should be added to confirm:

```
ALL production entry points → RobotTaskIRGenerator.generate() → FinalPlanValidator.validate()
```

This means:
- Every call to `RobotTaskIR()` construction should happen inside `RobotTaskIRGenerator.generate()` (not bypassed)
- `FinalPlanValidator.validate()` should only be called from within `RobotTaskIRGenerator.generate()` (or tests)

Currently this holds true for all functional entry points.

---

## 8. Next Steps

1. **Resolve placeholder validation**: Remove or upgrade `_placeholder_validation()` in constraint compiler to avoid confusion — consumers should always read `validation_result` from the IR, not CG metadata.
2. **Consolidate legacy parser**: The `RuleInstructionParser` regex methods should be deprecated in favor of `parse_task_semantics()` — fallback creates two behaviors.
3. **De-duplicate `_load_parsed_task()`**: Extract to a shared utility to ensure identical behavior across IR generator and constraint compiler.
4. **Implement `main.py` stubs**: Wire `--instruction` and `--api` to the real pipeline.
5. **Add architecture test**: Verify all paths go through `FinalPlanValidator`.
6. **Clean up `sys.path.insert`** hacks — add proper `pyproject.toml`/`setup.py` for editable install.
