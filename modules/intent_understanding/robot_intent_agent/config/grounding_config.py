"""
Grounding engine configuration — weights, thresholds, and spatial axis semantics.

All tunable parameters for the GroundingEngine live here. No weights are
hardcoded in the engine itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ══════════════════════════════════════════════════════════════
# Spatial axis semantics
# ══════════════════════════════════════════════════════════════

@dataclass
class SpatialConfig:
    """Defines which coordinate axes map to which spatial directions.

    From the robot's perspective looking at the workspace:
      - x: forward/backward depth
      - y: left/right lateral position
      - z: up/down
    """

    left_right_axis: str = "y"        # Axis for left/right comparison
    front_back_axis: str = "x"        # Axis for front/back comparison
    up_down_axis: str = "z"           # Axis for up/down comparison
    default_ordinal_axis: str = "y"   # Default sort axis for first/second/third

    # Direction polarity: when sorting by the axis in ascending order,
    # does index-0 correspond to "left" or "right"?
    left_is_lower: bool = True         # If True, lower y = more left
    front_is_higher: bool = True       # If True, higher x = more front

    # Ordinal direction: when sorting by default_ordinal_axis in ascending order,
    # does index-0 mean "first" (leftmost) or "last" (rightmost)?
    first_is_lowest: bool = True       # If True, first = lowest coordinate on ordinal axis

    def axis_for(self, direction: str) -> Optional[str]:
        """Return the axis name for a spatial direction."""
        if direction in ("left", "right", "leftmost", "rightmost"):
            return self.left_right_axis
        if direction in ("front", "back", "frontmost", "backmost"):
            return self.front_back_axis
        if direction in ("high", "low", "highest", "lowest"):
            return self.up_down_axis
        if direction in ("near", "far", "nearest", "farthest", "middle"):
            return self.default_ordinal_axis
        return None

    def sort_objects(
        self, objects: list, axis: str, ascending: bool = True
    ) -> list:
        """Sort objects by a coordinate axis."""
        def _coord(obj) -> float:
            pos = getattr(obj, "position", None)
            if pos is None:
                return 0.0
            return getattr(pos, axis, 0.0)

        return sorted(objects, key=_coord, reverse=not ascending)


# ══════════════════════════════════════════════════════════════
# Grounding configuration
# ══════════════════════════════════════════════════════════════

@dataclass
class GroundingConfig:
    """All tunable parameters for entity and role grounding.

    Weights control the contribution of each score component to the
    language_score and feasibility_score.  Thresholds control ambiguity
    detection and hard rejection.
    """

    # ── Language score weights ──────────────────────────────
    category_weight: float = 1.0
    color_weight: float = 1.0
    material_weight: float = 0.5
    size_weight: float = 0.8
    spatial_weight: float = 0.8
    ordinal_weight: float = 0.9
    motion_weight: float = 0.6
    exact_id_match_bonus: float = 2.0   # Added on top when an explicit ID is provided

    # ── Feasibility score weights ───────────────────────────
    role_affordance_weight: float = 1.0
    state_compatibility_weight: float = 0.5
    negative_constraint_weight: float = 1.5  # Penalty for conflicting with avoid

    # ── Score combination ───────────────────────────────────
    feasibility_blend: str = "multiply"   # "multiply" | "weighted_sum"
    feasibility_blend_weight: float = 0.35  # Only used for "weighted_sum"

    # ── Thresholds ──────────────────────────────────────────
    min_accept_score: float = 0.12        # Below this → reject entirely
    min_selection_margin: float = 0.15    # top1 - top2 below this → ambiguous
    hard_reject_score: float = 0.0        # Score at or below this → hard reject

    # ── Spatial config ──────────────────────────────────────
    spatial: SpatialConfig = field(default_factory=SpatialConfig)

    # ── Role feasibility requirements ───────────────────────
    # Each role lists required affordances. At least one must match.
    role_required_affordances: Dict[str, List[str]] = field(default_factory=lambda: {
        "theme": ["graspable", "movable"],
        "destination": ["support_surface", "container", "fixed", "surface"],
        "support_surface": ["support_surface", "fixed"],
        "recipient": [],   # Recipient is usually "user", not a scene object
        "obstacle": [],    # Obstacles just need to exist in scene
        "source": ["graspable", "movable"],
    })

    # ── Role forbidden affordances ──────────────────────────
    role_forbidden_affordances: Dict[str, List[str]] = field(default_factory=lambda: {
        "theme": ["fixed"],
        "support_surface": ["fragile", "movable", "graspable"],
    })

    # ── Feature flags ───────────────────────────────────────
    enable_clarification: bool = True
    enable_cross_object_spatial: bool = True    # Compare across peers for spatial
    enable_cross_object_size: bool = True       # Compare across peers for size
    enable_ordinal_resolution: bool = True      # First/second/third resolution
    enable_middle_resolution: bool = True       # "中间那个" resolution
    enable_role_invariants: bool = True         # theme≠dest, avoid≠theme checks

    # ── Cross-language category aliases ─────────────────────
    # (shared with the rest of the system — kept here for reference)
    cn_category_aliases: Dict[str, List[str]] = field(default_factory=lambda: {
        "cup": ["杯", "杯子", "水杯", "玻璃杯"],
        "bottle": ["瓶", "瓶子", "药瓶"],
        "medicine_bottle": ["药瓶", "药", "瓶"],
        "box": ["盒", "盒子", "箱"],
        "tray": ["托盘", "盘"],
        "table": ["桌", "桌子", "台"],
        "cabinet": ["柜子", "柜", "橱柜"],
        "book": ["书", "书本"],
        "glass_cup": ["玻璃杯", "杯", "玻璃"],
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
        "ball": ["球", "小球"],
        "block": ["方块", "积木", "块"],
        "cube": ["方块", "积木", "方"],
        "needle": ["针", "细针"],
        "device": ["设备", "装置", "仪器"],
        "rubber": ["橡胶"],
        "metal": ["金属", "铁"],
    })

    # ── Color map ───────────────────────────────────────────
    color_map: Dict[str, str] = field(default_factory=lambda: {
        "红色": "red", "蓝色": "blue", "绿色": "green", "黄色": "yellow",
        "白色": "white", "黑色": "black", "紫色": "purple", "透明": "transparent",
        "红": "red", "蓝": "blue", "绿": "green", "黄": "yellow",
        "白": "white", "黑": "black", "紫": "purple",
    })

    # ── Size cue map ────────────────────────────────────────
    size_cues: Dict[str, str] = field(default_factory=lambda: {
        "大": "large", "最大": "largest", "比较大": "large",
        "小": "small", "最小": "smallest", "比较小": "small",
    })

    # ── Spatial cue map ─────────────────────────────────────
    spatial_cues: Dict[str, str] = field(default_factory=lambda: {
        "左边": "left", "左侧": "left", "左": "left", "最左": "leftmost",
        "右边": "right", "右侧": "right", "右": "right", "最右": "rightmost",
        "前面": "front", "前方": "front", "前": "front",
        "后面": "back", "后方": "back", "后": "back",
        "近处": "near", "近": "near", "最近": "nearest",
        "远处": "far", "远": "far", "最远": "farthest",
        "高处": "high", "高": "high",
        "低处": "low", "低": "low",
        "中间": "middle", "中": "middle",
    })

    # ── Ordinal cue map ─────────────────────────────────────
    ordinal_cues: Dict[str, str] = field(default_factory=lambda: {
        "第一个": "first", "第1个": "first", "首个": "first",
        "第二个": "second", "第2个": "second",
        "第三个": "third", "第3个": "third",
        "第四个": "fourth", "第4个": "fourth",
        "第五个": "fifth", "第5个": "fifth",
        "最后一个": "last", "最后": "last",
    })

    # ── Motion cue map ──────────────────────────────────────
    motion_cues_moving: List[str] = field(default_factory=lambda: [
        "移动", "运动", "飘动", "晃动", "moving", "正在移动", "移动中的",
    ])
    motion_cues_static: List[str] = field(default_factory=lambda: [
        "静止", "不动", "static", "停",
    ])

    def derive_color_hint(self, instruction: str, exclude_colors: Optional[set] = None) -> Optional[str]:
        """Extract color hint from instruction text.

        Returns the LAST color mention (closest to theme description in
        typical Chinese instruction patterns where negation comes first).
        Optionally excludes colors that appear only in negated clauses.
        """
        exclude = exclude_colors or set()
        matches = []
        for cn_word, cn_color in sorted(self.color_map.items(), key=lambda x: -len(x[0])):
            if cn_word in instruction and cn_color not in exclude:
                idx = instruction.rfind(cn_word)  # Use rfind for last occurrence
                matches.append((idx, cn_color))
        if matches:
            matches.sort(key=lambda x: -x[0])  # Sort by position (last first)
            return matches[0][1]
        return None

    def derive_color_hints_all(self, instruction: str) -> List[Tuple[int, str, str]]:
        """Extract ALL color mentions with their positions.
        Returns list of (position, cn_word, en_color) sorted by position.
        """
        matches = []
        for cn_word, cn_color in self.color_map.items():
            pos = 0
            while True:
                idx = instruction.find(cn_word, pos)
                if idx == -1:
                    break
                matches.append((idx, cn_word, cn_color))
                pos = idx + 1
        matches.sort(key=lambda x: x[0])
        return matches

    def derive_size_hints(self, instruction: str) -> List[str]:
        """Extract size cues from instruction text."""
        found = []
        for cn_word, label in sorted(self.size_cues.items(), key=lambda x: -len(x[0])):
            if cn_word in instruction:
                found.append(label)
        return found

    def derive_spatial_hints(self, instruction: str) -> List[str]:
        """Extract spatial cues from instruction text."""
        found = []
        for cn_word, label in sorted(self.spatial_cues.items(), key=lambda x: -len(x[0])):
            if cn_word in instruction:
                found.append(label)
        return found

    def derive_ordinal_hints(self, instruction: str) -> List[str]:
        """Extract ordinal cues from instruction text."""
        found = []
        for cn_word, label in sorted(self.ordinal_cues.items(), key=lambda x: -len(x[0])):
            if cn_word in instruction:
                found.append(label)
        return found

    def derive_motion_hint(self, instruction: str) -> Optional[str]:
        """Extract motion state expectation from instruction."""
        for cue in self.motion_cues_moving:
            if cue in instruction:
                return "moving"
        for cue in self.motion_cues_static:
            if cue in instruction:
                return "static"
        return None


# Singleton default config
_default_config: Optional[GroundingConfig] = None


def get_grounding_config() -> GroundingConfig:
    """Get the global grounding configuration singleton."""
    global _default_config
    if _default_config is None:
        _default_config = GroundingConfig()
    return _default_config


def set_grounding_config(config: GroundingConfig) -> None:
    """Override the global grounding configuration (for testing)."""
    global _default_config
    _default_config = config
