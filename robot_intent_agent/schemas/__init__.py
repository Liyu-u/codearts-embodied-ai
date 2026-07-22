"""
Schema 模块 — Pydantic v2 数据模型 + JSON Schema 双生成

导出:
    - Scene       : SemanticSceneGraph, SceneObject, SpatialRelation, RobotState
    - BehaviorTree: BehaviorTree, BTNode, BTNodeType, SkillAction
    - Constraint  : 全部约束类型 + ConstraintSet
    - RobotTaskIR : 最终统一中间表示
"""

from .scene import (
    SpatialPredicate,
    Affordance,
    Position,
    Orientation,
    BoundingBox,
    SceneObject,
    SpatialRelation,
    GripperState,
    JointState,
    RobotState,
    SemanticSceneGraph,
)

from .behavior_tree import (
    BTNodeType,
    DecoratorType,
    BTStatus,
    SkillAction,
    ConditionCheck,
    BTNode,
    BehaviorTree,
)

from .constraint import (
    ConstraintPriority,
    ConstraintType,
    ForceConstraint,
    VelocityConstraint,
    CollisionConstraint,
    HeightConstraint,
    TemporalConstraint,
    PreferenceConstraint,
    AnyConstraint,
    ConstraintSet,
)

from .robot_task_ir import (
    TaskMetadata,
    PreconditionAssertion,
    PreconditionSet,
    OptimizationSpace,
    RobotTaskIR,
    ParamValue,
    DecisionTraceNode,
    GroundedEntity,
    TaskIntent,
    RiskObject,
    ConstraintSource,
    OverrideLedgerEntry,
    ExplainReport,
)

# ── IntentFrame v1 (Phase 1) ──
from .intent_frame import (
    ActionKind as IntentActionKind,
    ProhibitionType,
    ConditionPredicate,
    ConstraintOperator as IntentConstraintOperator,
    ConstraintUnit,
    MannerKind,
    UrgencyKind,
    EntityDescriptors,
    EntityReference as IntentEntityReference,
    Prohibition as IntentProhibition,
    Condition as IntentCondition,
    UserConstraint as IntentUserConstraint,
    SequenceStep,
    IntentFrame,
    EngineTrace,
    intent_frame_json_schema,
    make_prohibition_id,
    make_condition_id,
    make_constraint_id,
)

__all__ = [
    # Scene
    "SpatialPredicate", "Affordance",
    "Position", "Orientation", "BoundingBox",
    "SceneObject", "SpatialRelation",
    "GripperState", "JointState", "RobotState",
    "SemanticSceneGraph",
    # BehaviorTree
    "BTNodeType", "DecoratorType", "BTStatus",
    "SkillAction", "ConditionCheck", "BTNode", "BehaviorTree",
    # Constraint
    "ConstraintPriority", "ConstraintType",
    "ForceConstraint", "VelocityConstraint", "CollisionConstraint",
    "HeightConstraint", "TemporalConstraint", "PreferenceConstraint",
    "AnyConstraint", "ConstraintSet",
    # RobotTaskIR v3.0
    "TaskMetadata", "PreconditionAssertion", "PreconditionSet",
    "OptimizationSpace", "RobotTaskIR",
    "ParamValue", "DecisionTraceNode", "GroundedEntity",
    "TaskIntent", "RiskObject", "ConstraintSource",
    "OverrideLedgerEntry", "ExplainReport",
]
