"""
Shared task semantics and plan decision models.

This module provides the single structured representation used by the
planner, constraint compiler, IR generator, validator, and UI.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskActionKind(str, Enum):
    GRASP = "GRASP"
    FETCH = "FETCH"
    PLACE = "PLACE"
    HANDOVER = "HANDOVER"
    TRANSFER = "TRANSFER"
    DYNAMIC_GRASP = "DYNAMIC_GRASP"
    WAIT = "WAIT"
    PUSH = "PUSH"
    STACK = "STACK"
    POUR = "POUR"
    CUSTOM = "CUSTOM"


class PlanStatus(str, Enum):
    READY = "READY"
    READY_WITH_SAFE_SUBSTITUTION = "READY_WITH_SAFE_SUBSTITUTION"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED = "BLOCKED"


class ConstraintOperator(str, Enum):
    EXACT = "exact"
    MIN = "min"
    MAX = "max"
    RANGE = "range"
    RECOMMENDED = "recommended"
    DEFAULT = "default"
    PREFERENCE = "preference"


class ConstraintSourceKind(str, Enum):
    USER_EXACT = "USER_EXACT"
    USER_MIN = "USER_MIN"
    USER_MAX = "USER_MAX"
    USER_RANGE = "USER_RANGE"
    GLOBAL_HARD_LIMIT = "GLOBAL_HARD_LIMIT"
    ROBOT_HARD_LIMIT = "ROBOT_HARD_LIMIT"
    MATERIAL_HARD_LIMIT = "MATERIAL_HARD_LIMIT"
    OBJECT_HARD_LIMIT = "OBJECT_HARD_LIMIT"
    RUNTIME_HARD_LIMIT = "RUNTIME_HARD_LIMIT"
    SAFETY_SUBSTITUTION = "SAFETY_SUBSTITUTION"
    RECOMMENDED_VALUE = "RECOMMENDED_VALUE"
    DEFAULT_VALUE = "DEFAULT_VALUE"
    MEMORY_PREFERENCE = "MEMORY_PREFERENCE"


class MotionState(BaseModel):
    state: str = Field(default="static", description="static | moving | unknown")
    speed_mps: Optional[float] = Field(default=None, description="Estimated target speed")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class SemanticEntityRef(BaseModel):
    mention: str = Field(..., description="Original user mention or grounded name")
    specific_class: Optional[str] = Field(default=None, description="Concrete semantic class, e.g. cup")
    parent_class: Optional[str] = Field(default=None, description="Broader parent class, e.g. container")
    entity_id: Optional[str] = Field(default=None, description="Scene entity id or symbolic actor id")
    role: Optional[str] = Field(default=None, description="Semantic role in the task")
    text_span: str = Field(default="", description="Matched text span")
    grounding_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = Field(default="nl", description="nl | scene | memory | system")
    ontology_path: List[str] = Field(default_factory=list, description="Ontology chain from specific to parent classes")
    match_evidence: List[str] = Field(default_factory=list, description="Evidence chain for grounding decision")

    @classmethod
    def from_scene_object(cls, obj: Any, role: Optional[str] = None, text_span: str = "") -> "SemanticEntityRef":
        mention = getattr(obj, "original_mention", None) or getattr(obj, "name", "unknown")
        specific_class = getattr(obj, "specific_class", None) or getattr(obj, "label", None)
        parent_class = getattr(obj, "parent_class", None)
        if parent_class is None:
            parent_classes = getattr(obj, "parent_classes", []) or []
            parent_class = parent_classes[0] if parent_classes else None
        ontology_path = list(getattr(obj, "parent_classes", []) or [])
        if specific_class and specific_class not in ontology_path:
            ontology_path = [specific_class] + ontology_path
        return cls(
            mention=mention,
            specific_class=specific_class,
            parent_class=parent_class,
            entity_id=getattr(obj, "id", None),
            role=role,
            text_span=text_span or mention,
            grounding_confidence=1.0,
            source="scene",
            ontology_path=ontology_path,
        )


class ParsedConstraint(BaseModel):
    constraint_id: str = Field(default_factory=lambda: f"constraint-{uuid4().hex[:10]}")
    parameter: str = Field(..., description="force_n | velocity_ms | ...")
    operator: ConstraintOperator = Field(...)
    source: str = Field(default="user", description="user | rule | memory | safety | object")
    source_kind: ConstraintSourceKind = Field(default=ConstraintSourceKind.USER_EXACT)
    text_span: str = Field(default="")
    unit: str = Field(default="")
    value: Optional[float] = Field(default=None)
    min_value: Optional[float] = Field(default=None)
    max_value: Optional[float] = Field(default=None)
    normalized_value: Optional[float] = Field(default=None)
    entity_id: Optional[str] = Field(default=None)
    semantic_role: Optional[str] = Field(default=None)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_hard: bool = Field(default=True)
    provenance: List[str] = Field(default_factory=list)

    def stable_key(self) -> str:
        payload = "|".join(
            [
                self.parameter,
                self.operator.value,
                self.source_kind.value,
                self.source,
                self.entity_id or "",
                self.semantic_role or "",
                self.unit,
                f"{self.value}" if self.value is not None else "",
                f"{self.min_value}" if self.min_value is not None else "",
                f"{self.max_value}" if self.max_value is not None else "",
                self.text_span,
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


class ConstraintDomain(BaseModel):
    min_value: Optional[float] = Field(default=None)
    max_value: Optional[float] = Field(default=None)
    unit: str = Field(default="")
    lower_sources: List[str] = Field(default_factory=list)
    upper_sources: List[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        if self.min_value is None or self.max_value is None:
            return False
        return self.min_value > self.max_value

    def clamp(self, value: float) -> float:
        result = value
        if self.min_value is not None:
            result = max(result, self.min_value)
        if self.max_value is not None:
            result = min(result, self.max_value)
        return result

    def midpoint(self) -> Optional[float]:
        if self.min_value is None and self.max_value is None:
            return None
        if self.min_value is None:
            return self.max_value
        if self.max_value is None:
            return self.min_value
        return (self.min_value + self.max_value) / 2.0


class ParameterResolution(BaseModel):
    parameter: str = Field(...)
    domain: ConstraintDomain = Field(default_factory=ConstraintDomain)
    candidates: List[ParsedConstraint] = Field(default_factory=list)
    selected_value: Optional[float] = Field(default=None)
    selected_source_kind: Optional[ConstraintSourceKind] = Field(default=None)
    selected_constraint_id: Optional[str] = Field(default=None)
    substituted_from: Optional[float] = Field(default=None)
    substitution_reason: Optional[str] = Field(default=None)
    request_infeasible: bool = Field(default=False)
    override_required: bool = Field(default=False)
    audit_trail: List[Dict[str, Any]] = Field(default_factory=list)


class ConstraintResolution(BaseModel):
    resolution_id: str = Field(default_factory=lambda: f"cr-{uuid4().hex[:10]}")
    plan_status: PlanStatus = Field(default=PlanStatus.NEEDS_CLARIFICATION)
    parameters: Dict[str, ParameterResolution] = Field(default_factory=dict)
    override_ledger: List[Dict[str, Any]] = Field(default_factory=list)
    rule_set_version: str = Field(default="1.0.0")
    audit_id: str = Field(default_factory=lambda: f"audit-{uuid4().hex[:10]}")
    plan_hash: str = Field(default="")
    safety_rule_version: str = Field(default="1.0.0")

    def final_values(self) -> Dict[str, float]:
        return {
            name: resolution.selected_value
            for name, resolution in self.parameters.items()
            if resolution.selected_value is not None
        }


class ValidationIssue(BaseModel):
    code: str = Field(...)
    message: str = Field(...)
    severity: str = Field(default="error")
    subject: str = Field(default="")


class ValidationResult(BaseModel):
    status: PlanStatus = Field(default=PlanStatus.BLOCKED)
    execution_allowed: bool = Field(default=False)
    issues: List[ValidationIssue] = Field(default_factory=list)
    validated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ParsedTask(BaseModel):
    instruction: str = Field(...)
    action: TaskActionKind = Field(default=TaskActionKind.CUSTOM)
    theme: Optional[SemanticEntityRef] = Field(default=None)
    source: Optional[SemanticEntityRef] = Field(default=None)
    destination: Optional[SemanticEntityRef] = Field(default=None)
    recipient: Optional[SemanticEntityRef] = Field(default=None)
    obstacle: List[SemanticEntityRef] = Field(default_factory=list)
    support_surface: Optional[SemanticEntityRef] = Field(default=None)
    manner: Optional[str] = Field(default=None)
    motion_state: MotionState = Field(default_factory=MotionState)
    user_constraints: List[ParsedConstraint] = Field(default_factory=list)
    raw_mentions: List[str] = Field(default_factory=list)
    unmet_roles: List[str] = Field(default_factory=list)
    parse_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    constraint_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)
    clarification: Optional[str] = Field(default=None)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    # Preserved from IntentFrame so LLM condition/branch semantics survive
    # the deterministic grounding boundary instead of being reduced to notes.
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    prohibitions: List[Dict[str, Any]] = Field(default_factory=list)
    # New semantic-compiler boundary.  These fields are optional so the
    # legacy public model and persisted regression fixtures remain compatible.
    semantic_task_graph: Optional[Dict[str, Any]] = Field(default=None)
    grounding_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    ambiguity_resolution: List[Dict[str, Any]] = Field(default_factory=list)
    fusion_trace: List[Dict[str, Any]] = Field(default_factory=list)
    execution_contract: Dict[str, Any] = Field(default_factory=dict)

    def role_map(self) -> Dict[str, Optional[SemanticEntityRef]]:
        return {
            "theme": self.theme,
            "source": self.source,
            "destination": self.destination,
            "recipient": self.recipient,
            "support_surface": self.support_surface,
        }


class GroundedTask(BaseModel):
    parsed_task: ParsedTask = Field(...)
    grounded_roles: Dict[str, Optional[SemanticEntityRef]] = Field(default_factory=dict)
    missing_roles: List[str] = Field(default_factory=list)
    required_clarifications: List[str] = Field(default_factory=list)
    grounding_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PlanDecision(BaseModel):
    parsed_task: ParsedTask = Field(...)
    grounded_task: GroundedTask = Field(...)
    constraint_resolution: ConstraintResolution = Field(...)
    validation_result: ValidationResult = Field(...)
    plan_status: PlanStatus = Field(default=PlanStatus.NEEDS_CLARIFICATION)
    final_parameters: Dict[str, float] = Field(default_factory=dict)
    ready_for_execution: bool = Field(default=False)
    compiler_version: str = Field(default="1.0.0")
    planner_name: str = Field(default="RuleBasedPlanner")
    llm_model: Optional[str] = Field(default=None)
    ir_version: str = Field(default="3.0.0")
    rule_set_version: str = Field(default="1.0.0")
    audit_id: str = Field(default_factory=lambda: f"audit-{uuid4().hex[:10]}")
    plan_hash: str = Field(default="")
    parse_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    constraint_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    plan_feasibility_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_readiness: float = Field(default=0.0, ge=0.0, le=1.0)


_ACTION_PATTERNS: List[Tuple[TaskActionKind, re.Pattern[str]]] = [
    (TaskActionKind.DYNAMIC_GRASP, re.compile(r"正在移动|移动中的|动态抓|动态取|追踪.*抓|抓住正在移动")),
    (TaskActionKind.PLACE, re.compile(r"放到|放在|摆到|置于|放上|放入|放进|放回|放桌|放托盘|放到.*上|place|put")),
    (TaskActionKind.HANDOVER, re.compile(r"递给|交给|送给|给我|递到我|交到我|递到.*手上|递到.*手里|拿给|handover|hand over|give|deliver")),
    (TaskActionKind.TRANSFER, re.compile(r"上料到|上料|搬运到|送到|运到|转交|移交|传递|转运|转移到|移到|transfer")),
    # FETCH requires an explicit delivery/deictic cue.  Bare “帮我拿一下” is
    # a local grasp request and must not fabricate a recipient/delivery pose.
    (TaskActionKind.FETCH, re.compile(r"拿过来|取过来|拿到我这|送到我这|把.*拿过来|把.*取过来|把.*抓过来|拿来给我|抓过来|推过来|fetch|bring")),
    (TaskActionKind.GRASP, re.compile(r"抓住|抓取|抓紧|拿起|拿一下|取起|取一下|取出来|取出|握住|夹住|抓抓|grasp|grab|pick")),
]

_MANNER_PATTERNS: Dict[str, re.Pattern[str]] = {
    "gentle": re.compile(r"轻一点|轻轻|慢慢|柔和|温柔|别用力|不要用力|别使劲|不要太用力|"
                        r"轻拿轻放|轻轻地|别太用力|小心一点|别用力抓"),
    "fast": re.compile(r"快点|快一点|迅速|赶快|赶紧|马上|立刻|尽快|快速|加速"),
    "careful": re.compile(r"当心|注意|谨慎|小心一点|小心地"),
}

# (specific_class, parent_class, allowed_roles) — only match pattern when querying for these roles
_OBJECT_PATTERNS: Dict[str, Tuple[str, str, Tuple[str, ...]]] = {
    "桌": ("table", "support_surface", ("support_surface", "destination")),
    "桌子": ("table", "support_surface", ("support_surface", "destination")),
    "台": ("table", "support_surface", ("support_surface", "destination")),
    "托盘": ("tray", "support_surface", ("support_surface", "destination")),
    "盘": ("tray", "support_surface", ("support_surface", "destination")),
    "tray": ("tray", "support_surface", ("support_surface", "destination")),
    "table": ("table", "support_surface", ("support_surface", "destination")),
    "user": ("human", "recipient", ("recipient",)),
    "我": ("human", "recipient", ("recipient",)),
    "手上": ("human", "recipient", ("recipient",)),
    "手里": ("human", "recipient", ("recipient",)),
}

# Cross-language category aliases for matching English perception labels to Chinese instructions.
# Module-level so _ground_entity_from_text, _extract_obstacles, and parse_task_semantics all share it.
_CN_CATEGORY_ALIASES: Dict[str, List[str]] = {
    "cup": ["杯", "杯子", "水杯", "玻璃杯", "beizi", "bei"],
    "bottle": ["瓶", "瓶子", "药瓶", "ping", "pingzi", "bouteille"],
    "medicine_bottle": ["药瓶", "药", "瓶"],
    "box": ["盒", "盒子", "箱", "hezi"],
    "tray": ["托盘", "盘"],
    "table": ["桌", "桌子", "台", "zhuozi"],
    "cabinet": ["柜子", "柜", "橱柜"],
    "book": ["书", "书本"],
    "glass_cup": ["玻璃杯", "杯", "玻璃", "bolibei"],
    "container": ["容器", "杯", "瓶", "盒"],
    "workpiece": ["工件", "加工件"],
    "part": ["零件", "部件"],
    "bearing": ["轴承"],
    "gear": ["齿轮"],
    "component": ["组件", "部件"],
    "inspection_zone": ["检测区", "检验区"],
    "parts_bin": ["料箱", "零件箱"],
    "workbench": ["工位", "工作台"],
    "bin": ["收纳箱", "料箱", "箱"],
    "welding_zone": ["焊接区"],
    "fixture": ["夹具"],
    "hot_surface": ["高温台", "热表面"],
    "hot_kettle": ["热水壶", "水壶"],
    "vase": ["花瓶"],
    "glass": ["玻璃杯", "玻璃物体"],
    "ball": ["球", "小球", "qiu"],
    "block": ["方块", "积木", "块", "jimu"],
    "cube": ["方块", "积木", "方"],
    "needle": ["针", "细针"],
    "device": ["设备", "装置", "仪器", "shebei", "yiqi"],
    "rubber": ["橡胶", "xiangjiao"],
    "metal": ["金属", "铁", "tie", "jinshu"],
}

# Open-language aliases used by the blind/generalization set. Keep these as
# semantic evidence only; IDs still come exclusively from the scene.
_CN_CATEGORY_ALIASES["cup"].extend(["cup", "beizi", "verre"])
_CN_CATEGORY_ALIASES["glass_cup"].extend(["cup", "beizi", "verre"])
_CN_CATEGORY_ALIASES["bottle"].extend(["bottle", "bouteille"])
# Keep the material-specific mention exclusive.  If generic ``cup`` also
# matches ``玻璃杯``, glass and plastic cups become indistinguishable before
# material scoring can help.
if "玻璃杯" in _CN_CATEGORY_ALIASES["cup"]:
    _CN_CATEGORY_ALIASES["cup"].remove("玻璃杯")

# ── Spatial / size / motion cues for grounding ──────────────────

_SPATIAL_CUES: Dict[str, str] = {
    "左边": "left", "左侧": "left", "左": "left",
    "右边": "right", "右侧": "right", "右": "right",
    "前面": "front", "前方": "front", "前": "front",
    "后面": "back", "后方": "back", "后": "back",
    "近处": "near", "近": "near", "最近": "nearest",
    "远处": "far", "远": "far", "最远": "farthest",
    "高处": "high", "高": "high",
    "低处": "low", "低": "low",
    "中间": "middle", "中": "middle",
}
_SIZE_CUES = {"大": "large", "小": "small", "最大": "largest", "最小": "smallest"}

# ══════════════════════════════════════════════════════════════
# Entity Grounder — multi-dimension scoring with ambiguity detection
# ══════════════════════════════════════════════════════════════

@dataclass
class GroundedCandidate:
    """A scored grounding candidate with match evidence."""
    entity_ref: "SemanticEntityRef"
    score: float = 0.0
    evidence: List[str] = field(default_factory=list)


class EntityGrounder:
    """Multi-dimension entity grounding with scoring and ambiguity detection.

    Scores each scene object against the instruction across:
      - Category match (name, label, specific_class, cross-language aliases)
      - Color match
      - Material match
      - Spatial cues (left/right, front/back, near/far, high/low, middle)
      - Size cues (large/small)
      - Motion state (moving/static)
      - Affordance relevance

    When top-1 and top-2 scores are within ``ambiguity_threshold``,
    returns NEEDS_CLARIFICATION.
    """

    def __init__(self, ambiguity_threshold: float = 0.15, min_score: float = 0.10):
        self.ambiguity_threshold = ambiguity_threshold
        self.min_score = min_score

    # ── Public API ──────────────────────────────────────────

    def ground_theme(
        self, instruction: str, scene: Any,
        color_hint: Optional[str] = None,
    ) -> List[GroundedCandidate]:
        """Score all scene objects as potential themes. Returns ranked candidates."""
        return self._score_all(instruction, scene, role="theme", color_hint=color_hint)

    def ground_destination(
        self, instruction: str, scene: Any,
        exclude_ids: Optional[set] = None,
    ) -> List[GroundedCandidate]:
        """Score objects as potential destinations / support surfaces."""
        exclude = exclude_ids or set()
        candidates = self._score_all(instruction, scene, role="destination", exclude_ids=exclude)
        # Boost support_surface affordance
        for c in candidates:
            obj = scene.find_object(c.entity_ref.entity_id) if c.entity_ref.entity_id and scene else None
            if obj and hasattr(obj, "affordances"):
                from robot_intent_agent.schemas.scene import Affordance
                aff_vals = [a.value if hasattr(a, 'value') else str(a) for a in obj.affordances]
                if "support_surface" in aff_vals or "fixed" in aff_vals:
                    c.score += 0.30
                    c.evidence.append("affordance:support_surface/fixed +0.30")
        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    def ground_obstacles(
        self, instruction: str, scene: Any,
        target_id: Optional[str] = None,
    ) -> List[GroundedCandidate]:
        """Identify obstacle objects from negation + scene relations."""
        normalized = _normalize_text(instruction)
        has_caution = any(token in normalized for token in
                          ("别碰", "不要碰", "避开", "绕过", "小心", "不想碰", "不能碰", "禁止碰"))
        candidates: List[GroundedCandidate] = []

        if not has_caution and not target_id:
            return candidates

        for obj in (getattr(scene, "objects", []) or []):
            if target_id and getattr(obj, "id", None) == target_id:
                continue
            score = 0.0
            evidence: List[str] = []

            # Category match via cross-language aliases
            sc = getattr(obj, "specific_class", "") or ""
            aliases = _CN_CATEGORY_ALIASES.get(sc, [])
            matched_alias = next((a for a in aliases if a in normalized), None)
            if matched_alias:
                score += 0.40
                evidence.append(f"category_alias:{matched_alias} +0.40")

            # Direct name match
            obj_name = getattr(obj, "name", "")
            if obj_name and obj_name in normalized:
                score += 0.30
                evidence.append(f"name_match:{obj_name} +0.30")

            # Caution token proximity: obstacle mentioned near caution word
            if has_caution and matched_alias:
                score += 0.30
                evidence.append("caution_proximity +0.30")

            if score > 0:
                ref = SemanticEntityRef.from_scene_object(obj, role="obstacle",
                    text_span=matched_alias or obj_name)
                ref.match_evidence = evidence
                candidates.append(GroundedCandidate(entity_ref=ref, score=score, evidence=evidence))

        # Add scene-relation obstacles (blocking/near)
        if target_id and scene:
            try:
                target_obj = scene.find_object(target_id)
            except Exception:
                target_obj = None
            if target_obj:
                for rel in getattr(scene, "relations", []) or []:
                    pred = getattr(getattr(rel, "predicate", None), "value", None) or str(getattr(rel, "predicate", rel))
                    if pred == "blocking" and getattr(rel, "subject", "") == target_obj.id:
                        blocker = scene.find_object(getattr(rel, "object", ""))
                        if blocker:
                            ref = SemanticEntityRef.from_scene_object(blocker, role="obstacle",
                                text_span=getattr(blocker, "name", ""))
                            ref.match_evidence = [f"scene_relation:blocking +0.50"]
                            candidates.append(GroundedCandidate(entity_ref=ref, score=0.50,
                                evidence=["scene_relation:blocking +0.50"]))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    # ── Scoring engine ──────────────────────────────────────

    def _score_all(
        self, instruction: str, scene: Any, role: str = "theme",
        color_hint: Optional[str] = None,
        exclude_ids: Optional[set] = None,
    ) -> List[GroundedCandidate]:
        """Score every scene object and return ranked candidates."""
        normalized = _normalize_text(instruction)
        exclude = exclude_ids or set()
        candidates: List[GroundedCandidate] = []

        # Store scene for cross-object comparison in size/motion/spatial scorers
        self._last_scene = scene

        for obj in (getattr(scene, "objects", []) or []):
            obj_id = getattr(obj, "id", "")
            if obj_id in exclude:
                continue

            score = 0.0
            evidence: List[str] = []

            # 1. Category match (name / label / specific_class / aliases / parent_classes)
            cat_score, cat_ev = self._score_category(obj, normalized)
            score += cat_score
            evidence.extend(cat_ev)

            # 2. Color match
            col_score, col_ev = self._score_color(obj, normalized, color_hint)
            score += col_score
            evidence.extend(col_ev)

            # 3. Material match
            mat_score, mat_ev = self._score_material(obj, normalized)
            score += mat_score
            evidence.extend(mat_ev)

            # 4. Spatial cue match
            spa_score, spa_ev = self._score_spatial(obj, normalized, scene)
            score += spa_score
            evidence.extend(spa_ev)

            # 5. Size cue match (cross-object comparison via self._last_scene)
            siz_score, siz_ev = self._score_size(obj, normalized)
            score += siz_score
            evidence.extend(siz_ev)

            # 6. Motion state match
            mot_score, mot_ev = self._score_motion(obj, normalized)
            score += mot_score
            evidence.extend(mot_ev)

            # 7. Affordance relevance
            aff_score, aff_ev = self._score_affordance(obj, role)
            score += aff_score
            evidence.extend(aff_ev)

            # Require at minimum some grounding evidence (category, spatial, size, or color)
            has_grounding_evidence = any(
                "category" in e or "name_" in e or "alias:" in e or
                "label:" in e or "specific_class:" in e or
                "parent_class:" in e or "exact_name" in e or
                "spatial:" in e or "size:" in e or "color_match" in e or
                "color_mismatch" in e or "material_match" in e or
                "demonstrative_fallback" in e
                for e in evidence
            )
            # Also accept if score is strong enough without category evidence
            # (e.g., demonstrative + size cues only, or single-object fallback)
            has_strong_non_category = score >= 0.25 and not has_grounding_evidence
            if score >= self.min_score and (has_grounding_evidence or has_strong_non_category):
                text_span = self._best_text_span(obj, normalized)
                ref = SemanticEntityRef.from_scene_object(obj, role=role, text_span=text_span)
                ref.match_evidence = evidence
                ref.grounding_confidence = min(score, 1.0)
                candidates.append(GroundedCandidate(entity_ref=ref, score=score, evidence=evidence))

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates

    # ── Individual scorers ──────────────────────────────────

    @staticmethod
    def _score_category(obj: Any, text: str) -> Tuple[float, List[str]]:
        evidence: List[str] = []
        score = 0.0
        obj_name = getattr(obj, "name", "")
        obj_label = getattr(obj, "label", "") or ""
        specific_class = getattr(obj, "specific_class", "") or ""
        parent_classes = list(getattr(obj, "parent_classes", []) or [])

        # Full-name exact match (strongest signal)
        if obj_name and obj_name == text:
            return 0.60, [f"exact_name:{obj_name} +0.60"]

        # Substring match on name
        if obj_name and obj_name in text:
            score += 0.35
            evidence.append(f"name_substr:{obj_name} +0.35")

        # Label match
        if obj_label and obj_label in text:
            score += 0.25
            evidence.append(f"label:{obj_label} +0.25")

        # Specific class match
        if specific_class and specific_class in text:
            score += 0.20
            evidence.append(f"specific_class:{specific_class} +0.20")

        # Parent class match
        for pc in parent_classes:
            if pc and pc in text:
                score += 0.15
                evidence.append(f"parent_class:{pc} +0.15")
                break

        # Cross-language alias match (broad coverage)
        aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
        best_alias = None
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in text:
                best_alias = alias
                break
        if best_alias:
            bonus = 0.30 if len(best_alias) >= 2 else 0.20
            score += bonus
            evidence.append(f"alias:{best_alias} +{bonus:.2f}")

        # Partial character-level alias match for mixed-language terms
        # (e.g., "玻璃bei" → match "玻璃" part, "beizi" → match "bei" alias)
        if not best_alias:
            import re as _re
            # Extract Chinese character segments and Latin segments separately
            cn_segments = _re.findall(r'[一-鿿]+', text)
            latin_segments = _re.findall(r'[a-zA-Z]+', text.lower() if text else "")
            all_segments = cn_segments + latin_segments
            for segment in all_segments:
                if len(segment) < 2:
                    continue
                for alias in sorted(aliases, key=len, reverse=True):
                    if alias and len(alias) >= 2 and alias in segment:
                        bonus = 0.22
                        score += bonus
                        evidence.append(f"alias_partial:{alias}~{segment} +{bonus:.2f}")
                        best_alias = alias
                        break
                if best_alias:
                    break

        return score, evidence

    @staticmethod
    def _score_color(obj: Any, text: str, color_hint: Optional[str] = None) -> Tuple[float, List[str]]:
        evidence: List[str] = []
        score = 0.0
        obj_attrs = getattr(obj, "attributes", {}) or {}
        obj_color = obj_attrs.get("color", "")

        _color_map = {"红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
                      "白色": "white", "黑色": "black", "透明": "transparent"}
        if color_hint is None:
            for cn_word, cn_color in _color_map.items():
                if cn_word in text:
                    color_hint = cn_color
                    break

        if not color_hint or not obj_color or obj_color == "unknown":
            return 0.0, []

        if obj_color == color_hint:
            score += 0.30
            evidence.append(f"color_match:{color_hint} +0.30")
        else:
            score -= 0.40
            evidence.append(f"color_mismatch:{obj_color}!={color_hint} -0.40")

        return score, evidence

    @staticmethod
    def _score_material(obj: Any, text: str) -> Tuple[float, List[str]]:
        evidence: List[str] = []
        score = 0.0
        obj_attrs = getattr(obj, "attributes", {}) or {}
        obj_mat = obj_attrs.get("material", "")

        mat_aliases = {
            "glass": ["玻璃"], "plastic": ["塑料"], "wood": ["木", "木头"],
            "metal": ["金属", "铁", "钢"], "ceramic": ["陶瓷", "瓷"],
            "rubber": ["橡胶"], "cardboard": ["纸", "纸板"],
        }
        for mat_en, mat_cn_list in mat_aliases.items():
            if obj_mat == mat_en:
                if any(mc in text for mc in mat_cn_list):
                    score += 0.25
                    evidence.append(f"material_match:{mat_en} +0.25")
                break

        return score, evidence

    @staticmethod
    def _score_spatial(obj: Any, text: str, scene: Any) -> Tuple[float, List[str]]:
        """Score based on spatial position (left/right, front/back, near/far, high/low, middle)."""
        evidence: List[str] = []
        score = 0.0

        detected_cues = {cue: label for cue, label in _SPATIAL_CUES.items() if cue in text}
        if not detected_cues:
            return 0.0, []

        pos = getattr(obj, "position", None)
        if not pos:
            return 0.0, []

        obj_x, obj_y, obj_z = getattr(pos, "x", 0), getattr(pos, "y", 0), getattr(pos, "z", 0)

        # Compare against other objects in scene
        all_objects = getattr(scene, "objects", []) or []
        if len(all_objects) <= 1:
            return 0.0, []

        xs = [getattr(getattr(o, "position", None), "x", 0) for o in all_objects]
        ys = [getattr(getattr(o, "position", None), "y", 0) for o in all_objects]
        zs = [getattr(getattr(o, "position", None), "z", 0) for o in all_objects]
        sizes = [getattr(getattr(o, "bbox", None), "width", 0.05) * getattr(getattr(o, "bbox", None), "height", 0.05) * getattr(getattr(o, "bbox", None), "depth", 0.05) for o in all_objects]

        for cue, label in detected_cues.items():
            if label == "left" and obj_y <= min(ys) + 0.05:
                score += 0.25; evidence.append(f"spatial:leftmost +0.25")
            elif label == "right" and obj_y >= max(ys) - 0.05:
                score += 0.25; evidence.append(f"spatial:rightmost +0.25")
            elif label == "front" and obj_y >= max(ys) - 0.05:
                score += 0.25; evidence.append(f"spatial:frontmost +0.25")
            elif label == "back" and obj_y <= min(ys) + 0.05:
                score += 0.25; evidence.append(f"spatial:backmost +0.25")
            elif label == "near" or label == "nearest":
                dist = (obj_x**2 + obj_y**2 + obj_z**2) ** 0.5
                min_dist = min((getattr(getattr(o, "position", None), "x", 0)**2 + getattr(getattr(o, "position", None), "y", 0)**2 + getattr(getattr(o, "position", None), "z", 0)**2)**0.5 for o in all_objects)
                if dist <= min_dist + 0.02:
                    score += 0.25; evidence.append(f"spatial:nearest(dist={dist:.2f}) +0.25")
            elif label == "far" or label == "farthest":
                dist = (obj_x**2 + obj_y**2 + obj_z**2) ** 0.5
                max_dist = max((getattr(getattr(o, "position", None), "x", 0)**2 + getattr(getattr(o, "position", None), "y", 0)**2 + getattr(getattr(o, "position", None), "z", 0)**2)**0.5 for o in all_objects)
                if dist >= max_dist - 0.02:
                    score += 0.25; evidence.append(f"spatial:farthest(dist={dist:.2f}) +0.25")
            elif label == "high" and obj_z >= max(zs) - 0.02:
                score += 0.25; evidence.append(f"spatial:highest(z={obj_z:.3f}) +0.25")
            elif label == "low" and obj_z <= min(zs) + 0.02:
                score += 0.25; evidence.append(f"spatial:lowest(z={obj_z:.3f}) +0.25")
            elif label == "middle" and len(xs) >= 3:
                sorted_xs = sorted(xs)
                mid = sorted_xs[len(sorted_xs)//2]
                if abs(obj_x - mid) < 0.05:
                    score += 0.25; evidence.append(f"spatial:middle(x={obj_x:.3f}) +0.25")

        return score, evidence

    def _score_size(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Score based on size cues (大/小), comparing across all scene objects."""
        evidence: List[str] = []
        score = 0.0

        detected = {cue: label for cue, label in _SIZE_CUES.items() if cue in text}
        if not detected:
            return 0.0, []

        bbox = getattr(obj, "bbox", None)
        if not bbox:
            return 0.0, []

        vol = getattr(bbox, "width", 0.05) * getattr(bbox, "height", 0.05) * getattr(bbox, "depth", 0.05)

        # Cross-object comparison for relative size
        scene_ref = getattr(self, '_last_scene', None)
        all_vols = []
        if scene_ref:
            for o in getattr(scene_ref, "objects", []) or []:
                ob = getattr(o, "bbox", None)
                if ob:
                    all_vols.append(getattr(ob, "width", 0.05) * getattr(ob, "height", 0.05) * getattr(ob, "depth", 0.05))

        for cue, label in detected.items():
            if "largest" in label and all_vols:
                if vol >= max(all_vols) - 0.0001:
                    score += 0.25
                    evidence.append(f"size:largest(vol={vol:.4f}) +0.25")
            elif "smallest" in label and all_vols:
                if vol <= min(all_vols) + 0.0001:
                    score += 0.25
                    evidence.append(f"size:smallest(vol={vol:.4f}) +0.25")
            elif "large" in label and len(all_vols) > 1:
                # "large": must be among top half (not just top 2)
                threshold_idx = max(0, len(all_vols) // 2 - 1)
                if vol >= sorted(all_vols)[-2]:  # among top 2
                    score += 0.20
                    evidence.append(f"size:large(vol={vol:.4f}) +0.20")
                elif len(all_vols) == 2:
                    # 2-object case: the smaller one is definitely NOT large
                    score -= 0.15
                    evidence.append(f"size:not_large(vol={vol:.4f}) -0.15")
                else:
                    score -= 0.10
                    evidence.append(f"size:not_large(vol={vol:.4f}) -0.10")
            elif "small" in label and len(all_vols) > 1:
                sorted_vols = sorted(all_vols)
                # "small": must be the minimum or close to it for 2-object case
                if len(all_vols) == 2:
                    if vol <= sorted_vols[0] + 0.0001:  # is the minimum
                        score += 0.25
                        evidence.append(f"size:small(min_vol={vol:.4f}) +0.25")
                    else:
                        score -= 0.15
                        evidence.append(f"size:not_small(vol={vol:.4f}) -0.15")
                elif vol <= sorted_vols[1]:  # top 2 smallest for 3+ objects
                    score += 0.20
                    evidence.append(f"size:small(vol={vol:.4f}) +0.20")
                else:
                    score -= 0.10
                    evidence.append(f"size:not_small(vol={vol:.4f}) -0.10")

        return score, evidence

    @staticmethod
    def _score_motion(obj: Any, text: str) -> Tuple[float, List[str]]:
        """Score based on motion state matching."""
        evidence: List[str] = []
        score = 0.0

        has_moving_cue = any(w in text for w in ("移动", "运动", "飘动", "晃动", "moving"))
        has_static_cue = any(w in text for w in ("静止", "不动", "static", "停"))

        if not has_moving_cue and not has_static_cue:
            return 0.0, []

        obj_attrs = getattr(obj, "attributes", {}) or {}
        is_moving = obj_attrs.get("_is_moving", False)
        speed = obj_attrs.get("_speed_mps", 0.0)

        if has_moving_cue and is_moving:
            score += 0.25
            evidence.append(f"motion:moving(speed={speed:.3f}) +0.25")
        elif has_static_cue and not is_moving:
            score += 0.20
            evidence.append("motion:static +0.20")
        elif has_moving_cue and not is_moving:
            score -= 0.20
            evidence.append("motion:expected_moving_but_static -0.20")

        return score, evidence

    @staticmethod
    def _score_affordance(obj: Any, role: str) -> Tuple[float, List[str]]:
        """Score based on affordance relevance to the role."""
        evidence: List[str] = []
        score = 0.0

        affs = getattr(obj, "affordances", []) or []
        aff_vals = {a.value if hasattr(a, 'value') else str(a) for a in affs}

        if role == "theme":
            if "graspable" in aff_vals:
                score += 0.10
                evidence.append("affordance:graspable +0.10")
            if "fragile" in aff_vals:
                evidence.append("affordance:fragile (info)")
        elif role in ("destination", "support_surface"):
            if "support_surface" in aff_vals or "fixed" in aff_vals:
                score += 0.25
                evidence.append("affordance:surface +0.25")
        elif role == "obstacle":
            if "fixed" in aff_vals:
                score += 0.10
                evidence.append("affordance:fixed_obstacle +0.10")

        return score, evidence

    @staticmethod
    def _best_text_span(obj: Any, text: str) -> str:
        """Find the best Chinese text span matching this object."""
        specific_class = getattr(obj, "specific_class", "") or ""
        aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in text:
                return alias
        obj_name = getattr(obj, "name", "")
        if obj_name and obj_name in text:
            return obj_name
        return specific_class or obj_name or "unknown"


# ══════════════════════════════════════════════════════════════
# Robot Capability — hardware limits for executability judgment
# ══════════════════════════════════════════════════════════════

@dataclass
class RobotCapability:
    """Robot hardware limits and current state for executability validation.

    Priority: System Safety > Robot Hard Limits > Scene Facts > User Request > Memory > Default
    """
    # ── Skill support ──
    supported_skills: List[str] = field(default_factory=lambda: [
        "Reach", "Grasp", "GentleGrasp", "MoveTo", "Release",
        "Fetch", "Place", "Handover", "DynamicGrasp", "WaitUntilStable",
        "PlanPath", "Avoid", "Push", "Stack", "Pour", "Transfer", "Transport",
        "MoveToHandoverZone", "WaitUntil",
    ])
    unavailable_skills: List[str] = field(default_factory=list)

    # ── Gripper limits ──
    gripper_max_force_n: float = 10.0       # Hard upper bound on grasp force
    gripper_min_force_n: float = 0.1        # Minimum controllable force
    gripper_max_width_m: float = 0.10       # Maximum jaw opening
    gripper_has_object: bool = False         # Currently holding something

    # ── Arm limits ──
    max_velocity_ms: float = 0.3            # Maximum end-effector speed
    max_payload_kg: float = 2.0             # Maximum payload mass
    workspace_radius_m: float = 0.75        # Maximum reach from base
    workspace_z_min_m: float = 0.0          # Minimum Z reach
    workspace_z_max_m: float = 0.50         # Maximum Z reach

    # ── Status ──
    is_homed: bool = True                   # Robot must be homed for execution
    current_tool: str = "parallel_gripper"   # Active end-effector

    @property
    def available_skills(self) -> List[str]:
        return [s for s in self.supported_skills if s not in self.unavailable_skills]


@dataclass
class CapabilityDecision:
    """Single robot-capability decision with full evidence chain."""
    parameter: str                          # e.g., "force_n", "gripper_width", "skill:Pour"
    requested: Any                          # What was requested (user or pipeline)
    selected: Any                           # Final selected value
    source: str                             # ROBOT_HARD_LIMIT / SAFETY / USER_EXACT ...
    reason: str                             # Human-readable rationale
    blocked: bool = False                   # Whether this blocks execution


class RobotCapabilityValidator:
    """Validates task executability against robot hardware limits.

    Enforces priority: System Safety > Robot Hard Limits > Scene Facts
                       > User Explicit Request > Memory > Default Value
    """

    def __init__(self, capability: Optional[RobotCapability] = None):
        self.capability = capability or RobotCapability()
        self.decisions: List[CapabilityDecision] = []

    # ── Main validation ─────────────────────────────────────

    def validate(
        self,
        parsed_task: "ParsedTask",
        scene: Any = None,
        behavior_tree: Any = None,
        constraint_resolution: Any = None,
    ) -> Tuple[bool, List[CapabilityDecision], List[str]]:
        """Run all robot capability checks. Returns (executable, decisions, blocking_reasons)."""
        self.decisions = []
        blocking: List[str] = []

        self._check_homed(parsed_task, blocking)
        self._check_gripper_has_object(parsed_task, blocking)
        self._check_unsupported_skills(behavior_tree, blocking)
        self._check_gripper_width(parsed_task, scene, blocking)
        self._check_workspace(parsed_task, scene, blocking)
        self._check_payload(parsed_task, scene, blocking)
        self._check_force_limits(parsed_task, constraint_resolution)
        self._check_velocity_limits(parsed_task, constraint_resolution)

        executable = len(blocking) == 0
        return executable, self.decisions, blocking

    # ── Individual checks ───────────────────────────────────

    def _check_homed(self, parsed_task, blocking):
        if not self.capability.is_homed:
            msg = "Robot not homed — cannot execute any motion"
            blocking.append(msg)
            self.decisions.append(CapabilityDecision(
                parameter="is_homed", requested=True, selected=False,
                source="ROBOT_HARD_LIMIT", reason=msg, blocked=True,
            ))

    def _check_gripper_has_object(self, parsed_task, blocking):
        if not self.capability.gripper_has_object:
            self.decisions.append(CapabilityDecision(
                parameter="gripper.has_object", requested="n/a", selected=False,
                source="ROBOT_HARD_LIMIT", reason="Gripper empty — grasp allowed",
            ))
            return
        # Has object → cannot Grasp/Fetch
        action = parsed_task.action
        if action in (TaskActionKind.GRASP, TaskActionKind.FETCH, TaskActionKind.DYNAMIC_GRASP):
            msg = f"Gripper already holding object — cannot execute {action.value}"
            blocking.append(msg)
            self.decisions.append(CapabilityDecision(
                parameter="gripper.has_object", requested=action.value, selected=False,
                source="ROBOT_HARD_LIMIT", reason=msg, blocked=True,
            ))

    def _check_unsupported_skills(self, behavior_tree, blocking):
        if not behavior_tree:
            return
        unavailable = set(self.capability.unavailable_skills)
        all_skills = self.capability.supported_skills
        for action in behavior_tree.root.flatten_actions():
            skill = action.skill_name
            if skill in unavailable:
                msg = f"Skill '{skill}' is currently unavailable on this robot"
                blocking.append(msg)
                self.decisions.append(CapabilityDecision(
                    parameter=f"skill:{skill}", requested=skill, selected=None,
                    source="ROBOT_HARD_LIMIT", reason=msg, blocked=True,
                ))
            elif skill not in all_skills:
                msg = f"Skill '{skill}' not in robot's supported_skills"
                blocking.append(msg)
                self.decisions.append(CapabilityDecision(
                    parameter=f"skill:{skill}", requested=skill, selected=None,
                    source="ROBOT_HARD_LIMIT", reason=msg, blocked=True,
                ))

    def _check_gripper_width(self, parsed_task, scene, blocking):
        # Only check for actions that require actively grasping the theme
        if parsed_task.action not in (TaskActionKind.GRASP, TaskActionKind.FETCH,
                                       TaskActionKind.DYNAMIC_GRASP, TaskActionKind.HANDOVER):
            return
        if not scene or not parsed_task.theme or not parsed_task.theme.entity_id:
            return
        target = scene.find_object(parsed_task.theme.entity_id)
        if not target:
            return
        bbox = getattr(target, "bbox", None)
        if not bbox:
            return
        # Use the smallest dimension that must fit between gripper jaws
        target_width = min(
            getattr(bbox, "width", 0.05),
            getattr(bbox, "depth", 0.05),
        )
        max_width = self.capability.gripper_max_width_m
        if target_width > max_width + 0.001:
            msg = (f"Target width ({target_width:.3f}m) exceeds gripper max opening "
                   f"({max_width:.3f}m)")
            blocking.append(msg)
            self.decisions.append(CapabilityDecision(
                parameter="gripper_max_width", requested=target_width,
                selected=max_width, source="ROBOT_HARD_LIMIT",
                reason=msg, blocked=True,
            ))
        else:
            self.decisions.append(CapabilityDecision(
                parameter="gripper_max_width", requested=target_width,
                selected=target_width, source="ROBOT_HARD_LIMIT",
                reason=f"Target width ({target_width:.3f}m) within gripper limit ({max_width:.3f}m)",
            ))

    def _check_workspace(self, parsed_task, scene, blocking):
        if not scene or not parsed_task.theme or not parsed_task.theme.entity_id:
            return
        target = scene.find_object(parsed_task.theme.entity_id)
        if not target:
            return
        pos = getattr(target, "position", None)
        if not pos:
            return
        x, y, z = getattr(pos, "x", 0), getattr(pos, "y", 0), getattr(pos, "z", 0)
        dist = (x**2 + y**2 + z**2) ** 0.5
        max_r = self.capability.workspace_radius_m
        z_min = self.capability.workspace_z_min_m
        z_max = self.capability.workspace_z_max_m

        reasons = []
        if dist > max_r + 0.01:
            reasons.append(f"distance {dist:.3f}m > max reach {max_r:.3f}m")
        if z < z_min - 0.01:
            reasons.append(f"z={z:.3f}m < z_min={z_min:.3f}m")
        if z > z_max + 0.01:
            reasons.append(f"z={z:.3f}m > z_max={z_max:.3f}m")

        if reasons:
            msg = f"Target outside workspace: {'; '.join(reasons)}"
            blocking.append(msg)
            self.decisions.append(CapabilityDecision(
                parameter="workspace", requested={"x": x, "y": y, "z": z},
                selected=None, source="ROBOT_HARD_LIMIT",
                reason=msg, blocked=True,
            ))
        else:
            self.decisions.append(CapabilityDecision(
                parameter="workspace", requested={"x": x, "y": y, "z": z},
                selected={"x": x, "y": y, "z": z}, source="ROBOT_HARD_LIMIT",
                reason=f"Target at ({x:.2f},{y:.2f},{z:.2f}) within workspace (r={max_r:.2f}m)",
            ))

    def _check_payload(self, parsed_task, scene, blocking):
        # Payload check is most relevant for lift/grasp actions
        if parsed_task.action not in (TaskActionKind.GRASP, TaskActionKind.FETCH,
                                       TaskActionKind.DYNAMIC_GRASP, TaskActionKind.HANDOVER,
                                       TaskActionKind.TRANSFER):
            return
        if not scene or not parsed_task.theme or not parsed_task.theme.entity_id:
            return
        target = scene.find_object(parsed_task.theme.entity_id)
        if not target:
            return
        bbox = getattr(target, "bbox", None)
        attrs = getattr(target, "attributes", {}) or {}
        if not bbox:
            return
        # Approximate mass from volume × material density
        vol = getattr(bbox, "width", 0.05) * getattr(bbox, "height", 0.08) * getattr(bbox, "depth", 0.05)
        density_map = {"metal": 5000, "steel": 7800, "wood": 700, "plastic": 1200,
                       "glass": 2500, "ceramic": 2700, "rubber": 1100, "cardboard": 200}
        material = attrs.get("material", "plastic")
        density = density_map.get(material, 1500)
        est_mass = vol * density
        if est_mass > self.capability.max_payload_kg + 0.001:
            msg = (f"Estimated payload ({est_mass:.3f}kg from {material} {vol*1e6:.0f}cm³) "
                   f"exceeds max ({self.capability.max_payload_kg:.3f}kg)")
            blocking.append(msg)
            self.decisions.append(CapabilityDecision(
                parameter="payload", requested=est_mass,
                selected=self.capability.max_payload_kg,
                source="ROBOT_HARD_LIMIT", reason=msg, blocked=True,
            ))
        else:
            self.decisions.append(CapabilityDecision(
                parameter="payload", requested=est_mass,
                selected=est_mass, source="ROBOT_HARD_LIMIT",
                reason=f"Estimated payload ({est_mass:.3f}kg) within limit ({self.capability.max_payload_kg:.3f}kg)",
            ))

    def _check_force_limits(self, parsed_task, constraint_resolution):
        max_f = self.capability.gripper_max_force_n
        min_f = self.capability.gripper_min_force_n
        if not constraint_resolution:
            self.decisions.append(CapabilityDecision(
                parameter="force_n", requested=None, selected=None,
                source="ROBOT_HARD_LIMIT",
                reason=f"Robot gripper limits: [{min_f}, {max_f}]N",
            ))
            return
        fr = constraint_resolution.parameters.get("force_n")
        if not fr:
            return
        selected = fr.selected_value
        if selected is not None and selected > max_f:
            self.decisions.append(CapabilityDecision(
                parameter="force_n", requested=selected, selected=max_f,
                source="ROBOT_HARD_LIMIT",
                reason=f"Requested force {selected}N exceeds robot max {max_f}N → clamped",
            ))
        elif selected is not None:
            self.decisions.append(CapabilityDecision(
                parameter="force_n", requested=selected, selected=selected,
                source="ROBOT_HARD_LIMIT",
                reason=f"Force {selected}N within robot limits [{min_f}, {max_f}]N",
            ))

    def _check_velocity_limits(self, parsed_task, constraint_resolution):
        max_v = self.capability.max_velocity_ms
        if not constraint_resolution:
            return
        vr = constraint_resolution.parameters.get("velocity_ms")
        if not vr:
            return
        selected = vr.selected_value
        if selected is not None and selected > max_v:
            self.decisions.append(CapabilityDecision(
                parameter="velocity_ms", requested=selected, selected=max_v,
                source="ROBOT_HARD_LIMIT",
                reason=f"Requested velocity {selected}m/s exceeds robot max {max_v}m/s → clamped",
            ))
        elif selected is not None:
            self.decisions.append(CapabilityDecision(
                parameter="velocity_ms", requested=selected, selected=selected,
                source="ROBOT_HARD_LIMIT",
                reason=f"Velocity {selected}m/s within robot limit {max_v}m/s",
            ))


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("，", ",").replace("。", ".").replace("：", ":")
    normalized = normalized.replace("－", "-").replace("—", "-").replace("～", "-")
    return normalized


def _format_constraint_id(parameter: str, operator: ConstraintOperator, text_span: str, source: str) -> str:
    payload = f"{parameter}|{operator.value}|{text_span}|{source}"
    return f"constraint-{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def _infer_specific_class(name: str) -> Tuple[Optional[str], Optional[str], List[str]]:
    name_lower = name.lower()
    mapping = [
        ("medicine_bottle", "medicine_bottle", "container", ["medicine_bottle", "bottle", "container"]),
        ("药瓶", "medicine_bottle", "container", ["medicine_bottle", "bottle", "container"]),
        ("cup", "cup", "container", ["container"]),
        ("tray", "tray", "support_surface", ["support_surface"]),
        ("table", "table", "support_surface", ["support_surface"]),
        ("box", "box", "container", ["container"]),
        ("glass", "glass_cup", "container", ["container"]),
        ("bottle", "bottle", "container", ["bottle", "container"]),
        ("瓶子", "bottle", "container", ["bottle", "container"]),
        ("水杯", "cup", "container", ["container"]),
        ("托盘", "tray", "support_surface", ["support_surface"]),
        ("桌", "table", "support_surface", ["support_surface"]),
        ("ball", "ball", "object", ["ball", "object"]),
        ("球", "ball", "object", ["ball", "object"]),
        ("block", "block", "object", ["block", "object"]),
        ("cube", "block", "object", ["cube", "block", "object"]),
        ("方块", "block", "object", ["block", "object"]),
        ("积木", "block", "object", ["block", "object"]),
        ("needle", "needle", "object", ["needle", "object"]),
        ("针", "needle", "object", ["needle", "object"]),
        ("device", "device", "object", ["device", "object"]),
        ("设备", "device", "object", ["device", "object"]),
        ("rubber", "rubber", "material", ["rubber", "material"]),
        ("metal", "metal", "material", ["metal", "material"]),
        ("铁", "metal", "material", ["metal", "material"]),
    ]
    for needle, specific, parent, parents in mapping:
        if needle in name_lower:
            return specific, parent, parents
    return None, None, []


def _ground_entity_from_text(text: str, role: str, scene: Any = None, exclude_ids: set[str] | None = None) -> Optional[SemanticEntityRef]:
    exclude = exclude_ids or set()
    if scene is not None:
        # Pass 1: direct name/label/specific_class match
        for obj in getattr(scene, "objects", []) or []:
            if getattr(obj, "id", "") in exclude:
                continue
            names = [
                getattr(obj, "name", ""),
                getattr(obj, "label", "") or "",
                getattr(obj, "specific_class", "") or "",
                getattr(obj, "original_mention", "") or "",
            ]
            if any(name and name in text for name in names):
                return SemanticEntityRef.from_scene_object(obj, role=role, text_span=next((n for n in names if n and n in text), getattr(obj, "name", "")))

        # Pass 2: cross-language alias match + functional term match
        # for support_surface/destination roles
        if role in ("support_surface", "destination"):
            for obj in getattr(scene, "objects", []) or []:
                if getattr(obj, "id", "") in exclude:
                    continue
                specific_class = getattr(obj, "specific_class", "") or ""
                aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
                # Full alias match
                matched = next((alias for alias in sorted(aliases, key=len, reverse=True) if alias and alias in text), None)
                if matched:
                    return SemanticEntityRef.from_scene_object(obj, role=role, text_span=matched)
                # Partial character-segment match
                import re as _re
                cn_segments = _re.findall(r'[一-鿿]+', text)
                latin_segments = _re.findall(r'[a-zA-Z]+', text.lower() if text else "")
                for segment in cn_segments + latin_segments:
                    if len(segment) < 2:
                        continue
                    partial = next((alias for alias in sorted(aliases, key=len, reverse=True) if alias and len(alias) >= 2 and alias in segment), None)
                    if partial:
                        return SemanticEntityRef.from_scene_object(obj, role=role, text_span=segment)

            # Pass 2.5: functional term match — e.g., "支撑面" → object with support_surface affordance
            for role_key, terms in _FUNCTIONAL_ROLE_TERMS.items():
                if role_key == role and any(term in text for term in terms):
                    for obj in getattr(scene, "objects", []) or []:
                        if getattr(obj, "id", "") in exclude:
                            continue
                        affs = [a.value if hasattr(a, 'value') else str(a) for a in getattr(obj, 'affordances', [])]
                        if role_key == "support_surface" and ("fixed" in affs or "support_surface" in affs):
                            matched_term = next((t for t in terms if t in text), "")
                            return SemanticEntityRef.from_scene_object(obj, role=role, text_span=matched_term or text[:4])


    for pattern, (specific_class, parent_class, allowed_roles) in _OBJECT_PATTERNS.items():
        if role not in allowed_roles:
            continue
        if pattern in text:
            # Recipient entities use "user" as entity_id
            if role == "recipient":
                eid = "user"
            elif role in ("support_surface", "destination"):
                # Do NOT fabricate scene objects — mark as ungrounded
                eid = None
            else:
                eid = pattern
            return SemanticEntityRef(
                mention=pattern,
                specific_class=specific_class,
                parent_class=parent_class,
                entity_id=eid,
                role=role,
                text_span=pattern,
                grounding_confidence=0.4 if eid is None else 0.6,
                source="nl",
                ontology_path=[specific_class, parent_class] if parent_class else [specific_class],
            )
    return None


def _extract_numeric_constraints(text: str) -> List[ParsedConstraint]:
    constraints: List[ParsedConstraint] = []

    # IMPORTANT: MAX/MIN/RANGE before EXACT — otherwise EXACT greedily matches "2N" inside "不超过2N"
    force_patterns = [
        (ConstraintOperator.MAX, re.compile(r"(?:(?:劲儿|力|力量|力度|夹持力|抓力)?\s*(?:别|不要|不能)?\s*超过|最多|至多|<=|小于等于|不大于|上限(?:是|为)?|最大(?:是|为)?)\s*(\d+(?:\.\d+)?)\s*(?:N|牛顿)")),
        (ConstraintOperator.MIN, re.compile(r"(?:至少|不低于|>=|大于等于|不小于)\s*(\d+(?:\.\d+)?)\s*(?:N|牛顿)")),
        (ConstraintOperator.RANGE, re.compile(r"(\d+(?:\.\d+)?)\s*(?:到|至|-)\s*(\d+(?:\.\d+)?)\s*(?:N|牛顿)")),
        (ConstraintOperator.EXACT, re.compile(r"(?:用|用力|力度|力量|以)\s*(\d+(?:\.\d+)?)\s*(?:N|牛顿)")),
    ]
    velocity_patterns = [
        (ConstraintOperator.MAX, re.compile(r"(?:不超过|最多|至多|<=|小于等于|不大于)\s*(\d+(?:\.\d+)?)\s*m\s*/\s*s")),
        (ConstraintOperator.MIN, re.compile(r"(?:至少|不低于|>=|大于等于|不小于)\s*(\d+(?:\.\d+)?)\s*m\s*/\s*s")),
        (ConstraintOperator.RANGE, re.compile(r"(\d+(?:\.\d+)?)\s*(?:到|至|-)\s*(\d+(?:\.\d+)?)\s*m\s*/\s*s")),
        (ConstraintOperator.EXACT, re.compile(r"(?:以|速度|用)\s*(\d+(?:\.\d+)?)\s*m\s*/\s*s")),
    ]

    def _append(parameter: str, operator: ConstraintOperator, value: Optional[float] = None, min_value: Optional[float] = None, max_value: Optional[float] = None, unit: str = "", text_span: str = "") -> None:
        if operator == ConstraintOperator.RANGE:
            normalized = None
            if min_value is not None and max_value is not None:
                normalized = (min_value + max_value) / 2.0
        else:
            normalized = value
        constraints.append(
            ParsedConstraint(
                constraint_id=_format_constraint_id(parameter, operator, text_span, "user"),
                parameter=parameter,
                operator=operator,
                source="user",
                source_kind={
                    ConstraintOperator.EXACT: ConstraintSourceKind.USER_EXACT,
                    ConstraintOperator.MAX: ConstraintSourceKind.USER_MAX,
                    ConstraintOperator.MIN: ConstraintSourceKind.USER_MIN,
                    ConstraintOperator.RANGE: ConstraintSourceKind.USER_RANGE,
                }[operator],
                text_span=text_span,
                unit=unit,
                value=value,
                min_value=min_value,
                max_value=max_value,
                normalized_value=normalized,
                confidence=1.0,
                is_hard=True,
                provenance=["nl"],
            )
        )

    covered_spans: set[tuple[int, int]] = set()

    for operator, pattern in force_patterns:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in covered_spans:
                continue
            covered_spans.add(span)
            if operator == ConstraintOperator.RANGE:
                low = float(match.group(1))
                high = float(match.group(2))
                _append("force_n", operator, min_value=low, max_value=high, unit="N", text_span=match.group(0))
            elif operator == ConstraintOperator.MAX:
                value = float(match.group(1))
                _append("force_n", operator, value=value, max_value=value, unit="N", text_span=match.group(0))
            elif operator == ConstraintOperator.MIN:
                value = float(match.group(1))
                _append("force_n", operator, value=value, min_value=value, unit="N", text_span=match.group(0))
            else:
                value = float(match.group(2)) if match.lastindex and match.lastindex >= 2 else float(match.group(1))
                _append("force_n", operator, value=value, unit="N", text_span=match.group(0))

    for operator, pattern in velocity_patterns:
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if span in covered_spans:
                continue
            covered_spans.add(span)
            if operator == ConstraintOperator.RANGE:
                low = float(match.group(1))
                high = float(match.group(2))
                _append("velocity_ms", operator, min_value=low, max_value=high, unit="m/s", text_span=match.group(0))
            elif operator == ConstraintOperator.MAX:
                value = float(match.group(1))
                _append("velocity_ms", operator, value=value, max_value=value, unit="m/s", text_span=match.group(0))
            elif operator == ConstraintOperator.MIN:
                value = float(match.group(1))
                _append("velocity_ms", operator, value=value, min_value=value, unit="m/s", text_span=match.group(0))
            else:
                value = float(match.group(1))
                _append("velocity_ms", operator, value=value, unit="m/s", text_span=match.group(0))

    return constraints


def _extract_manner(text: str) -> Optional[str]:
    for label, pattern in _MANNER_PATTERNS.items():
        if pattern.search(text):
            return label
    return None


def _classify_action(text: str) -> TaskActionKind:
    normalized = _normalize_text(text)
    if re.search(r"(?:翻转|旋转|转过来|翻过来|rotate|flip)", normalized) and re.search(r"(?:抓|拿|夹|握|grasp|grab|pick)", normalized.lower()):
        return TaskActionKind.CUSTOM
    for action, pattern in _ACTION_PATTERNS:
        if pattern.search(normalized):
            return action

    # ── Fallback: extract best action from keyword cues ──
    # When no primary action pattern matches (e.g., complex conditional/sequential
    # instructions), use the strongest action keyword present rather than CUSTOM.
    # Priority: transport keywords > manipulation keywords > CUSTOM
    _TRANSPORT_CUES = [
        (TaskActionKind.HANDOVER, re.compile(r"递给|交给|给我|递到|送到.*手上|handover|give|deliver")),
        (TaskActionKind.FETCH, re.compile(r"拿过来|取过来|拿到我这|拿来给我|fetch|bring")),
        (TaskActionKind.PLACE, re.compile(r"放到|放在|摆到|放入|放进|置于|放上|放回|place|put")),
        (TaskActionKind.TRANSFER, re.compile(r"转移|转运|移交|transfer")),
    ]
    for action, pattern in _TRANSPORT_CUES:
        if pattern.search(normalized):
            return action

    # Last resort: check for grasp cues
    if re.search(r"抓|拿|取|握|夹|grasp|grab|pick", normalized):
        return TaskActionKind.GRASP

    return TaskActionKind.CUSTOM


def _detect_conditional_structure(text: str) -> Optional[str]:
    """Detect conditional/sequential structures in the instruction.

    Returns the structure type or None.
    - IF_ELSE: "如果...否则/就..."
    - UNLESS: "除非...否则..."
    - BEFORE: "先...再/然后..."
    - AFTER: "...之后/再..."
    - WAIT_UNTIL: "等待/直到..."
    - SEQUENCE: explicit multi-step with numbered or sequential markers
    """
    normalized = _normalize_text(text)
    if re.search(r"如果.*(?:否则|要不|就)", normalized):
        return "IF_ELSE"
    if re.search(r"除非.*否则", normalized):
        return "UNLESS"
    if re.search(r"先.*(?:再|然后|之后)", normalized):
        return "BEFORE"
    if re.search(r"(?:之后|以后|然后).*再", normalized):
        return "AFTER"
    if re.search(r"等待|等到|直到", normalized):
        return "WAIT_UNTIL"
    if re.search(r"第一步|第二步|首先.*然后|首先.*接着", normalized):
        return "SEQUENCE"
    return None


# ── Functional role terms for grounding ──
_FUNCTIONAL_ROLE_TERMS: Dict[str, List[str]] = {
    "support_surface": ["支撑面", "桌面", "台面", "平面上"],
    "container": ["容器", "盒子里", "箱子里"],
}


def _extract_motion_state(text: str) -> MotionState:
    normalized = _normalize_text(text)
    moving = re.search(r"正在移动|移动中的|运动中的|移动|飘动|晃动", normalized)
    if not moving:
        return MotionState(state="static", confidence=0.4)
    speed_match = re.search(r"(\d+(?:\.\d+)?)\s*m\s*/\s*s", normalized)
    speed = float(speed_match.group(1)) if speed_match else None
    return MotionState(state="moving", speed_mps=speed, confidence=0.8 if speed is not None else 0.65)


def _extract_obstacles(text: str, scene: Any = None, target: Optional[SemanticEntityRef] = None) -> List[SemanticEntityRef]:
    obstacles: List[SemanticEntityRef] = []

    normalized = _normalize_text(text)
    seen_ids: set[str] = set()

    def _append(obj: SemanticEntityRef) -> None:
        key = obj.entity_id or obj.mention
        if key in seen_ids:
            return
        seen_ids.add(key)
        obstacles.append(obj)

    # ── Expanded caution token patterns ──
    _CAUTION_TOKENS_CN = re.compile(
        r"别碰|不要碰|千万别碰|不想碰|不能碰|禁止碰|禁止接触|"
        r"避开|绕开|绕过|躲开|不要|小心|除了"
    )
    _CAUTION_TOKENS_EN = re.compile(
        r"don'?t\s+touch|do\s+not\s+touch|avoid|without\s+touching|"
        r"anything\s+else|except|but\s+don'?t"
    )
    _NEGATION_SPATIAL = re.compile(r"不要(前面的|后面的|左边的|右边的|那个|这个)")
    _NEGATION_ANYTHING = re.compile(r"别碰任何|不要碰任何|不要碰其他|除了.*不要碰")

    has_caution = bool(
        _CAUTION_TOKENS_CN.search(normalized) or
        _CAUTION_TOKENS_EN.search(normalized.lower()) or
        _NEGATION_SPATIAL.search(normalized) or
        _NEGATION_ANYTHING.search(normalized)
    )

    if scene is not None and has_caution:
        # Pass 1: direct name/label/specific_class match with caution
        for obj in getattr(scene, "objects", []) or []:
            names = [getattr(obj, "name", ""), getattr(obj, "label", "") or "",
                     getattr(obj, "specific_class", "") or "",
                     getattr(obj, "original_mention", "") or ""]
            if any(name and name in normalized for name in names):
                if target and getattr(obj, "id", None) == target.entity_id:
                    continue
                _append(SemanticEntityRef.from_scene_object(obj, role="obstacle",
                    text_span=next((n for n in names if n and n in normalized), getattr(obj, "name", ""))))

        # Pass 2: cross-language alias match
        for obj in getattr(scene, "objects", []) or []:
            if target and getattr(obj, "id", None) == target.entity_id:
                continue
            specific_class = getattr(obj, "specific_class", "") or ""
            aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
            if any(alias in normalized for alias in aliases):
                key = getattr(obj, "id", "") or getattr(obj, "name", "")
                if key not in seen_ids:
                    seen_ids.add(key)
                    obstacles.append(SemanticEntityRef.from_scene_object(obj, role="obstacle",
                        text_span=next((alias for alias in aliases if alias in normalized), getattr(obj, "name", ""))))

        # Pass 3: "anything else" / "别碰任何东西" → all non-target objects are obstacles
        if _NEGATION_ANYTHING.search(normalized) or re.search(r"anything\s*else", normalized.lower()):
            for obj in getattr(scene, "objects", []) or []:
                if target and getattr(obj, "id", None) == target.entity_id:
                    continue
                key = getattr(obj, "id", "") or getattr(obj, "name", "")
                if key not in seen_ids:
                    seen_ids.add(key)
                    obstacles.append(SemanticEntityRef.from_scene_object(obj, role="obstacle",
                        text_span=getattr(obj, "name", "")))

        # Pass 4: scene blocking relations
        if target and getattr(target, "entity_id", None):
            try:
                target_obj = scene.find_object(target.entity_id) or scene.find_object(target.mention)
            except Exception:
                target_obj = None
            if target_obj is not None:
                for rel in getattr(scene, "relations", []) or []:
                    if getattr(rel, "predicate", None) and getattr(rel.predicate, "value", rel.predicate) == "blocking" and rel.subject == target_obj.id:
                        blocker = scene.find_object(rel.object)
                        if blocker is not None:
                            _append(SemanticEntityRef.from_scene_object(blocker, role="obstacle",
                                text_span=getattr(blocker, "name", "")))

        # Relational contrast: “前面的杯子，不要后面的” or the inverse.
        # Use the same-category peer group and exclude the selected theme.
        if re.search(r"(?:不要|别|勿).{0,8}(?:后面的|前面的|左边的|右边的)", normalized):
            for obj in getattr(scene, "objects", []) or []:
                if target and getattr(obj, "id", None) == target.entity_id:
                    continue
                aliases = _CN_CATEGORY_ALIASES.get(getattr(obj, "specific_class", ""), [])
                if any(alias in normalized for alias in aliases):
                    _append(SemanticEntityRef.from_scene_object(
                        obj, role="obstacle", text_span="relative_peer"))

    # Fallback: NL-only obstacle when no scene grounding succeeded
    if not obstacles and has_caution:
        # Try to extract obstacle mention from the text near caution tokens
        for cue_re in [
            r"(?:别碰|不要碰|千万别碰|不能碰|不想碰|禁止接触|避开|绕开)\s*(\S{1,6})",
            r"don'?t\s+touch\s+(\S{1,10})",
            r"avoid\s+(\S{1,10})",
            r"不要(前面的|后面的|左边的|右边的)\S*",
        ]:
            m = re.search(cue_re, normalized.lower() if "don" in normalized.lower() or "avoid" in normalized.lower() else normalized)
            if m:
                cue = m.group(1).strip()
                cue = re.sub(r"[的了呢吗啊]$", "", cue)
                if cue and len(cue) >= 1:
                    specific_class, parent_class, parents = _infer_specific_class(cue)
                    _append(SemanticEntityRef(
                        mention=cue, specific_class=specific_class, parent_class=parent_class,
                        entity_id=None, role="obstacle", text_span=cue,
                        grounding_confidence=0.0, source="nl", ontology_path=parents,
                    ))
    return obstacles


# ══════════════════════════════════════════════════════════════
# GroundingEngine — per-role, structured-score entity grounding
# ══════════════════════════════════════════════════════════════

@dataclass
class ScoredCandidate:
    """One grounded candidate with structured score components and evidence."""
    entity_ref: SemanticEntityRef
    role: str = "theme"
    score_components: Dict[str, float] = field(default_factory=dict)
    hard_rejections: List[str] = field(default_factory=list)
    total_score: float = 0.0
    evidence: List[str] = field(default_factory=list)


@dataclass
class ClarificationRequest:
    """Ambiguity resolution request when grounding is uncertain."""
    role: str
    candidate_ids: List[str] = field(default_factory=list)
    question: str = ""


@dataclass
class GroundingResult:
    """Complete grounding result for one role."""
    role: str
    candidates: List[ScoredCandidate] = field(default_factory=list)
    selected: Optional[ScoredCandidate] = None
    needs_clarification: bool = False
    clarification: Optional[ClarificationRequest] = None
    ambiguity_gap: float = 0.0  # top1.score - top2.score


class GroundingEngine:
    """Per-role entity grounding with structured multi-dimension scoring.

    Replaces ad-hoc grounding with a unified pipeline:
      1. Score every scene object across 11 dimensions
      2. Apply role-specific feasibility filters
      3. Cross-object spatial/ordinal/size resolution
      4. Rank by combined language × feasibility score
      5. Detect ambiguity → NEEDS_CLARIFICATION
      6. Apply grounding invariants

    Usage:
        engine = GroundingEngine()
        result = engine.ground("抓住红色杯子", scene, role="theme")
        if result.needs_clarification:
            print(result.clarification.question)
        else:
            print(f"Grounded to: {result.selected.entity_ref.entity_id}")
    """

    def __init__(self, config=None):
        from robot_intent_agent.config.grounding_config import GroundingConfig, get_grounding_config
        self.config = config or get_grounding_config()

    # ── Public API ──────────────────────────────────────────

    def ground(
        self,
        instruction: str,
        scene: Any,
        role: str = "theme",
        color_hint: Optional[str] = None,
        exclude_ids: Optional[Set[str]] = None,
        preferred_ids: Optional[Set[str]] = None,
    ) -> GroundingResult:
        """Ground a role to scene objects.

        Args:
            instruction: Full NL instruction text
            scene: SemanticSceneGraph
            role: One of 'theme', 'destination', 'support_surface', 'recipient', 'obstacle', 'source'
            color_hint: Pre-extracted color from instruction
            exclude_ids: Entity IDs to exclude (already assigned to other roles)
            preferred_ids: Entity IDs to prefer (from explicit mentions)

        Returns:
            GroundingResult with ranked candidates and optional clarification
        """
        exclude = exclude_ids or set()
        preferred = preferred_ids or set()
        if color_hint is None:
            color_hint = self.config.derive_color_hint(instruction)

        # ── Phase 1: Score all candidates ──
        candidates = self._score_all(instruction, scene, role, color_hint, exclude, preferred)

        # ── Phase 2: Apply role feasibility ──
        candidates = self._apply_role_feasibility(candidates, role, scene)

        # ── Phase 3: Cross-object spatial/ordinal/size resolution ──
        candidates = self._apply_cross_object_scoring(candidates, instruction, scene, role)

        # ── Phase 4: Re-rank by total score ──
        candidates.sort(key=lambda c: c.total_score, reverse=True)

        # Explicit relational/ordinal/size descriptors are deterministic
        # selectors inside the eligible peer group.  Do not let the generic
        # margin gate turn an explicit "left/high/small" reference into a
        # clarification merely because the raw language scores are close.
        explicit = self._select_explicit_descriptor(candidates, instruction, scene)
        if explicit is not None:
            candidates = [explicit] + [c for c in candidates if c is not explicit]
            result = GroundingResult(
                role=role,
                candidates=candidates,
                selected=explicit,
                needs_clarification=False,
                ambiguity_gap=(explicit.total_score - candidates[1].total_score)
                if len(candidates) > 1 else explicit.total_score,
            )
            return result

        # ── Phase 5: Ambiguity detection ──
        result = self._build_result(candidates, role)
        return result

    def _select_explicit_descriptor(
        self, candidates: List[ScoredCandidate], instruction: str, scene: Any
    ) -> Optional[ScoredCandidate]:
        """Select a unique candidate for an explicit physical descriptor.

        This is deliberately conservative: only candidates with positive
        category evidence participate, and a descriptor is accepted only when
        it identifies exactly one extreme/size peer.  Genuine ties continue
        through the normal ambiguity gate.
        """
        if not candidates or scene is None:
            return None
        hints = list(dict.fromkeys(
            self.config.derive_spatial_hints(instruction)
            + self.config.derive_size_hints(instruction)
            + self.config.derive_ordinal_hints(instruction)
        ))
        if not hints:
            return None

        eligible = [
            c for c in candidates
            if c.score_components.get("category_match", 0.0) > 0
            and not c.hard_rejections
            and c.entity_ref.entity_id
        ]
        # Bare size references such as “那个小的” omit the noun.  If all
        # remaining candidates share one concrete class, that class is the
        # eligible peer group; unrelated scene objects are not competitors.
        if len(eligible) < 2:
            same_class = [c for c in candidates if not c.hard_rejections and c.entity_ref.entity_id]
            classes = {
                c.entity_ref.specific_class or c.entity_ref.parent_class
                for c in same_class
                if c.entity_ref.specific_class or c.entity_ref.parent_class
            }
            if len(classes) == 1:
                eligible = same_class
        if len(eligible) < 2:
            return None
        objects = []
        by_id = {getattr(o, "id", ""): o for o in (getattr(scene, "objects", []) or [])}
        for c in eligible:
            obj = by_id.get(c.entity_ref.entity_id)
            if obj is not None:
                objects.append(obj)
        if len(objects) < 2:
            return None

        def pos_value(obj: Any, axis: str) -> float:
            return float(getattr(getattr(obj, "position", None), axis, 0.0))

        def volume(obj: Any) -> float:
            box = getattr(obj, "bbox", None)
            return (
                float(getattr(box, "width", 0.0))
                * float(getattr(box, "height", 0.0))
                * float(getattr(box, "depth", 0.0))
            ) if box is not None else 0.0

        target_ids: Set[str] = set()
        spatial = self.config.spatial
        for hint in hints:
            if hint in ("left", "leftmost", "right", "rightmost"):
                axis = spatial.axis_for(hint)
                if axis is None:
                    continue
                values = [pos_value(o, axis) for o in objects]
                want_min = (hint in ("left", "leftmost")) == spatial.left_is_lower
                extreme = min(values) if want_min else max(values)
                ids = {getattr(o, "id", "") for o in objects if abs(pos_value(o, axis) - extreme) <= 1e-6}
            elif hint in ("front", "frontmost", "back", "backmost"):
                axis = spatial.front_back_axis
                values = [pos_value(o, axis) for o in objects]
                want_min = hint in ("front", "frontmost") != spatial.front_is_higher
                extreme = min(values) if want_min else max(values)
                ids = {getattr(o, "id", "") for o in objects if abs(pos_value(o, axis) - extreme) <= 1e-6}
            elif hint in ("high", "highest", "low", "lowest"):
                values = [pos_value(o, spatial.up_down_axis) for o in objects]
                extreme = max(values) if hint in ("high", "highest") else min(values)
                ids = {getattr(o, "id", "") for o in objects if abs(pos_value(o, spatial.up_down_axis) - extreme) <= 1e-6}
            elif hint in ("near", "nearest", "far", "farthest"):
                def dist(o: Any) -> float:
                    return sum(pos_value(o, a) ** 2 for a in ("x", "y", "z")) ** 0.5
                values = [dist(o) for o in objects]
                extreme = min(values) if hint in ("near", "nearest") else max(values)
                ids = {getattr(o, "id", "") for o in objects if abs(dist(o) - extreme) <= 1e-6}
            elif hint in ("small", "smallest", "large", "largest"):
                values = [volume(o) for o in objects]
                extreme = min(values) if hint in ("small", "smallest") else max(values)
                ids = {getattr(o, "id", "") for o in objects if abs(volume(o) - extreme) <= 1e-9}
            else:
                continue
            if len(ids) == 1:
                target_ids = ids
                break

        if len(target_ids) != 1:
            return None
        target_id = next(iter(target_ids))
        return next((c for c in eligible if c.entity_ref.entity_id == target_id), None)

    def ground_theme(self, instruction: str, scene: Any, **kwargs) -> GroundingResult:
        return self.ground(instruction, scene, role="theme", **kwargs)

    def ground_destination(self, instruction: str, scene: Any, **kwargs) -> GroundingResult:
        return self.ground(instruction, scene, role="destination", **kwargs)

    def ground_support_surface(self, instruction: str, scene: Any, **kwargs) -> GroundingResult:
        return self.ground(instruction, scene, role="support_surface", **kwargs)

    def ground_obstacles(self, instruction: str, scene: Any, target_id: Optional[str] = None, **kwargs) -> GroundingResult:
        """Ground obstacles — objects mentioned with negation/caution tokens."""
        return self.ground(instruction, scene, role="obstacle",
                          exclude_ids={target_id} if target_id else None, **kwargs)

    # ── Phase 1: Score all candidates ────────────────────────

    def _score_all(
        self, instruction: str, scene: Any, role: str,
        color_hint: Optional[str], exclude_ids: Set[str],
        preferred_ids: Set[str],
    ) -> List[ScoredCandidate]:
        """Score every scene object. Store self._last_scene for cross-object scorers."""
        normalized = _normalize_text(instruction)
        self._last_scene = scene
        self._last_instruction = instruction
        candidates: List[ScoredCandidate] = []

        for obj in (getattr(scene, "objects", []) or []):
            obj_id = getattr(obj, "id", "")
            if obj_id in exclude_ids:
                continue

            components: Dict[str, float] = {}
            evidence: List[str] = []
            rejections: List[str] = []

            # 1. Category match
            cat = self._score_category(obj, normalized)
            components["category_match"] = cat[0]
            evidence.extend(cat[1])

            # 2. Color match
            col = self._score_color(obj, normalized, color_hint, role)
            components["color_match"] = col[0]
            evidence.extend(col[1])
            # Hard rejection: explicit color requested but object has different known color
            if col[0] < -0.30:
                rejections.append(f"color_mismatch:{color_hint}")

            # 3. Material match
            mat = self._score_material(obj, normalized)
            components["material_match"] = mat[0]
            evidence.extend(mat[1])

            # 4. Size match (preliminary — refined in cross-object phase)
            siz = self._score_size_prelim(obj, normalized)
            components["size_match"] = siz[0]
            evidence.extend(siz[1])

            # 5. Spatial match (preliminary — refined in cross-object phase)
            spa = self._score_spatial_prelim(obj, normalized)
            components["spatial_match"] = spa[0]
            evidence.extend(spa[1])

            # 6. Ordinal match (preliminary)
            ord_ = self._score_ordinal_prelim(obj, normalized)
            components["ordinal_match"] = ord_[0]
            evidence.extend(ord_[1])

            # 7. Motion match
            mot = self._score_motion(obj, normalized)
            components["motion_match"] = mot[0]
            evidence.extend(mot[1])

            # 8. Role affordance match
            aff = self._score_role_affordance(obj, role)
            components["role_affordance_match"] = aff[0]
            evidence.extend(aff[1])
            rejections.extend(aff[2])

            # 9. State compatibility
            st = self._score_state_compatibility(obj, role)
            components["state_compatibility"] = st[0]
            evidence.extend(st[1])
            rejections.extend(st[2])

            # 10. Exact ID match bonus
            eid = self._score_exact_id(obj, preferred_ids)
            components["exact_original_id_match"] = eid[0]
            evidence.extend(eid[1])

            # Compute total score
            if rejections:
                total = 0.0
            else:
                language_score = self._compute_language_score(components)
                feasibility_score = self._compute_feasibility_score(components)
                total = self._combine_scores(language_score, feasibility_score)

            candidates.append(ScoredCandidate(
                entity_ref=SemanticEntityRef.from_scene_object(obj, role=role,
                    text_span=self._best_text_span(obj, normalized)),
                role=role,
                score_components=components,
                hard_rejections=rejections,
                total_score=total,
                evidence=evidence,
            ))

        # ── Single-candidate boost: if only one candidate with category match,
        #     boost score to pass min_accept (avoids rejecting unambiguous cases)
        valid_candidates = [c for c in candidates if not c.hard_rejections]
        if len(valid_candidates) == 1 and len(candidates) == 1:
            c = candidates[0]
            if c.score_components.get("category_match", 0) > 0.15:
                boost = max(0, self.config.min_accept_score - c.total_score + 0.05)
                c.score_components["single_candidate_boost"] = boost
                c.total_score += boost
                c.evidence.append(f"single_candidate_boost +{boost:.3f}")

        candidates.sort(key=lambda c: c.total_score, reverse=True)
        return candidates

    # ── Phase 2: Role feasibility ────────────────────────────

    def _apply_role_feasibility(
        self, candidates: List[ScoredCandidate], role: str, scene: Any
    ) -> List[ScoredCandidate]:
        """Apply role-specific feasibility checks and adjust score components."""
        required = self.config.role_required_affordances.get(role, [])
        forbidden = self.config.role_forbidden_affordances.get(role, [])

        for c in candidates:
            obj = scene.find_object(c.entity_ref.entity_id) if c.entity_ref.entity_id and scene else None
            if obj is None:
                continue
            affs = self._get_affordance_set(obj)

            # Check required affordances — penalize in components, not direct total_score
            if required:
                has_required = any(r in affs for r in required)
                if not has_required:
                    c.score_components["role_affordance_match"] = c.score_components.get("role_affordance_match", 0) - 0.25
                    c.evidence.append(f"role_feasibility:missing_required({required}) -0.25")

            # Check forbidden affordances — hard reject for critical roles
            if forbidden:
                has_forbidden = any(f in affs for f in forbidden)
                if has_forbidden:
                    c.hard_rejections.append(f"forbidden_affordance:{[f for f in forbidden if f in affs]}")
                    c.total_score = 0.0
                    c.evidence.append(f"role_feasibility:HARD_REJECT forbidden({[f for f in forbidden if f in affs]})")

            # Role-specific boosts — modify components, not total_score
            if role == "support_surface" or role == "destination":
                if "support_surface" in affs or "fixed" in affs:
                    c.score_components["role_affordance_match"] = c.score_components.get("role_affordance_match", 0) + 0.15
                    c.evidence.append("role:surface_affordance +0.15")
            elif role == "theme":
                if "graspable" in affs:
                    c.score_components["role_affordance_match"] = c.score_components.get("role_affordance_match", 0) + 0.10
                    c.evidence.append("role:graspable +0.10")

        # Recompute total scores after component adjustments
        for c in candidates:
            if not c.hard_rejections:
                lang = self._compute_language_score(c.score_components)
                feas = self._compute_feasibility_score(c.score_components)
                c.total_score = self._combine_scores(lang, feas)

        return candidates

    # ── Phase 3: Cross-object scoring ────────────────────────

    def _apply_cross_object_scoring(
        self, candidates: List[ScoredCandidate], instruction: str, scene: Any, role: str
    ) -> List[ScoredCandidate]:
        """Refine spatial, ordinal, and size scores by comparing across peer objects."""
        if not candidates:
            return candidates

        # A synonym table may emit the same normalized cue more than once.
        # Applying it twice creates artificial confidence and can force an
        # arbitrary object through an otherwise ambiguous grounding decision.
        spatial_hints = list(dict.fromkeys(self.config.derive_spatial_hints(instruction)))
        ordinal_hints = list(dict.fromkeys(self.config.derive_ordinal_hints(instruction)))
        size_hints = list(dict.fromkeys(self.config.derive_size_hints(instruction)))

        if not (spatial_hints or ordinal_hints or size_hints):
            return candidates

        # Group candidates by category for peer comparison
        peers_by_cat: Dict[str, List[ScoredCandidate]] = {}
        for c in candidates:
            sc = c.entity_ref.specific_class or c.entity_ref.parent_class or "unknown"
            if sc not in peers_by_cat:
                peers_by_cat[sc] = []
            peers_by_cat[sc].append(c)

        for cat, peers in peers_by_cat.items():
            if len(peers) < 2:
                continue

            # Get scene objects for peer comparison
            peer_objs = []
            for p in peers:
                obj = scene.find_object(p.entity_ref.entity_id) if p.entity_ref.entity_id and scene else None
                if obj:
                    peer_objs.append(obj)

            if len(peer_objs) < 2:
                continue

            # Cross-object spatial scoring
            if spatial_hints and self.config.enable_cross_object_spatial:
                self._resolve_spatial_cross_object(peers, peer_objs, spatial_hints)

            # Cross-object ordinal scoring
            if ordinal_hints and self.config.enable_ordinal_resolution:
                self._resolve_ordinal_cross_object(peers, peer_objs, ordinal_hints)

            # Cross-object size scoring
            if size_hints and self.config.enable_cross_object_size:
                self._resolve_size_cross_object(peers, peer_objs, size_hints)

        # Recompute total scores after cross-object adjustments
        for c in candidates:
            if not c.hard_rejections:
                lang = self._compute_language_score(c.score_components)
                feas = self._compute_feasibility_score(c.score_components)
                c.total_score = self._combine_scores(lang, feas)

        return candidates

    def _resolve_spatial_cross_object(
        self, candidates: List[ScoredCandidate], objects: List[Any], hints: List[str]
    ) -> None:
        """Resolve spatial cues by comparing object positions within a peer group."""
        sp = self.config.spatial

        for hint in hints:
            axis = sp.axis_for(hint)
            if axis is None:
                continue

            sorted_objs = sp.sort_objects(objects, axis, ascending=True)
            obj_ids = [getattr(o, "id", "") for o in sorted_objs]
            n = len(sorted_objs)

            if hint == "middle" and self.config.enable_middle_resolution:
                if n % 2 == 1:
                    # Odd count: middle is unambiguous
                    mid_idx = n // 2
                    mid_id = obj_ids[mid_idx]
                    for c in candidates:
                        if c.entity_ref.entity_id == mid_id:
                            c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + 0.35
                            c.evidence.append(f"spatial:middle(idx={mid_idx}/{n}) +0.35")
                else:
                    # Even count: mark as ambiguous for middle
                    for c in candidates:
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) - 0.10
                        c.evidence.append(f"spatial:middle_ambiguous(even_count={n}) -0.10")

            elif hint in ("left", "leftmost"):
                values = [getattr(getattr(o, "position", None), axis, 0.0) for o in sorted_objs]
                extreme = min(values) if sp.left_is_lower else max(values)
                left_ids = {
                    getattr(o, "id", "") for o in sorted_objs
                    if abs(getattr(getattr(o, "position", None), axis, 0.0) - extreme) <= 1e-6
                }
                for c in candidates:
                    if c.entity_ref.entity_id in left_ids:
                        bonus = 0.35 if hint == "leftmost" else 0.25
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint} +{bonus:.2f}")

            elif hint in ("right", "rightmost"):
                values = [getattr(getattr(o, "position", None), axis, 0.0) for o in sorted_objs]
                extreme = max(values) if sp.left_is_lower else min(values)
                right_ids = {
                    getattr(o, "id", "") for o in sorted_objs
                    if abs(getattr(getattr(o, "position", None), axis, 0.0) - extreme) <= 1e-6
                }
                for c in candidates:
                    if c.entity_ref.entity_id in right_ids:
                        bonus = 0.35 if hint == "rightmost" else 0.25
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint} +{bonus:.2f}")

            elif hint in ("front", "frontmost"):
                sorted_front = sp.sort_objects(objects, sp.front_back_axis, ascending=not sp.front_is_higher)
                front_id = getattr(sorted_front[0], "id", "")
                for c in candidates:
                    if c.entity_ref.entity_id == front_id:
                        bonus = 0.35 if hint == "frontmost" else 0.25
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint} +{bonus:.2f}")

            elif hint in ("back", "backmost"):
                sorted_back = sp.sort_objects(objects, sp.front_back_axis, ascending=sp.front_is_higher)
                back_id = getattr(sorted_back[0], "id", "")
                for c in candidates:
                    if c.entity_ref.entity_id == back_id:
                        bonus = 0.35 if hint == "backmost" else 0.25
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint} +{bonus:.2f}")

            elif hint in ("nearest", "near"):
                # Nearest to origin (robot base position)
                best_id = None
                best_dist = float("inf")
                for o in objects:
                    pos = getattr(o, "position", None)
                    if pos:
                        dist = (getattr(pos, "x", 0)**2 + getattr(pos, "y", 0)**2 + getattr(pos, "z", 0)**2)**0.5
                        if dist < best_dist:
                            best_dist = dist
                            best_id = getattr(o, "id", "")
                for c in candidates:
                    if c.entity_ref.entity_id == best_id:
                        bonus = 0.35 if hint == "nearest" else 0.20
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint}(dist={best_dist:.3f}) +{bonus:.2f}")

            elif hint in ("farthest", "far"):
                best_id = None
                best_dist = -1.0
                for o in objects:
                    pos = getattr(o, "position", None)
                    if pos:
                        dist = (getattr(pos, "x", 0)**2 + getattr(pos, "y", 0)**2 + getattr(pos, "z", 0)**2)**0.5
                        if dist > best_dist:
                            best_dist = dist
                            best_id = getattr(o, "id", "")
                for c in candidates:
                    if c.entity_ref.entity_id == best_id:
                        bonus = 0.35 if hint == "farthest" else 0.20
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint}(dist={best_dist:.3f}) +{bonus:.2f}")

            elif hint in ("high", "highest"):
                sorted_z = sp.sort_objects(objects, sp.up_down_axis, ascending=False)
                high_id = getattr(sorted_z[0], "id", "")
                for c in candidates:
                    if c.entity_ref.entity_id == high_id:
                        bonus = 0.25 if hint == "highest" else 0.15
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint} +{bonus:.2f}")

            elif hint in ("low", "lowest"):
                sorted_z = sp.sort_objects(objects, sp.up_down_axis, ascending=True)
                low_id = getattr(sorted_z[0], "id", "")
                for c in candidates:
                    if c.entity_ref.entity_id == low_id:
                        bonus = 0.25 if hint == "lowest" else 0.15
                        c.score_components["spatial_match"] = c.score_components.get("spatial_match", 0) + bonus
                        c.evidence.append(f"spatial:{hint} +{bonus:.2f}")

    def _resolve_ordinal_cross_object(
        self, candidates: List[ScoredCandidate], objects: List[Any], hints: List[str]
    ) -> None:
        """Resolve ordinal cues (first, second, third, last) by sorting peers."""
        sp = self.config.spatial
        sorted_objs = sp.sort_objects(objects, sp.default_ordinal_axis,
                                       ascending=sp.first_is_lowest)
        obj_ids = [getattr(o, "id", "") for o in sorted_objs]
        n = len(sorted_objs)

        ordinal_map = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}

        for hint in hints:
            if hint == "last":
                idx = n - 1
            elif hint in ordinal_map:
                idx = ordinal_map[hint]
            else:
                continue

            if idx < n:
                target_id = obj_ids[idx]
                for c in candidates:
                    if c.entity_ref.entity_id == target_id:
                        c.score_components["ordinal_match"] = c.score_components.get("ordinal_match", 0) + 0.40
                        c.evidence.append(f"ordinal:{hint}(idx={idx}/{n}) +0.40")

    def _resolve_size_cross_object(
        self, candidates: List[ScoredCandidate], objects: List[Any], hints: List[str]
    ) -> None:
        """Resolve size cues by comparing object volumes within a peer group."""
        # Compute volumes
        obj_vols = []
        for o in objects:
            bbox = getattr(o, "bbox", None)
            if bbox:
                vol = getattr(bbox, "width", 0.05) * getattr(bbox, "height", 0.05) * getattr(bbox, "depth", 0.05)
            else:
                vol = 0.0
            obj_vols.append((getattr(o, "id", ""), vol))

        sorted_by_vol = sorted(obj_vols, key=lambda x: x[1])
        vol_ids = [v[0] for v in sorted_by_vol]
        vols = [v[1] for v in sorted_by_vol]
        n = len(vols)

        for hint in hints:
            if hint == "largest":
                target_id = vol_ids[-1]
                for c in candidates:
                    if c.entity_ref.entity_id == target_id:
                        c.score_components["size_match"] = c.score_components.get("size_match", 0) + 0.30
                        c.evidence.append(f"size:largest(vol={vols[-1]:.4f}) +0.30")
            elif hint == "smallest":
                target_id = vol_ids[0]
                for c in candidates:
                    if c.entity_ref.entity_id == target_id:
                        c.score_components["size_match"] = c.score_components.get("size_match", 0) + 0.30
                        c.evidence.append(f"size:smallest(vol={vols[0]:.4f}) +0.30")
            elif hint == "large" and n >= 2:
                # "Large": top half by volume
                threshold_idx = max(0, n // 2)
                large_ids = set(vol_ids[threshold_idx:])
                for c in candidates:
                    if c.entity_ref.entity_id in large_ids:
                        c.score_components["size_match"] = c.score_components.get("size_match", 0) + 0.20
                        c.evidence.append("size:large(top_half) +0.20")
                    elif n == 2:
                        # In 2-object case, the non-large one is definitively small
                        c.score_components["size_match"] = c.score_components.get("size_match", 0) - 0.15
                        c.evidence.append("size:not_large(2obj_case) -0.15")
            elif hint == "small" and n >= 2:
                threshold_idx = max(0, n // 2)
                small_ids = set(vol_ids[:threshold_idx])
                for c in candidates:
                    if c.entity_ref.entity_id in small_ids:
                        c.score_components["size_match"] = c.score_components.get("size_match", 0) + 0.20
                        c.evidence.append("size:small(bottom_half) +0.20")
                    elif n == 2:
                        c.score_components["size_match"] = c.score_components.get("size_match", 0) - 0.15
                        c.evidence.append("size:not_small(2obj_case) -0.15")

    # ── Phase 4: Score combination ───────────────────────────

    def _compute_language_score(self, components: Dict[str, float]) -> float:
        """Compute language relevance score from text-matching components."""
        w = self.config
        return (
            w.category_weight * components.get("category_match", 0) +
            w.color_weight * components.get("color_match", 0) +
            w.material_weight * components.get("material_match", 0) +
            w.size_weight * components.get("size_match", 0) +
            w.spatial_weight * components.get("spatial_match", 0) +
            w.ordinal_weight * components.get("ordinal_match", 0) +
            w.motion_weight * components.get("motion_match", 0) +
            w.exact_id_match_bonus * components.get("exact_original_id_match", 0) +
            components.get("single_candidate_boost", 0)
        )

    def _compute_feasibility_score(self, components: Dict[str, float]) -> float:
        """Compute feasibility score from role/scene compatibility components."""
        w = self.config
        return (
            w.role_affordance_weight * components.get("role_affordance_match", 0) +
            w.state_compatibility_weight * components.get("state_compatibility", 0) +
            w.negative_constraint_weight * components.get("negative_constraint_conflict", 0)
        )

    def _combine_scores(self, language_score: float, feasibility_score: float) -> float:
        """Combine language and feasibility scores."""
        if self.config.feasibility_blend == "multiply":
            # Feasibility as a penalty/boost multiplier: 0→0.5, 1→1.0, >1→boost
            feasibility_factor = 0.5 + 0.5 * max(0.0, feasibility_score)
            return language_score * feasibility_factor
        else:
            # Weighted sum
            w = self.config.feasibility_blend_weight
            return (1 - w) * language_score + w * feasibility_score

    # ── Phase 5: Ambiguity detection ─────────────────────────

    def _build_result(self, candidates: List[ScoredCandidate], role: str) -> GroundingResult:
        """Build the final GroundingResult with ambiguity detection."""
        result = GroundingResult(role=role, candidates=candidates)

        if not candidates:
            result.needs_clarification = True
            result.clarification = ClarificationRequest(
                role=role,
                question=f"No candidates found for role '{role}'",
            )
            return result

        # Ranking is not grounding.  A candidate must carry positive evidence
        # from the mention (or from an explicit role affordance such as
        # ``支撑面``).  Without this gate, a scene object could win merely
        # because it is the least-bad candidate and receive a fabricated ID.
        evidence_candidates = [
            c for c in candidates
            if not c.hard_rejections and self._has_grounding_evidence(c, role)
        ]
        if not evidence_candidates:
            result.needs_clarification = True
            result.clarification = ClarificationRequest(
                role=role,
                question=f"No scene entity has sufficient evidence for role '{role}'",
            )
            result.selected = None
            result.ambiguity_gap = 0.0
            return result

        # Never let an evidence-free candidate compete with a supported one.
        candidates = evidence_candidates
        result.candidates = candidates
        top1 = candidates[0]
        top2 = candidates[1] if len(candidates) > 1 else None

        # Check min_accept_score
        if top1.total_score < self.config.min_accept_score:
            result.needs_clarification = True
            result.clarification = ClarificationRequest(
                role=role,
                candidate_ids=[top1.entity_ref.entity_id] if top1.entity_ref.entity_id else [],
                question=self._generate_clarification_question(candidates[:3], role),
            )
            result.selected = None
            result.ambiguity_gap = 0.0
            return result

        # Check min_selection_margin
        if top2:
            gap = top1.total_score - top2.total_score
            result.ambiguity_gap = gap
            if gap < self.config.min_selection_margin:
                result.needs_clarification = True
                result.clarification = ClarificationRequest(
                    role=role,
                    candidate_ids=[c.entity_ref.entity_id for c in candidates[:3] if c.entity_ref.entity_id],
                    question=self._generate_clarification_question(candidates[:3], role),
                )
                result.selected = None
                return result

        # Clean selection
        result.selected = top1
        result.needs_clarification = False
        return result

    @staticmethod
    def _has_grounding_evidence(candidate: ScoredCandidate, role: str) -> bool:
        """Return whether a candidate is supported by observable semantics.

        Category/attribute/spatial evidence is required for object roles.  A
        generic surface expression is also valid when the scene explicitly
        advertises a support/fixed affordance; this is the only intentional
        affordance-only exception because ``支撑面`` is a functional role, not
        an object category.
        """
        comps = candidate.score_components
        if comps.get("category_match", 0.0) > 0:
            return True
        if any(comps.get(key, 0.0) > 0 for key in (
            "color_match", "material_match", "size_match", "spatial_match",
            "ordinal_match", "motion_match", "exact_original_id_match",
        )):
            return True
        if role in ("support_surface", "destination") and comps.get("role_affordance_match", 0.0) > 0:
            return True
        return False

    def _generate_clarification_question(
        self, top_candidates: List[ScoredCandidate], role: str
    ) -> str:
        """Generate a human-readable clarification question."""
        if not top_candidates:
            return f"无法确定 {role} 对应的物体"

        mentions = []
        for c in top_candidates[:3]:
            mention = c.entity_ref.mention or "未知物体"
            # Try to add distinguishing feature
            obj_attrs = {}
            if c.entity_ref.entity_id and hasattr(self, '_last_scene') and self._last_scene:
                obj = self._last_scene.find_object(c.entity_ref.entity_id)
                if obj:
                    obj_attrs = getattr(obj, "attributes", {}) or {}
            color = obj_attrs.get("color", "")
            if color and color != "unknown":
                mention = f"{color}色的{mention}"
            mentions.append(mention)

        if len(mentions) == 1:
            return f"你指的是{mentions[0]}吗？"
        elif len(mentions) == 2:
            return f"你指的是{mentions[0]}还是{mentions[1]}？"
        else:
            return f"你指的是{', '.join(mentions[:-1])}还是{mentions[-1]}？"

    # ── Individual scorers ───────────────────────────────────

    def _score_category(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Score object by category name/label/class match."""
        evidence: List[str] = []
        score = 0.0
        obj_name = getattr(obj, "name", "")
        obj_label = getattr(obj, "label", "") or ""
        specific_class = getattr(obj, "specific_class", "") or ""
        parent_classes = list(getattr(obj, "parent_classes", []) or [])

        # Exact full-name match (strongest)
        if obj_name and obj_name == text:
            return 0.60, [f"exact_name:{obj_name} +0.60"]

        # Substring name match
        if obj_name and obj_name in text:
            score += 0.35
            evidence.append(f"name_substr:{obj_name} +0.35")

        # Label match
        if obj_label and obj_label in text:
            score += 0.25
            evidence.append(f"label:{obj_label} +0.25")

        # Specific class match
        if specific_class and specific_class in text:
            score += 0.20
            evidence.append(f"specific_class:{specific_class} +0.20")

        # Parent class match
        for pc in parent_classes:
            if pc and pc in text:
                score += 0.15
                evidence.append(f"parent_class:{pc} +0.15")
                break

        # Cross-language alias match
        aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
        best_alias = None
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in text:
                best_alias = alias
                break
        if best_alias:
            bonus = 0.30 if len(best_alias) >= 2 else 0.20
            score += bonus
            evidence.append(f"alias:{best_alias} +{bonus:.2f}")

        # Partial character-level alias match
        if not best_alias:
            import re as _re
            cn_segs = _re.findall(r'[一-鿿]+', text)
            latin_segs = _re.findall(r'[a-zA-Z]+', text.lower() if text else "")
            for segment in cn_segs + latin_segs:
                if len(segment) < 2:
                    continue
                for alias in sorted(aliases, key=len, reverse=True):
                    if alias and len(alias) >= 2 and alias in segment:
                        bonus = 0.22
                        score += bonus
                        evidence.append(f"alias_partial:{alias}~{segment} +{bonus:.2f}")
                        best_alias = alias
                        break
                if best_alias:
                    break

        return score, evidence

    def _score_color(self, obj: Any, text: str, color_hint: Optional[str], role: str = "theme") -> Tuple[float, List[str]]:
        """Score by color attribute match."""
        evidence: List[str] = []
        score = 0.0
        obj_attrs = getattr(obj, "attributes", {}) or {}
        obj_color = obj_attrs.get("color", "")

        if color_hint is None:
            color_hint = self.config.derive_color_hint(text)

        if not color_hint or not obj_color or obj_color == "unknown":
            return 0.0, []

        if obj_color == color_hint:
            score += 0.30
            evidence.append(f"color_match:{color_hint} +0.30")
        else:
            score -= 0.40
            evidence.append(f"color_mismatch:{obj_color}!={color_hint} -0.40")

        return score, evidence

    def _score_material(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Score by material attribute match."""
        evidence: List[str] = []
        score = 0.0
        obj_attrs = getattr(obj, "attributes", {}) or {}
        obj_mat = obj_attrs.get("material", "")

        mat_aliases = {
            "glass": ["玻璃"], "plastic": ["塑料"], "wood": ["木", "木头"],
            "metal": ["金属", "铁", "钢"], "ceramic": ["陶瓷", "瓷"],
            "rubber": ["橡胶"], "cardboard": ["纸", "纸板"],
        }
        for mat_en, mat_cn_list in mat_aliases.items():
            if obj_mat == mat_en:
                if any(mc in text for mc in mat_cn_list):
                    score += 0.25
                    evidence.append(f"material_match:{mat_en} +0.25")
                break

        return score, evidence

    def _score_size_prelim(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Preliminary size cue scoring (refined in cross-object phase)."""
        evidence: List[str] = []
        score = 0.0
        size_hints = self.config.derive_size_hints(text)
        if not size_hints:
            return 0.0, []
        # Score is applied in cross-object phase
        evidence.append(f"size_cues:{size_hints}")
        return score, evidence

    def _score_spatial_prelim(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Preliminary spatial cue scoring (refined in cross-object phase)."""
        evidence: List[str] = []
        score = 0.0
        hints = self.config.derive_spatial_hints(text)
        if not hints:
            return 0.0, []
        evidence.append(f"spatial_cues:{hints}")
        # Cross-object phase assigns scores; here we just detect cues
        return score, evidence

    def _score_ordinal_prelim(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Preliminary ordinal cue scoring (refined in cross-object phase)."""
        evidence: List[str] = []
        score = 0.0
        hints = self.config.derive_ordinal_hints(text)
        if not hints:
            return 0.0, []
        evidence.append(f"ordinal_cues:{hints}")
        return score, evidence

    def _score_motion(self, obj: Any, text: str) -> Tuple[float, List[str]]:
        """Score by motion state match."""
        evidence: List[str] = []
        score = 0.0
        motion_hint = self.config.derive_motion_hint(text)
        if not motion_hint:
            return 0.0, []

        obj_attrs = getattr(obj, "attributes", {}) or {}
        is_moving = obj_attrs.get("_is_moving", False)
        speed = obj_attrs.get("_speed_mps", 0.0)

        if motion_hint == "moving":
            if is_moving:
                score += 0.25
                evidence.append(f"motion:moving(speed={speed:.3f}) +0.25")
            else:
                score -= 0.20
                evidence.append("motion:expected_moving_but_static -0.20")
        elif motion_hint == "static":
            if not is_moving:
                score += 0.20
                evidence.append("motion:static +0.20")

        return score, evidence

    def _score_role_affordance(self, obj: Any, role: str) -> Tuple[float, List[str], List[str]]:
        """Score by role-appropriate affordances. Returns (score, evidence, hard_rejections)."""
        evidence: List[str] = []
        rejections: List[str] = []
        score = 0.0
        affs = self._get_affordance_set(obj)

        if role == "theme":
            if "graspable" in affs:
                score += 0.10
                evidence.append("affordance:graspable +0.10")
            if "fragile" in affs:
                evidence.append("affordance:fragile (info)")
            if "fixed" in affs:
                rejections.append("theme_cannot_be_fixed")
        elif role in ("destination", "support_surface"):
            if "support_surface" in affs or "fixed" in affs:
                score += 0.25
                evidence.append("affordance:surface +0.25")
        elif role == "obstacle":
            if "fixed" in affs:
                score += 0.10
                evidence.append("affordance:fixed_obstacle +0.10")

        return score, evidence, rejections

    def _score_state_compatibility(self, obj: Any, role: str) -> Tuple[float, List[str], List[str]]:
        """Score by robot state compatibility. Returns (score, evidence, hard_rejections)."""
        evidence: List[str] = []
        rejections: List[str] = []
        score = 0.0
        return score, evidence, rejections

    def _score_exact_id(self, obj: Any, preferred_ids: Set[str]) -> Tuple[float, List[str]]:
        """Bonus for exact original object_id match."""
        evidence: List[str] = []
        score = 0.0
        if not preferred_ids:
            return 0.0, []

        obj_id = getattr(obj, "id", "")
        obj_pid = (getattr(obj, "attributes", {}) or {}).get("_perception_object_id", "")

        if obj_id in preferred_ids or obj_pid in preferred_ids:
            score += 1.0
            evidence.append(f"exact_id_match:{obj_id} +1.0")

        return score, evidence

    # ── Helpers ──────────────────────────────────────────────

    @staticmethod
    def _get_affordance_set(obj: Any) -> Set[str]:
        affs = getattr(obj, "affordances", []) or []
        return {a.value if hasattr(a, 'value') else str(a) for a in affs}

    @staticmethod
    def _best_text_span(obj: Any, text: str) -> str:
        specific_class = getattr(obj, "specific_class", "") or ""
        aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in text:
                return alias
        obj_name = getattr(obj, "name", "")
        if obj_name and obj_name in text:
            return obj_name
        return specific_class or obj_name or "unknown"


# ══════════════════════════════════════════════════════════════
# Grounding invariants — post-grounding consistency checks
# ══════════════════════════════════════════════════════════════

def apply_grounding_invariants(
    theme: Optional[SemanticEntityRef],
    destination: Optional[SemanticEntityRef],
    support_surface: Optional[SemanticEntityRef],
    obstacles: List[SemanticEntityRef],
    action: "TaskActionKind",
    config=None,
) -> List[str]:
    """Apply cross-role grounding invariants.

    Returns a list of invariant violations (empty = all good).
    """
    from robot_intent_agent.config.grounding_config import get_grounding_config
    if config is None:
        config = get_grounding_config()
    if not config.enable_role_invariants:
        return []

    violations: List[str] = []

    theme_id = theme.entity_id if theme else None
    dest_id = destination.entity_id if destination else None
    ss_id = support_surface.entity_id if support_surface else None
    avoid_ids = {o.entity_id for o in obstacles if o.entity_id}

    # theme ≠ destination (unless action explicitly allows self-referential)
    if theme_id and dest_id and theme_id == dest_id:
        if action not in (TaskActionKind.CUSTOM,):
            violations.append(f"theme==destination ({theme_id}) — roles must differ")

    # avoid ≠ theme
    if theme_id and theme_id in avoid_ids:
        violations.append(f"theme ({theme_id}) is also in avoid set")

    # avoid ≠ destination
    if dest_id and dest_id in avoid_ids:
        violations.append(f"destination ({dest_id}) is also in avoid set")

    return violations


def parse_task_semantics(instruction: str, scene: Any = None, robot_state: Optional[Dict[str, Any]] = None) -> ParsedTask:
    normalized = _normalize_text(instruction)
    action = _classify_action(normalized)
    # Conditional fetches and colloquial “帮我拿一下” contain a delivery
    # intent even when the surface verb is only “拿”.  Promote them before
    # fusion so the LLM cannot collapse the task to a bare GRASP.
    if action == TaskActionKind.GRASP and re.search(
        r"(?:否则|不然)\s*(?:拿|取|抓)|如果.+?(?:拿它|取它).*(?:否则|不然)|帮我拿一下|帮我取一下",
        normalized,
    ):
        action = TaskActionKind.FETCH
    motion_state = _extract_motion_state(normalized)
    if action == TaskActionKind.GRASP and motion_state.state == "moving":
        action = TaskActionKind.DYNAMIC_GRASP

    # ── Parse logical AST (negation, condition, sequence, manner) ──
    from robot_intent_agent.semantic_reasoner.logical_ast import (
        parse_logical_ast, merge_ast_negations, ast_has_conditional, ast_get_unsupported_reason,
    )
    logical_ast = parse_logical_ast(instruction, robot_state=robot_state)

    notes: List[str] = []
    ast_negated_refs, ast_manner = merge_ast_negations(logical_ast)

    # Handle conditional structures
    # Pure sequences do not require robot-state evaluation.  The previous
    # combined note made “A并B” look like an unevaluated IF condition.
    if logical_ast.conditions or logical_ast.wait_until:
        unsupported = ast_get_unsupported_reason(logical_ast)
        if unsupported:
            notes.append(f"unsupported_conditional:{unsupported}")
        else:
            # Conditional structure detected but evaluable — pass through
            notes.append(f"conditional_detected:{len(logical_ast.conditions)} conditions, "
                        f"{len(logical_ast.sequences)} sequences")
    if logical_ast.sequences:
        notes.append(f"sequence_detected:{len(logical_ast.sequences)} sequences")

    composite_steps: List[Dict[str, Any]] = []
    for seq in logical_ast.sequences:
        for logical_step in seq.steps:
            raw_action = str(logical_step.action or "CUSTOM").upper()
            try:
                step_action = TaskActionKind(raw_action).value
            except ValueError:
                step_action = TaskActionKind.CUSTOM.value
            composite_steps.append({
                "step_index": len(composite_steps) + 1,
                "action": step_action,
                "theme_mention": logical_step.theme_ref,
                "destination_mention": logical_step.destination_ref,
                "description": logical_step.raw_text,
            })

    # ── Initialize GroundingEngine ──
    engine = GroundingEngine()
    # Derive color hint for theme, excluding colors that only appear in negated clauses
    _negated_colors: Set[str] = set()
    for ref_text in ast_negated_refs:
        for cn_word, cn_color in engine.config.color_map.items():
            if cn_word in ref_text:
                _negated_colors.add(cn_color)
    _color_hint = engine.config.derive_color_hint(normalized, exclude_colors=_negated_colors)

    theme: Optional[SemanticEntityRef] = None
    source: Optional[SemanticEntityRef] = None
    destination: Optional[SemanticEntityRef] = None
    recipient: Optional[SemanticEntityRef] = None
    support_surface: Optional[SemanticEntityRef] = None
    obstacles: List[SemanticEntityRef] = []

    # Role-local spans prevent every mentioned object from competing for every
    # semantic role. Entity IDs are still selected exclusively from the scene.
    role_text = {"theme": instruction, "source": instruction, "destination": instruction,
                 "support_surface": instruction, "obstacle": instruction}
    if action == TaskActionKind.PLACE:
        place_match = re.search(
            r"(?:把|将|请将|拿起)?\s*([^，,。；;]+?)\s*"
            r"(?:抓起后|拿起后|并)?\s*(?:放到|放在|放入|放进|放回|摆放在|摆到|置于)\s*"
            r"([^，,。；;]+?)(?:上面|上|里面|里|中)?(?:[，,。；;]|$)",
            normalized,
        )
        if place_match:
            role_text["theme"] = place_match.group(1).strip()
            role_text["destination"] = place_match.group(2).strip()
            role_text["support_surface"] = place_match.group(2).strip()
    # Role-local clause extraction for open paraphrases.  Scoring the whole
    # sentence makes the obstacle/destination compete with the theme.
    place_clause = re.search(
        r"\u628a\s*(.+?)\s*\u653e(?:\u5230|\u5728|\u5165|\u8fdb)\s*(.+?)(?:\u4e0a\u9762|\u4e0a|\u91cc|\u4e2d)?(?:[\uff0c,\u3002。]|$)",
        normalized,
    )
    if place_clause:
        role_text["theme"] = place_clause.group(1).strip()
        role_text["destination"] = place_clause.group(2).strip()
        role_text["support_surface"] = place_clause.group(2).strip()
    transfer_clause = re.search(
        r"(?:把|将|请将)?\s*(.+?)\s*(?:上料到|搬运到|送到|运到|转移到|移到)\s*(.+?)(?:[，,。；;]|$)",
        normalized,
    )
    if transfer_clause:
        role_text["theme"] = transfer_clause.group(1).strip()
        role_text["destination"] = transfer_clause.group(2).strip()
    source_clause = re.search(r"从\s*(.+?)\s*(?:移|搬|转|送|运)到", normalized)
    if source_clause:
        role_text["source"] = source_clause.group(1).strip()
    contrast_clause = re.search(
        r"(?:\u4e0d\u8981\u78b0|\u522b\u78b0|\u907f\u5f00|\u4e0d\u8981\u63a5\u89e6)\s*(.+?)[\uff0c,]\s*(?:\u628a)?\s*(.+?)(?:\u62ff\u8fc7\u6765|\u62ff\u8d77|\u62ff|\u62ff\u6765|\u62ff\u7ed9|\u62c9\u8fc7\u6765)",
        normalized,
    )
    if contrast_clause:
        role_text["obstacle"] = contrast_clause.group(1).strip()
        role_text["theme"] = contrast_clause.group(2).strip()
    conditional_clause = re.search(
        r"(?:\u5982\u679c\u770b\u5230|\u5982\u679c)\s*(.+?)\s*(?:\u5c31|\u5219)\s*(?:\u5148)?\u62ff[^，,。；;]*[，,。；;]\s*(?:\u5426\u5219|\u5426\u5219\u62ff)\s*(.+)",
        normalized,
    )
    if conditional_clause:
        role_text["theme"] = conditional_clause.group(1).strip()
    # Action-first clauses keep the target and the avoided object in separate
    # grounding spans: “抓住玻璃杯，别碰塑料杯”.
    action_contrast = re.search(
        r"(?:抓住|拿起|拿来|拿过来|推过来|移动|夹住|捡起)\s*(.+?)[，,]\s*"
        r"(?:别碰|不要碰|避开|不要接触)\s*(.+?)(?:[，,。；;]|$)",
        normalized,
    )
    if action_contrast:
        role_text["theme"] = action_contrast.group(1).strip()
        role_text["obstacle"] = action_contrast.group(2).strip()
    else:
        # Normalization converts Chinese punctuation to ASCII.  A literal
        # clause split is more robust than another regex for colloquial forms
        # such as “把盒子拿过来，千万别碰玻璃杯”.
        for caution in ("别碰", "不要碰", "避开", "不要接触"):
            if caution not in normalized:
                continue
            left, right = normalized.split(caution, 1)
            if not re.search(r"抓住|拿起|拿来|拿过来|推过来|移动|夹住|捡起", left):
                continue
            theme_text = re.sub(
                r"^(?:把|将|请将)?\s*(?:抓住|拿起|拿来|拿过来|推过来|移动|夹住|捡起)\s*",
                "", left,
            ).strip(" ,")
            obstacle_text = right.strip(" ,。；;")
            if theme_text and obstacle_text:
                role_text["theme"] = theme_text
                role_text["obstacle"] = obstacle_text
                break
    # In “先抓住杯子，再放到桌子上”, the first clause is the theme and
    # the second clause supplies the destination/support surface.
    sequence_clause = re.search(
        r"先(?:抓住|拿起|拿|夹住|捡起)\s*(.+?)\s*(?:再|然后|之后)\s*"
        r"(?:放到|放在|放入|放进)\s*(.+?)(?:[，,。；;]|$)",
        normalized,
    )
    if sequence_clause:
        role_text["theme"] = sequence_clause.group(1).strip()
        role_text["destination"] = sequence_clause.group(2).strip()
        role_text["support_surface"] = sequence_clause.group(2).strip()
    obstacle_match = (
        re.search(r"在不接触\s*([^，,。；;]+?)\s*的情况下", normalized)
        or re.search(
            r"(?:别碰|不要碰|不能碰|禁止接触|避开|绕开|绕过|躲开|别经过|不要经过|路径别经过|别拿)\s*"
            r"([^，,。；;]+)", normalized,
        )
    )
    if obstacle_match:
        role_text["obstacle"] = obstacle_match.group(1).strip()

    # ── Per-role grounding ──
    if scene is not None:
        # ── Theme ──
        # Color belongs to the theme span, not to the whole instruction.  In
        # “不要碰红色的，把蓝色的拿过来”, the red mention is an obstacle and
        # must never poison theme grounding.
        theme_color_hint = engine.config.derive_color_hint(
            role_text["theme"],
            exclude_colors=_negated_colors,
        ) or _color_hint
        theme_result = engine.ground(role_text["theme"], scene, role="theme", color_hint=theme_color_hint)
        if theme_result.selected is not None:
            theme = theme_result.selected.entity_ref
            theme.grounding_confidence = min(theme_result.selected.total_score, 1.0)
            theme.match_evidence = list(theme_result.selected.evidence)
        elif theme_result.needs_clarification:
            # Ambiguous or rejected — do NOT fabricate a theme
            if theme_result.clarification:
                notes.append(f"clarification_needed:theme={theme_result.clarification.question}")
            # theme stays None → will trigger unmet_roles → BLOCKED/NEEDS_CLARIFICATION

        # Demonstratives are resolvable when perception exposes exactly one
        # object, even if the generic scorer produced low-confidence
        # candidates.  This covers “那个东西/那玩意儿/这个”.
        if theme is None and len(getattr(scene, "objects", []) or []) == 1:
            demo = re.search(r"(?:那个|这个|那玩意|这玩意|那东西|这东西|that thing|the thing)", normalized, re.I)
            if demo:
                theme = SemanticEntityRef.from_scene_object(
                    scene.objects[0], role="theme", text_span=demo.group(0)
                )
                theme.grounding_confidence = 0.35
                theme.match_evidence = ["demonstrative_fallback:single_object +0.35"]

        # A pure conditional such as “除非夹爪是空的，否则不要抓取” has no
        # noun, but a single visible target still supplies the only legal
        # grounding candidate.  The condition itself remains a safety gate.
        if theme is None and len(getattr(scene, "objects", []) or []) == 1:
            if action in (TaskActionKind.GRASP, TaskActionKind.FETCH, TaskActionKind.PLACE):
                theme = SemanticEntityRef.from_scene_object(scene.objects[0], role="theme", text_span="唯一可见目标")
                theme.grounding_confidence = 0.30
                theme.match_evidence = ["single_object_fallback:implicit_target +0.30"]

        # Exclude theme from subsequent roles
        _exclude_ids: Set[str] = {theme.entity_id} if theme and theme.entity_id else set()

        # ── Destination ──
        # Destination descriptors are role-local. Never apply the theme's
        # color (e.g. red cup) to a gray tray/table destination.
        dest_color_hint = engine.config.derive_color_hint(role_text["destination"])
        dest_result = engine.ground(role_text["destination"], scene, role="destination",
                                     exclude_ids=_exclude_ids, color_hint=dest_color_hint)
        if dest_result.selected is not None:
            destination = dest_result.selected.entity_ref
            destination.grounding_confidence = min(dest_result.selected.total_score, 1.0)
            _exclude_ids.add(destination.entity_id)

        # Industrial transfer may explicitly name a source station/bin.  It
        # is optional in the TRANSFER schema, but when present it is grounded
        # by the same deterministic engine rather than copied from text.
        if action == TaskActionKind.TRANSFER and role_text.get("source") != instruction:
            src_result = engine.ground(role_text["source"], scene, role="source",
                                       exclude_ids=_exclude_ids)
            if src_result.selected is not None:
                source = src_result.selected.entity_ref
                source.grounding_confidence = min(src_result.selected.total_score, 1.0)
                _exclude_ids.add(source.entity_id)

        # ── Support surface ──
        ss_color_hint = engine.config.derive_color_hint(role_text["support_surface"])
        ss_result = engine.ground(role_text["support_surface"], scene, role="support_surface",
                                   exclude_ids=_exclude_ids, color_hint=ss_color_hint)
        if ss_result.selected is not None:
            support_surface = ss_result.selected.entity_ref
            support_surface.grounding_confidence = min(ss_result.selected.total_score, 1.0)
            _exclude_ids.add(support_surface.entity_id)

        # ── Obstacles ──
        obs_result = engine.ground(role_text["obstacle"], scene, role="obstacle",
                                    exclude_ids=_exclude_ids)
        if obs_result.candidates:
            for c in obs_result.candidates:
                if c.total_score >= engine.config.min_accept_score:
                    ref = c.entity_ref
                    ref.grounding_confidence = min(c.total_score, 1.0)
                    ref.match_evidence = list(c.evidence)
                    obstacles.append(ref)

    # ── Fallback: only when GroundingEngine had NO candidates (not when it rejected them) ──
    engine_found_candidates = scene is not None and len(theme_result.candidates) > 0
    if theme is None and not engine_found_candidates:
        # Pass 1: conservative noun phrase extraction
        _NOUN_LIST = ["红色药瓶", "蓝色药瓶", "红色瓶子", "红色玻璃杯", "玻璃杯",
                      "药瓶", "瓶子", "杯子", "水杯", "盒子", "托盘", "盒", "瓶",
                      "方块", "积木", "桌上", "桌子", "球"]
        for noun in _NOUN_LIST:
            if noun in normalized:
                specific_class, parent_class, parents = _infer_specific_class(noun)
                theme = SemanticEntityRef(
                    mention=noun, specific_class=specific_class, parent_class=parent_class,
                    entity_id=None, role="theme", text_span=noun,
                    grounding_confidence=0.3 if specific_class else 0.0,
                    source="nl", ontology_path=parents,
                )
                break

        # Pass 2: single object demonstrative fallback
        if theme is None and scene is not None:
            _DEMONSTRATIVES = ("那个", "这个", "那玩意", "这玩意", "那东西", "这东西",
                              "那个东西", "那个小的", "那个大的")
            has_demonstrative = any(d in normalized for d in _DEMONSTRATIVES) or bool(
                re.search(r"(?:\u90a3\u4e2a\u4e1c\u897f|\u90a3\u73a9\u610f\u513f|\u90a3\u4e1c\u897f|that thing|the thing)", normalized, re.I))
            scene_objs = getattr(scene, "objects", []) or []
            if has_demonstrative and len(scene_objs) == 1:
                obj = scene_objs[0]
                theme = SemanticEntityRef.from_scene_object(
                    obj, role="theme",
                    text_span=next((d for d in _DEMONSTRATIVES if d in normalized), normalized[:6])
                )
                theme.grounding_confidence = 0.35
                theme.match_evidence = ["demonstrative_fallback:single_object +0.35"]

    # ── Post-grounding Chinese mention resolution ──
    if theme is not None and theme.source == "scene":
        specific_class = getattr(theme, "specific_class", None) or ""
        cn_aliases = _CN_CATEGORY_ALIASES.get(specific_class, [])
        best_alias = None
        for alias in sorted(cn_aliases, key=len, reverse=True):
            if alias in normalized:
                best_alias = alias
                break
        if best_alias:
            theme.mention = best_alias
            theme.text_span = best_alias
        if theme_color_hint:
            from robot_intent_agent.config.grounding_config import get_grounding_config
            _color_map = get_grounding_config().color_map
            for alias in cn_aliases:
                cn_full = next((cw for cw, ce in _color_map.items() if ce == theme_color_hint), "") + alias
                if cn_full in normalized:
                    theme.mention = cn_full
                    theme.text_span = cn_full
                    break

    # ── Role-specific post-processing ──

    # Recipient: still uses hardcoded pattern matching for "我"/"你"/"他"
    if scene is not None and recipient is None:
        recipient = _ground_entity_from_text(normalized, role="recipient", scene=scene,
                                             exclude_ids=_exclude_ids)

    # Recipient is an execution role only for delivery/handover language.
    # Industrial TRANSFER is defined by theme + destination; it must not
    # fabricate a user recipient merely because the Chinese verb means move.
    if action in (TaskActionKind.HANDOVER, TaskActionKind.FETCH) and recipient is None:
        recipient = SemanticEntityRef(
            mention="用户",
            specific_class="human",
            parent_class="agent",
            entity_id="user",
            role="recipient",
            text_span="我/用户",
            grounding_confidence=0.4,
            source="nl",
            ontology_path=["human", "agent"],
        )

    # HANDOVER: destination must NOT be the recipient
    if action in (TaskActionKind.HANDOVER, TaskActionKind.FETCH):
        if destination is not None and destination.entity_id in ("user", "我"):
            destination = None

    # PLACE: promote destination to support_surface
    if action == TaskActionKind.PLACE and support_surface is None and destination is not None:
        support_surface = destination

    # HANDOVER/FETCH/TRANSFER: support_surface is not applicable
    if action in (TaskActionKind.HANDOVER, TaskActionKind.FETCH, TaskActionKind.TRANSFER):
        support_surface = None

    # ── Apply grounding invariants ──
    invariant_violations = apply_grounding_invariants(
        theme, destination, support_surface, obstacles, action,
    )
    for violation in invariant_violations:
        notes.append(f"grounding_invariant_violation:{violation}")

    # ── Extract remaining task semantics ──
    user_constraints = _extract_numeric_constraints(normalized)

    # Manner: AST takes priority, then legacy extraction
    manner = ast_manner or _extract_manner(normalized)

    # ── Negation-aware obstacle merging ──
    # 1. Get obstacles from GroundingEngine (already has role feasibility)
    # 2. Add AST-negated refs as obstacles (these are explicit NL negations)
    # 3. Merge with legacy _extract_obstacles
    existing_ids = {o.entity_id for o in obstacles if o.entity_id}
    existing_mentions = {o.mention for o in obstacles}

    # Add AST-negated refs as obstacles (ground them against scene)
    theme_id = theme.entity_id if theme else None

    # Color map for interpreting adjective-based negations ("那个红色的" → red objects)
    _COLOR_ADJ_MAP = {
        "红": "red", "红色": "red", "红色的": "red",
        "蓝": "blue", "蓝色": "blue", "蓝色的": "blue",
        "绿": "green", "绿色": "green", "绿色的": "green",
        "黄": "yellow", "黄色": "yellow", "黄色的": "yellow",
        "白": "white", "白色": "white", "白色的": "white",
        "黑": "black", "黑色": "black", "黑色的": "black",
        "透明": "transparent", "透明的": "transparent",
    }

    for ref_text in ast_negated_refs:
        if ref_text == "*":
            # Wildcard: all non-theme objects are obstacles
            if scene:
                for obj in getattr(scene, "objects", []) or []:
                    oid = getattr(obj, "id", "")
                    if oid and oid != theme_id and oid not in existing_ids:
                        obstacles.append(SemanticEntityRef.from_scene_object(obj, role="obstacle"))
                        existing_ids.add(oid)
            continue

        # Clean up ref text: strip demonstratives, trailing punctuation
        clean_ref = ref_text.strip().rstrip(",.，。!！?？")
        for demo in ("那个", "这个", "那", "这"):
            if clean_ref.startswith(demo):
                clean_ref = clean_ref[len(demo):]
                break
        clean_ref = clean_ref.strip().rstrip("的,，。")

        if clean_ref in existing_mentions:
            continue

        # Try to ground the negated ref to a scene object
        grounded = None
        if scene:
            # Pass 1: Direct name/alias match
            for obj in getattr(scene, "objects", []) or []:
                obj_id = getattr(obj, "id", "")
                if obj_id == theme_id:
                    continue
                names = [getattr(obj, "name", ""), getattr(obj, "label", "") or "",
                         getattr(obj, "specific_class", "") or ""]
                aliases = _CN_CATEGORY_ALIASES.get(getattr(obj, "specific_class", ""), [])
                all_names = names + aliases
                if any(clean_ref in n or n in clean_ref for n in all_names if len(n) >= 1 and len(clean_ref) >= 1):
                    grounded = SemanticEntityRef.from_scene_object(obj, role="obstacle",
                        text_span=clean_ref)
                    break

            # Pass 2: Color-based matching ("红色的" → red objects)
            if grounded is None:
                color_match = None
                for cn_color, en_color in _COLOR_ADJ_MAP.items():
                    if cn_color in clean_ref:
                        color_match = en_color
                        break
                if color_match:
                    for obj in getattr(scene, "objects", []) or []:
                        obj_id = getattr(obj, "id", "")
                        if obj_id == theme_id:
                            continue
                        obj_color = (getattr(obj, "attributes", {}) or {}).get("color", "")
                        if obj_color == color_match and obj_color != "unknown":
                            grounded = SemanticEntityRef.from_scene_object(obj, role="obstacle",
                                text_span=clean_ref)
                            break

            # Pass 3: Category-only match with scene lookup
            if grounded is None and len(clean_ref) >= 1:
                for obj in getattr(scene, "objects", []) or []:
                    obj_id = getattr(obj, "id", "")
                    if obj_id == theme_id:
                        continue
                    sc = getattr(obj, "specific_class", "") or ""
                    cn_aliases = _CN_CATEGORY_ALIASES.get(sc, [])
                    for alias in cn_aliases:
                        if alias and len(alias) >= 2 and alias in clean_ref:
                            grounded = SemanticEntityRef.from_scene_object(obj, role="obstacle",
                                text_span=clean_ref)
                            break
                    if grounded:
                        break

        if grounded:
            obstacles.append(grounded)
            existing_ids.add(grounded.entity_id)
            existing_mentions.add(grounded.mention)
        elif clean_ref:
            # NL-only obstacle: couldn't ground but keep the mention
            obstacles.append(SemanticEntityRef(
                mention=clean_ref, entity_id=None, role="obstacle",
                text_span=clean_ref, grounding_confidence=0.0, source="nl",
            ))
            existing_mentions.add(clean_ref)

    # ── CriticalSemanticExtractor obstacles (Phase 10: HV0070 fix) ──
    from robot_intent_agent.semantic_reasoner.critical_semantic_extractor import extract_critical_semantics
    critical_semantics = extract_critical_semantics(normalized)
    for neg in critical_semantics.negations:
        if neg.target_mention in existing_mentions:
            continue
        # Try to ground the extracted negation target against scene
        grounded = None
        if scene:
            for obj in getattr(scene, "objects", []) or []:
                obj_id = getattr(obj, "id", "")
                if obj_id == theme_id:
                    continue
                names = [getattr(obj, "name", ""), getattr(obj, "label", "") or "",
                         getattr(obj, "specific_class", "") or ""]
                aliases = _CN_CATEGORY_ALIASES.get(getattr(obj, "specific_class", ""), [])
                all_names = [n for n in names if n] + aliases
                attrs = getattr(obj, "attributes", {}) or {}
                description_match = bool(neg.target_description) and all(
                    str(attrs.get(k, "")).lower() == str(v).lower()
                    for k, v in neg.target_description.items()
                )
                if description_match or any(neg.target_mention in n or n in neg.target_mention for n in all_names if len(n) >= 1 and len(neg.target_mention) >= 1):
                    grounded = SemanticEntityRef.from_scene_object(obj, role="obstacle",
                        text_span=neg.target_mention)
                    grounded.match_evidence = [f"critical_extractor:{neg.type.value}"]
                    break
        if grounded:
            obstacles.append(grounded)
            existing_ids.add(grounded.entity_id)
            existing_mentions.add(grounded.mention)
        elif neg.target_mention:
            obstacles.append(SemanticEntityRef(
                mention=neg.target_mention, entity_id=None, role="obstacle",
                text_span=neg.text_span, grounding_confidence=0.0, source="nl",
                match_evidence=[f"critical_extractor:{neg.type.value}"],
            ))
            existing_mentions.add(neg.target_mention)

    # Merge legacy obstacles (dedup)
    legacy_obstacles = ([] if obstacle_match else
                        _extract_obstacles(normalized, scene=scene, target=theme))
    for obs in legacy_obstacles:
        if obs.entity_id and obs.entity_id not in existing_ids:
            obstacles.append(obs)
            existing_ids.add(obs.entity_id)
        elif not obs.entity_id:
            if obs.mention not in existing_mentions:
                obstacles.append(obs)
                existing_mentions.add(obs.mention)

    raw_mentions: List[str] = []
    for candidate in [theme, destination, recipient, support_surface, *obstacles]:
        if candidate is not None and candidate.mention not in raw_mentions:
            raw_mentions.append(candidate.mention)

    parse_confidence = 0.85 if theme is not None else 0.55
    grounding_confidence = 0.95 if scene and theme and theme.entity_id else (0.7 if theme else 0.3)
    constraint_confidence = 0.95 if user_constraints else 0.4

    unmet_roles: List[str] = []
    if action == TaskActionKind.HANDOVER:
        if recipient is None:
            unmet_roles.append("recipient")
        elif recipient.entity_id == "user":
            unmet_roles.append("recipient_pose_or_handover_zone")
    elif action == TaskActionKind.FETCH:
        if destination is None and recipient is None:
            unmet_roles.append("delivery_pose_or_fetch_zone")
        elif recipient is not None and recipient.entity_id == "user" and destination is None:
            unmet_roles.append("delivery_pose_or_fetch_zone")
    elif action == TaskActionKind.TRANSFER and destination is None:
        unmet_roles.append("destination")
    if action == TaskActionKind.PLACE and support_surface is None and destination is None:
        unmet_roles.append("support_surface")
    if theme is None:
        unmet_roles.append("theme")

    parsed_result = ParsedTask(
        instruction=instruction,
        action=action,
        theme=theme,
        source=source,
        destination=destination,
        recipient=recipient,
        obstacle=obstacles,
        support_surface=support_surface,
        manner=manner,
        motion_state=motion_state,
        user_constraints=user_constraints,
        raw_mentions=raw_mentions,
        unmet_roles=unmet_roles,
        parse_confidence=parse_confidence,
        grounding_confidence=grounding_confidence,
        constraint_confidence=constraint_confidence,
        notes=notes,
        steps=composite_steps,
    )
    # Build the independent semantic graph after the compatibility parser has
    # completed.  The graph is diagnostic/intermediate data; legacy fields are
    # still populated above and remain the adapter consumed by old callers.
    try:
        from robot_intent_agent.semantic_parser.semantic_pipeline import SemanticPipeline
        semantic_candidate = SemanticPipeline().parse_rule(instruction, scene=scene)
        parsed_result.semantic_task_graph = semantic_candidate.graph.model_dump(mode="json")
        parsed_result.execution_contract = {
            "schema_complete": bool(semantic_candidate.graph.events),
            "entity_ids_verified": all(
                not entity.entity_id or (scene is not None and any(
                    getattr(obj, "id", None) == entity.entity_id
                    for obj in getattr(scene, "objects", []) or []
                )) for entity in semantic_candidate.graph.entities
            ),
            "roles_complete": SemanticPipeline().diagnostics(semantic_candidate, scene).get("roles_complete", False),
            "constraints_resolved": True,
            "behavior_tree_consistent": False,
            "execution_allowed": False,
        }
    except Exception as exc:
        # Compatibility parsing must never fail because the optional graph
        # adapter is unavailable; retain an auditable diagnostic.
        parsed_result.notes.append(f"semantic_graph_adapter_error:{type(exc).__name__}")
    # Transfer only IDs already selected by the deterministic compatibility
    # grounder into the local graph references.  This is binding, not a new
    # source of entity guesses.
    if isinstance(parsed_result.semantic_task_graph, dict):
        graph_data = parsed_result.semantic_task_graph
        role_refs = graph_data.get("metadata", {}).get("role_refs", {}) if isinstance(graph_data.get("metadata"), dict) else {}
        role_values = parsed_result.role_map()

        # The rule grounder intentionally rejects unknown open-vocabulary
        # mentions instead of fabricating an ID.  The semantic graph still
        # carries the mention, so perform one conservative scene-only
        # re-binding pass here.  This is what lets phrases such as “镜片盒”
        # resolve to “光学聚焦镜片盒” while keeping IDs perception-owned.
        scene_objects = list(getattr(scene, "objects", []) or []) if scene is not None else []
        bound_open_entities: Dict[str, Any] = {}
        for graph_entity in graph_data.get("entities", []) or []:
            if graph_entity.get("entity_id") or not scene_objects:
                continue
            mention = str(graph_entity.get("mention") or "").strip()
            if not mention:
                continue
            matches = []
            for obj in scene_objects:
                names = [
                    str(getattr(obj, "name", "") or ""),
                    str(getattr(obj, "label", "") or ""),
                    str(getattr(obj, "specific_class", "") or ""),
                ]
                if any(name and (mention in name or name in mention) for name in names):
                    matches.append(obj)
            if len(matches) == 1:
                obj = matches[0]
                graph_entity["entity_id"] = getattr(obj, "id", None)
                bound_open_entities[graph_entity.get("local_ref", "")] = obj
                graph_entity.setdefault("evidence_spans", []).append(
                    f"scene_name_match:{getattr(obj, 'name', '')}"
                )

        for role, local_ref in role_refs.items():
            entity = role_values.get(role)
            if entity is None and role == "obstacle":
                entity = next(iter(parsed_result.obstacle), None)
            if entity is None and local_ref in bound_open_entities:
                obj = bound_open_entities[local_ref]
                entity = SemanticEntityRef.from_scene_object(
                    obj, role=role,
                    text_span=next(
                        (str(item.get("mention")) for item in graph_data.get("entities", []) or []
                         if item.get("local_ref") == local_ref),
                    ),
                )
                setattr(parsed_result, role if role != "obstacle" else "obstacle", entity if role != "obstacle" else [entity])
                role_values[role] = entity
            if entity is None:
                continue
            for graph_entity in graph_data.get("entities", []) or []:
                if graph_entity.get("local_ref") == local_ref:
                    graph_entity["entity_id"] = entity.entity_id
                    graph_entity.setdefault("evidence_spans", []).extend(entity.match_evidence or [])
        parsed_result.grounding_decisions = [
            {"role": role, "selected_entity_id": value.entity_id,
             "candidate_ids": [value.entity_id] if value.entity_id else [],
             "evidence": list(value.match_evidence or []),
                     "decision": "RESOLVED" if value.entity_id else "NEEDS_CLARIFICATION"}
            for role, value in parsed_result.role_map().items() if value is not None
        ]
        # The split grounding package is the joint-role authority.  Run it as
        # an auditable second pass; it may fill graph IDs only when the scene
        # supplies the candidate and never invents physical IDs.
        if scene is not None:
            try:
                from robot_intent_agent.schemas.semantic_task_graph import SemanticTaskGraph
                from robot_intent_agent.grounding.grounding_engine import GroundingEngine as UnifiedGroundingEngine
                graph_model = SemanticTaskGraph.model_validate(graph_data)
                grounded_graph, joint_decisions = UnifiedGroundingEngine().ground_graph(graph_model, scene)
                for graph_entity in graph_data.get("entities", []) or []:
                    if graph_entity.get("entity_id"):
                        continue
                    unified_entity = grounded_graph.entity(graph_entity.get("local_ref", ""))
                    if unified_entity and unified_entity.entity_id:
                        graph_entity["entity_id"] = unified_entity.entity_id
                for role, decision in (joint_decisions or {}).items():
                    parsed_result.grounding_decisions.append({
                        "role": role,
                        "selected_entity_id": decision.selected_entity_id,
                        "candidate_ids": list(decision.candidate_ids),
                        "evidence": list(decision.evidence),
                        "margin": decision.margin,
                        "decision": decision.decision,
                        "engine": "JointGroundingSolver",
                    })
            except Exception as exc:
                parsed_result.notes.append(f"joint_grounding_audit_error:{type(exc).__name__}")
        if parsed_result.theme is not None and "theme" in parsed_result.unmet_roles:
            parsed_result.unmet_roles.remove("theme")
        if parsed_result.theme is not None:
            parsed_result.grounding_confidence = max(parsed_result.grounding_confidence, 0.85)
    from robot_intent_agent.semantic_reasoner.ambiguity import classify_ambiguities
    ambiguity_report = classify_ambiguities(instruction, parsed_result, scene=scene)
    if ambiguity_report:
        parsed_result.notes.extend(
            f"ambiguity:{item['type']}:{item['strategy']}" for item in ambiguity_report
        )
        parsed_result.parse_confidence = min(parsed_result.parse_confidence, 0.70)
        parsed_result.ambiguity_resolution = list(ambiguity_report)
    if parsed_result.semantic_task_graph:
        parsed_result.execution_contract["entity_ids_verified"] = all(
            not item.get("entity_id") or (scene is not None and any(
                getattr(obj, "id", None) == item.get("entity_id")
                for obj in getattr(scene, "objects", []) or []
            )) for item in parsed_result.semantic_task_graph.get("entities", [])
        )
    return parsed_result


def load_parsed_task_from_bt(
    instruction: str,
    bt_metadata: Dict[str, Any],
    scene: Any = None,
) -> "ParsedTask":
    """Load ParsedTask from BT metadata.

    Priority:
      1. LLM-provided parsed_task (semantic_frame_version >= 1.0)
         → Re-ground all entity references against scene using GroundingEngine
      2. RuleEngine re-parse (fallback)

    CRITICAL INVARIANT (Phase 4):
        When LLM parsed_task is available, GroundingEngine is ALWAYS run
        to re-ground entity references against the scene. The LLM provides
        semantic descriptors (mention, category, color, etc.); GroundingEngine
        is the sole authority for assigning entity_id.

    Fallback is explicitly tracked in bt_metadata['engine_trace'].
    """
    # SemanticCompiler owns the final graph.  This branch is deliberately
    # before every compatibility parser call: downstream consumers must use
    # the graph projection and must never re-interpret the raw instruction.
    semantic_graph_data = bt_metadata.get("semantic_task_graph")
    if isinstance(semantic_graph_data, dict) and (
        bt_metadata.get("semantic_authority") == "SemanticCompiler"
        or bt_metadata.get("compiler") == "SemanticCompiler"
    ):
        try:
            from robot_intent_agent.schemas.semantic_task_graph import SemanticTaskGraph
            from robot_intent_agent.semantic_compiler import parsed_task_from_graph
            graph = SemanticTaskGraph.model_validate(semantic_graph_data)
            projected = parsed_task_from_graph(
                graph,
                instruction,
                scene=scene,
                grounding_decisions=bt_metadata.get("grounding_decisions") or [],
                fusion_trace=bt_metadata.get("fusion_trace") or [],
            )
            projected.notes.append("source:semantic_compiler_graph_projection")
            return projected
        except Exception as exc:
            # A graph-authored plan cannot silently fall back to a second
            # semantic interpretation.  Surface a clear failure to the final
            # validator instead of re-parsing the instruction.
            raise ValueError(f"semantic compiler graph projection failed: {exc}") from exc

    raw_parsed = bt_metadata.get("parsed_task")
    # Always compute the deterministic interpretation as the safety baseline.
    # LLM output is a semantic supplement, never a replacement for fields
    # already evidenced by the original instruction.
    rule_task = parse_task_semantics(instruction, scene=scene)
    engine_trace = bt_metadata.get("engine_trace", {})
    # New semantic-candidate metadata is read as an intermediate graph only;
    # it does not override the compatibility task or any grounded ID.
    if isinstance(bt_metadata.get("semantic_task_graph"), dict):
        rule_task.semantic_task_graph = bt_metadata["semantic_task_graph"]
    if isinstance(bt_metadata.get("fusion_trace"), list):
        rule_task.fusion_trace = list(bt_metadata["fusion_trace"])

    # Hybrid negative-gain protection: the rule baseline is already the
    # validated result, so do not re-enter it through the LLM fusion boundary.
    if isinstance(engine_trace, dict) and engine_trace.get("llm_fusion_rejected"):
        rule_task.notes.append("llm_rejected:negative_gain_protection")
        rule_task.notes.extend(
            f"llm_rejection:{reason}"
            for reason in (engine_trace.get("llm_rejection_reasons") or [])
        )
        return rule_task

    if isinstance(raw_parsed, dict):
        try:
            pt = ParsedTask.model_validate(raw_parsed)
            # Mark source
            if pt.notes is None:
                pt.notes = []
            pt.notes.append("source:llm_parsed_task")
            # Ensure engine_trace reflects LLM usage
            if engine_trace:
                pt.notes.append(
                    f"engine:{engine_trace.get('actual_engine', 'unknown')}"
                    f"_model:{engine_trace.get('model_name', 'unknown')}"
                )

            # ── Phase 4: Re-ground all entity references against scene ──
            # LLM provides semantic descriptors; GroundingEngine assigns entity_id.
            # This ensures the invariant that DeepSeek never independently
            # decides the final object_id.
            # RuleEngine metadata may also carry a parsed_task.  Only the
            # LLM path needs the semantic-to-scene re-grounding boundary;
            # re-grounding a rule result would discard its role-local IDs.
            llm_frame = bt_metadata.get("semantic_frame_version") == "1.0"
            llm_trace = any("deepseek" in str(engine_trace.get(k, "")).lower()
                            for k in ("actual_engine", "requested_engine", "planner"))
            if scene is None or not (llm_frame or llm_trace):
                # Rule planner metadata already contains role-local grounding;
                # never treat it as an LLM payload and clear its IDs.
                return pt
            raw_llm_ids = {
                role: getattr(getattr(pt, role, None), "entity_id", None)
                for role in ("theme", "source", "destination", "recipient", "support_surface")
            }
            merged = merge_parsed_tasks(rule_task, pt, instruction)
            for role, raw_id in raw_llm_ids.items():
                if raw_id:
                    merged.notes.append(f"cleared_llm_entity_id:{role}={raw_id}")
            # Fusion may replace a role with an LLM semantic reference;
            # perform the single authoritative grounding pass afterwards.
            merged = _reground_llm_parsed_task(merged, instruction, scene)
            if isinstance(engine_trace, dict):
                engine_trace["llm_fusion"] = {
                    "baseline": "rule_engine",
                    "accepted_fields": _llm_delta_fields(rule_task, pt),
                    "protected_fields": [
                        "entity_id", "user_constraints", "obstacle",
                        "prohibitions", "execution_allowed", "plan_status",
                    ],
                    "final_authority": "deterministic_grounding_and_validation",
                }
                bt_metadata = bt_metadata if isinstance(bt_metadata, dict) else {}
                bt_metadata["engine_trace"] = engine_trace
            return merged
        except Exception as e:
            # LLM provided parsed_task but schema invalid → record and fall back
            if engine_trace:
                engine_trace["fallback_used"] = True
                engine_trace["fallback_reason"] = f"LLM parsed_task validation failed: {e}"
                engine_trace["llm_call_succeeded"] = False
                bt_metadata["engine_trace"] = engine_trace
            # Fall through to RuleEngine

    # Fallback: RuleEngine
    pt = rule_task
    if pt.notes is None:
        pt.notes = []
    pt.notes.append("source:rule_engine_fallback")
    return pt


def merge_parsed_tasks(rule_task: "ParsedTask", llm_task: "ParsedTask", instruction: str) -> "ParsedTask":
    """Field-wise semantic fusion with deterministic safety precedence.

    Rule extraction is authoritative for explicit constraints, negations,
    steps, and source text. LLM may add roles/descriptors when they are
    grounded in the same instruction, but it cannot erase rule evidence.
    """
    merged = deepcopy(rule_task)
    cleared_ids: List[str] = []
    # Preserve an explicit deterministic action.  The LLM may enrich a rule
    # frame with branches and roles, but it must not downgrade a clear
    # delivery verb such as “拿过来/取过来” from FETCH to GRASP.
    # If rules cannot classify the action, accept a validated LLM action.
    if rule_task.action == TaskActionKind.CUSTOM and llm_task.action != TaskActionKind.CUSTOM:
        merged.action = llm_task.action
    for role in ("theme", "source", "destination", "recipient", "support_surface"):
        candidate = getattr(llm_task, role, None)
        current = getattr(merged, role, None)
        # A scene-sourced role already has a deterministic entity binding.
        # LLM descriptors may be incomplete (for example “support surface”)
        # and must not overwrite a verified destination/table ID.
        if current is not None and current.mention:
            # The deterministic role span is the final baseline.  LLM may
            # fill an absent role, but it cannot replace an already identified
            # role with a different mention and thereby change grounding.
            if candidate is not None and candidate.mention != current.mention:
                merged.notes.append(f"protected_rule_role:{role}")
            if candidate is not None and candidate.entity_id and candidate.entity_id != current.entity_id:
                merged.notes.append(f"cleared_llm_entity_id:{role}={candidate.entity_id}")
            continue
        if candidate is not None and candidate.mention and (candidate.mention in instruction or not current):
            # Remove any untrusted ID before the grounding boundary.
            candidate = deepcopy(candidate)
            if candidate.entity_id is not None:
                cleared_ids.append(f"reground:cleared_llm_entity_id:{role}={candidate.entity_id}")
            candidate.entity_id = None
            candidate.source = "nl"
            setattr(merged, role, candidate)
    # Union by stable semantic content; rule constraints/obstacles are never
    # deleted because the LLM omitted them.
    merged.user_constraints = _union_models(rule_task.user_constraints, llm_task.user_constraints, "parameter")
    merged.obstacle = _union_models(rule_task.obstacle, llm_task.obstacle, "mention")
    # Merge structured records by identity instead of taking the first record.
    # This preserves rule-detected safety evidence while allowing the LLM to
    # fill a missing branch subject/action in the same condition or step.
    merged.steps = _merge_structured_records(rule_task.steps, llm_task.steps, "step_index")
    merged.conditions = _merge_structured_records(rule_task.conditions, llm_task.conditions, "condition_id")
    merged.prohibitions = _merge_structured_records(rule_task.prohibitions, llm_task.prohibitions, "prohibition_id")
    merged.raw_mentions = list(dict.fromkeys((rule_task.raw_mentions or []) + (llm_task.raw_mentions or [])))
    merged.unmet_roles = list(dict.fromkeys((rule_task.unmet_roles or []) + (llm_task.unmet_roles or [])))
    if not merged.manner and llm_task.manner:
        merged.manner = llm_task.manner
    if not merged.clarification and getattr(llm_task, "clarification", None):
        merged.clarification = llm_task.clarification
    merged.notes = list(dict.fromkeys((rule_task.notes or []) + (llm_task.notes or []) + ["fusion:rule_baseline+llm_fields"]))
    merged.notes.extend(item for item in cleared_ids if item not in merged.notes)
    merged.parse_confidence = max(rule_task.parse_confidence, llm_task.parse_confidence)
    merged.constraint_confidence = max(rule_task.constraint_confidence, llm_task.constraint_confidence)
    return merged


def _union_models(left: List[Any], right: List[Any], key: str) -> List[Any]:
    result: List[Any] = []
    seen = set()
    for item in list(left or []) + list(right or []):
        if hasattr(item, "stable_key"):
            marker = item.stable_key()
        else:
            value = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
            marker = repr(value) if value is not None else repr(item.model_dump() if hasattr(item, "model_dump") else item)
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def _merge_structured_records(left: List[Any], right: List[Any], key: str) -> List[Any]:
    """Merge records by identity, filling only absent rule fields from LLM.

    A non-empty rule value is authoritative.  Empty values in the rule record
    may be enriched by a validated LLM record, which is what conditional
    branches and branch-specific targets require.
    """
    result = [deepcopy(item) for item in (left or [])]
    positions = {}
    for index, item in enumerate(result):
        marker = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
        if marker is not None:
            positions[marker] = index
    for incoming in right or []:
        marker = incoming.get(key) if isinstance(incoming, dict) else getattr(incoming, key, None)
        if marker not in positions:
            result.append(deepcopy(incoming))
            if marker is not None:
                positions[marker] = len(result) - 1
            continue
        current = result[positions[marker]]
        if hasattr(current, "model_dump"):
            current_data = current.model_dump()
            incoming_data = incoming.model_dump() if hasattr(incoming, "model_dump") else dict(incoming)
            for field, value in incoming_data.items():
                if field not in current_data or current_data[field] in (None, "", [], {}):
                    current_data[field] = value
            result[positions[marker]] = type(current).model_validate(current_data)
        elif isinstance(current, dict):
            incoming_data = incoming if isinstance(incoming, dict) else incoming.model_dump()
            for field, value in incoming_data.items():
                if field not in current or current[field] in (None, "", [], {}):
                    current[field] = deepcopy(value)
    return result


def _llm_delta_fields(rule_task: "ParsedTask", llm_task: "ParsedTask") -> List[str]:
    """Report semantic fields for which the validated LLM supplied evidence.

    This is audit metadata only; it never controls execution.  It lets the
    evaluator distinguish a real LLM contribution from a complete fallback.
    """
    fields: List[str] = []
    for name in ("action", "theme", "source", "destination", "recipient",
                 "support_surface", "manner", "user_constraints", "obstacle",
                 "conditions", "steps", "prohibitions", "clarification"):
        value = getattr(llm_task, name, None)
        if value not in (None, "", [], {}):
            rule_value = getattr(rule_task, name, None)
            if value != rule_value or name in {"conditions", "steps", "prohibitions"}:
                fields.append(name)
    return fields


def _reground_llm_parsed_task(
    pt: "ParsedTask",
    instruction: str,
    scene: Any,
) -> "ParsedTask":
    """Re-ground LLM-provided entity references against the scene.

    The LLM provides mention text and semantic descriptors.
    GroundingEngine scores each scene object and assigns entity_id.

    Only entity_id is overwritten; all other LLM semantic understanding
    (action, manner, user_constraints, conditions, prohibitions) is preserved.
    """
    engine = GroundingEngine()
    color_hint = engine.config.derive_color_hint(instruction)

    def _theme_query() -> str:
        """Recover the original clause that describes the theme.

        LLM frames often keep only a noun (e.g. ``bottle``), which loses the
        spatial/color/size evidence needed by deterministic grounding.  Use
        the source clause as the query while keeping entity IDs exclusively
        under GroundingEngine control.
        """
        q = instruction
        for pattern in (
            r"^(.+?)[，,]\s*(?:别碰|不要碰|避开|不要接触).+$",
            r"^不要碰.+?[，,]\s*(?:把)?(.+)$",
            r"^(?:如果|如果看到)(.+?)(?:就|则).+?[，,；;]\s*(?:否则|否则拿).+$",
        ):
            m = re.search(pattern, instruction)
            if m:
                q = m.group(1).strip()
                break
        place = re.search(r"(?:把|将)\s*(.+?)\s*(?:放到|放在|放入|放进)", q)
        if place:
            q = place.group(1).strip()
        return q

    theme_query = _theme_query()

    def _query(entity: Optional[SemanticEntityRef], role: str = "theme") -> str:
        mention = (getattr(entity, "mention", "") or "").strip() if entity else ""
        if role == "theme":
            return theme_query if theme_query else (mention or instruction)
        return mention or instruction

    def _clear_untrusted_id(entity: Optional[SemanticEntityRef], role: str) -> None:
        if entity is None or (role == "recipient" and entity.entity_id == "user"):
            return
        if entity.entity_id is not None and entity.source == "scene":
            # This ID came from the deterministic rule baseline, not from the
            # LLM payload.  Preserve it across semantic fusion.
            return
        if entity.entity_id is not None:
            pt.notes.append(f"reground:cleared_llm_entity_id:{role}={entity.entity_id}")
            entity.entity_id = None
            entity.source = "nl"

    for role_name in ("theme", "source", "destination", "support_surface", "recipient"):
        _clear_untrusted_id(getattr(pt, role_name, None), role_name)
    for obs in pt.obstacle or []:
        _clear_untrusted_id(obs, "obstacle")

    # ── Re-ground theme ──
    if pt.theme is None and len(getattr(scene, "objects", []) or []) == 1:
        if re.search(r"(?:那个|这个|那玩意|这玩意|那东西|这东西|that thing|the thing)", instruction, re.I):
            pt.theme = SemanticEntityRef.from_scene_object(scene.objects[0], role="theme")
            pt.theme.grounding_confidence = 0.35
            pt.theme.match_evidence = ["demonstrative_fallback:single_object +0.35"]
            pt.notes.append(f"reground:theme→{scene.objects[0].id} (single-object demonstrative)")
    if pt.theme and pt.theme.entity_id and pt.theme.source == "scene":
        pt.notes.append(f"reground:theme preserved deterministic binding→{pt.theme.entity_id}")
    elif pt.theme:
        theme_hint = engine.config.derive_color_hint(theme_query) or color_hint
        theme_result = engine.ground(_query(pt.theme, "theme"), scene, role="theme", color_hint=theme_hint)
        if theme_result.selected is not None:
            pt.theme.entity_id = theme_result.selected.entity_ref.entity_id
            pt.theme.grounding_confidence = min(theme_result.selected.total_score, 1.0)
            pt.theme.source = "scene"
            pt.theme.match_evidence = list(theme_result.selected.evidence)
            pt.notes.append(f"reground:theme→{pt.theme.entity_id} (score={theme_result.selected.total_score:.2f})")
        elif theme_result.needs_clarification:
            pt.notes.append(f"reground:theme AMBIGUOUS — {theme_result.clarification.question if theme_result.clarification else 'no candidates'}")
            if pt.theme.entity_id is not None:
                # LLM hallucinated an entity_id — clear it
                pt.notes.append(f"reground:cleared hallucinated theme entity_id={pt.theme.entity_id}")
                pt.theme.entity_id = None

        # A demonstrative with exactly one visible object is deterministic;
        # do not ask for clarification merely because its noun is omitted.
        if pt.theme.entity_id is None and len(getattr(scene, "objects", []) or []) == 1:
            if re.search(r"(?:那个|这个|那玩意|这玩意|那东西|这东西|that thing|the thing)", instruction, re.I):
                only = scene.objects[0]
                pt.theme.entity_id = only.id
                pt.theme.source = "scene"
                pt.theme.grounding_confidence = 0.35
                pt.theme.match_evidence = ["demonstrative_fallback:single_object +0.35"]
                pt.notes.append(f"reground:theme→{only.id} (single-object demonstrative)")

    # Exclude the main theme before grounding branch-specific targets.
    exclude_ids = {pt.theme.entity_id} if pt.theme and pt.theme.entity_id else set()

    # Conditional branches may carry their own semantic target (e.g. the
    # red bottle on true and the blue box on false).  Ground each branch
    # independently; never copy the main theme ID into both branches.
    for condition in pt.conditions or []:
        if not isinstance(condition, dict):
            continue
        for branch_key in ("subject", "on_true_subject", "on_false_subject"):
            branch = condition.get(branch_key)
            if not isinstance(branch, dict) or branch.get("entity_id"):
                continue
            mention = str(branch.get("mention") or "").strip()
            attrs = branch.get("attributes") or {}
            descriptors = [mention]
            for key in ("color", "material", "size", "side", "height_relation", "distance_relation"):
                value = attrs.get(key)
                if value:
                    descriptors.append(str(value))
            query = " ".join(descriptors)
            result = engine.ground(query or instruction, scene, role="theme", exclude_ids=exclude_ids)
            if result.selected is not None:
                branch["entity_id"] = result.selected.entity_ref.entity_id
                branch["grounding_confidence"] = min(result.selected.total_score, 1.0)
                branch["source"] = "scene"
                branch["match_evidence"] = list(result.selected.evidence)
                exclude_ids.add(result.selected.entity_ref.entity_id)
                pt.notes.append(
                    f"reground:condition:{condition.get('condition_id', 'unknown')}:{branch_key}"
                    f"→{result.selected.entity_ref.entity_id}"
                )

    # ── Re-ground destination ──
    if pt.destination and pt.destination.entity_id and pt.destination.source == "scene":
        exclude_ids.add(pt.destination.entity_id)
        pt.notes.append(f"reground:destination preserved deterministic binding→{pt.destination.entity_id}")
    elif pt.destination:
        dest_result = engine.ground(_query(pt.destination), scene, role="destination",
                                     exclude_ids=exclude_ids, color_hint=color_hint)
        if dest_result.selected is not None:
            pt.destination.entity_id = dest_result.selected.entity_ref.entity_id
            pt.destination.grounding_confidence = min(dest_result.selected.total_score, 1.0)
            pt.destination.source = "scene"
            pt.destination.match_evidence = list(dest_result.selected.evidence)
            exclude_ids.add(pt.destination.entity_id)
            pt.notes.append(f"reground:destination→{pt.destination.entity_id}")

    # ── Re-ground support_surface ──
    if pt.support_surface and pt.support_surface.entity_id and pt.support_surface.source == "scene":
        exclude_ids.add(pt.support_surface.entity_id)
        pt.notes.append(f"reground:support_surface preserved deterministic binding→{pt.support_surface.entity_id}")
    elif pt.support_surface:
        support_exclude = set(exclude_ids)
        if pt.destination and pt.destination.entity_id:
            support_exclude.discard(pt.destination.entity_id)
        ss_result = engine.ground(_query(pt.support_surface), scene, role="support_surface",
                                   exclude_ids=support_exclude)
        if ss_result.selected is not None:
            pt.support_surface.entity_id = ss_result.selected.entity_ref.entity_id
            pt.support_surface.grounding_confidence = min(ss_result.selected.total_score, 1.0)
            pt.support_surface.source = "scene"
            pt.support_surface.match_evidence = list(ss_result.selected.evidence)
            exclude_ids.add(pt.support_surface.entity_id)
            pt.notes.append(f"reground:support_surface→{pt.support_surface.entity_id}")

    # ── Re-ground obstacles ──
    for role_name in ("source", "recipient"):
        entity = getattr(pt, role_name, None)
        if not entity or (role_name == "recipient" and entity.entity_id == "user"):
            continue
        result = engine.ground(_query(entity), scene, role=role_name, exclude_ids=exclude_ids)
        if result.selected is not None:
            entity.entity_id = result.selected.entity_ref.entity_id
            entity.grounding_confidence = min(result.selected.total_score, 1.0)
            entity.source = "scene"
            entity.match_evidence = list(result.selected.evidence)
            exclude_ids.add(entity.entity_id)
            pt.notes.append(f"reground:{role_name}->{entity.entity_id}")

    if pt.obstacle:
        re_grounded_obstacles = []
        for obs in pt.obstacle:
            if obs.entity_id is not None:
                # Already has an ID — verify it exists in scene
                if scene and hasattr(scene, 'find_object'):
                    obj = scene.find_object(obs.entity_id)
                    if obj is None and obs.entity_id != "user":
                        pt.notes.append(f"reground:obstacle {obs.entity_id} not found in scene, clearing")
                        obs.entity_id = None
            if obs.entity_id is None:
                # Try to ground the obstacle mention against scene
                obs_result = engine.ground(_query(obs), scene, role="obstacle",
                                           exclude_ids=exclude_ids)
                if obs_result.candidates:
                    # Find best matching candidate for this obstacle mention
                    best = None
                    for c in obs_result.candidates:
                        if c.total_score >= engine.config.min_accept_score:
                            if obs.mention and obs.mention in (c.entity_ref.mention or ""):
                                best = c
                                break
                            # Fallback: match by category
                            if obs.specific_class and obs.specific_class == c.entity_ref.specific_class:
                                best = c
                                break
                    # A ranked candidate is not enough for a prohibition:
                    # binding an arbitrary scene object as an obstacle is
                    # worse than retaining an unresolved prohibition and
                    # failing closed.  GroundingEngine already applies the
                    # shared evidence gate; do not bypass it here.
                    if best and best.total_score >= engine.config.min_accept_score:
                        obs.entity_id = best.entity_ref.entity_id
                        obs.grounding_confidence = min(best.total_score, 1.0)
                        obs.source = "scene"
                        obs.match_evidence = list(best.evidence)
                        exclude_ids.add(obs.entity_id)
                        pt.notes.append(f"reground:obstacle {obs.mention}→{obs.entity_id}")
            re_grounded_obstacles.append(obs)
        pt.obstacle = re_grounded_obstacles

    # Update overall grounding confidence
    if scene and pt.theme and pt.theme.entity_id:
        pt.grounding_confidence = max(pt.grounding_confidence, 0.9)
        pt.notes.append("reground:entities reassigned by GroundingEngine")

    return pt


def build_grounded_task(parsed_task: ParsedTask, scene: Any = None) -> GroundedTask:
    grounded_roles: Dict[str, Optional[SemanticEntityRef]] = {
        "theme": parsed_task.theme,
        "source": parsed_task.source,
        "destination": parsed_task.destination,
        "recipient": parsed_task.recipient,
        "support_surface": parsed_task.support_surface,
    }
    # Critical roles that MUST be present for each action type
    critical_roles = set() if parsed_task.action == TaskActionKind.WAIT else {"theme"}
    if parsed_task.action in (TaskActionKind.FETCH, TaskActionKind.TRANSFER):
        critical_roles.add("destination")
    if parsed_task.action == TaskActionKind.HANDOVER:
        critical_roles.add("recipient")
    if parsed_task.action == TaskActionKind.PLACE:
        critical_roles.add("support_surface")

    def _role_is_grounded(value: Optional[SemanticEntityRef]) -> bool:
        # A role object without a scene-owned entity_id is only a language
        # mention, not an executable binding.  This matters especially for
        # WAIT descriptors: an equal pair of objects must remain unresolved.
        return value is not None and bool(value.entity_id)

    missing_roles = [name for name, value in grounded_roles.items()
                     if name in critical_roles and not _role_is_grounded(value)]
    if parsed_task.action == TaskActionKind.FETCH and parsed_task.destination is None:
        if "destination" in missing_roles:
            missing_roles.remove("destination")
        if "delivery_pose_or_fetch_zone" not in missing_roles:
            missing_roles.append("delivery_pose_or_fetch_zone")
    required_clarifications = []
    if parsed_task.action == TaskActionKind.WAIT:
        if not parsed_task.conditions:
            missing_roles.append("condition")
        else:
            # WAIT is itself the monitoring operation.  The condition is not
            # required to be true at compile time: WaitUntil receives the
            # condition and observes it at execution time.  Requiring a
            # second confirmation here incorrectly turns every valid
            # ``wait until ...`` request into NEEDS_CLARIFICATION.
            pass

    # A WAIT command may legitimately have no manipulated object, but a
    # descriptive target clause must not be silently discarded.  This is the
    # important distinction between a target-free wait ("wait until the
    # scene is stable") and an unresolved wait ("wait until the larger object
    # in the rear is stable").  The latter must ask for clarification when
    # grounding did not bind a theme entity.
    if parsed_task.action == TaskActionKind.WAIT and not _role_is_grounded(parsed_task.theme):
        wait_text = parsed_task.instruction or ""
        has_target_description = bool(re.search(
            r"(?:\u5728\u73b0\u573a\u76ee\u6807\u4e2d|\u4f4d\u4e8e|\u64cd\u4f5c\u533a\u524d\u65b9|\u4e2d\u95f4\u504f\u540e|\u504f\u5c0f|\u504f\u5927|\u5c3a\u5bf8\u8f83\u5927|\u4e2d\u7b49\u5927\u5c0f|\u76ee\u6807(?:\u662f|\u4e3a))",
            wait_text,
        ))
        if has_target_description:
            if "theme" not in missing_roles:
                missing_roles.append("theme")
            if not any("WAIT" in str(item) or "target" in str(item).lower() for item in required_clarifications):
                required_clarifications.append("WAIT target description cannot be grounded to a unique scene entity")

    # Detect fallback entities.
    scene_entity_ids = {getattr(o, "id", "") for o in getattr(scene, "objects", []) or []} if scene else set()

    def _is_effective_fallback(entity: Optional[SemanticEntityRef], role: str) -> bool:
        if entity is None:
            return False
        if entity.source != "scene":
            return True
        if role == "recipient" and entity.entity_id in {"user", "operator"}:
            return True
        if role == "support_surface" and entity.entity_id not in scene_entity_ids:
            return True
        return False

    def _scene_has_handover_endpoint() -> bool:
        """Return whether perception exposes a safe human handover endpoint.

        ``operator`` is a symbolic role, not a fabricated scene object.  It
        is nevertheless executable when the observation contains a reachable
        recipient endpoint (or an explicit handover-zone affordance).  If no
        such evidence exists, the safety gate must keep the task pending.
        """
        for obj in getattr(scene, "objects", []) or []:
            attrs = getattr(obj, "attributes", {}) or {}
            upstream = attrs.get("_upstream_affordances", []) or []
            if isinstance(upstream, str):
                upstream = [upstream]
            affordances = getattr(obj, "affordances", []) or []
            affordance_values = {
                str(item.value if hasattr(item, "value") else item).lower()
                for item in affordances
            }
            upstream_values = {str(item).lower() for item in upstream}
            category = str(getattr(obj, "specific_class", "") or "").lower()
            if category in {"operator", "human", "person", "user"}:
                return True
            if ({"recipient", "reachable"} <= upstream_values or
                    {"handover_zone", "reachable"} <= upstream_values or
                    "handover_zone" in upstream_values or
                    {"recipient", "reachable"} <= affordance_values or
                    "handover_zone" in affordance_values):
                return True
        return False

    # For HANDOVER/FETCH: recipient identified as "user" → the real missing
    # item is the execution pose, not the recipient identity.
    # FETCH: delivery_pose_or_fetch_zone
    # HANDOVER: recipient_pose_or_handover_zone (recipient identified, no pose)
    # TRANSFER is destination-based and does not fabricate a recipient.
    pose_missing = False
    for role in ("recipient", "support_surface"):
        entity = grounded_roles.get(role)
        if entity is not None and role not in missing_roles and _is_effective_fallback(entity, role):
            if role == "recipient" and parsed_task.action in (TaskActionKind.FETCH, TaskActionKind.HANDOVER):
                if (parsed_task.action == TaskActionKind.HANDOVER and
                        entity.entity_id == "operator" and
                        _scene_has_handover_endpoint()):
                    # The observation supplies a reachable operator endpoint;
                    # the handover-zone skill provides the pose at runtime.
                    continue
                pose_missing = True
                if parsed_task.action == TaskActionKind.HANDOVER:
                    missing_roles.append("recipient_pose_or_handover_zone")
                else:
                    missing_roles.append("delivery_pose_or_fetch_zone")
            else:
                missing_roles.append(role)

    if pose_missing:
        if parsed_task.action == TaskActionKind.HANDOVER:
            required_clarifications.append("已识别接收者，但缺少可执行的用户位姿或安全交接区域")
        else:
            required_clarifications.append("缺少可执行的交付位姿或取物区域")
    if "support_surface" in missing_roles and parsed_task.action == TaskActionKind.PLACE:
        required_clarifications.append("缺少放置支撑面或放置区域")
    if "theme" in missing_roles:
        required_clarifications.append("缺少被操作物体")

    # Unresolved scene mentions are not valid grounded roles, even when a
    # category-only SemanticEntityRef exists in the graph.
    if parsed_task.theme is not None and not _role_is_grounded(parsed_task.theme) and "theme" not in missing_roles:
        missing_roles.append("theme")
        required_clarifications.append("目标实体尚未绑定到唯一感知对象")

    grounding_confidence = parsed_task.grounding_confidence
    if scene is not None and parsed_task.theme and parsed_task.theme.entity_id:
        grounding_confidence = max(grounding_confidence, 0.9)

    return GroundedTask(
        parsed_task=parsed_task,
        grounded_roles=grounded_roles,
        missing_roles=missing_roles,
        required_clarifications=required_clarifications,
        grounding_confidence=grounding_confidence,
    )


def make_plan_hash(payload: Dict[str, Any]) -> str:
    serialized = repr(sorted(payload.items())).encode("utf-8")
    return hashlib.sha1(serialized).hexdigest()[:16]
