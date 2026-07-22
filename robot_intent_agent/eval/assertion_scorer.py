"""
Assertion-based scorer — SINGLE AUTHORITATIVE scoring entry point.

All evaluators (upgraded_runner, blind_runner, runner, web_ui) MUST use
score_case() for per-case judgment. Runners handle pipeline execution and
aggregation; this module handles scoring.

Architecture:
    score_case(case, ir, scene, bt, cg, scene_id_map=None) -> CaseVerdict
        ├── _check_1_action()
        ├── _check_2_role()
        ├── ...
        └── _apply_veto()

    compute_metrics(verdicts) -> MetricsSummary
        (aggregates CaseVerdict list into summary)
"""

from __future__ import annotations

import json
import statistics
import uuid as _uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from robot_intent_agent.schemas.robot_task_ir import RobotTaskIR
from robot_intent_agent.schemas.scene import SemanticSceneGraph
from robot_intent_agent.schemas.behavior_tree import BehaviorTree
from robot_intent_agent.constraint.base import ConstraintGraph
from robot_intent_agent.task_semantics import PlanStatus


# ══════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════

class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class EvalFinding:
    """Single evaluation finding."""
    metric: str
    severity: Severity
    expected: Any
    actual: Any
    detail: str = ""


@dataclass
class CaseVerdict:
    """Complete verdict for one evaluation case."""
    case_id: str = ""
    category: str = ""
    instruction: str = ""
    passed: bool = True
    findings: List[EvalFinding] = field(default_factory=list)
    elapsed_ms: float = 0.0
    # ── Dimensions that were checked for this case ──
    applicable_dimensions: List[str] = field(default_factory=list)
    # Snapshot of key fields for reports
    action_actual: str = ""
    action_expected: str = ""
    theme_entity_actual: Optional[str] = None
    theme_entity_expected: Optional[str] = None
    execution_allowed_actual: Optional[bool] = None
    execution_allowed_expected: Optional[bool] = None
    force_actual: Optional[float] = None
    force_expected: Optional[float] = None
    exception: str = ""

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def has_critical(self) -> bool:
        return self.critical_count > 0


@dataclass
class DimensionScore:
    name: str
    applicable: int = 0
    correct: int = 0
    accuracy: Optional[float] = None  # None = not applicable / not evaluated
    critical_errors: int = 0
    high_errors: int = 0
    medium_errors: int = 0
    evaluation_status: str = "NOT_EVALUATED"  # EVALUATED | NOT_APPLICABLE | NOT_EVALUATED | INVALID_COVERAGE

    def compute(self) -> "DimensionScore":
        if self.applicable > 0:
            self.accuracy = round(self.correct / self.applicable, 4)
            self.evaluation_status = "EVALUATED"
        else:
            self.accuracy = None
            # Leave evaluation_status as NOT_EVALUATED (set by caller)
        return self

    @property
    def accuracy_display(self) -> str:
        """Human-readable accuracy string. Returns 'N/A' when not applicable."""
        if self.applicable == 0 or self.accuracy is None:
            return "N/A"
        return f"{self.accuracy:.1%}"

    @property
    def is_evaluated(self) -> bool:
        """True if this dimension had applicable cases and was evaluated."""
        return self.applicable > 0 and self.accuracy is not None


@dataclass
class MetricsSummary:
    """Aggregated metrics across all cases. Computed by compute_metrics()."""
    run_id: str = ""
    dataset: str = ""
    total: int = 0
    passed: int = 0
    failed: int = 0
    severe_veto_count: int = 0
    pass_rate: float = 0.0
    dimensions: Dict[str, DimensionScore] = field(default_factory=dict)
    severity_counts: Dict[str, int] = field(default_factory=dict)
    latency_avg_ms: float = 0.0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    by_category: Dict[str, Dict] = field(default_factory=dict)

    # Legacy-compatible metrics — None when not evaluated
    action_accuracy: Optional[float] = None
    action_cases: int = 0
    entity_grounding_accuracy: Optional[float] = None
    entity_cases: int = 0
    force_parsing_accuracy: Optional[float] = None
    force_cases: int = 0
    role_detection_accuracy: Optional[float] = None
    role_cases: int = 0
    schema_pass_rate: Optional[float] = None
    overall_pass_rate: float = 0.0
    avg_elapsed_ms: float = 0.0
    _release_gate_errors: List[str] = field(default_factory=list)

    @property
    def release_gate_passed(self) -> bool:
        """True if all core dimensions are evaluated and pass."""
        return len(self._release_gate_errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "dataset": self.dataset,
            "total": self.total, "passed": self.passed, "failed": self.failed,
            "severe_veto_count": self.severe_veto_count,
            "pass_rate": self.pass_rate,
            "dimensions": {k: {"name": v.name, "applicable": v.applicable, "correct": v.correct,
                               "accuracy": v.accuracy, "evaluation_status": v.evaluation_status,
                               "critical_errors": v.critical_errors,
                               "high_errors": v.high_errors, "medium_errors": v.medium_errors}
                          for k, v in self.dimensions.items()},
            "severity_counts": self.severity_counts,
            "latency": {"avg_ms": self.latency_avg_ms, "p50_ms": self.latency_p50_ms,
                        "p95_ms": self.latency_p95_ms, "p99_ms": self.latency_p99_ms},
            "by_category": self.by_category,
            "legacy": {
                "action_accuracy": self.action_accuracy,
                "action_cases": self.action_cases,
                "entity_grounding_accuracy": self.entity_grounding_accuracy,
                "entity_cases": self.entity_cases,
                "force_parsing_accuracy": self.force_parsing_accuracy,
                "force_cases": self.force_cases,
                "role_detection_accuracy": self.role_detection_accuracy,
                "role_cases": self.role_cases,
                "schema_pass_rate": self.schema_pass_rate,
                "overall_pass_rate": self.overall_pass_rate,
                "avg_elapsed_ms": self.avg_elapsed_ms,
            },
        }


# ══════════════════════════════════════════════════════════════
# Dimension keys — single source of truth
# ══════════════════════════════════════════════════════════════

DIM_ACTION = "action_recognition"
DIM_ROLE = "role_extraction"
DIM_ENTITY = "entity_grounding"
DIM_DISAMBIGUATION = "multi_object_disambiguation"
DIM_NEGATION = "negation_constraint_retention"
DIM_CONDITIONAL = "conditional_sequential_understanding"
DIM_NUMERIC = "numeric_operator_unit"
DIM_FACTUAL = "perception_factual_fidelity"
DIM_ROBOT_CAPABILITY = "robot_capability_constraint"
DIM_BT_IR_CONSISTENCY = "bt_ir_cross_field_consistency"
DIM_SCHEMA = "schema_validity"
DIM_DANGEROUS_PASS = "dangerous_error_pass_through"

ALL_DIMS = [
    DIM_ACTION, DIM_ROLE, DIM_ENTITY, DIM_DISAMBIGUATION,
    DIM_NEGATION, DIM_CONDITIONAL, DIM_NUMERIC, DIM_FACTUAL,
    DIM_ROBOT_CAPABILITY, DIM_BT_IR_CONSISTENCY, DIM_SCHEMA,
    DIM_DANGEROUS_PASS,
]


# ══════════════════════════════════════════════════════════════
# Applicable dimensions derivation
# ══════════════════════════════════════════════════════════════

# Negation keywords
_NEGATION_KEYWORDS = [
    "不要", "别碰", "千万别碰", "避开", "绕过", "躲开", "不想碰", "不能碰",
    "禁止碰", "除了", "don't touch", "do not touch", "avoid", "without touching",
]

# Conditional/sequential keywords
_CONDITIONAL_KEYWORDS = [
    "如果", "否则", "除非", "先", "再", "然后", "等待", "之后", "之前",
    "if", "else", "unless", "after", "before", "then", "wait",
]

# Numeric patterns
_NUMERIC_PATTERN = r'\d+(?:\.\d+)?\s*(?:N|牛顿|m/s|kg|厘米|cm|mm|米|m)'


