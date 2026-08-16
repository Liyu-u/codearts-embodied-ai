"""Runtime safety and fault injection module for v3.0.

The lightweight deterministic gates are importable without optional test/
planner dependencies; the legacy fault-injection exports remain available
when their full runtime dependencies are installed.
"""
from robot_intent_agent.safety.action_conflict_checker import find_action_constraint_conflicts
from robot_intent_agent.safety.perception_quality import assess_perception_quality

try:
    from robot_intent_agent.safety.fault_injection import (
    InjectionScenario,
    InjectionEvent,
    InjectionTimeline,
    RuntimeSnapshot,
    ExpectedSafetyOutcome,
    ActualSafetyOutcome,
    InvariantResult,
    InjectionRunResult,
    FaultInjectionRunner,
    SafetyInvariant,
    check_all_invariants,
    )
    from robot_intent_agent.safety.pre_execution_validator import (
    PreExecutionValidator,
    PreExecutionRevalidationResult,
    revalidate_before_execution,
    )
except ImportError:  # optional runtime dependencies are unavailable
    InjectionScenario = InjectionEvent = InjectionTimeline = RuntimeSnapshot = None
    ExpectedSafetyOutcome = ActualSafetyOutcome = InvariantResult = None
    InjectionRunResult = FaultInjectionRunner = SafetyInvariant = None
    check_all_invariants = None
    PreExecutionValidator = PreExecutionRevalidationResult = revalidate_before_execution = None

__all__ = [
    "InjectionScenario",
    "InjectionEvent",
    "InjectionTimeline",
    "RuntimeSnapshot",
    "ExpectedSafetyOutcome",
    "ActualSafetyOutcome",
    "InvariantResult",
    "InjectionRunResult",
    "FaultInjectionRunner",
    "SafetyInvariant",
    "check_all_invariants",
    "PreExecutionValidator",
    "PreExecutionRevalidationResult",
    "revalidate_before_execution",
    "find_action_constraint_conflicts",
    "assess_perception_quality",
]
