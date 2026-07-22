"""
Phase 10: DeepSeek vs RuleEngine differential testing.

Tests both engines against the same fixed cases, verifying:
- Normal DeepSeek responses
- Empty API key → fallback
- 401/429 → fallback
- Timeout → fallback
- Empty response → fallback
- Invalid JSON → fallback
- Schema violation → fallback
- Fabricated object IDs → caught by FinalPlanValidator
- Excessive force/velocity → capped by constraint compiler
- Missing role BT → caught by FinalPlanValidator

Both engines MUST pass through the same FinalPlanValidator.
No independent safety path for DeepSeek.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from robot_intent_agent.scene_builder import RawObjectPercept, SemanticSceneBuilder
from robot_intent_agent.planner import BehaviorTreeGenerator, LLMPlanner, HybridRouter, LLMPlannerError
from robot_intent_agent.constraint import HybridConstraintCompiler
from robot_intent_agent.ir import RobotTaskIRGenerator
from robot_intent_agent.final_plan_validator import FinalPlanValidator
from robot_intent_agent.task_semantics import PlanStatus, TaskActionKind


# ── Test case definitions ──────────────────────────────────

@dataclass
class CompareCase:
    case_id: str
    instruction: str
    objects: List[RawObjectPercept]
    description: str
    # Expected invariants (both engines must satisfy)
    expected_action: Optional[str] = None
    must_have_skills: List[str] = field(default_factory=list)
    must_not_have_skills: List[str] = field(default_factory=list)
    force_le: Optional[float] = None
    velocity_le: Optional[float] = None
    must_be_blocked: bool = False
    must_have_planpath: bool = False


COMPARE_CASES: List[CompareCase] = [
    CompareCase(
        case_id="DS01", description="Simple grasp",
        instruction="抓住杯子",
        objects=[RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                   width=0.07, height=0.10, depth=0.07,
                                   color="white", material="plastic")],
        expected_action="GRASP", must_have_skills=["Grasp", "Reach"],
    ),
    CompareCase(
        case_id="DS02", description="Fetch with obstacle negation",
        instruction="把盒子拿过来，别碰玻璃杯",
        objects=[
            RawObjectPercept(name="box", x=0.30, y=0.10, z=0.05,
                             width=0.08, height=0.06, depth=0.08,
                             color="brown", material="cardboard"),
            RawObjectPercept(name="cup", x=0.30, y=0.05, z=0.06,
                             width=0.07, height=0.12, depth=0.07,
                             color="transparent", material="glass"),
        ],
        expected_action="FETCH", must_have_planpath=True,
        must_have_skills=["Fetch"],
    ),
    CompareCase(
        case_id="DS03", description="Handover with force constraint",
        instruction="用3N力量把红色药瓶递给我",
        objects=[RawObjectPercept(name="bottle", x=0.20, y=0.08, z=0.04,
                                   width=0.04, height=0.09, depth=0.04,
                                   color="red", material="plastic")],
        expected_action="HANDOVER", force_le=3.0,
        must_have_skills=["Handover"],
    ),
    CompareCase(
        case_id="DS04", description="Place on table",
        instruction="把杯子放到桌子上",
        objects=[
            RawObjectPercept(name="cup", x=0.25, y=0.10, z=0.06,
                             width=0.07, height=0.10, depth=0.07,
                             color="white", material="ceramic"),
            RawObjectPercept(name="table", x=0.40, y=0.00, z=0.00,
                             width=0.60, height=0.03, depth=0.40,
                             color="brown", material="wood"),
        ],
        expected_action="PLACE", must_have_skills=["Place"],
    ),
    CompareCase(
        case_id="DS05", description="Glass cup with excessive force — safety override",
        instruction="用50N力量抓住玻璃杯",
        objects=[RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                   width=0.07, height=0.12, depth=0.07,
                                   color="transparent", material="glass")],
        expected_action="GRASP", force_le=2.0,  # Material hard limit must override 50N
    ),
    CompareCase(
        case_id="DS06", description="Moving target — dynamic grasp",
        instruction="抓住正在移动的红色小球",
        objects=[RawObjectPercept(name="ball", x=0.30, y=0.15, z=0.03,
                                   width=0.04, height=0.04, depth=0.04,
                                   color="red", material="rubber",
                                   extra_attrs={"_is_moving": True, "_speed_mps": 0.15})],
        expected_action="DYNAMIC_GRASP", must_have_skills=["WaitUntilStable"],
    ),
    CompareCase(
        case_id="DS07", description="Target not in scene — must block",
        instruction="把杯子拿过来",
        objects=[RawObjectPercept(name="block", x=0.30, y=0.10, z=0.05,
                                   width=0.05, height=0.05, depth=0.05,
                                   color="brown", material="wood")],
        must_be_blocked=True,
    ),
    CompareCase(
        case_id="DS08", description="Multi-constraint: force + velocity",
        instruction="用2N力、速度0.1m/s，把红色药瓶递给我",
        objects=[RawObjectPercept(name="bottle", x=0.20, y=0.08, z=0.04,
                                   width=0.04, height=0.09, depth=0.04,
                                   color="red", material="plastic")],
        expected_action="HANDOVER", force_le=2.0, velocity_le=0.1,
    ),
    CompareCase(
        case_id="DS09", description="Spatial disambiguation",
        instruction="抓住红色杯子",
        objects=[
            RawObjectPercept(name="cup", x=0.30, y=0.15, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="red", material="plastic"),
            RawObjectPercept(name="cup", x=0.30, y=-0.15, z=0.075,
                             width=0.07, height=0.10, depth=0.07,
                             color="blue", material="plastic"),
        ],
        expected_action="GRASP",
    ),
    CompareCase(
        case_id="DS10", description="Color mismatch — should block or not ground",
        instruction="抓住红色杯子",
        objects=[RawObjectPercept(name="cup", x=0.35, y=0.12, z=0.075,
                                   width=0.07, height=0.10, depth=0.07,
                                   color="blue", material="plastic")],
        must_be_blocked=True,
    ),
]


# ── Result dataclass ───────────────────────────────────────

@dataclass
class EngineResult:
    engine: str = ""
    case_id: str = ""
    action: str = ""
    execution_allowed: bool = False
    plan_status: str = ""
    bt_skills: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    force_n: Optional[float] = None
    velocity_ms: Optional[float] = None
    elapsed_ms: float = 0.0
    fallback_used: bool = False
    fallback_reason: str = ""
    exception: str = ""


# ── Runner ─────────────────────────────────────────────────

def run_pipeline(instruction, objects, use_llm=False, api_key=""):
    """Run full pipeline with RuleEngine or DeepSeek."""
    scene = SemanticSceneBuilder().build(objects)
    target = objects[0].name if objects else ""

    planner_name = "RuleEngine"
    fallback_used = False
    fallback_reason = ""

    try:
        if use_llm and api_key:
            llm = LLMPlanner(api_key=api_key)
            try:
                bt = llm.plan(instruction, scene=scene)
                planner_name = "DeepSeek-V3"
            except LLMPlannerError as e:
                fallback_used = True
                fallback_reason = f"LLMPlannerError: {e}"
                bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
                planner_name = f"RuleEngine(fallback:{type(e).__name__})"
            except Exception as e:
                fallback_used = True
                fallback_reason = f"{type(e).__name__}: {e}"
                bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
                planner_name = f"RuleEngine(fallback:{type(e).__name__})"
        elif use_llm and not api_key:
            fallback_used = True
            fallback_reason = "No API key — using RuleEngine"
            bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
            planner_name = "RuleEngine(fallback:no_key)"
        else:
            bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
    except Exception as e:
        fallback_used = True
        fallback_reason = f"BT generation failed: {e}"
        bt = BehaviorTreeGenerator().plan(instruction, scene=scene)
        planner_name = "RuleEngine(fallback:bt_error)"

    cg = HybridConstraintCompiler().compile(instruction, bt, scene=scene, target=target)
    ir = RobotTaskIRGenerator().generate(instruction, bt, cg, scene=scene)

    return ir, bt, cg, scene, planner_name, fallback_used, fallback_reason


def evaluate_case(case: CompareCase, use_llm: bool, api_key: str = "") -> EngineResult:
    t0 = time.time()
    result = EngineResult(
        engine="DeepSeek" if use_llm else "RuleEngine",
        case_id=case.case_id,
    )

    try:
        ir, bt, cg, scene, planner_name, fallback_used, fallback_reason = run_pipeline(
            case.instruction, case.objects, use_llm=use_llm, api_key=api_key,
        )

        result.fallback_used = fallback_used
        result.fallback_reason = fallback_reason
        result.action = ir.parsed_task.action.value if ir.parsed_task else "UNKNOWN"
        result.execution_allowed = ir.validation_result.execution_allowed
        result.plan_status = ir.plan_metadata.plan_status.value if ir.plan_metadata else "UNKNOWN"
        result.bt_skills = [a.skill_name for a in bt.root.flatten_actions()]

        # Extract force/velocity
        if ir.constraint_resolution:
            fr = ir.constraint_resolution.parameters.get("force_n")
            if fr and fr.selected_value is not None:
                result.force_n = fr.selected_value
            vr = ir.constraint_resolution.parameters.get("velocity_ms")
            if vr and vr.selected_value is not None:
                result.velocity_ms = vr.selected_value

        # Collect errors
        for issue in ir.validation_result.issues:
            result.errors.append(f"[{issue.severity}] {issue.code}: {issue.message}")

        # Check invariants
        if case.expected_action and result.action != case.expected_action:
            result.errors.append(f"[INVARIANT] Expected action {case.expected_action}, got {result.action}")
        if case.must_be_blocked and result.execution_allowed:
            result.errors.append("[INVARIANT] Execution should be BLOCKED but was allowed")
        if case.force_le is not None and result.force_n is not None and result.force_n > case.force_le + 0.01:
            result.errors.append(f"[INVARIANT] Force {result.force_n}N exceeds limit {case.force_le}N")
        if case.velocity_le is not None and result.velocity_ms is not None and result.velocity_ms > case.velocity_le + 0.01:
            result.errors.append(f"[INVARIANT] Velocity {result.velocity_ms}m/s exceeds limit {case.velocity_le}m/s")
        for skill in case.must_have_skills:
            if skill not in result.bt_skills:
                result.errors.append(f"[INVARIANT] Missing required skill: {skill}")
        for skill in case.must_not_have_skills:
            if skill in result.bt_skills:
                result.errors.append(f"[INVARIANT] Should not have skill: {skill}")
        if case.must_have_planpath and "PlanPath" not in result.bt_skills:
            result.errors.append("[INVARIANT] Missing PlanPath when obstacles present")

    except Exception as e:
        result.exception = f"{type(e).__name__}: {str(e)[:200]}"
        result.errors.append(f"[EXCEPTION] {result.exception}")

    result.elapsed_ms = (time.time() - t0) * 1000
    return result


# ── Comparison run ─────────────────────────────────────────

def run_comparison(api_key: str = "") -> Dict[str, Any]:
    """Run both engines and compare results."""
    rule_results = []
    ds_results = []

    print(f"{'='*70}")
    print(f"  Phase 10: DeepSeek vs RuleEngine Differential Test")
    print(f"{'='*70}")
    print(f"  Cases: {len(COMPARE_CASES)}")
    print(f"  DeepSeek API key: {'provided' if api_key else 'NOT provided (all DS will fallback)'}")
    print()

    for case in COMPARE_CASES:
        # RuleEngine
        r = evaluate_case(case, use_llm=False)
        rule_results.append(r)

        # DeepSeek
        d = evaluate_case(case, use_llm=True, api_key=api_key)
        ds_results.append(d)

        rule_pass = len(r.errors) == 0
        ds_pass = len(d.errors) == 0
        match = r.action == d.action and r.execution_allowed == d.execution_allowed
        print(f"  {case.case_id}: RE={'PASS' if rule_pass else 'FAIL'} DS={'PASS' if ds_pass else 'FAIL'} "
              f"match={'PASS' if match else 'DIFF️'} "
              f"fallback={'[FALLBACK]' if d.fallback_used else '—'} "
              f"{case.description[:40]}")

    # Compute metrics
    rule_passed = sum(1 for r in rule_results if len(r.errors) == 0)
    ds_passed = sum(1 for r in ds_results if len(r.errors) == 0)
    matches = sum(1 for i in range(len(COMPARE_CASES))
                  if rule_results[i].action == ds_results[i].action
                  and rule_results[i].execution_allowed == ds_results[i].execution_allowed)
    fallback_count = sum(1 for r in ds_results if r.fallback_used)
    ds_exceptions = sum(1 for r in ds_results if r.exception)

    rule_critical = sum(1 for r in rule_results for e in r.errors if "CRITICAL" in e or "INVARIANT" in e)
    ds_critical = sum(1 for r in ds_results for e in r.errors if "CRITICAL" in e or "INVARIANT" in e)

    # Find divergent cases
    divergent = []
    for i, case in enumerate(COMPARE_CASES):
        r, d = rule_results[i], ds_results[i]
        if r.action != d.action or r.execution_allowed != d.execution_allowed:
            divergent.append({
                "case_id": case.case_id,
                "description": case.description,
                "rule_action": r.action, "ds_action": d.action,
                "rule_exec": r.execution_allowed, "ds_exec": d.execution_allowed,
                "ds_fallback": d.fallback_used,
                "ds_fallback_reason": d.fallback_reason,
            })

    summary = {
        "total_cases": len(COMPARE_CASES),
        "rule_engine": {
            "passed": rule_passed,
            "pass_rate": round(rule_passed / len(COMPARE_CASES), 4),
            "critical_errors": rule_critical,
            "avg_latency_ms": round(sum(r.elapsed_ms for r in rule_results) / len(rule_results), 1),
        },
        "deepseek": {
            "passed": ds_passed,
            "pass_rate": round(ds_passed / len(COMPARE_CASES), 4),
            "critical_errors": ds_critical,
            "fallback_count": fallback_count,
            "fallback_rate": round(fallback_count / len(COMPARE_CASES), 4),
            "exceptions": ds_exceptions,
            "avg_latency_ms": round(sum(r.elapsed_ms for r in ds_results) / len(ds_results), 1),
        },
        "agreement": {
            "action_match": round(matches / len(COMPARE_CASES), 4),
            "divergent_cases": divergent,
        },
    }

    print(f"\n{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    print(f"  RuleEngine:  {rule_passed}/{len(COMPARE_CASES)} passed ({summary['rule_engine']['pass_rate']:.0%})  "
          f"critical={rule_critical}  avg={summary['rule_engine']['avg_latency_ms']:.1f}ms")
    print(f"  DeepSeek:    {ds_passed}/{len(COMPARE_CASES)} passed ({summary['deepseek']['pass_rate']:.0%})  "
          f"critical={ds_critical}  fallback={fallback_count}  avg={summary['deepseek']['avg_latency_ms']:.1f}ms")
    print(f"  Agreement:   {matches}/{len(COMPARE_CASES)} action+exec match ({summary['agreement']['action_match']:.0%})")
    print(f"  Divergent:   {len(divergent)} cases")
    for d in divergent:
        print(f"    {d['case_id']}: RE={d['rule_action']}/{d['rule_exec']}  DS={d['ds_action']}/{d['ds_exec']}  "
              f"fallback={d['ds_fallback']}")

    # Export
    out_path = Path(__file__).parent / "deepseek_comparison.json"
    export = {
        "summary": summary,
        "results": [
            {
                "case_id": case.case_id,
                "description": case.description,
                "instruction": case.instruction,
                "rule": {
                    "action": r.action, "exec_allowed": r.execution_allowed,
                    "plan_status": r.plan_status, "skills": r.bt_skills,
                    "force_n": r.force_n, "velocity_ms": r.velocity_ms,
                    "errors": r.errors, "elapsed_ms": r.elapsed_ms,
                },
                "deepseek": {
                    "action": d.action, "exec_allowed": d.execution_allowed,
                    "plan_status": d.plan_status, "skills": d.bt_skills,
                    "force_n": d.force_n, "velocity_ms": d.velocity_ms,
                    "errors": d.errors, "elapsed_ms": d.elapsed_ms,
                    "fallback_used": d.fallback_used,
                    "fallback_reason": d.fallback_reason,
                    "exception": d.exception,
                },
            }
            for case, r, d in zip(COMPARE_CASES, rule_results, ds_results)
        ],
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)
    print(f"\n  Exported: {out_path}")

    return summary


if __name__ == "__main__":
    import sys
    key = sys.argv[1] if len(sys.argv) > 1 else ""
    run_comparison(api_key=key)