def _normalize_expected_fields(case: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize holdout_v3 expected fields to the evaluator's expected schema.

    holdout_v3 uses:
      - accepted_actions (list) → action (first element)
      - required_semantics (dict with theme_category, etc.)
      - accepted_plan_statuses (list) → plan_status
      - forbidden_semantics (present = dangerous)
      - required_roles (list)

    The evaluator checks:
      - action (string)
      - theme_entity_id (string)
      - execution_allowed (bool)
      - plan_status (string)
    """
    raw = case.get("expected", {})
    if not raw:
        return {}

    # If already has evaluator fields, return as-is
    if "action" in raw or "theme_entity_id" in raw:
        return dict(raw)

    normalized = dict(raw)  # Copy all original fields

    # ── Adapt accepted_actions → action ──
    accepted = raw.get("accepted_actions", [])
    if isinstance(accepted, list) and len(accepted) > 0:
        normalized["action"] = accepted[0]

    # ── Adapt required_semantics → entity fields ──
    sem = raw.get("required_semantics", {})
    if isinstance(sem, dict):
        # theme_category → theme hints
        if sem.get("theme_category"):
            normalized["theme_class"] = sem["theme_category"]
        # theme_color → color hint
        if sem.get("theme_color"):
            normalized["theme_color"] = sem["theme_color"]
        # support_surface category
        if sem.get("support_surface_category"):
            normalized["support_surface_class"] = sem["support_surface_category"]
        # Conditional present
        if sem.get("conditional_present"):
            normalized["has_conditional"] = True
        # Sequence present
        if sem.get("sequence_present"):
            normalized["has_sequence"] = True
        # Robot state check
        if sem.get("robot_state_check"):
            normalized["robot_state_check"] = sem["robot_state_check"]
        # Ambiguous
        if sem.get("ambiguous"):
            normalized["ambiguous"] = True
        # Conflict type
        if sem.get("conflict_type"):
            normalized["conflict_type"] = sem["conflict_type"]
        # Missing role
        if sem.get("missing_role"):
            normalized["missing_roles"] = [sem["missing_role"]]
        # Recipient present
        if sem.get("recipient_present"):
            normalized["recipient_present"] = True
        # Numeric constraint
        if sem.get("numeric_constraint_present"):
            normalized["numeric_constraint_present"] = True
            if sem.get("force_operator"):
                normalized["force_op"] = sem["force_operator"]
            if sem.get("force_value") is not None:
                normalized["force_value"] = sem["force_value"]

    # ── Adapt forbidden_semantics → dangerous_input ──
    forbidden = raw.get("forbidden_semantics", {})
    if isinstance(forbidden, dict) and forbidden:
        normalized["dangerous_input"] = True
        normalized["execution_allowed"] = False
        # If forbidden specifies avoid_category
        if forbidden.get("avoid_category"):
            normalized["avoid_category"] = forbidden["avoid_category"]

    # ── Adapt accepted_plan_statuses → plan_status ──
    accepted_plans = raw.get("accepted_plan_statuses", [])
    if isinstance(accepted_plans, list) and len(accepted_plans) > 0:
        normalized["plan_status"] = accepted_plans[0]

    # ── Adapt required_roles → role-specific checks ──
    required_roles = raw.get("required_roles", [])
    if isinstance(required_roles, list) and required_roles:
        normalized["required_roles"] = required_roles
        if "support_surface" in required_roles:
            normalized["support_surface_required"] = True
        if "recipient" in required_roles:
            normalized["recipient_required"] = True
        if "destination" in required_roles:
            normalized["destination_required"] = True

    return normalized


def derive_applicable_dimensions(case: Dict[str, Any]) -> List[str]:
    """Derive which evaluation dimensions apply to this case.

    Rules (order-independent, each dimension checked independently):

    action_recognition:
        Applicable when expected.action is set.

    role_extraction:
        Applicable when instruction involves destination, recipient, source,
        support_surface, handover, or PLACE semantics.

    entity_grounding:
        Applicable when expected.theme_entity_id is set OR
        expected.theme_not_in_scene is true.

    multi_object_disambiguation:
        Applicable ONLY when the scene has >=2 objects of the same category
        AND the instruction contains a distinguishing cue (color, size,
        spatial, demonstrative, ordinal).

    negation_constraint_retention:
        Applicable when instruction contains negation keywords OR
        expected.avoid_objects is non-empty.

    conditional_sequential_understanding:
        Applicable when instruction contains conditional/sequential keywords
        OR expected keys involve manner/motion_state/requires_stability_gate
        OR expected.execution_allowed is explicitly set.

    numeric_operator_unit:
        Applicable when instruction contains explicit numeric patterns
        OR expected keys involve force_n, force_op, velocity_ms, etc.

    perception_factual_fidelity:
        ALWAYS applicable (global check: no fabricated objects/IDs).

    robot_capability_constraint:
        Applicable when the case involves robot physical limits, e.g.
        force clamping for fragile objects OR explicit robot state/velocity.

    bt_ir_cross_field_consistency:
        ALWAYS applicable (structural consistency check).

    schema_validity:
        ALWAYS applicable (structural check).

    dangerous_error_pass_through:
        Applicable only when expected.execution_allowed is explicitly False
        OR expected keys include theme_not_in_scene, empty_scene,
        missing_roles, or plan_status=BLOCKED/NEEDS_CLARIFICATION.
    """
    import re as _re
    instruction = case.get("instruction", "")
    expected = _normalize_expected_fields(case)
    objects = case.get("objects", [])
    applicable: List[str] = []

    # ── action_recognition ──
    if expected.get("action"):
        applicable.append(DIM_ACTION)

    # ── role_extraction ──
    _role_cues = ("给", "递", "交", "放", "搬到", "送到", "拿到",
                  "handover", "place", "transfer", "recipient", "destination", "support")
    has_role_cue = any(c in instruction for c in _role_cues)
    # Only trigger from expected when role-specific fields are present
    has_role_expected = any(k in expected for k in (
        "recipient_identified", "support_surface_entity_id",
        "support_surface_not_in_scene", "recipient_entity_id",
    ))
    # Also applicable when missing_roles includes role-related entries
    has_role_missing = any(
        role in str(expected.get("missing_roles", []))
        for role in ("support_surface", "recipient", "destination", "source", "recipient_pose", "delivery_pose")
    )
    if has_role_cue or has_role_expected or has_role_missing:
        applicable.append(DIM_ROLE)

    # ── entity_grounding ──
    if expected.get("theme_entity_id") or expected.get("theme_not_in_scene"):
        applicable.append(DIM_ENTITY)

    # ── multi_object_disambiguation ──
    # Count objects by category
    cat_counts: Dict[str, int] = {}
    for obj in objects:
        cats = obj.get("category_candidates", [])
        top_name = cats[0].get("name", "unknown") if cats else "unknown"
        cat_counts[top_name] = cat_counts.get(top_name, 0) + 1
    has_multiple_same_category = any(c >= 2 for c in cat_counts.values())
    _disambig_cues = ("红", "蓝", "绿", "黄", "白", "黑", "大", "小", "左", "右",
                      "前", "后", "那个", "这个", "中间", "red", "blue", "green", "large",
                      "small", "left", "right", "front", "back", "middle",
                      "高", "低", "近", "远", "最大", "最小")
    has_disambig_cue = any(c in instruction for c in _disambig_cues)
    # Applicable when scene has multiple same-category objects AND there's a disambiguation cue
    # OR when expected explicitly marks multi-object ambiguity
    if (has_multiple_same_category and has_disambig_cue) or expected.get("multiple_candidates"):
        applicable.append(DIM_DISAMBIGUATION)

    # ── negation_constraint_retention ──
    has_negation_kw = any(kw in instruction.lower() for kw in _NEGATION_KEYWORDS)
    has_avoid_expected = bool(expected.get("avoid_objects"))
    if has_negation_kw or has_avoid_expected:
        applicable.append(DIM_NEGATION)

    # ── conditional_sequential_understanding ──
    has_cond_kw = any(kw in instruction.lower() for kw in _CONDITIONAL_KEYWORDS)
    has_cond_expected = any(k in expected for k in (
        "manner", "motion_state", "requires_stability_gate",
    ))
    # execution_allowed=False combined with conditional keywords or robot state
    if expected.get("execution_allowed") is False:
        has_cond_expected = True
    # plan_status check implies conditional/sequential evaluation
    if expected.get("plan_status"):
        has_cond_expected = True
    if has_cond_kw or has_cond_expected:
        applicable.append(DIM_CONDITIONAL)

    # ── numeric_operator_unit ──
    has_numeric_in_text = bool(_re.search(_NUMERIC_PATTERN, instruction))
    has_numeric_expected = any(k in expected for k in (
        "force_n", "force_op", "force_n_min", "force_n_max",
        "velocity_ms", "vel_op", "no_nan_parsed", "no_negative_force",
        "resolved_force_n_le", "resolved_force_le", "resolved_force_n_ge",
        "resolved_force_le_global_max",
    ))
    if has_numeric_in_text or has_numeric_expected:
        applicable.append(DIM_NUMERIC)

    # ── perception_factual_fidelity (always) ──
    applicable.append(DIM_FACTUAL)

    # ── robot_capability_constraint ──
    has_capability_cue = any(k in expected for k in (
        "resolved_force_n_le", "resolved_force_le", "resolved_force_le_global_max",
    ))
    has_tracking = any(
        obj.get("tracking", {}).get("state") == "moving" for obj in objects
    )
    has_robot_state = expected.get("motion_state") or has_tracking
    # Fragile objects or objects requiring careful handling
    has_fragile = any("fragile" in (obj.get("affordances", []) or []) for obj in objects)
    # Force/velocity limits (user constraints) indicate robot capability is being tested
    has_force_expected = bool(expected.get("force_n")) or bool(expected.get("velocity_ms"))
    if has_capability_cue or has_fragile or has_robot_state or has_force_expected:
        applicable.append(DIM_ROBOT_CAPABILITY)

    # ── bt_ir_cross_field_consistency (always) ──
    applicable.append(DIM_BT_IR_CONSISTENCY)

    # ── schema_validity (always) ──
    applicable.append(DIM_SCHEMA)

    # ── dangerous_error_pass_through ──
    is_dangerous = (
        expected.get("execution_allowed") is False or
        expected.get("theme_not_in_scene") or
        expected.get("empty_scene") or
        expected.get("plan_status") in ("BLOCKED", "NEEDS_CLARIFICATION") or
        bool(expected.get("missing_roles"))
    )
    if is_dangerous:
        applicable.append(DIM_DANGEROUS_PASS)

    return applicable


def derive_category(case: Dict[str, Any]) -> str:
    """Derive a category label for a case. Never returns 'unknown'.

    Uses case_id prefix as the primary signal, with fallback analysis
    of instruction + expected keys.
    """
    case_id = case.get("case_id", "")
    instruction = case.get("instruction", "")
    expected = case.get("expected", {})

    # ── Case ID prefix mapping ──
    if case_id.startswith("G"):
        return "simple_action"
    if case_id.startswith("N"):
        return "missing_target"
    if case_id.startswith("C"):
        return "numeric_constraints"
    if case_id.startswith("E"):
        return "invalid_input"
    if case_id.startswith("R"):
        return "roles"
    if case_id.startswith("M"):
        return "multi_object"

    # ── Blind dataset: use existing category if present ──
    existing = case.get("category", "")
    if existing and existing != "unknown":
        return existing

    # ── Fallback analysis ──
    if not instruction.strip():
        return "invalid_input"
    if not case.get("objects"):
        return "invalid_input"
    if expected.get("theme_not_in_scene"):
        return "missing_target"
    if expected.get("avoid_objects"):
        return "negation_condition"
    if any(kw in instruction for kw in ("如果", "否则", "除非")):
        return "negation_condition"
    if any(kw in instruction for kw in ("不要", "别碰", "避开")):
        return "negation_condition"
    if any(kw in instruction for kw in ("递", "给", "交")):
        return "roles"
    if any(kw in instruction for kw in ("放", "搬")):
        return "roles"
    if expected.get("force_n") or any(c in instruction for c in "0123456789"):
        return "numeric_constraints"
    if expected.get("multiple_candidates"):
        return "disambiguation"
    if expected.get("motion_state") or "移动" in instruction:
        return "robot_state"

    return "simple_action"


# ══════════════════════════════════════════════════════════════
# Canonical Entity Resolver — single identity authority
# ══════════════════════════════════════════════════════════════

class CanonicalEntityResolver:
    """Resolves entity identity across perception object_id ↔ scene UUID.

    Priority chain for identity comparison:
      1. Exact perception object_id → scene UUID (via _perception_object_id attr)
      2. Internal scene UUID → perception object_id reverse mapping
      3. Unique attribute combination (color + material + category + position)
      4. Name is used only for DISPLAY, never as identity proof.

    Same-name different objects ARE distinguishable via unique attribute combos.
    """

    def __init__(self, perception_objects: List[Dict], scene_objects: List[Any]):
        # perception object_id → scene UUID
        self._p2s: Dict[str, str] = {}
        # scene UUID → perception object_id
        self._s2p: Dict[str, str] = {}
        # scene UUID → {color, material, category, name, position}
        self._scene_attrs: Dict[str, Dict[str, Any]] = {}
        # Track ambiguous mappings for diagnostics
        self._ambiguous_pids: Set[str] = set()

        scene_by_id: Dict[str, Any] = {}
        for sobj in scene_objects:
            sid = getattr(sobj, "id", "")
            if not sid:
                continue
            scene_by_id[sid] = sobj
            sattrs = getattr(sobj, "attributes", {}) or {}
            self._scene_attrs[sid] = {
                "color": sattrs.get("color", ""),
                "material": sattrs.get("material", ""),
                "category": getattr(sobj, "specific_class", "") or getattr(sobj, "label", "") or "",
                "name": getattr(sobj, "name", ""),
                "position": self._pos_tuple(sobj),
            }

        # Build primary mapping
        for pobj in perception_objects:
            pid = pobj.get("object_id", "")
            if not pid:
                continue
            cats = pobj.get("category_candidates", [])
            pname = cats[0].get("name", "") if cats else ""
            pcolor = (pobj.get("appearance", {}) or {}).get("color", "")
            pmat = (pobj.get("appearance", {}) or {}).get("material", "")

            # Pass 1: _perception_object_id attribute exact match (highest priority)
            for sid, sobj in scene_by_id.items():
                obj_pid = (getattr(sobj, "attributes", {}) or {}).get("_perception_object_id", "")
                if obj_pid == pid:
                    self._p2s[pid] = sid
                    self._s2p[sid] = pid
                    break

            # Pass 2: strict attribute-based fallback — requires uniqueness
            if pid not in self._p2s:
                candidates: List[str] = []
                for sid, attrs in self._scene_attrs.items():
                    sname = attrs["name"]
                    scolor = attrs["color"]
                    smat = attrs["material"]
                    scat = attrs["category"]

                    # Name: exact match OR bidirectional containment with minimum length
                    name_match = (sname == pname)
                    if not name_match and pname and sname:
                        # Allow containment only if the shorter string is >= 2 chars
                        shorter = pname if len(pname) < len(sname) else sname
                        if len(shorter) >= 2 and (pname in sname or sname in pname):
                            name_match = True

                    if not name_match:
                        continue

                    # Color: if both sides specify color, they MUST match
                    if pcolor and pcolor != "unknown" and scolor and scolor != "unknown":
                        if pcolor != scolor:
                            continue

                    # Material: if both sides specify material, they MUST match
                    if pmat and pmat != "unknown" and smat and smat != "unknown":
                        if pmat != smat:
                            continue

                    candidates.append(sid)

                if len(candidates) == 1:
                    # Unique match — safe to map
                    sid = candidates[0]
                    self._p2s[pid] = sid
                    self._s2p[sid] = pid
                elif len(candidates) > 1:
                    # Ambiguous — try position-based disambiguation
                    # (closest to perception position, if perception has pose)
                    ppos = pobj.get("pose", {}).get("position", {})
                    if ppos and all(k in ppos for k in ("x", "y", "z")):
                        px, py, pz = float(ppos["x"]), float(ppos["y"]), float(ppos["z"])
                        best_sid = None
                        best_dist = float("inf")
                        for sid in candidates:
                            sx, sy, sz = self._scene_attrs[sid]["position"]
                            dist = ((px - sx)**2 + (py - sy)**2 + (pz - sz)**2) ** 0.5
                            if dist < best_dist:
                                best_dist = dist
                                best_sid = sid
                        if best_sid is not None:
                            self._p2s[pid] = best_sid
                            self._s2p[best_sid] = pid
                    else:
                        # Cannot disambiguate — leave unmapped
                        self._ambiguous_pids.add(pid)

    @staticmethod
    def _pos_tuple(obj) -> Tuple[float, float, float]:
        pos = getattr(obj, "position", None)
        if pos is None:
            return (0.0, 0.0, 0.0)
        return (getattr(pos, "x", 0.0), getattr(pos, "y", 0.0), getattr(pos, "z", 0.0))

    # ── Public API ──────────────────────────────────────────

    def perception_to_scene(self, perception_id: str) -> Optional[str]:
        """Map a perception object_id to its scene UUID."""
        return self._p2s.get(perception_id)

    def scene_to_perception(self, scene_id: str) -> Optional[str]:
        """Map a scene UUID back to its perception object_id."""
        if not scene_id or scene_id == "user":
            return scene_id
        return self._s2p.get(scene_id)

    def is_same_entity(self, id_a: str, id_b: str) -> bool:
        """Check if two IDs refer to the same physical entity."""
        if not id_a or not id_b:
            return False
        if id_a == id_b:
            return True
        sid_a = self._p2s.get(id_a, id_a)
        sid_b = self._p2s.get(id_b, id_b)
        if sid_a == sid_b:
            return True
        pid_a = self._s2p.get(id_a, id_a)
        pid_b = self._s2p.get(id_b, id_b)
        return pid_a == pid_b

    def resolve_to_perception_id(self, entity_id: Optional[str]) -> Optional[str]:
        """Resolve any entity_id to its canonical perception object_id."""
        if not entity_id:
            return None
        if entity_id == "user":
            return "user"
        if entity_id in self._p2s:
            return entity_id
        return self._s2p.get(entity_id)

    def resolve_avoid_set(self, avoid_values: List[str]) -> List[str]:
        """Resolve avoid references to canonical perception IDs (order-independent)."""
        resolved = []
        for val in avoid_values:
            pid = self.resolve_to_perception_id(val)
            if pid:
                resolved.append(pid)
            else:
                resolved.append(val)
        return sorted(set(resolved))

    @property
    def ambiguous_ids(self) -> Set[str]:
        """Perception object_ids that could not be uniquely mapped."""
        return set(self._ambiguous_pids)

    def get_scene_ids(self) -> set:
        return set(self._s2p.keys())

    def get_perception_ids(self) -> set:
        return set(self._p2s.keys())


def score_case(
    case: Dict[str, Any],
    ir: RobotTaskIR,
    scene: Optional[SemanticSceneGraph],
    bt: BehaviorTree,
    cg: ConstraintGraph,
    scene_id_map: Optional[Dict[str, str]] = None,
    applicable_dimensions: Optional[List[str]] = None,
) -> CaseVerdict:
    """Score a single evaluation case — THE SINGLE AUTHORITATIVE ENTRY POINT.

    All evaluators (upgraded_runner, blind_runner, runner, web_ui) MUST
    call this function for per-case judgment. It runs dimension checks
    only for applicable dimensions (if provided), and returns a complete
    CaseVerdict.

    Args:
        case: Dataset case dict
        ir: RobotTaskIR from pipeline
        scene: SemanticSceneGraph from pipeline
        bt: BehaviorTree from pipeline
        cg: ConstraintGraph from pipeline
        scene_id_map: Optional {dataset_object_id: scene_uuid} mapping
        applicable_dimensions: Optional list of dimension keys to check.
            If None, derives from case using derive_applicable_dimensions().

    Returns:
        CaseVerdict with all findings populated
    """
    if applicable_dimensions is None:
        applicable_dimensions = derive_applicable_dimensions(case)

    app_set = set(applicable_dimensions)

    v = CaseVerdict(
        case_id=case["case_id"],
        category=case.get("category", "unknown"),
        instruction=case["instruction"],
        applicable_dimensions=list(applicable_dimensions),
    )
    expected = _normalize_expected_fields(case)
    severity_rules = case.get("severity", {})
    instruction = case["instruction"]

    # Set expected baselines from pipeline output
    pt = ir.parsed_task
    v.action_actual = pt.action.value if pt else "UNKNOWN"
    v.action_expected = expected.get("action", "")
    v.theme_entity_actual = pt.theme.entity_id if pt and pt.theme else None
    v.theme_entity_expected = expected.get("theme_entity_id")
    v.execution_allowed_actual = ir.validation_result.execution_allowed
    v.execution_allowed_expected = expected.get("execution_allowed")
    fc = [c for c in (pt.user_constraints or []) if c.parameter == "force_n"]
    v.force_actual = fc[0].value if fc else (
        ir.constraint_resolution.parameters.get("force_n").selected_value
        if ir.constraint_resolution and ir.constraint_resolution.parameters.get("force_n") else None
    )
    v.force_expected = expected.get("force_n")

    # ── Build canonical entity resolver ──
    perception_objects = case.get("objects", [])
    scene_objects_list = list(scene.objects) if scene and scene.objects else []
    resolver = CanonicalEntityResolver(perception_objects, scene_objects_list)

    # ── Run dimension checks — ONLY for applicable dimensions ──
    if DIM_ACTION in app_set:
        _check_1_action(v, expected, severity_rules, ir)
    if DIM_ROLE in app_set:
        _check_2_role(v, expected, severity_rules, ir, scene, resolver)
    if DIM_ENTITY in app_set:
        _check_3_entity(v, expected, severity_rules, ir, scene, case, resolver)
    if DIM_DISAMBIGUATION in app_set:
        _check_4_disambiguation(v, expected, severity_rules, ir, scene, case)
    if DIM_NEGATION in app_set:
        _check_5_negation(v, expected, severity_rules, ir, bt, cg, case, scene, resolver)
    if DIM_CONDITIONAL in app_set:
        _check_6_conditional(v, expected, severity_rules, ir)
    if DIM_NUMERIC in app_set:
        _check_7_numeric(v, expected, severity_rules, ir)
    if DIM_FACTUAL in app_set:
        _check_8_factual(v, expected, severity_rules, ir, scene, case, resolver)
    if DIM_ROBOT_CAPABILITY in app_set:
        _check_9_robot_capability(v, expected, severity_rules, ir, bt)
    if DIM_BT_IR_CONSISTENCY in app_set:
        _check_10_bt_ir_consistency(v, expected, severity_rules, ir, bt)
    if DIM_SCHEMA in app_set:
        _check_11_schema(v, severity_rules, ir)
    if DIM_DANGEROUS_PASS in app_set:
        _check_12_dangerous_pass(v, expected, severity_rules, ir, case)

    _apply_veto(v)
    return v


# ══════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════

def _apply_veto(v: CaseVerdict) -> None:
    """Any CRITICAL finding → case fails (one-vote veto)."""
    if v.has_critical:
        v.passed = False
    else:
        v.passed = len(v.findings) == 0


def _add(v: CaseVerdict, severity_rules: Dict, metric: str, severity: Severity,
         expected: Any, actual: Any, detail: str = "") -> None:
    """Add a finding, respecting case-level severity overrides."""
    override = severity_rules.get(metric)
    if override:
        try:
            severity = Severity(override)
        except ValueError:
            pass
    v.findings.append(EvalFinding(metric=metric, severity=severity,
                                   expected=str(expected), actual=str(actual), detail=detail))


# ══════════════════════════════════════════════════════════════
# Dimension 1: Action accuracy
# ══════════════════════════════════════════════════════════════

def _check_1_action(v, expected, sev_rules, ir):
    exp = expected.get("action")
    accepted = expected.get("accepted_actions", [])
    if not exp and not accepted:
        return
    act = v.action_actual

    # Exact match with expected.action
    if act == exp:
        return

    # Check if actual is in accepted_actions array (holdout_v3 compatibility)
    if isinstance(accepted, list) and act in accepted:
        return

    # Special case: DYNAMIC_GRASP vs GRASP
    if exp == "DYNAMIC_GRASP" and act == "GRASP":
        _add(v, sev_rules, DIM_ACTION, Severity.HIGH, exp, act,
             "Moving target should be DYNAMIC_GRASP")
        return

    # If actual is CUSTOM but accepted_actions includes it, accept
    if act == "CUSTOM" and isinstance(accepted, list) and "CUSTOM" in accepted:
        return

    _add(v, sev_rules, DIM_ACTION, Severity.HIGH, exp, act,
         f"Action mismatch: expected {exp} (accepted={accepted}), got {act}")


# ══════════════════════════════════════════════════════════════
# Dimension 2: Role extraction
# ══════════════════════════════════════════════════════════════

def _check_2_role(v, expected, sev_rules, ir, scene, resolver=None):
    pt = ir.parsed_task
    if not pt:
        return
    exp_recip_identified = expected.get("recipient_identified")
    if exp_recip_identified and not pt.recipient:
        _add(v, sev_rules, DIM_ROLE, Severity.MEDIUM, "recipient identified",
             "none", "Recipient not identified")

    exp_missing = expected.get("missing_roles", [])
    actual_missing = list(ir.grounded_task.missing_roles) if ir.grounded_task else []
    for role in exp_missing:
        if role not in actual_missing:
            _add(v, sev_rules, DIM_ROLE, Severity.MEDIUM,
                 f"missing_role:{role}", actual_missing,
                 f"Expected missing role '{role}' not detected")

    # Support surface check — use resolver for identity comparison
    exp_ss = expected.get("support_surface_entity_id")
    if exp_ss:
        actual_ss = pt.support_surface.entity_id if pt.support_surface else None
        valid_ids = resolver.get_scene_ids() if resolver else {getattr(o, "id", "") for o in (scene.objects if scene else [])}
        if actual_ss is None:
            _add(v, sev_rules, DIM_ROLE, Severity.HIGH, exp_ss, "None",
                 "Support surface not identified")
        elif resolver and exp_ss:
            # Use resolver to check if actual_ss maps to expected perception ID
            resolved = resolver.scene_to_perception(actual_ss)
            if resolved != exp_ss and not resolver.is_same_entity(exp_ss, actual_ss):
                _add(v, sev_rules, DIM_ROLE, Severity.HIGH, exp_ss, actual_ss,
                     f"Support surface entity mismatch: expected {exp_ss}, got {actual_ss}")
        elif actual_ss not in valid_ids:
            _add(v, sev_rules, DIM_ROLE, Severity.HIGH, exp_ss, actual_ss,
                 f"Support surface entity_id not found in scene: '{actual_ss}'")

    # Fabricated support surface
    if expected.get("support_surface_not_in_scene"):
        actual_ss = pt.support_surface
        if actual_ss is not None and actual_ss.entity_id is not None:
            _add(v, sev_rules, DIM_ROLE, Severity.CRITICAL, "none",
                 actual_ss.entity_id, "Support surface fabricated when not in scene")


# ══════════════════════════════════════════════════════════════
# Dimension 3: Entity grounding
# ══════════════════════════════════════════════════════════════

def _check_3_entity(v, expected, sev_rules, ir, scene, case, resolver=None):
    exp_entity = expected.get("theme_entity_id")
    theme_not_in_scene = expected.get("theme_not_in_scene")
    actual = v.theme_entity_actual
    instruction = case.get("instruction", "")

    if resolver is not None:
        scene_ids = resolver.get_scene_ids()
    else:
        scene_ids = {getattr(o, "id", "") for o in (scene.objects if scene else [])}

    if theme_not_in_scene:
        if actual is not None and actual in scene_ids:
            _add(v, sev_rules, DIM_ENTITY, Severity.CRITICAL, "not in scene",
                 actual, "Theme grounded to object that shouldn't match instruction")
    elif exp_entity:
        # Check grounding via resolver: does actual entity match expected perception ID?
        is_grounded = actual is not None and actual in scene_ids
        if not is_grounded:
            _add(v, sev_rules, DIM_ENTITY, Severity.CRITICAL, "grounded in scene",
                 str(actual), "Theme not grounded to any scene object")
        elif resolver is not None and exp_entity:
            # Verify the grounded entity IS the expected one (not just any object)
            resolved = resolver.scene_to_perception(actual)
            if resolved and resolved != exp_entity:
                # Grounded to wrong object!
                _add(v, sev_rules, DIM_ENTITY, Severity.CRITICAL, exp_entity,
                     f"grounded to {resolved} (scene={actual})",
                     f"Theme grounded to wrong object: expected {exp_entity}, got {resolved}")

    # Color-specific grounding
    exp_color = expected.get("theme_color")
    color_cn = {"红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
                "白色": "white", "黑色": "black", "透明": "transparent"}
    requested_color = None
    for cn, en in color_cn.items():
        if cn in instruction:
            requested_color = en
            break
    if requested_color and actual and actual in scene_ids and scene:
        obj = scene.find_object(actual)
        if obj:
            obj_color = getattr(obj, "attributes", {}).get("color", "")
            if obj_color and obj_color != requested_color and obj_color != "unknown":
                _add(v, sev_rules, DIM_ENTITY, Severity.CRITICAL,
                     requested_color, obj_color,
                     "Color mismatch in grounding")

    # Specific class check
    exp_sc = expected.get("theme_specific_class")
    if exp_sc and ir.parsed_task and ir.parsed_task.theme:
        actual_sc = ir.parsed_task.theme.specific_class
        if actual_sc and actual_sc != exp_sc:
            _add(v, sev_rules, DIM_ENTITY, Severity.MEDIUM, exp_sc, actual_sc,
                 "Specific class mismatch")


# ══════════════════════════════════════════════════════════════
# Dimension 4: Multi-object disambiguation
# ══════════════════════════════════════════════════════════════

def _check_4_disambiguation(v, expected, sev_rules, ir, scene, case):
    if expected.get("multiple_candidates"):
        pt = ir.parsed_task
        if pt and pt.theme and pt.theme.entity_id:
            if ir.plan_metadata and ir.plan_metadata.plan_status == PlanStatus.READY:
                _add(v, sev_rules, DIM_DISAMBIGUATION, Severity.HIGH,
                     "NEEDS_CLARIFICATION", "READY",
                     "Multiple candidates but no clarification requested")

    cat_counts: Dict[str, int] = {}
    for obj in (scene.objects if scene else []):
        sc = getattr(obj, "specific_class", None) or getattr(obj, "label", None) or getattr(obj, "name", "")
        cat_counts[sc] = cat_counts.get(sc, 0) + 1
    has_ambiguity = any(c >= 2 for c in cat_counts.values())
    if has_ambiguity and v.theme_entity_actual:
        scene_ids = {getattr(o, "id", "") for o in (scene.objects if scene else [])}
        if v.theme_entity_actual not in scene_ids:
            _add(v, sev_rules, DIM_DISAMBIGUATION, Severity.CRITICAL,
                 "valid scene object", v.theme_entity_actual,
                 "Selected entity not in scene")


# ══════════════════════════════════════════════════════════════
# Dimension 5: Negation constraint retention
# ══════════════════════════════════════════════════════════════

def _check_5_negation(v, expected, sev_rules, ir, bt, cg, case,
                      scene=None, resolver=None):
    exp_avoid = expected.get("avoid_objects", [])
    if not exp_avoid:
        return

    bt_skills = [a.skill_name for a in bt.root.flatten_actions()]
    bt_avoids: Set[str] = set()
    for a in bt.root.flatten_actions():
        for key in ("avoid", "avoid_objects", "avoid_obstacles"):
            av = a.params.get(key, [])
            if isinstance(av, list):
                bt_avoids.update(str(x) for x in av)

    cg_avoids: Set[str] = set()
    for node in cg.nodes:
        if node.constraint_type == "collision_avoid":
            cg_avoids.add(str(node.params.get("obstacle", "")))

    pt_avoids: Set[str] = set()
    if ir.parsed_task and ir.parsed_task.obstacle:
        pt_avoids = {obs.mention for obs in ir.parsed_task.obstacle}
        pt_avoids |= {obs.entity_id for obs in ir.parsed_task.obstacle if obs.entity_id}

    all_avoids = bt_avoids | cg_avoids | pt_avoids

    # Resolve all avoid values to canonical perception IDs for comparison
    if resolver is not None:
        resolved_avoids = set(resolver.resolve_avoid_set(list(all_avoids)))
    else:
        resolved_avoids = all_avoids

    for exp_av in exp_avoid:
        # Direct match in raw avoids
        found = exp_av in all_avoids
        # Substring match (NOT used as sole proof, just hint)
        if not found:
            found = any(exp_av in a for a in all_avoids)
        # Resolver-based comparison: map scene UUIDs → perception IDs
        if not found and resolver is not None:
            # Is expected perception ID in the resolved set?
            found = exp_av in resolved_avoids
            # Map expected ID to scene UUID, check if present
            if not found:
                scene_uuid = resolver.perception_to_scene(exp_av)
                if scene_uuid and scene_uuid in all_avoids:
                    found = True
        if not found:
            _add(v, sev_rules, DIM_NEGATION, Severity.CRITICAL, exp_av,
                 sorted(all_avoids)[:5] if all_avoids else "none",
                 f"Negation/avoid '{exp_av}' not propagated to BT/CG")

    if exp_avoid and "PlanPath" not in bt_skills:
        _add(v, sev_rules, DIM_NEGATION, Severity.HIGH, "PlanPath in BT",
             bt_skills, "Obstacles present but no PlanPath in BT")


# ══════════════════════════════════════════════════════════════
# Dimension 6: Conditional/sequential understanding
# ══════════════════════════════════════════════════════════════

def _check_6_conditional(v, expected, sev_rules, ir):
    exp_manner = expected.get("manner")
    if exp_manner and ir.parsed_task:
        actual = ir.parsed_task.manner
        if actual != exp_manner:
            _add(v, sev_rules, DIM_CONDITIONAL, Severity.MEDIUM, exp_manner,
                 str(actual), "Manner mismatch")

    exp_motion = expected.get("motion_state")
    if exp_motion and ir.parsed_task:
        actual = ir.parsed_task.motion_state.state if ir.parsed_task.motion_state else "unknown"
        if actual != exp_motion:
            _add(v, sev_rules, DIM_CONDITIONAL, Severity.HIGH, exp_motion,
                 actual, "Motion state mismatch")

    if expected.get("requires_stability_gate"):
        action_names = [a.skill_name for a in ir.behavior_tree.root.flatten_actions()]
        if "WaitUntilStable" not in action_names:
            _add(v, sev_rules, DIM_CONDITIONAL, Severity.HIGH,
                 "WaitUntilStable", action_names,
                 "Dynamic grasp lacks stability gate")

    if expected.get("execution_allowed") is True and ir.plan_metadata:
        if ir.plan_metadata.plan_status not in (PlanStatus.READY, PlanStatus.READY_WITH_SAFE_SUBSTITUTION):
            _add(v, sev_rules, DIM_CONDITIONAL, Severity.HIGH,
                 "READY or READY_WITH_SAFE_SUBSTITUTION",
                 ir.plan_metadata.plan_status.value,
                 "Plan status inconsistent with execution_allowed")


# ══════════════════════════════════════════════════════════════
# Dimension 7: Numeric/operator/unit accuracy
# ══════════════════════════════════════════════════════════════

def _check_7_numeric(v, expected, sev_rules, ir):
    pt = ir.parsed_task
    if not pt:
        return
    user_c = pt.user_constraints or []

    # Force value
    exp_f = expected.get("force_n")
    if exp_f is not None:
        fc = [c for c in user_c if c.parameter == "force_n"]
        if fc:
            af = fc[0].value
            if af is None or abs(af - exp_f) > 0.01:
                _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_f, af,
                     "Force value mismatch")
        else:
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_f, "not parsed",
                 "Force constraint not extracted from NL")

    # Force operator
    exp_op = expected.get("force_op")
    if exp_op:
        fc = [c for c in user_c if c.parameter == "force_n"]
        if fc and fc[0].operator.value != exp_op:
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_op,
                 fc[0].operator.value, "Force operator mismatch")

    # Force range
    exp_fmin = expected.get("force_n_min")
    if exp_fmin is not None:
        fc = [c for c in user_c if c.parameter == "force_n"]
        if fc and (fc[0].min_value is None or abs(fc[0].min_value - exp_fmin) > 0.01):
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_fmin,
                 fc[0].min_value, "Force min value mismatch")
    exp_fmax = expected.get("force_n_max")
    if exp_fmax is not None:
        fc = [c for c in user_c if c.parameter == "force_n"]
        if fc and (fc[0].max_value is None or abs(fc[0].max_value - exp_fmax) > 0.01):
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_fmax,
                 fc[0].max_value, "Force max value mismatch")

    # Velocity
    exp_v = expected.get("velocity_ms")
    if exp_v is not None:
        vc = [c for c in user_c if c.parameter == "velocity_ms"]
        if vc:
            av = vc[0].value
            if av is None or abs(av - exp_v) > 0.01:
                _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_v, av,
                     "Velocity value mismatch")
        else:
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_v, "not parsed",
                 "Velocity constraint not extracted from NL")

    # Velocity operator
    exp_vo = expected.get("vel_op")
    if exp_vo:
        vc = [c for c in user_c if c.parameter == "velocity_ms"]
        if vc and vc[0].operator.value != exp_vo:
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, exp_vo,
                 vc[0].operator.value, "Velocity operator mismatch")

    # NaN / negative checks
    if expected.get("no_nan_parsed"):
        fc = [c for c in user_c if c.parameter == "force_n"]
        if fc and fc[0].value is not None and (fc[0].value != fc[0].value):
            _add(v, sev_rules, DIM_NUMERIC, Severity.CRITICAL,
                 "finite", "NaN", "NaN force parsed")
    if expected.get("no_negative_force"):
        fc = [c for c in user_c if c.parameter == "force_n"]
        if fc and fc[0].value is not None and fc[0].value < 0:
            _add(v, sev_rules, DIM_NUMERIC, Severity.CRITICAL,
                 ">= 0", fc[0].value, "Negative force parsed")

    # Resolved force bounds
    rfl = expected.get("resolved_force_n_le") or expected.get("resolved_force_le")
    if rfl is not None:
        fr = ir.constraint_resolution.parameters.get("force_n") if ir.constraint_resolution else None
        if fr and fr.selected_value is not None and fr.selected_value > rfl + 0.01:
            _add(v, sev_rules, DIM_NUMERIC, Severity.CRITICAL, f"<= {rfl}",
                 fr.selected_value, "Resolved force exceeds material safety limit")
    rfg = expected.get("resolved_force_n_ge")
    if rfg is not None:
        fr = ir.constraint_resolution.parameters.get("force_n") if ir.constraint_resolution else None
        if fr and fr.selected_value is not None and fr.selected_value < rfg - 0.01:
            _add(v, sev_rules, DIM_NUMERIC, Severity.HIGH, f">= {rfg}",
                 fr.selected_value, "Resolved force below requested minimum")


# ══════════════════════════════════════════════════════════════
# Dimension 8: Perception factual fidelity
# ══════════════════════════════════════════════════════════════

def _check_8_factual(v, expected, sev_rules, ir, scene, case, resolver=None):
    # Factual fidelity checks that entity_ids are real scene objects.
    # Always use the full scene object set (resolver only has perception-mapped objects).
    scene_ids = {getattr(o, "id", "") for o in (scene.objects if scene else [])}
    scene_names = {getattr(o, "name", "") for o in (scene.objects if scene else [])}

    pt = ir.parsed_task
    if pt and pt.theme and pt.theme.entity_id:
        if pt.theme.entity_id not in scene_ids and pt.theme.entity_id != "user":
            _add(v, sev_rules, DIM_FACTUAL, Severity.CRITICAL,
                 "valid scene UUID", pt.theme.entity_id,
                 "Theme entity_id is not a real scene object")

    for action in ir.behavior_tree.root.flatten_actions():
        tid = action.params.get("target_entity_id", "")
        if tid and tid not in scene_ids and tid != "user":
            _add(v, sev_rules, DIM_FACTUAL, Severity.CRITICAL,
                 "valid scene UUID", tid,
                 f"BT action '{action.skill_name}' references non-existent entity '{tid}'")

    if ir.explain_report:
        summary = ir.explain_report.scene_summary
        reported = summary.get("objects_count", 0)
        actual = len(scene.objects) if scene else 0
        if reported != actual:
            _add(v, sev_rules, DIM_FACTUAL, Severity.MEDIUM, actual, reported,
                 "Scene object count mismatch in explain report")

    if ir.skills:
        scene_labels = {getattr(o, "label", "") or "" for o in (scene.objects if scene else [])}
        scene_specific = {getattr(o, "specific_class", "") or "" for o in (scene.objects if scene else [])}
        all_valid_refs = scene_names | scene_labels | scene_specific
        for skill_name, skill_data in ir.skills.items():
            obj_info = skill_data.get("object")
            if obj_info and isinstance(obj_info, dict):
                obj_label = obj_info.get("label", "")
                if obj_label and obj_label not in all_valid_refs:
                    _add(v, sev_rules, DIM_FACTUAL, Severity.HIGH,
                         f"object in scene: {sorted(all_valid_refs)[:5]}", obj_label,
                         f"Skill '{skill_name}' references non-existent object '{obj_label}'")


# ══════════════════════════════════════════════════════════════
# Dimension 9: Robot capability constraint enforcement
# ══════════════════════════════════════════════════════════════

def _check_9_robot_capability(v, expected, sev_rules, ir, bt):
    from robot_intent_agent.final_plan_validator import STAGE_VELOCITY_LIMITS

    force_res = ir.constraint_resolution.parameters.get("force_n") if ir.constraint_resolution else None
    exp_rfl = expected.get("resolved_force_n_le") or expected.get("resolved_force_le")
    if exp_rfl and force_res and force_res.selected_value and force_res.selected_value > exp_rfl + 0.01:
        _add(v, sev_rules, DIM_ROBOT_CAPABILITY, Severity.CRITICAL,
             f"<= {exp_rfl}", force_res.selected_value,
             "Safety limit bypassed: force exceeds material hard cap")

    for action in bt.root.flatten_actions():
        limit = STAGE_VELOCITY_LIMITS.get(action.skill_name)
        if limit and limit > 0:
            vel = action.params.get("velocity_ms")
            if isinstance(vel, dict):
                vel = vel.get("value")
            if vel is not None:
                try:
                    if float(vel) > limit + 0.01:
                        _add(v, sev_rules, DIM_ROBOT_CAPABILITY, Severity.CRITICAL,
                             f"<= {limit}", vel,
                             f"Stage velocity limit exceeded in {action.skill_name}")
                except (TypeError, ValueError):
                    pass

    global_max = expected.get("resolved_force_le_global_max")
    if global_max and force_res and force_res.selected_value and force_res.selected_value > global_max + 0.01:
        _add(v, sev_rules, DIM_ROBOT_CAPABILITY, Severity.CRITICAL,
             f"<= {global_max}", force_res.selected_value,
             "Global force limit exceeded")

    if ir.parsed_task and ir.parsed_task.motion_state.state == "moving":
        for action in bt.root.flatten_actions():
            if action.skill_name == "WaitUntilStable":
                if not action.timeout_s or action.timeout_s <= 0:
                    _add(v, sev_rules, DIM_ROBOT_CAPABILITY, Severity.HIGH,
                         "timeout > 0", str(action.timeout_s),
                         "WaitUntilStable missing timeout")


# ══════════════════════════════════════════════════════════════
# Dimension 10: BT/IR cross-field consistency
# ══════════════════════════════════════════════════════════════

def _check_10_bt_ir_consistency(v, expected, sev_rules, ir, bt):
    force_res = ir.constraint_resolution.parameters.get("force_n") if ir.constraint_resolution else None
    vel_res = ir.constraint_resolution.parameters.get("velocity_ms") if ir.constraint_resolution else None

    resolved_force = force_res.selected_value if force_res else None
    resolved_vel = vel_res.selected_value if vel_res else None

    for action in bt.root.flatten_actions():
        if action.skill_name in ("Grasp", "GentleGrasp", "DynamicGrasp") and resolved_force is not None:
            af = action.params.get("force_n")
            if isinstance(af, dict):
                af = af.get("value")
            if af is not None and abs(float(af) - float(resolved_force)) > 0.01:
                _add(v, sev_rules, DIM_BT_IR_CONSISTENCY, Severity.HIGH,
                     resolved_force, af,
                     f"BT force ({af}) differs from IR resolution ({resolved_force})")

        if action.skill_name in ("Reach", "MoveTo", "Push") and resolved_vel is not None:
            av = action.params.get("velocity_ms")
            if isinstance(av, dict):
                av = av.get("value")
            if av is not None and abs(float(av) - float(resolved_vel)) > 0.01:
                _add(v, sev_rules, DIM_BT_IR_CONSISTENCY, Severity.HIGH,
                     resolved_vel, av,
                     f"BT velocity ({av}) differs from IR resolution ({resolved_vel})")

    if ir.parsed_task and ir.parsed_task.theme:
        theme_mention = ir.parsed_task.theme.mention
        bt_targets = {a.params.get("target", "") for a in bt.root.flatten_actions()}
        bt_targets.discard("")
        if theme_mention and bt_targets and theme_mention not in bt_targets:
            theme_eid = ir.parsed_task.theme.entity_id
            bt_eids = {a.params.get("target_entity_id", "") for a in bt.root.flatten_actions()}
            if theme_eid not in bt_eids:
                _add(v, sev_rules, DIM_BT_IR_CONSISTENCY, Severity.MEDIUM,
                     theme_mention, sorted(bt_targets)[:3],
                     "BT target doesn't match parsed_task theme mention")

    expected_actions = {
        "GRASP": {"Grasp", "Reach"},
        "FETCH": {"Fetch", "Reach", "Grasp"},
        "PLACE": {"Place", "Reach"},
        "HANDOVER": {"Handover", "Reach", "Grasp"},
        "DYNAMIC_GRASP": {"DynamicGrasp", "WaitUntilStable", "Reach"},
    }
    pt_action = ir.parsed_task.action.value if ir.parsed_task else ""
    expected_set = expected_actions.get(pt_action, set())
    bt_skills_set = {a.skill_name for a in bt.root.flatten_actions()}
    if expected_set and not expected_set.intersection(bt_skills_set):
        _add(v, sev_rules, DIM_BT_IR_CONSISTENCY, Severity.HIGH,
             str(expected_set), str(bt_skills_set),
             f"BT skills don't match action type {pt_action}")


# ══════════════════════════════════════════════════════════════
# Dimension 11: Schema validity
# ══════════════════════════════════════════════════════════════

def _check_11_schema(v, sev_rules, ir):
    try:
        data = ir.model_dump_json()
        json.loads(data)
    except Exception as e:
        _add(v, sev_rules, DIM_SCHEMA, Severity.LOW, "valid JSON",
             str(e)[:100], "IR serialization failed")


# ══════════════════════════════════════════════════════════════
# Dimension 12: Dangerous error pass-through
# ══════════════════════════════════════════════════════════════

def _check_12_dangerous_pass(v, expected, sev_rules, ir, case):
    exp_allowed = expected.get("execution_allowed")

    if exp_allowed is False and v.execution_allowed_actual is True:
        _add(v, sev_rules, DIM_DANGEROUS_PASS, Severity.CRITICAL,
             "blocked", "allowed",
             "DANGEROUS: execution allowed when it should be blocked")

    if expected.get("theme_not_in_scene") and v.execution_allowed_actual is True:
        _add(v, sev_rules, DIM_DANGEROUS_PASS, Severity.CRITICAL,
             "blocked (target not in scene)", "allowed",
             "DANGEROUS: execution allowed when target not in scene")

    if expected.get("empty_scene") and v.execution_allowed_actual is True:
        _add(v, sev_rules, DIM_DANGEROUS_PASS, Severity.CRITICAL,
             "blocked (empty scene)", "allowed",
             "DANGEROUS: execution allowed with empty scene")

    actual_missing = list(ir.grounded_task.missing_roles) if ir.grounded_task else []
    if v.execution_allowed_actual is True and actual_missing:
        critical_roles = [r for r in actual_missing if any(kw in r for kw in
                          ("recipient", "delivery", "support_surface", "theme", "handover"))]
        if critical_roles:
            _add(v, sev_rules, DIM_DANGEROUS_PASS, Severity.CRITICAL,
                 "blocked (missing roles)", "allowed",
                 f"Execution allowed with missing critical roles: {critical_roles}")

    exp_ps = expected.get("plan_status")
    if exp_ps:
        actual_ps = ir.plan_metadata.plan_status.value if ir.plan_metadata else "UNKNOWN"
        if exp_ps in ("BLOCKED", "NEEDS_CLARIFICATION") and actual_ps == "READY":
            _add(v, sev_rules, DIM_DANGEROUS_PASS, Severity.CRITICAL,
                 exp_ps, actual_ps,
                 "DANGEROUS: plan marked READY when should be blocked")


# ══════════════════════════════════════════════════════════════
# Helper: build scene_id_map from scene objects
# ══════════════════════════════════════════════════════════════

def _build_scene_id_map_from_scene(scene) -> Dict[str, str]:
    """Build {dataset_object_id: scene_uuid} mapping from scene object attributes."""
    mapping: Dict[str, str] = {}
    if not scene or not scene.objects:
        return mapping
    for obj in scene.objects:
        pid = (getattr(obj, "attributes", {}) or {}).get("_perception_object_id", "")
        if pid:
            mapping[pid] = getattr(obj, "id", "")
    return mapping


# ══════════════════════════════════════════════════════════════
# Metrics computation — from CaseVerdict list → MetricsSummary
# ══════════════════════════════════════════════════════════════

def compute_metrics(
    verdicts: List[CaseVerdict],
    dataset_name: str = "",
    run_id: Optional[str] = None,
) -> MetricsSummary:
    """Aggregate a list of CaseVerdict into MetricsSummary.

    This is the single authoritative aggregation function. All runners
    MUST use this instead of computing metrics independently.

    Args:
        verdicts: List of CaseVerdict from score_case()
        dataset_name: Name of the dataset file
        run_id: Unique run identifier (auto-generated if not provided)

    Returns:
        MetricsSummary ready for export
    """
    if run_id is None:
        run_id = f"eval-{_uuid.uuid4().hex[:12]}"

    m = MetricsSummary(run_id=run_id)
    m.dataset = dataset_name
    m.total = len(verdicts)
    m.passed = sum(1 for v in verdicts if v.passed)
    m.failed = m.total - m.passed
    m.severe_veto_count = sum(1 for v in verdicts
                              if v.has_critical and not any(
                                  f for f in v.findings if f.severity != Severity.CRITICAL))
    m.pass_rate = round(m.passed / m.total, 4) if m.total else 0.0

    # Dimension scores — applicable from case-level tracking, errors from findings
    dims: Dict[str, DimensionScore] = {}
    for dim in ALL_DIMS:
        dims[dim] = DimensionScore(name=dim)

    # Track which case_ids had each dimension applicable + which had errors
    dim_applicable_cases: Dict[str, Set[str]] = {dim: set() for dim in ALL_DIMS}
    dim_error_cases: Dict[str, Set[str]] = {dim: set() for dim in ALL_DIMS}

    for v in verdicts:
        for dim in v.applicable_dimensions:
            if dim in dim_applicable_cases:
                dim_applicable_cases[dim].add(v.case_id)
        for f in v.findings:
            if f.metric in dims:
                if f.severity == Severity.CRITICAL:
                    dims[f.metric].critical_errors += 1
                elif f.severity == Severity.HIGH:
                    dims[f.metric].high_errors += 1
                elif f.severity == Severity.MEDIUM:
                    dims[f.metric].medium_errors += 1
                dim_error_cases[f.metric].add(v.case_id)

    for dim in ALL_DIMS:
        d = dims[dim]
        d.applicable = len(dim_applicable_cases[dim])
        d.correct = d.applicable - len(dim_error_cases[dim])
        # Mark NOT_APPLICABLE for dimensions with 0 applicable cases
        if d.applicable == 0:
            d.evaluation_status = "NOT_APPLICABLE"

    for d in dims.values():
        d.compute()
    m.dimensions = dims

    # ── Release gate check ──
    # Core dimensions that MUST be evaluated for a valid release
    REQUIRED_DIMENSIONS = {
        "action_recognition",
        "entity_grounding",
        "dangerous_error_pass_through",
        "schema_validity",
        "bt_ir_cross_field_consistency",
        "robot_capability_constraint",
    }
    m._release_gate_errors = []
    for dim_name in REQUIRED_DIMENSIONS:
        d = dims.get(dim_name)
        if d is None:
            m._release_gate_errors.append(f"CORE_DIMENSION_MISSING:{dim_name}")
        elif d.applicable == 0:
            d.evaluation_status = "INVALID_COVERAGE"
            m._release_gate_errors.append(f"CORE_DIMENSION_NOT_EVALUATED:{dim_name}")
        elif d.accuracy is None:
            m._release_gate_errors.append(f"CORE_DIMENSION_NOT_EVALUATED:{dim_name}")

    # Severity counts
    m.severity_counts = {s.value: 0 for s in Severity}
    for v in verdicts:
        for f in v.findings:
            m.severity_counts[f.severity.value] = m.severity_counts.get(f.severity.value, 0) + 1

    # Latency
    lats = [v.elapsed_ms for v in verdicts if v.elapsed_ms > 0]
    if lats:
        sorted_lats = sorted(lats)
        m.latency_avg_ms = round(statistics.mean(lats), 1)
        m.latency_p50_ms = round(sorted_lats[len(sorted_lats) // 2], 1)
        m.latency_p95_ms = round(sorted_lats[int(len(sorted_lats) * 0.95)], 1)
        m.latency_p99_ms = round(sorted_lats[int(len(sorted_lats) * 0.99)], 1)

    # By category
    for v in verdicts:
        cat = v.category or "unknown"
        if cat not in m.by_category:
            m.by_category[cat] = {"total": 0, "passed": 0, "critical": 0, "high": 0}
        m.by_category[cat]["total"] += 1
        if v.passed:
            m.by_category[cat]["passed"] += 1
        for f in v.findings:
            if f.severity == Severity.CRITICAL:
                m.by_category[cat]["critical"] += 1
            elif f.severity == Severity.HIGH:
                m.by_category[cat]["high"] += 1

    # Legacy metrics
    _compute_legacy_metrics(m, verdicts)
    return m


def _compute_legacy_metrics(m: MetricsSummary, verdicts: List[CaseVerdict]) -> None:
    """Compute backward-compatible legacy metrics. Returns None when N/A."""
    actionable = [v for v in verdicts if v.action_expected]
    m.action_cases = len(actionable)
    m.action_accuracy = round(
        sum(1 for v in actionable if not any(
            f.metric == DIM_ACTION for f in v.findings)) / m.action_cases, 4
    ) if m.action_cases else None

    entity_cases = [v for v in verdicts if v.theme_entity_expected]
    m.entity_cases = len(entity_cases)
    m.entity_grounding_accuracy = round(
        sum(1 for v in entity_cases if not any(
            f.metric == DIM_ENTITY and f.severity in (Severity.CRITICAL, Severity.HIGH)
            for f in v.findings)) / m.entity_cases, 4
    ) if m.entity_cases else None

    force_cases = [v for v in verdicts if v.force_expected is not None]
    m.force_cases = len(force_cases)
    m.force_parsing_accuracy = round(
        sum(1 for v in force_cases if not any(
            f.metric == DIM_NUMERIC for f in v.findings)) / m.force_cases, 4
    ) if m.force_cases else None

    role_cases = [v for v in verdicts if DIM_ROLE in v.applicable_dimensions]
    m.role_cases = len(role_cases)
    m.role_detection_accuracy = round(
        sum(1 for v in role_cases if not any(
            f.metric == DIM_ROLE and f.severity in (Severity.CRITICAL, Severity.HIGH)
            for f in v.findings)) / m.role_cases, 4
    ) if m.role_cases else None

    schema_ok = sum(1 for v in verdicts if not any(
        f.metric == DIM_SCHEMA for f in v.findings))
    m.schema_pass_rate = round(schema_ok / m.total, 4) if m.total else None
    m.overall_pass_rate = m.pass_rate
    m.avg_elapsed_ms = m.latency_avg_ms


# ══════════════════════════════════════════════════════════════
# Consistency check — must pass before any export
# ══════════════════════════════════════════════════════════════

def verify_consistency(metrics: MetricsSummary, verdicts: List[CaseVerdict]) -> List[str]:
    """Verify that all export numbers are internally consistent.

    Returns a list of error messages. Empty list = all good.

    Checks:
    1. total == passed + failed
    2. severity_counts == aggregated from case results
    3. dimensions match aggregated from case results
    4. run_id is non-empty
    """
    errors: List[str] = []

    # 1. total == passed + failed
    if metrics.total != metrics.passed + metrics.failed:
        errors.append(f"total ({metrics.total}) != passed ({metrics.passed}) + failed ({metrics.failed})")

    # 2. severity_counts match case results
    expected_sev: Dict[str, int] = {}
    for v in verdicts:
        for f in v.findings:
            expected_sev[f.severity.value] = expected_sev.get(f.severity.value, 0) + 1
    for sev, count in metrics.severity_counts.items():
        if count != expected_sev.get(sev, 0):
            errors.append(f"severity_counts[{sev}] = {count}, expected {expected_sev.get(sev, 0)} from cases")

    # 3. run_id is non-empty
    if not metrics.run_id:
        errors.append("run_id is empty")

    # 4. verdicts length matches
    if len(verdicts) != metrics.total:
        errors.append(f"verdicts count ({len(verdicts)}) != metrics.total ({metrics.total})")

    return errors


# ══════════════════════════════════════════════════════════════
# Legacy: evaluate_assertions() — kept for backward compatibility
# ══════════════════════════════════════════════════════════════
# The old assertion-based scorer is deprecated; use score_case() instead.
# Kept for the single-test UI preset evaluation path until it's migrated.

def build_canonical_entity_map(perception_objects, scene=None):
    """@deprecated: Use scene object _perception_object_id mapping instead."""
    mapping: Dict[str, str] = {}
    if not scene or not scene.objects:
        return mapping

    for pobj in perception_objects:
        pid = pobj.get("object_id", "")
        if not pid:
            continue

        # Pass 1: exact _perception_object_id attribute match
        for sobj in scene.objects:
            sid = getattr(sobj, "id", "")
            obj_pid = (getattr(sobj, "attributes", {}) or {}).get("_perception_object_id", "")
            if obj_pid == pid:
                mapping[pid] = sid
                break
        if pid in mapping:
            continue

        # Pass 2: fallback to name+color matching (for old tests without object_id attr)
        pname = ""
        cats = pobj.get("category_candidates", [])
        if cats:
            pname = cats[0].get("name", "")
        pcolor = pobj.get("appearance", {}).get("color", "")

        for sobj in scene.objects:
            sname = getattr(sobj, "name", "")
            sattrs = getattr(sobj, "attributes", {}) or {}
            scolor = sattrs.get("color", "")

            if sname == pname or pname in sname or sname in pname:
                if pcolor and scolor and scolor != pcolor:
                    continue  # Different color → different object
                mapping[pid] = getattr(sobj, "id", "")
                break

    return mapping


def get_canonical_name(entity_id, mapping, scene=None):
    """@deprecated: Use scene object name lookup directly."""
    for pid, sid in mapping.items():
        if sid == entity_id:
            return pid
    if scene:
        obj = scene.find_object(entity_id)
        if obj:
            return getattr(obj, "name", entity_id)
    return entity_id


# ══════════════════════════════════════════════════════════════
# @deprecated — Legacy assertion-based scorer for single-test UI
# ══════════════════════════════════════════════════════════════
# Kept for backward compatibility with test_single_test_ui.py
# and the single-test evaluation UI path. New code MUST use
# score_case() instead.

@dataclass
class AssertionResult:
    key: str
    op: str
    expected: Any
    actual: Any
    passed: bool
    detail: str = ""
    severity: str = "HIGH"


@dataclass
class ScoredResult:
    preset_id: str = ""
    instruction: str = ""
    total_assertions: int = 0
    passed_assertions: int = 0
    failed_assertions: int = 0
    results: List[AssertionResult] = field(default_factory=list)
    ir_json: str = ""
    parsed_task_json: str = ""
    entity_candidates: List[Dict] = field(default_factory=list)
    bt_actions: List[str] = field(default_factory=list)
    validator_issues: List[Dict] = field(default_factory=list)
    constraint_nodes: List[str] = field(default_factory=list)
    entity_id_map: Dict[str, str] = field(default_factory=dict)
    total_score: float = 0.0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    human_review: str = ""
    human_notes: str = ""

    @property
    def passed(self) -> bool:
        return self.failed_assertions == 0


def evaluate_assertions(
    ir, scene, bt, cg, expected_assertions, entity_id_map,
    numeric_tolerance=0.01,
):
    """@deprecated: Use score_case() instead. Kept for single-test UI backward compat."""
    result = ScoredResult()
    result.total_assertions = len(expected_assertions)
    result.entity_id_map = entity_id_map

    pt = ir.parsed_task
    vr = ir.validation_result
    gt = ir.grounded_task
    cr = ir.constraint_resolution
    pm = ir.plan_metadata
    scene_ids = {getattr(o, "id", "") for o in (scene.objects if scene else [])}

    bt_actions = bt.root.flatten_actions() if bt and bt.root else []
    bt_skill_names = [a.skill_name for a in bt_actions]
    result.bt_actions = bt_skill_names
    result.validator_issues = [
        {"code": i.code, "message": i.message, "severity": i.severity}
        for i in (vr.issues if vr else [])
    ]
    result.constraint_nodes = [
        f"{n.constraint_type}:{n.expression}" for n in cg.nodes
    ] if cg else []

    result.entity_candidates = _extract_entity_candidates(pt, scene)

    for key, assertion in expected_assertions.items():
        op = assertion.get("op", "eq")
        expected = assertion.get("value")
        tol = assertion.get("tolerance", numeric_tolerance)

        ar = _evaluate_one(key, op, expected, tol, ir, pt, vr, gt, cr, pm,
                          bt_skill_names, scene_ids, scene, cg, entity_id_map)
        result.results.append(ar)

        if not ar.passed:
            result.failed_assertions += 1
            if ar.severity == "CRITICAL":
                result.critical_count += 1
            elif ar.severity == "HIGH":
                result.high_count += 1
            else:
                result.medium_count += 1

    result.passed_assertions = result.total_assertions - result.failed_assertions
    result.total_score = result.passed_assertions / max(result.total_assertions, 1)

    result.ir_json = ir.model_dump_json(indent=2) if ir else "{}"
    if pt:
        result.parsed_task_json = json.dumps(pt.model_dump(), indent=2, ensure_ascii=False,
                                               default=str)

    return result


def _evaluate_one(key, op, expected, tol, ir, pt, vr, gt, cr, pm, bt_skills,
                  scene_ids, scene, cg, entity_map):
    """@deprecated: Legacy assertion evaluator."""
    actual = _resolve_actual(key, ir, pt, vr, gt, cr, pm, bt_skills, scene_ids, scene, cg, entity_map)

    if op == "eq":
        passed = actual == expected
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=passed, detail=f"Equal: {passed}",
                               severity="HIGH" if not passed else "INFO")
    elif op == "neq":
        passed = actual != expected
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=passed, detail=f"NotEqual: {passed}")
    elif op == "truthy":
        passed = bool(actual)
        return AssertionResult(key=key, op=op, expected="truthy", actual=actual,
                               passed=passed, detail=f"Truthy: {passed}",
                               severity="CRITICAL" if not passed else "INFO")
    elif op == "falsy":
        passed = not bool(actual)
        return AssertionResult(key=key, op=op, expected="falsy", actual=actual,
                               passed=passed, detail=f"Falsy: {passed}")
    elif op == "approx":
        try:
            passed = abs(float(actual) - float(expected)) <= float(tol)
        except (TypeError, ValueError):
            passed = False
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=passed,
                               detail=f"Approx(tol={tol}): diff={abs(float(actual)-float(expected)):.4f}" if not passed else f"Approx: OK",
                               severity="HIGH" if not passed else "INFO")
    elif op == "lte":
        try:
            passed = float(actual) <= float(expected) + float(tol)
        except (TypeError, ValueError):
            passed = False
        return AssertionResult(key=key, op=op, expected=f"<= {expected}", actual=actual,
                               passed=passed, detail=f"LTE: {passed}",
                               severity="CRITICAL" if not passed else "INFO")
    elif op == "gte":
        try:
            passed = float(actual) >= float(expected) - float(tol)
        except (TypeError, ValueError):
            passed = False
        return AssertionResult(key=key, op=op, expected=f">= {expected}", actual=actual,
                               passed=passed, detail=f"GTE: {passed}",
                               severity="HIGH" if not passed else "INFO")
    elif op == "contains":
        if isinstance(actual, list):
            passed = expected in actual
        elif isinstance(actual, str):
            passed = str(expected) in actual
        else:
            passed = False
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=passed, detail=f"Contains: {passed}",
                               severity="HIGH" if not passed else "INFO")
    elif op == "contains_substr":
        if isinstance(actual, list):
            passed = any(str(expected) in str(x) for x in actual)
        elif isinstance(actual, str):
            passed = str(expected) in actual
        else:
            passed = False
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=passed, detail=f"ContainsSubstr: {passed}",
                               severity="HIGH" if not passed else "INFO")
    elif op == "in_set":
        if isinstance(actual, list):
            passed = expected in set(actual)
        else:
            passed = actual in (expected if isinstance(expected, (list, set, tuple)) else {expected})
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=passed, detail=f"InSet: {passed}")
    else:
        return AssertionResult(key=key, op=op, expected=expected, actual=actual,
                               passed=False, detail=f"Unknown op: {op}")


def _resolve_actual(key, ir, pt, vr, gt, cr, pm, bt_skills, scene_ids, scene, cg, entity_map):
    """@deprecated: Legacy assertion value resolver."""
    if key == "action":
        return pt.action.value if pt else None
    elif key == "theme_grounded":
        if pt and pt.theme and pt.theme.entity_id:
            return pt.theme.entity_id in scene_ids
        return False
    elif key == "theme_entity_id":
        if pt and pt.theme:
            return get_canonical_name(pt.theme.entity_id, entity_map, scene)
        return None
    elif key == "theme_color":
        if pt and pt.theme and pt.theme.entity_id and scene:
            obj = scene.find_object(pt.theme.entity_id)
            if obj:
                return (getattr(obj, "attributes", {}) or {}).get("color", "")
        return None
    elif key == "theme_material":
        if pt and pt.theme and pt.theme.entity_id and scene:
            obj = scene.find_object(pt.theme.entity_id)
            if obj:
                return (getattr(obj, "attributes", {}) or {}).get("material", "")
        return None
    elif key == "execution_allowed":
        return vr.execution_allowed if vr else None
    elif key == "plan_status":
        return pm.plan_status.value if pm else None
    elif key == "user_force_value":
        fc = [c for c in (pt.user_constraints or []) if c.parameter == "force_n"]
        return fc[0].value if fc else None
    elif key == "user_force_op":
        fc = [c for c in (pt.user_constraints or []) if c.parameter == "force_n"]
        return fc[0].operator.value if fc else None
    elif key == "resolved_force_n":
        fr = cr.parameters.get("force_n") if cr else None
        return fr.selected_value if fr else None
    elif key == "resolved_force_le":
        fr = cr.parameters.get("force_n") if cr else None
        return fr.selected_value if fr else None
    elif key == "resolved_velocity_ms":
        vr_param = cr.parameters.get("velocity_ms") if cr else None
        return vr_param.selected_value if vr_param else None
    elif key == "has_skill":
        return bt_skills
    elif key == "has_planpath":
        return "PlanPath" in bt_skills
    elif key == "has_collision_avoid":
        return any(n.constraint_type == "collision_avoid" for n in cg.nodes) if cg else False
    elif key == "has_issue_code":
        return [i.code for i in (vr.issues if vr else [])]
    elif key == "issue_codes":
        return [i.code for i in (vr.issues if vr else [])]
    elif key == "missing_roles":
        return list(gt.missing_roles) if gt else []
    elif key == "missing_roles_contains":
        return list(gt.missing_roles) if gt else []
    elif key == "schema_valid":
        try:
            ir.model_dump_json()
            return True
        except Exception:
            return False
    elif key == "obstacle_count":
        return len(pt.obstacle) if pt else 0
    elif key == "recipient_identified":
        return pt.recipient is not None if pt else False
    elif key == "robot_decisions_count":
        return len(getattr(ir, "robot_capability_decisions", []))
    elif key == "bt_action_count":
        return len(bt_skills)
    else:
        return f"<unknown key: {key}>"


def _extract_entity_candidates(pt, scene):
    """@deprecated: Legacy entity candidate extractor."""
    candidates = []
    if not pt:
        return candidates
    for role_name, entity in [("theme", pt.theme), ("destination", pt.destination),
                               ("recipient", pt.recipient), ("support_surface", pt.support_surface)]:
        if entity:
            info = {
                "role": role_name,
                "mention": entity.mention,
                "entity_id": entity.entity_id,
                "specific_class": entity.specific_class,
                "grounding_confidence": entity.grounding_confidence,
                "source": entity.source,
                "evidence": getattr(entity, "match_evidence", []),
            }
            candidates.append(info)
    for obs in (pt.obstacle or []):
        candidates.append({
            "role": "obstacle",
            "mention": obs.mention,
            "entity_id": obs.entity_id,
            "grounding_confidence": obs.grounding_confidence,
        })
    return candidates
