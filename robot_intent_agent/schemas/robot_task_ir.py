"""
Universal Task IR v3.0 -- Pydantic v2 data models.

v3.0 additions:
    - TaskIntent: structured NL intent (replaces bare string target)
    - RiskObject: scene hazard markers
    - ConstraintSource: constraint provenance enum
    - OverrideLedgerEntry: conflict resolution record
    - ExplainReport: XAI report (Markdown + Mermaid)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from uuid import uuid4

from pydantic import BaseModel, Field
from robot_intent_agent.task_semantics import (
    ParsedTask,
    GroundedTask,
    ConstraintResolution,
    ValidationResult,
    PlanStatus,
)

try:
    from .scene import SemanticSceneGraph
    from .behavior_tree import BehaviorTree
    from .constraint import ConstraintSet
except ImportError:
    SemanticSceneGraph = Any
    BehaviorTree = Any
    ConstraintSet = Any


# ============================================================
# v3.0: GroundedEntity
# ============================================================

class GroundedEntity(BaseModel):
    """Grounded entity -- binds NL mention to a physical scene object."""
    entity_id: str = Field(..., description="Unique physical object ID in scene")
    name: str = Field(..., description="Object name")
    label: Optional[str] = Field(default=None, description="Semantic label")
    affordances: List[str] = Field(default_factory=list, description="Affordance list")
    grounding_confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Grounding confidence")

    @classmethod
    def from_scene_object(cls, obj: Any, confidence: float = 1.0) -> "GroundedEntity":
        return cls(
            entity_id=getattr(obj, 'id', 'unknown'),
            name=getattr(obj, 'name', str(obj)),
            label=getattr(obj, 'label', None),
            affordances=[a.value for a in obj.affordances] if hasattr(obj, 'affordances') and obj.affordances else [],
            grounding_confidence=confidence,
        )


# ============================================================
# v2.0: ParamValue -- parameter with provenance
# ============================================================

class ParamValue(BaseModel):
    """Parameter with source and evidence chain."""
    value: Any = Field(..., description="Parameter value")
    source: List[str] = Field(default_factory=list, description="Source: memory | constraint | rule | llm | safety")
    evidence: List[str] = Field(default_factory=list, description="Evidence chain")

    @classmethod
    def from_value(cls, value: Any, source: str = "rule", evidence: str = "") -> "ParamValue":
        return cls(value=value, source=[source], evidence=[evidence] if evidence else [])


# ============================================================
# v2.0: DecisionTraceNode
# ============================================================

class DecisionTraceNode(BaseModel):
    """Explainable decision trace node with DAG dependencies."""
    module: str = Field(..., description="NL_PARSE | SCENE_GROUNDING | MEMORY_RETRIEVAL | CONSTRAINT_REASONING | CONFLICT_RESOLUTION | TASK_COMPILATION")
    input: str = Field(default="")
    output: str = Field(default="")
    reason: str = Field(default="")
    depends_on: List[str] = Field(default_factory=list)
    latency_ms: float = Field(default=0.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


# ============================================================
# v3.0: TaskIntent
# ============================================================

class TaskIntent(BaseModel):
    """Structured NL intent -- replaces bare string target pushing."""
    action: str = Field(default="grasp", description="Standard primitive: grasp | transfer | assemble | push | stack | pour | inspect")
    target: Optional[GroundedEntity] = Field(default=None, description="Grounded target entity")
    user_constraints: Dict[str, Any] = Field(default_factory=dict, description="User explicit requirements: {force_n: 100.0, velocity_ms: 5.0}")
    urgency: str = Field(default="normal", description="normal | high | emergency | critical")
    safety_goal: str = Field(default="collision_free", description="Safety objective")


class PlanMetadata(BaseModel):
    compiler_version: str = Field(default="1.0.0")
    planner_name: str = Field(default="RuleBasedPlanner")
    llm_model: Optional[str] = Field(default=None)
    rule_set_version: str = Field(default="1.0.0")
    audit_id: str = Field(default_factory=lambda: f"audit-{uuid4().hex[:8]}")
    plan_hash: str = Field(default="")
    plan_status: PlanStatus = Field(default=PlanStatus.NEEDS_CLARIFICATION)
    parse_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    constraint_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    plan_feasibility_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_readiness: float = Field(default=0.0, ge=0.0, le=1.0)


# ============================================================
# v3.0: RiskObject
# ============================================================

class RiskObject(BaseModel):
    """Scene hazard object."""
    entity_id: str = Field(..., description="Object ID")
    name: str = Field(default="")
    risk_type: str = Field(default="collision", description="collision | chemical_spill | vibration | thermal | radiation")
    priority: str = Field(default="high", description="low | medium | high | critical")
    description: str = Field(default="")


# ============================================================
# v3.0: ConstraintSource
# ============================================================

class ConstraintSource(BaseModel):
    """Constraint provenance metadata."""
    source: str = Field(default="rule", description="user | object_affordance | robot_limit | memory | safety_rule")
    priority: str = Field(default="hard", description="hard | soft")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = Field(default="")


# ============================================================
# v3.0: OverrideLedgerEntry
# ============================================================

class OverrideLedgerEntry(BaseModel):
    """Single conflict resolution record."""
    conflict_id: str = Field(default="")
    parameter: str = Field(default="")
    user_request: str = Field(default="")
    competing_constraint: str = Field(default="")
    resolved_value: str = Field(default="")
    arbitration_rule: str = Field(default="")


# ============================================================
# v3.0: ExplainReport
# ============================================================

class ExplainReport(BaseModel):
    """XAI explainability report (Markdown + Mermaid + Override Ledger)."""
    decision_report_md: str = Field(default="", description="Markdown decision report")
    constraint_explain_graph_mermaid: str = Field(default="", description="Mermaid constraint graph code")
    override_ledger: List[OverrideLedgerEntry] = Field(default_factory=list, description="Conflict resolution records")
    scene_summary: Dict[str, Any] = Field(default_factory=dict, description="SSOT scene summary")


# ============================================================
# TaskMetadata
# ============================================================

class TaskMetadata(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task-{uuid4().hex[:8]}")
    raw_instruction: str = Field(..., description="Raw NL instruction")
    language: str = Field(default="zh")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    user_context: Dict[str, Any] = Field(default_factory=dict)
    engine: Dict[str, str] = Field(default_factory=lambda: {
        "type": "Embodied Intent Reasoner",
        "version": "3.0",
        "planner": "RuleEngine",
        "reasoning_mode": "constraint-aware",
    })


# ============================================================
# Precondition + Optimization
# ============================================================

class PreconditionAssertion(BaseModel):
    assertion: str = Field(...)
    description: str = Field(default="")


class PreconditionSet(BaseModel):
    assertions: List[PreconditionAssertion] = Field(default_factory=list)
    def add(self, assertion: str, description: str = "") -> None:
        self.assertions.append(PreconditionAssertion(assertion=assertion, description=description))


class OptimizationSpace(BaseModel):
    force_range_n: tuple = Field(default=(0.1, 10.0))
    velocity_range_ms: tuple = Field(default=(0.05, 0.3))
    z_safe_margin_m: tuple = Field(default=(0.02, 0.10))
    collision_margin_m: tuple = Field(default=(0.03, 0.15))
    targets: List[str] = Field(default_factory=list)
    free_params: Dict[str, Any] = Field(default_factory=dict)


# ============================================================
# RobotTaskIR v3.0
# ============================================================

class RobotTaskIR(BaseModel):
    """Universal Task IR v3.0 -- the final output of the intent understanding pipeline."""
    ir_version: str = Field(default="3.0.0")
    task_metadata: TaskMetadata = Field(...)
    precondition_assertions: PreconditionSet = Field(default_factory=PreconditionSet)
    parsed_task: Optional[ParsedTask] = Field(default=None)
    grounded_task: Optional[GroundedTask] = Field(default=None)
    task_intent: Optional[TaskIntent] = Field(default=None)
    constraint_resolution: Optional[ConstraintResolution] = Field(default=None)
    validation_result: Optional[ValidationResult] = Field(default=None)
    robot_capability_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    plan_metadata: PlanMetadata = Field(default_factory=PlanMetadata)
    overall_confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    decision_trace: List[DecisionTraceNode] = Field(default_factory=list)
    explain_report: ExplainReport = Field(default_factory=ExplainReport)
    scene: Optional[Any] = Field(default=None)
    behavior_tree: Any = Field(default=None)
    skills: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    compiled_constraints: Any = Field(default=None)
    optimization_space: OptimizationSpace = Field(default_factory=OptimizationSpace)
    memory_context: Dict[str, Any] = Field(default_factory=dict)
    risk_objects: List[RiskObject] = Field(default_factory=list)
    # ── Phase 8: Semantic enforcement trace — full-chain prohibition/condition audit ──
    semantic_enforcement_trace: Dict[str, Any] = Field(default_factory=dict,
        description="Full-chain trace of every prohibition and condition through all pipeline stages")

    def model_post_init(self, __context: Any) -> None:
        if self.compiled_constraints is not None and hasattr(self.compiled_constraints, 'task_id'):
            if getattr(self.compiled_constraints, 'task_id', '') == "pending":
                self.compiled_constraints.task_id = self.task_metadata.task_id

    def summary(self) -> str:
        actions = []
        if self.behavior_tree and hasattr(self.behavior_tree, 'root'):
            actions = self.behavior_tree.root.flatten_actions()
        return (
            f"Task IR v3.0: {self.task_metadata.task_id}\n"
            f"  Instruction: {self.task_metadata.raw_instruction[:60]}\n"
            f"  Engine: {self.plan_metadata.planner_name} | Status: {self.plan_metadata.plan_status.value}\n"
            f"Confidence: {self.overall_confidence:.2f}\n"
            f"  Actions: {' > '.join(a.skill_name for a in actions[:6])}"
        )
