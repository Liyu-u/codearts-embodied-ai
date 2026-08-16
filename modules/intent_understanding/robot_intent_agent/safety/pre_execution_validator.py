"""
Pre-execution revalidation gate for v3.0.

This validator runs AFTER plan generation but BEFORE execution dispatch,
ensuring the plan is still safe given current runtime conditions.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from uuid import uuid4

from robot_intent_agent.schemas.behavior_tree import BehaviorTree
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR
from robot_intent_agent.task_semantics import PlanStatus, ValidationIssue


@dataclass
class PreExecutionRevalidationResult:
    reval_id: str = field(default_factory=lambda: f"reval-{uuid4().hex[:10]}")
    final_plan_status: PlanStatus = PlanStatus.BLOCKED
    execution_allowed: bool = False
    stop_requested: bool = False
    stop_reason: str = ""
    issues: List[Dict[str, str]] = field(default_factory=list)
    revalidated_at: str = field(default_factory=lambda: str(time.time()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reval_id": self.reval_id,
            "final_plan_status": self.final_plan_status.value,
            "execution_allowed": self.execution_allowed,
            "stop_requested": self.stop_requested,
            "stop_reason": self.stop_reason,
            "issues": self.issues,
            "revalidated_at": self.revalidated_at,
        }


class PreExecutionValidator:
    """Revalidation gate that runs before execution dispatch."""

    TTL_MS: float = 60000.0  # 60s plan TTL
    PERCEPTION_STALE_MS: float = 5000.0  # 5s perception staleness
    MAX_TARGET_POSITION_DELTA_M: float = 0.05  # 5cm max position change

    def __init__(self):
        self._stored_positions: Dict[str, tuple] = {}

    def store_target_position(self, entity_id: str, x: float, y: float, z: float) -> None:
        self._stored_positions[entity_id] = (x, y, z)

    def validate(
        self,
        ir: RobotTaskIR,
        bt: BehaviorTree,
        current_scene: SemanticSceneGraph,
        plan_age_ms: float = 0.0,
        perception_age_ms: float = 0.0,
        runtime_guard_available: bool = True,
        scene_revision_match: bool = True,
        plan_consumed: bool = False,
        plan_revoked: bool = False,
    ) -> PreExecutionRevalidationResult:
        """Revalidate a plan before execution dispatch."""
        issues: List[Dict[str, str]] = []
        stop_requested = False
        stop_reasons: List[str] = []

        # ── 1. Plan existence and id ──
        plan_id = getattr(getattr(ir, "plan_metadata", None), "plan_hash", None)
        if not plan_id:
            issues.append({"code": "MISSING_PLAN_ID", "severity": "error", "message": "Plan has no identifier"})

        # ── 2. Plan consumed or revoked ──
        if plan_consumed:
            issues.append({"code": "PLAN_CONSUMED", "severity": "error", "message": "Plan has already been consumed"})
            stop_requested = True
            stop_reasons.append("plan_already_consumed")
        if plan_revoked:
            issues.append({"code": "PLAN_REVOKED", "severity": "error", "message": "Plan has been revoked"})
            stop_requested = True
            stop_reasons.append("plan_revoked")

        # ── 3. Plan TTL ──
        if plan_age_ms > self.TTL_MS:
            issues.append({"code": "PLAN_EXPIRED", "severity": "error", "message": f"Plan age {plan_age_ms:.0f}ms exceeds TTL {self.TTL_MS:.0f}ms"})
            stop_requested = True
            stop_reasons.append("plan_expired")

        # ── 4. Scene revision ──
        if not scene_revision_match:
            issues.append({"code": "SCENE_REVISION_MISMATCH", "severity": "error", "message": "Scene revision changed since planning"})
            stop_requested = True
            stop_reasons.append("scene_revision_mismatch")

        # ── 5. Perception staleness and validity ──
        if perception_age_ms < 0:
            issues.append({"code": "PERCEPTION_TIMESTAMP_INVALID", "severity": "error", "message": f"Perception timestamp is negative ({perception_age_ms:.0f}ms)"})
            stop_requested = True
            stop_reasons.append("perception_timestamp_invalid")
        elif perception_age_ms > self.PERCEPTION_STALE_MS:
            issues.append({"code": "PERCEPTION_STALE", "severity": "error", "message": f"Perception age {perception_age_ms:.0f}ms exceeds staleness threshold"})
            stop_requested = True
            stop_reasons.append("perception_stale")

        # ── 6. Theme and role entities still exist ──
        scene_ids = {getattr(o, "id", "") for o in getattr(current_scene, "objects", [])} if current_scene else set()
        pt = getattr(ir, "parsed_task", None)
        if pt:
            # Check theme
            theme = getattr(pt, "theme", None)
            if theme:
                theme_id = getattr(theme, "entity_id", None)
                if theme_id and theme_id not in scene_ids and theme_id not in {"user", "operator"}:
                    issues.append({"code": "TARGET_MISSING", "severity": "error", "message": f"Target entity {theme_id} not found in current scene"})
                    stop_requested = True
                    stop_reasons.append("target_missing")

            # Check support_surface (critical for PLACE)
            ss = getattr(pt, "support_surface", None)
            if ss:
                ss_id = getattr(ss, "entity_id", None)
                if ss_id and ss_id not in scene_ids and ss_id != "user":
                    issues.append({"code": "SUPPORT_SURFACE_MISSING", "severity": "error", "message": f"Support surface {ss_id} not found in current scene"})
                    stop_requested = True
                    stop_reasons.append("support_surface_missing")

            # Check destination
            dest = getattr(pt, "destination", None)
            if dest:
                dest_id = getattr(dest, "entity_id", None)
                if dest_id and dest_id not in scene_ids and dest_id != "user":
                    issues.append({"code": "DESTINATION_MISSING", "severity": "error", "message": f"Destination {dest_id} not found in current scene"})
                    stop_requested = True
                    stop_reasons.append("destination_missing")

            # Check recipient
            recip = getattr(pt, "recipient", None)
            if recip:
                recip_id = getattr(recip, "entity_id", None)
                if recip_id and recip_id not in {"user", "operator"} and recip_id not in scene_ids:
                    issues.append({"code": "RECIPIENT_MISSING", "severity": "error", "message": f"Recipient {recip_id} not found in current scene"})
                    stop_requested = True
                    stop_reasons.append("recipient_missing")

        # ── 7. BT entity_ids all present ──
        scene_ids = {getattr(o, "id", "") for o in getattr(current_scene, "objects", [])} if current_scene else set()
        for action in bt.root.flatten_actions():
            tid = action.params.get("target_entity_id", "")
            if tid and tid not in scene_ids and tid not in {"user", "operator"}:
                issues.append({"code": "BT_ENTITY_MISSING", "severity": "error", "message": f"BT action {action.skill_name} references missing entity {tid}"})

        # ── 8. No error-level issues from original validation ──
        for issue in getattr(getattr(ir, "validation_result", None), "issues", []) or []:
            if getattr(issue, "severity", "") == "error":
                issues.append({"code": getattr(issue, "code", "UNKNOWN"), "severity": "error", "message": getattr(issue, "message", "")})

        # ── 9. Numeric constraints: force/velocity finite and within limits ──
        reso = getattr(ir, "constraint_resolution", None)
        if reso:
            for pname, pr in getattr(reso, "parameters", {}).items():
                sv = pr.selected_value
                if sv is not None:
                    if not math.isfinite(sv):
                        issues.append({"code": "NON_FINITE_VALUE", "severity": "error", "message": f"{pname}={sv} is not finite"})
                        stop_requested = True
                        stop_reasons.append(f"{pname}_not_finite")
                    if sv < 0:
                        issues.append({"code": "NEGATIVE_VALUE", "severity": "error", "message": f"{pname}={sv} is negative"})
                        stop_requested = True
                        stop_reasons.append(f"{pname}_negative")
                    if pr.domain.max_value is not None and sv > pr.domain.max_value + 1e-9:
                        issues.append({"code": "HARD_LIMIT_EXCEEDED", "severity": "error", "message": f"{pname}={sv} exceeds hard limit {pr.domain.max_value}"})
                        stop_requested = True
                        stop_reasons.append(f"{pname}_exceeds_limit")

        # ── 9b. Target position delta check ──
        if theme and getattr(theme, "entity_id", None):
            stored = self._stored_positions.get(getattr(theme, "entity_id", ""))
            if stored:
                for obj in getattr(current_scene, "objects", []) or []:
                    if getattr(obj, "id", "") == getattr(theme, "entity_id", ""):
                        if hasattr(obj, "position"):
                            px = getattr(obj.position, "x", 0.0)
                            py = getattr(obj.position, "y", 0.0)
                            pz = getattr(obj.position, "z", 0.0)
                            delta = ((px - stored[0])**2 + (py - stored[1])**2 + (pz - stored[2])**2)**0.5
                            if delta > self.MAX_TARGET_POSITION_DELTA_M:
                                issues.append({"code": "TARGET_POSITION_JUMPED", "severity": "error",
                                               "message": f"Target position changed by {delta:.3f}m (max {self.MAX_TARGET_POSITION_DELTA_M}m)"})
                                stop_requested = True
                                stop_reasons.append("target_position_jumped")
                        break

        # ── 9c. Material/fragility change detection ──
        for obj in getattr(current_scene, "objects", []) or []:
            if getattr(obj, "attributes", {}).get("fragile") and getattr(obj, "attributes", {}).get("material") == "glass":
                # If target suddenly became fragile, force re-check
                fr = reso.parameters.get("force_n") if reso else None
                if fr and fr.selected_value and fr.selected_value > 2.0 + 1e-9:
                    issues.append({"code": "FRAGILE_FORCE_LIMIT", "severity": "error",
                                   "message": f"Force {fr.selected_value}N exceeds fragile object limit 2.0N"})
                    stop_requested = True
                    stop_reasons.append("fragile_force_exceeded")

        # ── 10. Runtime guard availability ──
        if not runtime_guard_available:
            issues.append({"code": "RUNTIME_GUARD_UNAVAILABLE", "severity": "error", "message": "Runtime safety guard is not available"})
            stop_requested = True
            stop_reasons.append("runtime_guard_unavailable")

        # ── Determine final status ──
        has_errors = any(i.get("severity") == "error" for i in issues)
        has_missing = any("MISSING" in i.get("code", "") for i in issues)

        if stop_requested:
            plan_status = PlanStatus.BLOCKED
            execution_allowed = False
        elif has_errors:
            plan_status = PlanStatus.BLOCKED
            execution_allowed = False
        elif has_missing:
            plan_status = PlanStatus.NEEDS_CLARIFICATION
            execution_allowed = False
        else:
            existing_status = getattr(getattr(ir, "plan_metadata", None), "plan_status", PlanStatus.BLOCKED)
            existing_allowed = getattr(getattr(ir, "validation_result", None), "execution_allowed", False)
            plan_status = existing_status
            execution_allowed = existing_allowed

        return PreExecutionRevalidationResult(
            final_plan_status=plan_status,
            execution_allowed=execution_allowed,
            stop_requested=stop_requested,
            stop_reason="; ".join(stop_reasons) if stop_reasons else "",
            issues=issues,
        )


def revalidate_before_execution(
    ir: RobotTaskIR,
    bt: BehaviorTree,
    current_scene: SemanticSceneGraph,
    **kwargs,
) -> PreExecutionRevalidationResult:
    """Convenience function for pre-execution revalidation."""
    validator = PreExecutionValidator()
    return validator.validate(ir=ir, bt=bt, current_scene=current_scene, **kwargs)
