"""
Final plan validation gate — enhanced v3.1 with cross-field consistency.

Validates the single authoritative plan decision before serialization,
display, or dispatch. Error categories: SCHEMA, SEMANTIC, GROUNDING,
CONSTRAINT, CAPABILITY, CROSS_FIELD, PROVENANCE, EXECUTABILITY.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from robot_intent_agent.schemas.behavior_tree import BehaviorTree
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.constraint.base import ConstraintGraph
from robot_intent_agent.task_semantics import (
    ConstraintResolution,
    ConstraintOperator,
    ConstraintSourceKind,
    ParsedConstraint,
    ParsedTask,
    PlanStatus,
    RobotCapability,
    TaskActionKind,
    ValidationIssue,
    ValidationResult,
)

# Per-stage velocity hard limits (m/s)
STAGE_VELOCITY_LIMITS: Dict[str, float] = {
    "Reach": 0.2, "MoveTo": 0.2, "Transport": 0.2, "TransportToPreHandoverPose": 0.2,
    "ApproachHandoverZone": 0.1, "ControlledHandover": 0.1, "VerifyTransfer": 0.1,
    "Retract": 0.2, "Grasp": 0.0, "DynamicGrasp": 0.0,
    "Place": 0.0, "Handover": 0.1, "Fetch": 0.2,
}

HANDOVER_MOTION_ACTIONS = {
    "Reach", "MoveTo", "Transport", "TransportToPreHandoverPose", "ApproachHandoverZone",
    "ControlledHandover", "VerifyTransfer", "Retract", "Handover", "Fetch",
}

#── Error category codes ──────────────────────────────────────

class ErrorCategory:
    SCHEMA = "SCHEMA"
    SEMANTIC = "SEMANTIC"
    GROUNDING = "GROUNDING"
    CONSTRAINT = "CONSTRAINT"
    CAPABILITY = "CAPABILITY"
    CROSS_FIELD = "CROSS_FIELD"
    PROVENANCE = "PROVENANCE"
    EXECUTABILITY = "EXECUTABILITY"


# ── New error codes (Phase 7) ────────────────────────────────

class ErrorCode:
    # Role conflicts
    THEME_DESTINATION_ROLE_SWAP = "THEME_DESTINATION_ROLE_SWAP"
    THEME_IN_AVOID = "THEME_IN_AVOID"
    DESTINATION_IN_AVOID = "DESTINATION_IN_AVOID"
    RECIPIENT_IS_THEME = "RECIPIENT_IS_THEME"
    SUPPORT_SURFACE_NOT_GROUNDED = "SUPPORT_SURFACE_NOT_GROUNDED"
    SUPPORT_SURFACE_INFEASIBLE = "SUPPORT_SURFACE_INFEASIBLE"

    # Role mention grounding
    ROLE_MENTION_NOT_GROUNDED = "ROLE_MENTION_NOT_GROUNDED"

    # Negation propagation
    NEGATION_NOT_PROPAGATED = "NEGATION_NOT_PROPAGATED"

    # Condition/sequence
    CONDITIONAL_BRANCH_LOST = "CONDITIONAL_BRANCH_LOST"
    CONDITION_STATE_UNKNOWN = "CONDITION_STATE_UNKNOWN"
    COMPOSITE_ACTION_LOST = "COMPOSITE_ACTION_LOST"
    SELF_CONTRADICTORY_ACTION = "SELF_CONTRADICTORY_ACTION"
    NUMERIC_CONSTRAINT_CONFLICT = "NUMERIC_CONSTRAINT_CONFLICT"

    # Entity references
    ENTITY_ID_NOT_IN_SCENE = "ENTITY_ID_NOT_IN_SCENE"
    AMBIGUOUS_GROUNDING_FORCED = "AMBIGUOUS_GROUNDING_FORCED"

    # Cross-pipeline
    SCORER_PIPELINE_DISAGREEMENT = "SCORER_PIPELINE_DISAGREEMENT"


# Status priority: BLOCKED > NEEDS_CLARIFICATION > READY_WITH_SAFE_SUBSTITUTION > READY
STATUS_PRIORITY = {
    PlanStatus.BLOCKED: 0,
    PlanStatus.NEEDS_CLARIFICATION: 1,
    PlanStatus.READY_WITH_SAFE_SUBSTITUTION: 2,
    PlanStatus.READY: 3,
}


def _issue(category: str, code: str, message: str, severity: str = "error",
           subject: str = "") -> ValidationIssue:
    return ValidationIssue(
        code=f"{category}:{code}",
        message=message,
        severity=severity,
        subject=subject,
    )


class FinalPlanValidator:
    """Independent gate for execution readiness with cross-field consistency."""

    DISPATCHABLE_STATUSES = {PlanStatus.READY, PlanStatus.READY_WITH_SAFE_SUBSTITUTION}

    def validate(
        self,
        parsed_task: ParsedTask,
        behavior_tree: BehaviorTree,
        constraint_graph: ConstraintGraph,
        scene: Optional[SemanticSceneGraph],
        resolution: ConstraintResolution,
    ) -> ValidationResult:
        issues: List[ValidationIssue] = []

        # ── SCHEMA: structural validity ──
        self._validate_schema(parsed_task, behavior_tree, constraint_graph, scene, resolution, issues)

        # ── GROUNDING: entity references ──
        self._validate_grounding_invariants(parsed_task, behavior_tree, scene, issues)
        self._validate_required_roles(parsed_task, scene, issues)
        self._validate_entity_ids_in_scene(behavior_tree, scene, issues)
        self._validate_entity_reference_strictness(parsed_task, behavior_tree, scene, issues)
        self._validate_avoid_grounding(parsed_task, constraint_graph, behavior_tree, issues)
        self._validate_role_mention_grounding(parsed_task, scene, issues)

        # ── SEMANTIC: action/role consistency ──
        self._validate_action_consistency(parsed_task, behavior_tree, issues)
        self._validate_action_schema_contract(parsed_task, behavior_tree, scene, issues)
        self._validate_bt_action_contract(parsed_task, behavior_tree, issues)
        self._validate_role_completeness(parsed_task, issues)
        self._validate_role_non_conflict(parsed_task, scene, issues)
        self._validate_condition_completeness(parsed_task, issues)
        self._validate_unresolved_ambiguity(parsed_task, issues)

        # ── CROSS_FIELD: cross-structure consistency ──
        self._validate_missing_roles_vs_status(parsed_task, resolution, behavior_tree, issues)
        self._validate_plan_status_vs_execution(resolution, issues)
        self._validate_bt_ir_consistency(parsed_task, behavior_tree, resolution, constraint_graph, issues)

        # ── CONSTRAINT: numeric and safety limits ──
        self._validate_numeric_constraints(parsed_task, resolution, issues)
        self._validate_per_skill_velocity(behavior_tree, issues)
        self._validate_force_velocity_bounds(parsed_task, resolution, issues)
        self._validate_explicit_conflicts(
            parsed_task,
            constraint_graph,
            issues,
            semantic_authority=behavior_tree.metadata.get("semantic_authority") == "SemanticCompiler",
        )
        self._validate_dynamic_behavior(parsed_task, behavior_tree, issues)
        self._validate_obstacle_passing(parsed_task, behavior_tree, constraint_graph, issues)

        # ── PROVENANCE: traceability ──
        self._validate_provenance(parsed_task, constraint_graph, behavior_tree, issues)

        # ── Phase 8: Enforcement trace — prohibit/condition full-chain audit ──
        self._validate_enforcement_trace(parsed_task, behavior_tree, constraint_graph, issues)

        # ── EXECUTABILITY: final gate ──
        self._validate_executability(parsed_task, resolution, issues)

        # ── Status computation with priority ──
        has_critical = any(i.severity == "error" for i in issues)
        has_grounding_issue = any(
            "MISSING_" in i.code or "GROUNDING" in i.code or
            "ROLE_MENTION_NOT_GROUNDED" in i.code or "NOT_GROUNDED" in i.code
            for i in issues
        )
        has_negation_issue = any("NEGATION_NOT_PROPAGATED" in i.code for i in issues)
        has_condition_issue = any(
            "CONDITIONAL_BRANCH_LOST" in i.code or "CONDITION_STATE_UNKNOWN" in i.code
            for i in issues)
        has_ambiguity_issue = any("AMBIGUITY" in i.code for i in issues)
        has_schema_role_issue = any("MISSING_ACTION_SCHEMA_ROLE" in i.code for i in issues)
        has_graph_issue = any("SEMANTIC_GRAPH" in i.code or "UNKNOWN_" in i.code for i in issues)

        execution_allowed = not has_critical and resolution.plan_status in self.DISPATCHABLE_STATUSES

        # Status priority: BLOCKED > NEEDS_CLARIFICATION > READY_WITH_SAFE_SUBSTITUTION > READY
        if has_negation_issue or has_graph_issue:
            status = PlanStatus.BLOCKED
        elif execution_allowed:
            status = resolution.plan_status
        elif has_grounding_issue or has_condition_issue or has_schema_role_issue or has_ambiguity_issue:
            status = PlanStatus.NEEDS_CLARIFICATION
        else:
            status = PlanStatus.BLOCKED

        return ValidationResult(
            status=status,
            execution_allowed=execution_allowed,
            issues=issues,
        )

    def _validate_explicit_conflicts(
        self, parsed_task, constraint_graph, issues, semantic_authority: bool = False
    ):
        """Fail closed on explicit action and numeric contradictions."""
        text = "" if semantic_authority else (parsed_task.instruction or "")
        if re.search(r"(?:同时|但|又|并且)\s*(?:不要|别|禁止)\s*(?:抓取|抓|拿起|拿|移动|放置)", text):
            issues.append(_issue(
                ErrorCategory.SEMANTIC, ErrorCode.SELF_CONTRADICTORY_ACTION,
                "Instruction simultaneously requests and prohibits the manipulation action.",
                severity="error", subject="action",
            ))
        conflicts = (getattr(constraint_graph, "metadata", {}) or {}).get("conflicts", [])
        # A user exact request outside an object hard limit is a safe
        # substitution, not an internal contradiction.  Only graph conflicts
        # that do not correspond to a resolvable substitution are vetoes.
        resolution = getattr(constraint_graph, "metadata", {}).get("constraint_resolution", {}) if getattr(constraint_graph, "metadata", None) else {}
        substitution_parameters = {
            name for name, value in (resolution.get("parameters", {}) or {}).items()
            if isinstance(value, dict) and value.get("substitution_reason")
        }
        for conflict in conflicts:
            if substitution_parameters and any(name in str(conflict) for name in substitution_parameters):
                continue
            issues.append(_issue(
                ErrorCategory.CONSTRAINT, ErrorCode.NUMERIC_CONSTRAINT_CONFLICT,
                str(conflict), severity="error", subject="constraint",
            ))
        # Do not rely solely on the compiled graph: retain a direct semantic
        # guard so contradictory user bounds can never become executable.
        by_parameter = {}
        for constraint in parsed_task.user_constraints:
            by_parameter.setdefault(constraint.parameter, []).append(constraint)
        for parameter, constraints in by_parameter.items():
            lower_bounds = [
                c.min_value if c.min_value is not None else c.value
                for c in constraints if c.operator == ConstraintOperator.MIN
            ]
            upper_bounds = [
                c.max_value if c.max_value is not None else c.value
                for c in constraints if c.operator == ConstraintOperator.MAX
            ]
            lower_bounds = [v for v in lower_bounds if v is not None]
            upper_bounds = [v for v in upper_bounds if v is not None]
            if lower_bounds and upper_bounds and max(lower_bounds) > min(upper_bounds):
                issues.append(_issue(
                    ErrorCategory.CONSTRAINT, ErrorCode.NUMERIC_CONSTRAINT_CONFLICT,
                    f"Contradictory {parameter} bounds: minimum {max(lower_bounds)} "
                    f"exceeds maximum {min(upper_bounds)}.",
                    severity="error", subject=parameter,
                ))
        # Keep the safety invariant independent of the numeric parser's
        # surface coverage.  The semantic compiler may preserve only one
        # bound when a conjunction is written as “at least 5N and no more
        # than 2N”; the raw instruction still proves the contradiction.
        if re.search(
            r"(?:至少|不低于|不小于|最少|>=|≥)\s*\d+(?:\.\d+)?\s*(?:N|牛顿?)"
            r".*?(?:不超过|不大于|最多|至多|<=|≤)\s*\d+(?:\.\d+)?\s*(?:N|牛顿?)",
            text,
            re.IGNORECASE,
        ):
            bounds = [float(value) for value in re.findall(
                r"(?:至少|不低于|不小于|最少|>=|≥|不超过|不大于|最多|至多|<=|≤)\s*(\d+(?:\.\d+)?)",
                text,
                re.IGNORECASE,
            )]
            if len(bounds) >= 2:
                issues.append(_issue(
                    ErrorCategory.CONSTRAINT, ErrorCode.NUMERIC_CONSTRAINT_CONFLICT,
                    f"Contradictory force_n bounds in instruction: lower {bounds[0]} exceeds upper {bounds[1]}.",
                    severity="error", subject="force_n",
                ))

    # ══════════════════════════════════════════════════════════
    # SCHEMA: structural validity
    # ══════════════════════════════════════════════════════════

    def _validate_schema(self, parsed_task, behavior_tree, constraint_graph,
                         scene, resolution, issues):
        # Input instruction preserved
        if not parsed_task.instruction or not parsed_task.instruction.strip():
            issues.append(_issue(ErrorCategory.SCHEMA, "MISSING_INSTRUCTION",
                "ParsedTask.instruction is empty or missing", subject="instruction"))

        # BT must have root
        if not behavior_tree or not behavior_tree.root:
            issues.append(_issue(ErrorCategory.SCHEMA, "MISSING_BT_ROOT",
                "BehaviorTree has no root node", subject="behavior_tree"))

        # BT must have at least one action
        actions = behavior_tree.root.flatten_actions() if behavior_tree and behavior_tree.root else []
        if not actions:
            issues.append(_issue(ErrorCategory.SCHEMA, "NO_BT_ACTIONS",
                "BehaviorTree has no action nodes", severity="warning", subject="behavior_tree"))

        # task_id present
        if not behavior_tree or not behavior_tree.task_id:
            issues.append(_issue(ErrorCategory.SCHEMA, "MISSING_TASK_ID",
                "BehaviorTree has no task_id", severity="warning", subject="behavior_tree"))

    # ══════════════════════════════════════════════════════════
    # GROUNDING: entity references
    # ══════════════════════════════════════════════════════════

    def _validate_grounding_invariants(self, parsed_task, behavior_tree, scene, issues):
        action_names = [a.skill_name for a in behavior_tree.root.flatten_actions()]
        has_motion = bool(HANDOVER_MOTION_ACTIONS.intersection(action_names))

        # Check for invalid input data in scene objects
        if scene and scene.objects:
            for obj in scene.objects:
                attrs = getattr(obj, "attributes", {}) or {}
                if attrs.get("_has_invalid_input"):
                    issues.append(_issue(ErrorCategory.GROUNDING, "INVALID_INPUT_DATA",
                        f"Scene object '{getattr(obj, 'name', '')}' has invalid input data "
                        f"(non-numeric position, negative/zero size, etc.)",
                        severity="error", subject="scene"))

        if has_motion and (parsed_task.theme is None or parsed_task.theme.entity_id is None):
            issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_THEME_GROUNDING",
                "Cannot generate executable BT with motion actions when theme.entity_id is empty",
                subject="theme"))

        scene_entity_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", []) or []} if scene else set()
        scene_names = {getattr(o, "name", "") for o in getattr(scene, "objects", []) or []} if scene else set()

        for action in behavior_tree.root.flatten_actions():
            bt_target = action.params.get("target", "")
            bt_entity_id = action.params.get("target_entity_id", "")
            if bt_target and scene_entity_ids:
                if bt_entity_id and bt_entity_id in scene_entity_ids:
                    continue
                if bt_target in scene_entity_ids or bt_target in scene_names:
                    continue
                issues.append(_issue(ErrorCategory.GROUNDING, "BT_TARGET_NOT_GROUNDED",
                    f"BT action '{action.skill_name}' target '{bt_target}' has no valid entity_id reference",
                    severity="warning", subject=action.skill_name))

        if parsed_task.action == TaskActionKind.HANDOVER:
            for action in behavior_tree.root.flatten_actions():
                dest = action.params.get("destination", "")
                if action.skill_name == "MoveTo" and dest in ("user", "我"):
                    issues.append(_issue(ErrorCategory.GROUNDING, "HANDOVER_MOVETO_USER",
                        "HANDOVER must not generate MoveTo(user); recipient pose required",
                        subject="MoveTo"))

    def _validate_required_roles(self, parsed_task, scene, issues):
        if parsed_task.theme is None and parsed_task.action != TaskActionKind.WAIT:
            issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_THEME",
                "Task theme is not grounded", subject="theme"))

        if parsed_task.action == TaskActionKind.HANDOVER:
            if parsed_task.recipient is None or parsed_task.recipient.entity_id is None:
                issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_RECIPIENT",
                    "Recipient identity is missing", subject="recipient"))
            elif parsed_task.recipient.entity_id == "user":
                code = "MISSING_RECIPIENT_POSE"
                issues.append(_issue(ErrorCategory.GROUNDING, code,
                    "Recipient identified but no executable recipient_pose_or_handover_zone available",
                    subject="recipient_pose_or_handover_zone"))
        elif parsed_task.action in (TaskActionKind.FETCH, TaskActionKind.TRANSFER):
            if (parsed_task.action == TaskActionKind.FETCH and
                    (parsed_task.destination is None or parsed_task.destination.entity_id is None) and
                    (parsed_task.recipient is None or parsed_task.recipient.entity_id is None)):
                issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_DELIVERY_POSE",
                    "Fetch task has neither a grounded destination nor a recipient", subject="delivery_pose_or_recipient"))
            elif parsed_task.destination is None or parsed_task.destination.entity_id is None:
                issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_DESTINATION",
                    "Destination identity is missing", subject="destination"))

        if parsed_task.action == TaskActionKind.PLACE:
            if parsed_task.support_surface is None:
                issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_SUPPORT_SURFACE",
                    "Place task requires a support surface", subject="support_surface"))
            elif parsed_task.support_surface.source != "scene":
                issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_SUPPORT_SURFACE",
                    "Support surface not grounded in scene", subject="support_surface"))
            else:
                known_pose_ids = {getattr(o, 'id', '') for o in getattr(scene, 'objects', []) or []} if scene else set()
                if parsed_task.support_surface.entity_id not in known_pose_ids:
                    issues.append(_issue(ErrorCategory.GROUNDING, "MISSING_SUPPORT_SURFACE",
                        "Support surface not found in scene", subject="support_surface"))
                # Verify the support surface is not the same category as the theme
                # (e.g., a cup cannot be a support surface for another cup)
                elif scene and parsed_task.theme:
                    ss_obj = scene.find_object(parsed_task.support_surface.entity_id)
                    theme_obj = scene.find_object(parsed_task.theme.entity_id) if parsed_task.theme.entity_id else None
                    if ss_obj and theme_obj:
                        ss_class = getattr(ss_obj, 'specific_class', '') or getattr(ss_obj, 'label', '')
                        theme_class = getattr(theme_obj, 'specific_class', '') or getattr(theme_obj, 'label', '')
                        ss_affs = [a.value if hasattr(a, 'value') else str(a) for a in getattr(ss_obj, 'affordances', [])]
                        # Same category AND no support/fixed affordance = invalid
                        if ss_class and ss_class == theme_class and "fixed" not in ss_affs and "support_surface" not in ss_affs:
                            issues.append(_issue(ErrorCategory.GROUNDING, "INVALID_SUPPORT_SURFACE",
                                f"Object '{getattr(ss_obj, 'name', '')}' (class={ss_class}) used as support_surface "
                                f"for theme of same class — cannot place on same-type non-support object",
                                subject="support_surface"))

    def _validate_action_schema_contract(self, parsed_task, behavior_tree, scene, issues):
        """Enforce the domain action contract before execution is allowed."""
        from robot_intent_agent.domain.action_schemas import get_action_schema
        schema = get_action_schema(parsed_task.action.value if hasattr(parsed_task.action, "value") else parsed_task.action)
        roles = {name for name, value in parsed_task.role_map().items() if value is not None}
        # WAIT requires a condition atom rather than a scene entity role.
        if getattr(parsed_task, "conditions", None):
            roles.add("condition")
        missing = schema.missing_roles(roles)
        for role in missing:
            code = "MISSING_ACTION_SCHEMA_ROLE"
            issues.append(_issue(ErrorCategory.SEMANTIC, code,
                                 f"Action {schema.action} requires role '{role}'", severity="warning", subject=role))
        for role in schema.forbidden_roles:
            if role in roles:
                issues.append(_issue(ErrorCategory.SEMANTIC, "FORBIDDEN_ACTION_SCHEMA_ROLE",
                                     f"Action {schema.action} forbids role '{role}'", subject=role))
        # Explicitly reject any graph event whose local references do not
        # resolve; this catches accidental LLM-only entities early.
        graph = getattr(parsed_task, "semantic_task_graph", None)
        if isinstance(graph, dict):
            try:
                from robot_intent_agent.schemas.semantic_task_graph import SemanticTaskGraph
                graph_errors = SemanticTaskGraph.model_validate(graph).validate_local_references()
                for error in graph_errors:
                    issues.append(_issue(ErrorCategory.SEMANTIC, "SEMANTIC_GRAPH_REFERENCE_INVALID",
                                         error, subject="semantic_task_graph"))
            except Exception as exc:
                issues.append(_issue(ErrorCategory.SCHEMA, "SEMANTIC_GRAPH_INVALID",
                                     f"SemanticTaskGraph validation failed: {exc}", subject="semantic_task_graph"))

    def _validate_entity_ids_in_scene(self, behavior_tree, scene, issues):
        """Every entity_id in BT actions must exist in the scene (or be 'user')."""
        if not scene:
            return
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", [])}
        for action in behavior_tree.root.flatten_actions():
            for key in ("target_entity_id", "destination_entity_id"):
                eid = action.params.get(key, "")
                if eid and eid not in scene_ids and eid not in {"user", "operator"}:
                    issues.append(_issue(ErrorCategory.GROUNDING, "BT_ENTITY_NOT_IN_SCENE",
                        f"BT action '{action.skill_name}' {key}='{eid}' not found in scene objects",
                        subject=action.skill_name))

    def _validate_avoid_grounding(self, parsed_task, constraint_graph, behavior_tree, issues):
        """Avoid objects must appear as collision_avoid in CG or avoid_obstacles in BT."""
        if not parsed_task.obstacle:
            return
        avoid_mentions = {o.mention for o in parsed_task.obstacle}
        avoid_eids = {o.entity_id for o in parsed_task.obstacle if o.entity_id}

        # Check for unresolvable avoids — mentioned but not grounded to any scene object
        for obs in parsed_task.obstacle:
            if obs.entity_id is None:
                issues.append(_issue(ErrorCategory.GROUNDING, "AVOID_NOT_GROUNDED",
                    f"Avoid object '{obs.mention}' mentioned in NL but not grounded to any scene object",
                    severity="error", subject="obstacle"))

        cg_avoids = set()
        for n in constraint_graph.nodes:
            if n.constraint_type == "collision_avoid":
                cg_avoids.add(n.params.get("obstacle", ""))
        bt_avoids = set()
        for a in behavior_tree.root.flatten_actions():
            if a.skill_name in {"Avoid", "PlanPath"} and getattr(a, "target", None):
                bt_avoids.add(str(a.target))
            for key in ("avoid_obstacles", "avoid", "avoid_objects"):
                av = a.params.get(key, [])
                if isinstance(av, list):
                    bt_avoids.update(str(x) for x in av)
                elif isinstance(av, str) and av:
                    bt_avoids.add(av)

        all_avoids = cg_avoids | bt_avoids | avoid_eids
        for obstacle in parsed_task.obstacle:
            mention = obstacle.mention
            # The canonical downstream representation is the grounded scene
            # ID.  A localized display name is optional in BT params, so a
            # Chinese mention must not be treated as “lost” merely because
            # the constraint graph stores D_obs_* and BT stores the scene
            # label.  Accept either the ID or a display-name occurrence.
            grounded = obstacle.entity_id and obstacle.entity_id in all_avoids
            display_match = mention and any(mention in str(value) or str(value) in mention
                                            for value in all_avoids)
            if mention and not grounded and not display_match:
                issues.append(_issue(ErrorCategory.GROUNDING, "AVOID_NOT_PROPAGATED",
                    f"Avoid object '{mention}' not found in CG collision_avoid or BT avoid params",
                    subject="obstacle"))

    # ══════════════════════════════════════════════════════════
    # Phase 7: Role non-conflict — theme≠dest, theme∉avoid, etc.
    # ══════════════════════════════════════════════════════════

    def _validate_role_non_conflict(self, parsed_task, scene, issues):
        """Validate that roles do not conflict with each other."""
        theme_id = parsed_task.theme.entity_id if parsed_task.theme else None
        dest_id = parsed_task.destination.entity_id if parsed_task.destination else None
        ss_id = parsed_task.support_surface.entity_id if parsed_task.support_surface else None
        recip_id = parsed_task.recipient.entity_id if parsed_task.recipient else None
        avoid_ids = {o.entity_id for o in (parsed_task.obstacle or []) if o.entity_id}

        # theme ≠ destination
        if theme_id and dest_id and theme_id == dest_id:
            issues.append(_issue(ErrorCategory.CROSS_FIELD, ErrorCode.THEME_DESTINATION_ROLE_SWAP,
                f"Theme and destination refer to the same entity ({theme_id}). Roles must be distinct.",
                severity="error", subject="theme,destination"))

        # theme ≠ support_surface
        if theme_id and ss_id and theme_id == ss_id:
            issues.append(_issue(ErrorCategory.CROSS_FIELD, ErrorCode.THEME_DESTINATION_ROLE_SWAP,
                f"Theme and support_surface refer to the same entity ({theme_id}).",
                severity="error", subject="theme,support_surface"))

        # theme ∉ avoid
        if theme_id and theme_id in avoid_ids:
            issues.append(_issue(ErrorCategory.CROSS_FIELD, ErrorCode.THEME_IN_AVOID,
                f"Theme ({theme_id}) is also in the avoid set. Cannot simultaneously fetch and avoid.",
                severity="error", subject="theme,obstacle"))

        # destination ∉ avoid
        if dest_id and dest_id in avoid_ids:
            issues.append(_issue(ErrorCategory.CROSS_FIELD, ErrorCode.DESTINATION_IN_AVOID,
                f"Destination ({dest_id}) is in the avoid set.",
                severity="error", subject="destination,obstacle"))

        # recipient ≠ theme
        if theme_id and recip_id and theme_id == recip_id and recip_id != "user":
            issues.append(_issue(ErrorCategory.CROSS_FIELD, ErrorCode.RECIPIENT_IS_THEME,
                f"Recipient and theme are the same entity ({theme_id}).",
                severity="error", subject="recipient,theme"))

        # support_surface feasibility
        if ss_id and scene:
            ss_obj = scene.find_object(ss_id)
            if ss_obj:
                affs = {a.value if hasattr(a, 'value') else str(a) for a in getattr(ss_obj, 'affordances', [])}
                if "fixed" not in affs and "support_surface" not in affs:
                    issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.SUPPORT_SURFACE_INFEASIBLE,
                        f"Entity '{getattr(ss_obj, 'name', ss_id)}' used as support_surface "
                        f"but lacks 'fixed' or 'support_surface' affordance. Affordances: {affs}",
                        severity="error", subject="support_surface"))

    # ══════════════════════════════════════════════════════════
    # Phase 7: Role mention grounding — text roles must ground
    # ══════════════════════════════════════════════════════════

    def _validate_role_mention_grounding(self, parsed_task, scene, issues):
        """Roles mentioned in instruction text must be grounded to scene objects."""
        if not scene:
            return
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", []) or []}

        # Check theme: if theme is present but not grounded to scene
        if parsed_task.theme and parsed_task.theme.entity_id:
            if parsed_task.theme.entity_id not in scene_ids and parsed_task.theme.entity_id not in {"user", "operator"}:
                issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.ROLE_MENTION_NOT_GROUNDED,
                    f"Theme '{parsed_task.theme.mention}' has entity_id={parsed_task.theme.entity_id} "
                    f"not found in scene objects. Grounding failed.",
                    severity="error", subject="theme"))

        # Check support_surface
        if parsed_task.support_surface and parsed_task.support_surface.entity_id:
            if parsed_task.support_surface.entity_id not in scene_ids:
                issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.SUPPORT_SURFACE_NOT_GROUNDED,
                    f"Support surface '{parsed_task.support_surface.mention}' "
                    f"entity_id={parsed_task.support_surface.entity_id} not in scene.",
                    severity="error", subject="support_surface"))

        # Check destination
        if parsed_task.destination and parsed_task.destination.entity_id:
            if parsed_task.destination.entity_id not in scene_ids and parsed_task.destination.entity_id not in {"user", "operator"}:
                issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.ROLE_MENTION_NOT_GROUNDED,
                    f"Destination '{parsed_task.destination.mention}' "
                    f"entity_id={parsed_task.destination.entity_id} not in scene.",
                    severity="error", subject="destination"))

    # ══════════════════════════════════════════════════════════
    # Phase 7: Condition completeness
    # ══════════════════════════════════════════════════════════

    def _validate_unresolved_ambiguity(self, parsed_task, issues):
        """An explicit unresolved ambiguity must never be executable."""
        for item in getattr(parsed_task, "ambiguity_resolution", []) or []:
            if isinstance(item, dict) and str(item.get("status", "")).upper() == "UNRESOLVED":
                issues.append(_issue(
                    ErrorCategory.SEMANTIC,
                    "UNRESOLVED_AMBIGUITY",
                    item.get("clarification") or "An intent ambiguity requires clarification",
                    severity="error",
                    subject=item.get("ambiguity_id", "ambiguity"),
                ))

    def _validate_condition_completeness(self, parsed_task, issues):
        """Conditional structures must preserve branches; unknown state → not READY."""
        notes = getattr(parsed_task, "notes", []) or []

        for note in notes:
            # IF_ELSE missing else branch
            if note.startswith("unsupported_conditional:"):
                detail = note.split(":", 1)[1] if ":" in note else note
                if "missing else" in detail.lower() or "missing otherwise" in detail.lower():
                    issues.append(_issue(ErrorCategory.SEMANTIC, ErrorCode.CONDITIONAL_BRANCH_LOST,
                        f"Conditional structure has lost branch: {detail}",
                        severity="error", subject="condition"))
                else:
                    issues.append(_issue(ErrorCategory.SEMANTIC, ErrorCode.CONDITIONAL_BRANCH_LOST,
                        f"Unsupported conditional structure: {detail}. "
                        f"Cannot safely simplify to sequential actions.",
                        severity="error", subject="condition"))

            # Condition state unknown
            if note.startswith("conditional_detected:"):
                # Check if robot state evaluation was done
                if "_evaluated_" not in note.lower():
                    # Condition detected but state unknown → warn
                    issues.append(_issue(ErrorCategory.SEMANTIC, ErrorCode.CONDITION_STATE_UNKNOWN,
                        f"Conditional structure detected but robot state evaluation is unknown. "
                        f"Cannot confirm preconditions are met. {note}",
                        severity="warning", subject="condition"))

            # Composite action lost
            if note.startswith("clarification_needed:theme="):
                issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.AMBIGUOUS_GROUNDING_FORCED,
                    f"Ambiguous grounding detected: {note}. Theme selection requires clarification.",
                    severity="error", subject="theme"))

        # Check for composite action simplification
        if parsed_task.action == TaskActionKind.CUSTOM:
            issues.append(_issue(ErrorCategory.SEMANTIC, ErrorCode.COMPOSITE_ACTION_LOST,
                f"Action classified as CUSTOM — composite semantics may have been lost. "
                f"Original instruction: {parsed_task.instruction[:60]}",
                severity="warning", subject="action"))

    # ══════════════════════════════════════════════════════════
    # Phase 7: Entity reference strictness
    # ══════════════════════════════════════════════════════════

    def _validate_entity_reference_strictness(self, parsed_task, behavior_tree, scene, issues):
        """Entity references must use IDs not names; no ambiguous references."""
        if not scene:
            return
        scene_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", []) or []}
        scene_names = [getattr(o, "name", "") for o in getattr(scene, "objects", []) or []]

        # Count objects with same name → ambiguity detection
        name_counts: Dict[str, int] = {}
        for n in scene_names:
            name_counts[n] = name_counts.get(n, 0) + 1
        ambiguous_names = {n for n, c in name_counts.items() if c >= 2}

        # BT actions must use entity_id, not just name
        for action in behavior_tree.root.flatten_actions():
            target = action.params.get("target", "")
            target_eid = action.params.get("target_entity_id", "")

            # If using name for an ambiguous object → error
            if target and target in ambiguous_names and not target_eid:
                issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.AMBIGUOUS_GROUNDING_FORCED,
                    f"BT action '{action.skill_name}' references target='{target}' by name, "
                    f"but there are {name_counts[target]} objects with this name. "
                    f"Must use target_entity_id.",
                    severity="error", subject=action.skill_name))

            # If target_entity_id exists but not in scene
            if target_eid and target_eid not in scene_ids and target_eid not in {"user", "operator"}:
                issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.ENTITY_ID_NOT_IN_SCENE,
                    f"BT action '{action.skill_name}' target_entity_id='{target_eid}' "
                    f"not found in scene objects.",
                    severity="error", subject=action.skill_name))

        # Check for empty object references in skills
        if hasattr(parsed_task, '__dict__'):
            for role_name in ("theme", "destination", "support_surface", "recipient"):
                entity = getattr(parsed_task, role_name, None)
                if entity and hasattr(entity, 'entity_id') and entity.entity_id:
                    if entity.entity_id not in scene_ids and entity.entity_id not in {"user", "operator"}:
                        issues.append(_issue(ErrorCategory.GROUNDING, ErrorCode.ENTITY_ID_NOT_IN_SCENE,
                            f"Role '{role_name}' references entity_id='{entity.entity_id}' "
                            f"not found in scene.",
                            severity="error", subject=role_name))

    # ══════════════════════════════════════════════════════════
    # SEMANTIC: action/role consistency
    # ══════════════════════════════════════════════════════════

    def _validate_action_consistency(self, parsed_task, behavior_tree, issues):
        action_names = [a.skill_name for a in behavior_tree.root.flatten_actions()]

        # Check for unsupported conditional structures
        for note in getattr(parsed_task, "notes", []) or []:
            if note.startswith("unsupported_conditional:"):
                cond_type = note.split(":", 1)[1]
                issues.append(_issue(ErrorCategory.SEMANTIC, "UNSUPPORTED_CONDITIONAL",
                    f"Conditional structure '{cond_type}' detected but not supported by current BT schema. "
                    f"Cannot safely simplify to sequential actions.",
                    severity="error", subject="action"))
                return

        # CUSTOM action = unknown/unparseable — no safe execution template
        if parsed_task.action == TaskActionKind.CUSTOM:
            issues.append(_issue(ErrorCategory.SEMANTIC, "CUSTOM_ACTION_NO_TEMPLATE",
                f"Action classified as CUSTOM (unparseable/unknown) — no safe execution template exists",
                severity="error", subject="action"))
            return

        expected = {
            TaskActionKind.GRASP: {"Grasp", "Reach"},
            TaskActionKind.FETCH: {"Fetch", "Reach", "Grasp"},
            TaskActionKind.PLACE: {"Place", "Reach"},
            TaskActionKind.HANDOVER: {"Handover", "Reach", "Grasp", "ApproachHandoverZone", "ControlledHandover"},
            TaskActionKind.TRANSFER: {"Transfer", "Reach", "Grasp"},
            TaskActionKind.DYNAMIC_GRASP: {"DynamicGrasp", "WaitUntilStable", "Reach"},
        }.get(parsed_task.action, set())
        if expected and not expected.intersection(action_names):
            issues.append(_issue(ErrorCategory.SEMANTIC, "ACTION_MISMATCH",
                f"BT actions {action_names} do not match task action {parsed_task.action.value}",
                subject=parsed_task.action.value))

    def _validate_role_completeness(self, parsed_task, issues):
        """Verify that action-required roles have been extracted."""
        # TRANSFER is a destination-based manipulation action.  It must not
        # inherit the recipient requirement of FETCH/HANDOVER; that old check
        # was the reason every valid "搬运到/移送到" case was blocked after
        # semantic parsing had already produced theme + destination.
        if parsed_task.action == TaskActionKind.HANDOVER:
            if not parsed_task.recipient:
                issues.append(_issue(ErrorCategory.SEMANTIC, "RECIPIENT_NOT_EXTRACTED",
                    f"Action {parsed_task.action.value} requires a recipient but none was extracted",
                    subject="recipient"))
        if parsed_task.action == TaskActionKind.FETCH:
            if not parsed_task.destination and not parsed_task.recipient:
                issues.append(_issue(ErrorCategory.SEMANTIC, "DELIVERY_ROLE_NOT_EXTRACTED",
                    "FETCH action requires a destination or recipient", subject="destination_or_recipient"))
        if parsed_task.action == TaskActionKind.PLACE:
            if not parsed_task.support_surface and not parsed_task.destination:
                issues.append(_issue(ErrorCategory.SEMANTIC, "DESTINATION_NOT_EXTRACTED",
                    "PLACE action requires destination or support_surface",
                    subject="destination"))

    # ══════════════════════════════════════════════════════════
    # CROSS_FIELD: inter-structure consistency
    # ══════════════════════════════════════════════════════════

    def _validate_missing_roles_vs_status(self, parsed_task, resolution, behavior_tree, issues):
        """If grounded_task has missing_roles, plan_status must NOT be READY."""
        # Check parsed_task.unmet_roles
        if parsed_task.unmet_roles:
            if resolution.plan_status == PlanStatus.READY:
                issues.append(_issue(ErrorCategory.CROSS_FIELD, "UNMET_ROLES_BUT_READY",
                    f"Unmet roles {parsed_task.unmet_roles} but plan_status is READY",
                    subject="plan_status"))

        # Check for theme missing but execution allowed
        if (parsed_task.theme is None and parsed_task.action != TaskActionKind.WAIT
                and resolution.plan_status in self.DISPATCHABLE_STATUSES):
            issues.append(_issue(ErrorCategory.CROSS_FIELD, "NO_THEME_BUT_DISPATCHABLE",
                "Theme missing but plan_status is dispatchable",
                subject="plan_status"))

    def _validate_plan_status_vs_execution(self, resolution, issues):
        """NEEDS_CLARIFICATION/BLOCKED must have execution_allowed=false."""
        if resolution.plan_status in (PlanStatus.NEEDS_CLARIFICATION, PlanStatus.BLOCKED):
            pass  # execution_allowed is computed by caller based on issues
        if resolution.plan_status not in self.DISPATCHABLE_STATUSES:
            issues.append(_issue(ErrorCategory.EXECUTABILITY, "NON_DISPATCHABLE_STATUS",
                f"Plan status {resolution.plan_status.value} is not dispatchable",
                severity="error", subject=resolution.plan_status.value))

    def _validate_bt_ir_consistency(self, parsed_task, behavior_tree, resolution, constraint_graph, issues):
        """BT skill parameters must be consistent with IR constraint_resolution."""
        final_force = resolution.parameters.get("force_n").selected_value if resolution.parameters.get("force_n") else None
        final_velocity = resolution.parameters.get("velocity_ms").selected_value if resolution.parameters.get("velocity_ms") else None

        for action in behavior_tree.root.flatten_actions():
            # Force consistency
            if action.skill_name in ("Grasp", "GentleGrasp", "DynamicGrasp") and final_force is not None:
                action_force = action.params.get("force_n")
                if isinstance(action_force, dict):
                    action_force = action_force.get("value")
                if action_force is not None and abs(float(action_force) - float(final_force)) > 0.01:
                    issues.append(_issue(ErrorCategory.CROSS_FIELD, "FORCE_MISMATCH",
                        f"BT force ({action_force}) differs from IR resolution ({final_force})",
                        subject=action.skill_name))

            # Velocity consistency
            if action.skill_name in ("Reach", "MoveTo", "Transport", "Push") and final_velocity is not None:
                action_velocity = action.params.get("velocity_ms")
                if isinstance(action_velocity, dict):
                    action_velocity = action_velocity.get("value")
                if action_velocity is not None and abs(float(action_velocity) - float(final_velocity)) > 0.01:
                    issues.append(_issue(ErrorCategory.CROSS_FIELD, "BT_IR_VELOCITY_MISMATCH",
                        f"BT velocity ({action_velocity}) differs from IR resolution ({final_velocity})",
                        subject=action.skill_name))

        # BT theme entity must match parsed_task theme mention
        if parsed_task.theme and parsed_task.theme.mention:
            bt_targets = {a.params.get("target", "") for a in behavior_tree.root.flatten_actions()}
            bt_targets.discard("")
            bt_eids = {a.params.get("target_entity_id", "") for a in behavior_tree.root.flatten_actions()}
            bt_eids.discard("")
            theme_eid = parsed_task.theme.entity_id
            if bt_targets and theme_eid and theme_eid not in bt_eids:
                if parsed_task.theme.mention not in bt_targets:
                    issues.append(_issue(ErrorCategory.CROSS_FIELD, "BT_THEME_MISMATCH",
                        f"BT targets {sorted(bt_targets)[:3]} don't reference parsed_task theme '{parsed_task.theme.mention}'",
                        severity="warning", subject="theme"))

        # CG constraint count vs resolution parameters
        cg_constraints = len(constraint_graph.nodes)
        resolution_constraints = len(resolution.parameters)
        if cg_constraints > 0 and resolution_constraints == 0:
            issues.append(_issue(ErrorCategory.CROSS_FIELD, "CG_NO_RESOLUTION",
                f"ConstraintGraph has {cg_constraints} nodes but resolution has 0 parameters",
                severity="warning", subject="constraint_resolution"))

    def _validate_bt_action_contract(self, parsed_task, behavior_tree, issues):
        """Ensure the deterministic BT contains the executable action implied by IR.

        This is intentionally action-family based rather than case based.  It
        catches semantic/BT drift after LLM fusion while allowing the planner
        to insert auxiliary navigation and safety nodes.
        """
        actions = [a.skill_name for a in behavior_tree.root.flatten_actions()]
        if not actions:
            issues.append(_issue(ErrorCategory.CROSS_FIELD, "EMPTY_BEHAVIOR_TREE",
                "Parsed task has no executable behavior-tree action", subject="behavior_tree"))
            return

        required = {
            TaskActionKind.GRASP: {"Grasp", "GentleGrasp", "DynamicGrasp"},
            TaskActionKind.DYNAMIC_GRASP: {"DynamicGrasp"},
            TaskActionKind.PLACE: {"Place", "Transport"},
            TaskActionKind.HANDOVER: {"Handover", "ControlledHandover", "MoveToHandoverZone"},
            TaskActionKind.FETCH: {"Fetch", "Grasp", "GentleGrasp", "DynamicGrasp"},
            TaskActionKind.TRANSFER: {"Transfer", "MoveTo", "Transport", "TransportToPreHandoverPose"},
        }.get(parsed_task.action)
        if required and not any(name in required for name in actions):
            issues.append(_issue(ErrorCategory.CROSS_FIELD, "BT_ACTION_MISSING",
                f"IR action {parsed_task.action.value} has no matching BT skill; got {actions}",
                severity="error", subject=parsed_task.action.value))

        # Composite tasks must preserve the declared high-level order.  The
        # BT may contain navigation/safety nodes between these milestones.
        steps = getattr(parsed_task, "steps", None) or []
        if len(steps) > 1:
            skill_by_action = {
                "GRASP": {"Grasp", "GentleGrasp", "DynamicGrasp"},
                "PLACE": {"Place", "Transport"}, "HANDOVER": {"Handover", "ControlledHandover", "MoveToHandoverZone"},
                "FETCH": {"Fetch", "Grasp", "GentleGrasp", "DynamicGrasp"},
                "TRANSFER": {"Transfer", "MoveTo", "Transport", "TransportToPreHandoverPose"},
            }
            cursor = 0
            for step in steps:
                action_name = getattr(step, "action", None)
                action_name = getattr(action_name, "value", action_name)
                allowed = skill_by_action.get(str(action_name).upper())
                if not allowed:
                    continue
                found = next((i for i in range(cursor, len(actions)) if actions[i] in allowed), None)
                if found is None:
                    issues.append(_issue(ErrorCategory.CROSS_FIELD, "BT_STEP_ORDER_MISMATCH",
                        f"Composite step {getattr(step, 'step_index', '?')} ({action_name}) is missing or out of order",
                        severity="error", subject="steps"))
                    break
                cursor = found + 1

    # ══════════════════════════════════════════════════════════
    # CONSTRAINT: numeric and safety enforcement
    # ══════════════════════════════════════════════════════════

    def _validate_numeric_constraints(self, parsed_task, resolution, issues):
        for parameter, param_resolution in resolution.parameters.items():
            selected = param_resolution.selected_value
            if selected is None:
                continue
            if not isinstance(selected, (int, float)) or selected != selected:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "NON_FINITE_VALUE",
                    f"{parameter} is not finite ({selected})", subject=parameter))
            if param_resolution.domain.min_value is not None and selected < param_resolution.domain.min_value - 1e-9:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "LOWER_BOUND_VIOLATION",
                    f"{parameter}={selected} below feasible domain min={param_resolution.domain.min_value}",
                    subject=parameter))
            if param_resolution.domain.max_value is not None and selected > param_resolution.domain.max_value + 1e-9:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "UPPER_BOUND_VIOLATION",
                    f"{parameter}={selected} above feasible domain max={param_resolution.domain.max_value}",
                    subject=parameter))

        for constraint in parsed_task.user_constraints:
            if constraint.unit not in ("", "N", "m/s"):
                issues.append(_issue(ErrorCategory.CONSTRAINT, "BAD_UNIT",
                    f"Unexpected unit '{constraint.unit}'", subject=constraint.parameter))

    def _validate_force_velocity_bounds(self, parsed_task, resolution, issues):
        """Final force/velocity must satisfy both user constraints and robot hard limits."""
        # Robot hard limits
        robot = RobotCapability()
        # Force
        fr = resolution.parameters.get("force_n")
        if fr and fr.selected_value is not None:
            if fr.selected_value > robot.gripper_max_force_n + 0.01:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "FORCE_EXCEEDS_ROBOT_MAX",
                    f"Resolved force {fr.selected_value}N exceeds robot max {robot.gripper_max_force_n}N",
                    subject="force_n"))
            if fr.selected_value < robot.gripper_min_force_n - 0.01:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "FORCE_BELOW_ROBOT_MIN",
                    f"Resolved force {fr.selected_value}N below robot min {robot.gripper_min_force_n}N",
                    severity="warning", subject="force_n"))

        # Velocity
        vr = resolution.parameters.get("velocity_ms")
        if vr and vr.selected_value is not None:
            if vr.selected_value > robot.max_velocity_ms + 0.01:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "VELOCITY_EXCEEDS_ROBOT_MAX",
                    f"Resolved velocity {vr.selected_value}m/s exceeds robot max {robot.max_velocity_ms}m/s",
                    subject="velocity_ms"))

        # User constraint satisfaction
        for constraint in parsed_task.user_constraints:
            if constraint.parameter == "force_n" and fr and fr.selected_value is not None:
                if constraint.operator == ConstraintOperator.MAX and constraint.max_value is not None:
                    if fr.selected_value > constraint.max_value + 0.01:
                        issues.append(_issue(ErrorCategory.CONSTRAINT, "USER_MAX_FORCE_VIOLATED",
                            f"Resolved force {fr.selected_value}N exceeds user max {constraint.max_value}N",
                            subject="force_n"))
                elif constraint.operator == ConstraintOperator.MIN and constraint.min_value is not None:
                    if fr.selected_value < constraint.min_value - 0.01:
                        issues.append(_issue(ErrorCategory.CONSTRAINT, "USER_MIN_FORCE_VIOLATED",
                            f"Resolved force {fr.selected_value}N below user min {constraint.min_value}N",
                            severity="warning", subject="force_n"))
                elif constraint.operator == ConstraintOperator.EXACT and constraint.value is not None:
                    # Exact may be overridden by safety — check substitution reason
                    if abs(fr.selected_value - constraint.value) > 0.01:
                        if not fr.substitution_reason:
                            issues.append(_issue(ErrorCategory.CONSTRAINT, "USER_EXACT_FORCE_NOT_MET",
                                f"Resolved force {fr.selected_value}N differs from user exact {constraint.value}N without substitution",
                                severity="warning", subject="force_n"))

    def _validate_per_skill_velocity(self, behavior_tree, issues):
        for action in behavior_tree.root.flatten_actions():
            skill = action.skill_name
            limit = STAGE_VELOCITY_LIMITS.get(skill)
            if limit is None or limit <= 0:
                continue
            action_vel = action.params.get("velocity_ms")
            if isinstance(action_vel, dict):
                action_vel = action_vel.get("value")
            if action_vel is not None:
                try:
                    if float(action_vel) > limit + 0.01:
                        issues.append(_issue(ErrorCategory.CONSTRAINT, "STAGE_VELOCITY_EXCEEDED",
                            f"{skill} velocity {action_vel} m/s exceeds stage limit {limit} m/s",
                            subject=skill))
                except (TypeError, ValueError):
                    pass

    def _validate_dynamic_behavior(self, parsed_task, behavior_tree, issues):
        action_names = [a.skill_name for a in behavior_tree.root.flatten_actions()]
        if parsed_task.action == TaskActionKind.DYNAMIC_GRASP:
            if "WaitUntilStable" not in action_names:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "MISSING_STABILITY_GATE",
                    "Dynamic grasp requires WaitUntilStable", subject="WaitUntilStable"))
            for action in behavior_tree.root.flatten_actions():
                if action.skill_name == "WaitUntilStable":
                    if action.timeout_s is None or action.timeout_s <= 0:
                        issues.append(_issue(ErrorCategory.CONSTRAINT, "STABILITY_NO_TIMEOUT",
                            "WaitUntilStable requires a timeout", subject="WaitUntilStable"))
                    if not action.failure_conditions:
                        issues.append(_issue(ErrorCategory.CONSTRAINT, "STABILITY_NO_FAILURE",
                            "WaitUntilStable requires a failure branch", subject="WaitUntilStable"))
        # Also check: if motion_state is moving and action is GRASP (not DYNAMIC_GRASP), flag
        if parsed_task.motion_state.state == "moving" and parsed_task.action == TaskActionKind.GRASP:
            issues.append(_issue(ErrorCategory.CONSTRAINT, "MOVING_TARGET_NO_DYNAMIC",
                "Target is moving but action is GRASP, not DYNAMIC_GRASP",
                severity="warning", subject="action"))

    def _validate_obstacle_passing(self, parsed_task, behavior_tree, constraint_graph, issues):
        if not parsed_task.obstacle:
            return

        action_names = [a.skill_name for a in behavior_tree.root.flatten_actions()]
        bt_has_path_planning = "PlanPath" in action_names or "Avoid" in action_names
        cg_has_collision_avoid = any(n.constraint_type == "collision_avoid" for n in constraint_graph.nodes)

        # Check BT for path planning
        if not bt_has_path_planning:
            issues.append(_issue(ErrorCategory.CONSTRAINT, "NEGATION_NOT_PROPAGATED",
                f"ParsedTask has {len(parsed_task.obstacle)} obstacle(s) but BT has no PlanPath/Avoid node. "
                f"Negation constraints not propagated to behavior tree.",
                subject="obstacle"))

        # Check CG for collision avoidance
        if not cg_has_collision_avoid:
            issues.append(_issue(ErrorCategory.CONSTRAINT, "NEGATION_NOT_PROPAGATED",
                f"ParsedTask has {len(parsed_task.obstacle)} obstacle(s) but CG has no collision_avoid nodes. "
                f"Negation constraints not compiled into constraint graph.",
                subject="obstacle"))

        # Per-obstacle propagation check
        cg_avoid_objects = set()
        for n in constraint_graph.nodes:
            if n.constraint_type == "collision_avoid":
                cg_avoid_objects.add(n.params.get("obstacle", ""))

        bt_avoid_params = set()
        for a in behavior_tree.root.flatten_actions():
            if a.skill_name in {"Avoid", "PlanPath"} and getattr(a, "target", None):
                bt_avoid_params.add(str(a.target))
            for key in ("avoid_obstacles", "avoid", "avoid_objects"):
                av = a.params.get(key, [])
                if isinstance(av, list):
                    bt_avoid_params.update(str(x) for x in av)
                elif isinstance(av, str) and av:
                    bt_avoid_params.add(av)

        for obs in parsed_task.obstacle:
            obs_id = obs.entity_id or obs.mention
            in_cg = obs_id in cg_avoid_objects or any(obs_id in x for x in cg_avoid_objects)
            in_bt = obs_id in bt_avoid_params or any(obs_id in x for x in bt_avoid_params)
            if not in_cg and not in_bt:
                issues.append(_issue(ErrorCategory.CONSTRAINT, "NEGATION_NOT_PROPAGATED",
                    f"Obstacle '{obs.mention}' (id={obs.entity_id}) not found in BT avoid params "
                    f"or CG collision_avoid nodes.",
                    subject="obstacle"))

    # ══════════════════════════════════════════════════════════
    # Phase 8: Enforcement trace — prohibit/condition full-chain audit
    # ══════════════════════════════════════════════════════════

    def _validate_enforcement_trace(self, parsed_task, behavior_tree,
                                     constraint_graph, issues):
        """Verify every hard prohibition and condition is enforced across all stages.

        Phase 8 invariant: no prohibition or condition should be lost between
        parsing, grounding, compilation, BT generation, and final validation.
        """
        bt_actions = behavior_tree.root.flatten_actions() if behavior_tree and behavior_tree.root else []
        bt_skill_names = {a.skill_name for a in bt_actions}
        cg_collision_avoids = set()
        for n in constraint_graph.nodes:
            if n.constraint_type == "collision_avoid":
                cg_collision_avoids.add(n.params.get("obstacle", ""))

        # Check each obstacle in parsed_task
        for obs in (parsed_task.obstacle or []):
            obs_id = obs.entity_id or obs.mention
            if not obs_id:
                continue

            # Check grounding
            if obs.entity_id is None:
                issues.append(_issue(
                    ErrorCategory.GROUNDING,
                    ErrorCode.NEGATION_NOT_PROPAGATED,
                    f"Prohibition target '{obs.mention}' not grounded to any scene entity",
                    severity="error", subject="obstacle",
                ))
                continue

            # Check compilation into constraint graph
            eid = obs.entity_id or ""
            if obs_id not in cg_collision_avoids and eid not in cg_collision_avoids:
                issues.append(_issue(
                    ErrorCategory.CONSTRAINT,
                    ErrorCode.NEGATION_NOT_PROPAGATED,
                    f"Prohibition target '{obs.mention}' (id={obs.entity_id}) not compiled into ConstraintGraph",
                    severity="error", subject="obstacle",
                ))

            # Check BT enforcement
            bt_has_enforcement = (
                "PlanPath" in bt_skill_names or
                "Avoid" in bt_skill_names
            )
            if not bt_has_enforcement:
                issues.append(_issue(
                    ErrorCategory.CONSTRAINT,
                    ErrorCode.NEGATION_NOT_PROPAGATED,
                    f"Prohibition target '{obs.mention}' has no BT enforcement (no PlanPath/Avoid node)",
                    severity="error", subject="obstacle",
                ))

        # Check conditions in notes
        for note in (parsed_task.notes or []):
            if note.startswith("unsupported_conditional:"):
                issues.append(_issue(
                    ErrorCategory.SEMANTIC,
                    ErrorCode.CONDITIONAL_BRANCH_LOST,
                    f"Unsupported conditional structure detected: {note.split(':', 1)[1] if ':' in note else note}. "
                    f"Cannot safely execute.",
                    severity="error", subject="condition",
                ))

    # ══════════════════════════════════════════════════════════
    # PROVENANCE: traceability of inferred fields
    # ══════════════════════════════════════════════════════════

    def _validate_provenance(self, parsed_task, constraint_graph, behavior_tree, issues):
        """Inferred fields must carry source attribution; null ≠ missing."""
        # Theme must have source if present
        if parsed_task.theme:
            if not parsed_task.theme.source:
                issues.append(_issue(ErrorCategory.PROVENANCE, "THEME_NO_SOURCE",
                    "Theme entity has no source attribution", severity="warning", subject="theme"))
            # If source is 'nl' but entity_id is None, it's an ungrounded NL-only match
            if parsed_task.theme.source == "nl" and parsed_task.theme.entity_id is None:
                # Acceptable for NL-only mentions, but should have low confidence
                if parsed_task.theme.grounding_confidence > 0.5:
                    issues.append(_issue(ErrorCategory.PROVENANCE, "NL_ONLY_HIGH_CONFIDENCE",
                        "NL-only theme (no scene grounding) has high confidence >0.5",
                        severity="warning", subject="theme"))

        # User constraints must have provenance
        for constraint in parsed_task.user_constraints:
            if not constraint.provenance:
                issues.append(_issue(ErrorCategory.PROVENANCE, "CONSTRAINT_NO_PROVENANCE",
                    f"Constraint '{constraint.parameter}' has no provenance", severity="warning",
                    subject=constraint.parameter))

        # BT metadata planner must match actual generator
        bt_planner = behavior_tree.metadata.get("planner", "")
        if bt_planner and bt_planner not in ("RuleBasedPlanner", "LLMPlanner", "HybridRouter", "RuleEngine"):
            issues.append(_issue(ErrorCategory.PROVENANCE, "UNKNOWN_PLANNER",
                f"BT planner '{bt_planner}' is not a known planner", severity="warning",
                subject="planner"))

    # ══════════════════════════════════════════════════════════
    # EXECUTABILITY: final gate
    # ══════════════════════════════════════════════════════════

    def _validate_executability(self, parsed_task, resolution, issues):
        """Final executability gate: ensure all preconditions for execution are met."""
        # plan_status must be consistent with resolution
        if resolution.plan_status in self.DISPATCHABLE_STATUSES:
            # Check all required parameters have valid selected values
            for param, pres in resolution.parameters.items():
                if pres.selected_value is None:
                    issues.append(_issue(ErrorCategory.EXECUTABILITY, "PARAM_NOT_RESOLVED",
                        f"Parameter '{param}' has no selected value in dispatchable plan",
                        severity="warning", subject=param))

        # BLOCKED should have at least one reason
        if resolution.plan_status == PlanStatus.BLOCKED:
            has_block_reason = any(
                "BLOCKED" in issue.code or "block" in issue.message.lower()
                for issue in issues
            )
            if not has_block_reason and not resolution.override_ledger:
                issues.append(_issue(ErrorCategory.EXECUTABILITY, "BLOCKED_NO_REASON",
                    "Plan is BLOCKED but no blocking reason found in issues or override ledger",
                    severity="warning", subject="plan_status"))
