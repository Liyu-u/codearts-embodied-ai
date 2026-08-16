"""
Schema 模块 — Pydantic v2 数据模型 + JSON Schema 双生成

导出:
    - Scene       : SemanticSceneGraph, SceneObject, SpatialRelation, RobotState
    - BehaviorTree: BehaviorTree, BTNode, BTNodeType, SkillAction
    - Constraint  : 全部约束类型 + ConstraintSet
    - RobotTaskIR : 最终统一中间表示
"""

from .scene import (
    SpatialPredicate, Affordance, Position, Orientation, BoundingBox,
    SceneObject, SpatialRelation, GripperState, JointState, RobotState,
    SemanticSceneGraph,
)
from .behavior_tree import (
    BTNodeType, DecoratorType, BTStatus, SkillAction, ConditionCheck,
    BTNode, BehaviorTree,
)
from .constraint import (
    ConstraintPriority, ConstraintType, ForceConstraint, VelocityConstraint,
    CollisionConstraint, HeightConstraint, TemporalConstraint,
    PreferenceConstraint, AnyConstraint, ConstraintSet,
)
from .robot_task_ir import (
    TaskMetadata, PreconditionAssertion, PreconditionSet, OptimizationSpace,
    RobotTaskIR, ParamValue, DecisionTraceNode, GroundedEntity, TaskIntent,
    RiskObject, ConstraintSource, OverrideLedgerEntry, ExplainReport,
)
from .intent_frame import (
    ActionKind as IntentActionKind, ProhibitionType, ConditionPredicate,
    ConstraintOperator as IntentConstraintOperator, ConstraintUnit, MannerKind,
    UrgencyKind, EntityDescriptors, EntityReference as IntentEntityReference,
    Prohibition as IntentProhibition, Condition as IntentCondition,
    UserConstraint as IntentUserConstraint, SequenceStep, IntentFrame,
    EngineTrace, intent_frame_json_schema, make_prohibition_id,
    make_condition_id, make_constraint_id,
)

from .semantic_task_graph import (
    EvidenceSpan, SpatialConstraint, SemanticEntity, SemanticEvent,
    SemanticRelation, SemanticCondition, SemanticConstraint,
    SemanticProhibition, CoreferenceChain, AmbiguityRecord,
    SemanticTaskGraph, SemanticCandidate,
)
from .intent_output import IntentConstraintOutput, IntentOutput
from .perception_observation import PerceptionObservation, inference_observation

__all__ = [
    "SpatialPredicate", "Affordance", "Position", "Orientation", "BoundingBox",
    "SceneObject", "SpatialRelation", "GripperState", "JointState", "RobotState",
    "SemanticSceneGraph", "BTNodeType", "DecoratorType", "BTStatus", "SkillAction",
    "ConditionCheck", "BTNode", "BehaviorTree", "ConstraintPriority", "ConstraintType",
    "ForceConstraint", "VelocityConstraint", "CollisionConstraint", "HeightConstraint",
    "TemporalConstraint", "PreferenceConstraint", "AnyConstraint", "ConstraintSet",
    "TaskMetadata", "PreconditionAssertion", "PreconditionSet", "OptimizationSpace",
    "RobotTaskIR", "ParamValue", "DecisionTraceNode", "GroundedEntity", "TaskIntent",
    "RiskObject", "ConstraintSource", "OverrideLedgerEntry", "ExplainReport",
    "IntentActionKind", "ProhibitionType", "ConditionPredicate", "IntentConstraintOperator",
    "ConstraintUnit", "MannerKind", "UrgencyKind", "EntityDescriptors", "IntentEntityReference",
    "IntentProhibition", "IntentCondition", "IntentUserConstraint", "SequenceStep",
    "IntentFrame", "EngineTrace", "intent_frame_json_schema", "make_prohibition_id",
    "make_condition_id", "make_constraint_id", "EvidenceSpan", "SpatialConstraint",
    "SemanticEntity", "SemanticEvent", "SemanticRelation", "SemanticCondition",
    "SemanticConstraint", "SemanticProhibition", "CoreferenceChain", "AmbiguityRecord",
    "SemanticTaskGraph", "SemanticCandidate",
    "IntentConstraintOutput", "IntentOutput", "PerceptionObservation",
    "inference_observation",
]
