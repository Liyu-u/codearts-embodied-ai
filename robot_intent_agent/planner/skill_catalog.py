"""
Robot Skill Catalog — 机器人通用技能库

定义 6 个原子技能:
    Reach    — 移动到目标上方安全位置
    Grasp    — 标准抓取
    MoveTo   — 移动物体到目标位置
    Release  — 释放物体
    Avoid    — 规避障碍物
    Inspect  — 视觉确认

每个 Skill 包含:
    name         : 技能名称
    description  : 可读描述
    preconditions: 前置条件列表
    effects      : 执行后效果列表
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ============================================================
# Skill 数据结构
# ============================================================

@dataclass
class SkillDefinition:
    """单个技能定义"""
    name: str
    description: str
    preconditions: List[str] = field(default_factory=list)
    effects: List[str] = field(default_factory=list)
    params_schema: Dict[str, Any] = field(default_factory=dict)
    safety_notes: List[str] = field(default_factory=list)

    def requires_target(self) -> bool:
        """是否需要目标物体"""
        return "{target}" in self.description

    def resolve_params(
        self,
        target: Optional[str] = None,
        destination: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """根据上下文填充参数"""
        params: Dict[str, Any] = {}
        if target:
            params["target"] = target
        if destination:
            params["destination"] = destination
        if extra:
            params.update(extra)
        return params


# ============================================================
# 技能库
# ============================================================

class SkillCatalog:
    """
    机器人通用技能库 (6 个原子技能)。

    用法:
        catalog = SkillCatalog()
        skill = catalog.get("Reach")
        print(skill.preconditions)  # ["gripper_is_ready", "target_in_view"]
    """

    _SKILLS: Dict[str, SkillDefinition] = {
        # ── Reach ──
        "Reach": SkillDefinition(
            name="Reach",
            description="Move end-effector to safe height above {target}",
            preconditions=[
                "gripper_is_ready",
                "target_in_view",
                "path_clear_to_target",
                "z >= MIN_Z_HEIGHT",
            ],
            effects=[
                "end_effector_at_target_safe_height",
                "ready_for_grasp_or_inspect",
            ],
            params_schema={
                "target": "str — 目标物体名称",
                "safe_z_offset": "float = 0.10 — 安全高度偏移 (m)",
            },
            safety_notes=["Ensure Z >= 0.02m before lateral move"],
        ),

        # ── Grasp ──
        "Grasp": SkillDefinition(
            name="Grasp",
            description="Close gripper around {target} with specified force",
            preconditions=[
                "end_effector_at_target_height",
                "gripper_open",
                "target_graspable",
            ],
            effects=[
                "target_in_hand",
                "gripper_closed",
            ],
            params_schema={
                "target": "str — 目标物体名称",
                "force_n": "float = 5.0 — 抓取力 (N), 范围 (0, 10]",
                "approach_axis": "str = 'z' — 接近方向",
            },
            safety_notes=["force must be <= 10.0N", "Verify grasp after close"],
        ),

        # ── MoveTo ──
        "MoveTo": SkillDefinition(
            name="MoveTo",
            description="Move {target} (in hand) to {destination}",
            preconditions=[
                "target_in_hand",
                "destination_reachable",
                "path_clear_to_destination",
            ],
            effects=[
                "target_at_destination",
                "ready_for_release",
            ],
            params_schema={
                "target": "str — 物体名称",
                "destination": "str — 目标位置描述",
                "velocity_ms": "float = 0.15 — 移动速度 (m/s)",
            },
            safety_notes=["Lift to safe Z before lateral movement"],
        ),

        # ── Release ──
        "Release": SkillDefinition(
            name="Release",
            description="Open gripper to release {target} at current position",
            preconditions=[
                "target_in_hand",
                "at_destination_or_safe_position",
            ],
            effects=[
                "gripper_open",
                "target_released",
            ],
            params_schema={
                "target": "str — 物体名称",
                "open_width_m": "float = 0.08 — 张开宽度 (m)",
            },
            safety_notes=["Ensure object stable before release"],
        ),

        # ── Push ──
        "Push": SkillDefinition(
            name="Push",
            description="Push {target} in a straight line from current position",
            preconditions=[
                "end_effector_near_target",
                "target_pushable",
                "path_clear_in_push_direction",
            ],
            effects=[
                "target_moved",
                "end_effector_at_final_position",
            ],
            params_schema={
                "target": "str — 目标物体名称",
                "direction": "str = 'forward' — 推动方向",
                "distance_m": "float = 0.10 — 推动距离 (m)",
                "velocity_ms": "float = 0.10 — 推动速度 (m/s)",
            },
            safety_notes=["Ensure target is pushable (not fragile)", "Limit distance to workspace"],
        ),

        # ── Stack ──
        "Stack": SkillDefinition(
            name="Stack",
            description="Place {target} on top of {destination}",
            preconditions=[
                "target_in_hand",
                "destination_object_stable",
                "destination_surface_clear",
            ],
            effects=[
                "target_on_destination",
                "stack_complete",
            ],
            params_schema={
                "target": "str — 被堆叠物体名称",
                "destination": "str — 底部物体名称或位置",
                "alignment_tolerance_m": "float = 0.005 — 对齐容差 (m)",
            },
            safety_notes=["Verify stack stability after placement"],
        ),

        # ── Pour ──
        "Pour": SkillDefinition(
            name="Pour",
            description="Pour contents of {target} into {destination}",
            preconditions=[
                "target_in_hand",
                "target_is_container",
                "destination_is_container",
                "destination_below_target",
            ],
            effects=[
                "contents_transferred",
                "target_empty",
            ],
            params_schema={
                "target": "str — 源容器名称",
                "destination": "str — 目标容器名称",
                "tilt_angle_deg": "float = 90.0 — 倾倒角度 (deg)",
                "duration_s": "float = 2.0 — 倾倒持续时间 (s)",
            },
            safety_notes=["Tilt slowly to avoid spillage", "Verify destination can hold contents"],
        ),

        # ── Avoid ──
        "Avoid": SkillDefinition(
            name="Avoid",
            description="Add waypoint to avoid collision with {target}",
            preconditions=[
                "obstacle_identified",
                "obstacle_position_known",
            ],
            effects=[
                "path_clear_of_obstacle",
                "safe_distance_maintained",
            ],
            params_schema={
                "target": "str — 需避开的障碍物名称",
                "min_distance_m": "float = 0.05 — 最小安全距离 (m)",
                "avoid_strategy": "str = 'go_around' — 绕行策略",
            },
            safety_notes=["Min distance >= 0.05m", "Re-plan if avoidance fails"],
        ),

        # ── Inspect ──
        "Inspect": SkillDefinition(
            name="Inspect",
            description="Visually confirm state of {target} (position/grasp/clearance)",
            preconditions=[
                "camera_available",
                "target_in_view",
            ],
            effects=[
                "target_state_confirmed",
                "readiness_verified",
            ],
            params_schema={
                "target": "str — 检查目标",
                "check_type": "str = 'position' — 检查类型: position|grasp|clearance",
            },
            safety_notes=["Used before critical actions for verification"],
        ),
    }

    @classmethod
    def get(cls, skill_name: str) -> SkillDefinition:
        """获取技能定义"""
        if skill_name not in cls._SKILLS:
            raise KeyError(
                f"Unknown skill '{skill_name}'. Available: {list(cls._SKILLS.keys())}"
            )
        return cls._SKILLS[skill_name]

    @classmethod
    def list_all(cls) -> List[str]:
        """列出所有技能名"""
        return list(cls._SKILLS.keys())

    @classmethod
    def get_primitive_skills(cls) -> List[str]:
        """获取基础操作技能 (不含 Inspect/Avoid)"""
        return ["Reach", "Grasp", "MoveTo", "Release"]

    @classmethod
    def get_safety_skills(cls) -> List[str]:
        """获取安全相关技能"""
        return ["Avoid", "Inspect"]
