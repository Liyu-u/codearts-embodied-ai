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
    success_conditions: List[str] = field(default_factory=list)
    failure_conditions: List[str] = field(default_factory=list)
    timeout_s: Optional[float] = None
    retry_policy: Dict[str, Any] = field(default_factory=dict)
    fallback: Optional[str] = None
    runtime_safety_guards: List[str] = field(default_factory=list)

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
        # ── WaitUntilStable (v3.0: 移动目标稳定门控) ──
        "WaitUntilStable": SkillDefinition(
            name="WaitUntilStable",
            description="Wait until {target} stops moving before proceeding",
            preconditions=["target_tracking_available","velocity_confidence>=0.7"],
            effects=["target_stable","ready_for_grasp"],
            params_schema={
                "target": "str — 目标物体名称",
                "max_speed_mps": "float = 0.01 — 稳定判定阈值 (m/s)",
                "timeout_s": "float = 5.0 — 超时时间 (s)",
                "required_consecutive_frames": "int = 3 — 连续稳定帧数",
                "min_velocity_confidence": "float = 0.70",
            },
            safety_notes=["Must complete before any grasp on moving target"],
            success_conditions=["target_velocity <= max_speed_mps for required_consecutive_frames"],
            failure_conditions=["timeout_exceeded", "tracking_lost", "velocity_confidence_drop"],
            timeout_s=5.0,
            retry_policy={"max_retries": 1, "reacquire_before_retry": True},
            fallback="ReSenseTarget",
            runtime_safety_guards=["velocity_threshold", "timeout_guard"],
        ),

        # ── PlanPath (v3.0: 无碰撞全局路径规划) ──
        "PlanPath": SkillDefinition(
            name="PlanPath",
            description="Plan collision-free global path to {target}, avoiding obstacles: {destination}",
            preconditions=[
                "target_position_known",
                "obstacle_positions_known",
            ],
            effects=[
                "collision_free_path_planned",
                "ready_for_approach",
            ],
            params_schema={
                "target": "str — 目标物体名称",
                "avoid_obstacles": "List[str] — 需规避的障碍物名称列表",
                "collision_check": "bool = True — 是否启用碰撞检测",
                "min_clearance_m": "float = 0.05 — 最小安全距离 (m)",
            },
            safety_notes=["Collision check must be enabled", "Min clearance >= 0.05m"],
            success_conditions=["collision_free_path_planned"],
            failure_conditions=["path_blocked", "clearance_violation", "planner_timeout"],
            timeout_s=4.0,
            retry_policy={"max_retries": 2, "replan_on_obstacle_motion": True},
            fallback="FallbackToSaferPath",
            runtime_safety_guards=["trajectory_collision_guard", "sweep_volume_guard"],
        ),

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
            success_conditions=["end_effector_at_target_safe_height"],
            failure_conditions=["target_lost", "path_blocked", "timeout_exceeded"],
            timeout_s=4.0,
            retry_policy={"max_retries": 1, "reacquire_target": True},
            fallback="PlanPath",
            runtime_safety_guards=["z_floor_guard", "collision_guard"],
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
            success_conditions=["target_in_hand", "gripper_closed"],
            failure_conditions=["grasp_slip", "contact_misalignment", "force_limit_violation", "timeout_exceeded"],
            timeout_s=3.0,
            retry_policy={"max_retries": 2, "reposition_before_retry": True},
            fallback="Inspect",
            runtime_safety_guards=["force_guard", "contact_guard"],
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
            success_conditions=["target_at_destination"],
            failure_conditions=["path_blocked", "destination_unreachable", "timeout_exceeded"],
            timeout_s=6.0,
            retry_policy={"max_retries": 2, "replan_before_retry": True},
            fallback="PlanPath",
            runtime_safety_guards=["trajectory_guard", "collision_guard"],
        ),

        # Canonical semantic-compiler name.  ``MoveTo`` remains as a
        # backwards-compatible alias for existing downstream consumers.
        "Transport": SkillDefinition(
            name="Transport",
            description="Transport {target} in hand to {destination}",
            preconditions=["target_in_hand", "destination_reachable", "path_clear_to_destination"],
            effects=["target_at_destination", "ready_for_release"],
            params_schema={"target": "str", "destination": "str", "velocity_ms": "float"},
            safety_notes=["Collision-free path required"],
            success_conditions=["target_at_destination"],
            failure_conditions=["path_blocked", "destination_unreachable", "timeout_exceeded"],
            timeout_s=6.0,
            runtime_safety_guards=["trajectory_guard", "collision_guard"],
        ),

        "MoveToHandoverZone": SkillDefinition(
            name="MoveToHandoverZone",
            description="Move the held {target} to the configured handover zone",
            preconditions=["target_in_hand", "handover_pose_known"],
            effects=["at_handover_zone"],
            params_schema={"target": "str", "handover_zone": "str"},
            safety_notes=["Recipient pose or configured handover zone is required"],
            success_conditions=["at_handover_zone"],
            failure_conditions=["handover_pose_missing", "timeout_exceeded"],
            timeout_s=6.0,
            runtime_safety_guards=["human_proximity_guard", "collision_guard"],
        ),

        "WaitUntil": SkillDefinition(
            name="WaitUntil",
            description="Wait until condition {target} is satisfied",
            preconditions=["condition_observable"],
            effects=["condition_satisfied"],
            params_schema={"condition": "str", "timeout_s": "float"},
            safety_notes=["Timeout is mandatory"],
            success_conditions=["condition_satisfied"],
            failure_conditions=["timeout_exceeded", "condition_unobservable"],
            timeout_s=5.0,
            runtime_safety_guards=["timeout_guard"],
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
            success_conditions=["target_released"],
            failure_conditions=["object_not_stable", "release_blocked", "timeout_exceeded"],
            timeout_s=2.0,
            retry_policy={"max_retries": 1, "retry_after_inspect": True},
            fallback="Inspect",
            runtime_safety_guards=["stability_guard"],
        ),

        # ── Fetch ──
        "Fetch": SkillDefinition(
            name="Fetch",
            description="Fetch {target} and deliver it toward {destination}",
            preconditions=["target_visible", "delivery_target_known"],
            effects=["target_relocated", "delivery_candidate_ready"],
            params_schema={
                "target": "str — 目标物体名称",
                "destination": "str — 交付位置或接收者",
            },
            safety_notes=["Must not fabricate a delivery destination"],
            success_conditions=["target_relocated_or_delivered"],
            failure_conditions=["destination_missing", "target_lost", "timeout_exceeded"],
            timeout_s=8.0,
            retry_policy={"max_retries": 1, "reacquire_target": True},
            fallback="WaitUntilStable",
            runtime_safety_guards=["delivery_destination_guard", "collision_guard"],
        ),

        # ── Place ──
        "Place": SkillDefinition(
            name="Place",
            description="Place {target} on support surface {destination}",
            preconditions=["target_in_hand", "support_surface_known", "placement_pose_known"],
            effects=["target_on_support_surface", "placement_complete"],
            params_schema={
                "target": "str — 被放置物体名称",
                "destination": "str — 支撑面或放置区域",
                "support_surface": "str — 支撑面名称",
                "placement_pose": "dict — 计算得到的放置位姿",
                "placement_region": "dict — 可放置区域",
            },
            safety_notes=["Do not fake placement with MoveTo+Release only"],
            success_conditions=["target_stable_on_surface", "released_clear"],
            failure_conditions=["surface_missing", "placement_pose_invalid", "timeout_exceeded"],
            timeout_s=8.0,
            retry_policy={"max_retries": 1, "recompute_pose": True},
            fallback="Inspect",
            runtime_safety_guards=["edge_clearance_guard", "contact_guard", "stability_guard"],
        ),

        # ── Handover ──
        "Handover": SkillDefinition(
            name="Handover",
            description="Hand over {target} to the recipient",
            preconditions=["recipient_known", "handover_pose_known", "target_in_hand"],
            effects=["recipient_can_receive", "handover_complete"],
            params_schema={
                "target": "str — 被交付物体",
                "recipient": "str — 接收者",
                "recipient_pose": "dict — 接收者位姿",
            },
            safety_notes=["Do not deliver to an unknown recipient pose"],
            success_conditions=["handover_acknowledged"],
            failure_conditions=["recipient_missing", "recipient_pose_missing", "timeout_exceeded"],
            timeout_s=8.0,
            retry_policy={"max_retries": 1, "reacquire_recipient": True},
            fallback="WaitUntilStable",
            runtime_safety_guards=["human_proximity_guard", "release_guard"],
        ),

        # ── Transfer ──
        "Transfer": SkillDefinition(
            name="Transfer",
            description="Transfer {target} from source to destination",
            preconditions=["source_known", "destination_known", "target_in_hand"],
            effects=["target_transferred"],
            params_schema={
                "target": "str — 被转移物体",
                "source": "str — 来源",
                "destination": "str — 目的地",
            },
            safety_notes=["Do not claim completion without source/destination grounding"],
            success_conditions=["target_transferred"],
            failure_conditions=["source_missing", "destination_missing", "timeout_exceeded"],
            timeout_s=8.0,
            retry_policy={"max_retries": 1, "replan_after_failure": True},
            fallback="Inspect",
            runtime_safety_guards=["trajectory_guard", "handover_guard"],
        ),

        # ── DynamicGrasp ──
        "DynamicGrasp": SkillDefinition(
            name="DynamicGrasp",
            description="Track and grasp a moving {target}",
            preconditions=["target_tracking_available", "motion_state_known", "graspable"],
            effects=["moving_target_captured"],
            params_schema={
                "target": "str — 目标物体名称",
                "target_speed_mps": "float — 目标速度",
                "max_wait_s": "float — 最大等待/追踪时间",
            },
            safety_notes=["Must stop if confidence drops", "Must not wait forever"],
            success_conditions=["target_captured_and_stable"],
            failure_conditions=["tracking_lost", "timeout_exceeded", "confidence_drop"],
            timeout_s=6.0,
            retry_policy={"max_retries": 1, "reacquire_before_retry": True},
            fallback="WaitUntilStable",
            runtime_safety_guards=["prediction_guard", "timeout_guard", "confidence_guard"],
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
