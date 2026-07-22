#!/usr/bin/env python3
"""
Generate the blind evaluation dataset.
Run once to produce blind_dataset.json.
Each expected value is based on semantic ground truth, NOT on current implementation output.
"""

from __future__ import annotations
import json


def _obj(obj_id, category, x, y, z, w=0.07, h=0.10, d=0.07, color="white", material="plastic",
         affordances=None, state="stationary", vx=0, vy=0, vz=0, vel_conf=0):
    """Factory for perception objects."""
    if affordances is None:
        affordances = ["graspable", "movable"]
    return {
        "object_id": obj_id,
        "category_candidates": [{"name": category, "score": 0.93}],
        "pose": {"position": {"x": x, "y": y, "z": z}},
        "geometry": {"size": {"width": w, "height": h, "depth": d}},
        "appearance": {"color": color, "material": material},
        "affordances": affordances,
        "tracking": {"state": state, "confidence": 0.96,
                     "velocity": {"x": vx, "y": vy, "z": vz},
                     "velocity_confidence": vel_conf},
    }


# ══════════════════════════════════════════════════════════════
# All blind test cases defined here
# ══════════════════════════════════════════════════════════════

CASES = []

# ── B01-B10: Simple single-action ─────────────────────────

CASES.append({
    "case_id": "B01", "category": "simple_action",
    "instruction": "抓住杯子",
    "objects": [_obj("obj-b01", "cup", 0.35, 0.12, 0.075)],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b01", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "wrong_action": "HIGH", "execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B02", "category": "simple_action",
    "instruction": "把盒子拿过来",
    "objects": [_obj("obj-b02", "box", 0.30, 0.10, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b02",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B03", "category": "simple_action",
    "instruction": "把杯子放到桌子上",
    "objects": [
        _obj("obj-b03a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b03b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b03a",
                 "support_surface_entity_id": "obj-b03b", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "wrong_support_surface": "HIGH",
                 "execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B04", "category": "simple_action",
    "instruction": "把药瓶递给我",
    "objects": [_obj("obj-b04", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic")],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b04", "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_action": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B05", "category": "simple_action",
    "instruction": "抓住红色方块",
    "objects": [_obj("obj-b05", "block", 0.22, 0.15, 0.04, w=0.05, h=0.05, d=0.05, color="red", material="wood")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b05", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "wrong_action": "HIGH"},
})

CASES.append({
    "case_id": "B06", "category": "simple_action",
    "instruction": "拿起杯子",
    "objects": [_obj("obj-b06", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b06", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B07", "category": "simple_action",
    "instruction": "把蓝色积木放到托盘上",
    "objects": [
        _obj("obj-b07a", "block", 0.15, 0.20, 0.03, w=0.04, h=0.04, d=0.04, color="blue", material="wood"),
        _obj("obj-b07b", "tray", 0.40, 0.00, 0.00, w=0.30, h=0.02, d=0.20, color="gray", material="plastic",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b07a",
                 "support_surface_entity_id": "obj-b07b", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "wrong_support_surface": "HIGH"},
})

CASES.append({
    "case_id": "B08", "category": "simple_action",
    "instruction": "把瓶子递给老人",
    "objects": [_obj("obj-b08", "bottle", 0.18, 0.06, 0.04, w=0.04, h=0.10, d=0.04, color="orange", material="plastic")],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b08", "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B09", "category": "simple_action",
    "instruction": "拿起那个东西",
    "objects": [_obj("obj-b09", "block", 0.30, 0.10, 0.05, w=0.05, h=0.05, d=0.05, color="gray", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b09",
                 "notes": "Ambiguous reference '那个东西' should still ground to the only visible object"},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B10", "category": "simple_action",
    "instruction": "把杯子放到桌面上",
    "objects": [
        _obj("obj-b10a", "cup", 0.20, 0.05, 0.06, color="white", material="ceramic"),
        _obj("obj-b10b", "table", 0.35, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b10a",
                 "support_surface_entity_id": "obj-b10b", "execution_allowed": True},
    "severity": {"wrong_support_surface": "HIGH"},
})

# ── B11-B20: Multi-object disambiguation ───────────────────

CASES.append({
    "case_id": "B11", "category": "disambiguation",
    "instruction": "把红色杯子拿过来",
    "objects": [
        _obj("obj-b11a", "cup", 0.30, 0.15, 0.075, color="red", material="plastic"),
        _obj("obj-b11b", "cup", 0.30, -0.15, 0.075, color="blue", material="plastic"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b11a",
                 "theme_color": "red",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B12", "category": "disambiguation",
    "instruction": "抓住蓝色杯子",
    "objects": [
        _obj("obj-b12a", "cup", 0.30, 0.15, 0.075, color="red", material="plastic"),
        _obj("obj-b12b", "cup", 0.30, -0.15, 0.075, color="blue", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b12b", "theme_color": "blue", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B13", "category": "disambiguation",
    "instruction": "把大盒子拿过来",
    "objects": [
        _obj("obj-b13a", "box", 0.25, 0.10, 0.06, w=0.06, h=0.05, d=0.06, color="brown", material="cardboard"),
        _obj("obj-b13b", "box", 0.25, -0.10, 0.08, w=0.15, h=0.12, d=0.15, color="brown", material="cardboard"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b13b",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "大盒子 should match the larger box (obj-b13b)"},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B14", "category": "disambiguation",
    "instruction": "抓住左边的瓶子",
    "objects": [
        _obj("obj-b14a", "bottle", 0.30, -0.20, 0.04, w=0.04, h=0.09, d=0.04, color="white", material="plastic"),
        _obj("obj-b14b", "bottle", 0.30, 0.20, 0.04, w=0.04, h=0.09, d=0.04, color="white", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b14a",
                 "notes": "From robot's perspective, y<0 is typically left. Either grounding is acceptable as long as only one is selected.",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B15", "category": "disambiguation",
    "instruction": "抓住右边的杯子",
    "objects": [
        _obj("obj-b15a", "cup", 0.30, -0.20, 0.075, color="white", material="plastic"),
        _obj("obj-b15b", "cup", 0.30, 0.20, 0.075, color="white", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b15b",
                 "notes": "y>0 is right in robot_base frame", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B16", "category": "disambiguation",
    "instruction": "抓住前面的杯子，不要后面的",
    "objects": [
        _obj("obj-b16a", "cup", 0.30, 0.25, 0.075, color="white", material="plastic"),
        _obj("obj-b16b", "cup", 0.30, -0.10, 0.075, color="white", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b16a",
                 "avoid_objects": ["obj-b16b"],
                 "notes": "前面=in_front_of=y>0, 后面=behind=y<0",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL"},
})

CASES.append({
    "case_id": "B17", "category": "disambiguation",
    "instruction": "抓住那个白色塑料杯子，不是玻璃的",
    "objects": [
        _obj("obj-b17a", "cup", 0.30, 0.12, 0.075, color="white", material="plastic"),
        _obj("obj-b17b", "cup", 0.30, -0.12, 0.075, color="transparent", material="glass",
             affordances=["graspable", "fragile", "movable"]),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b17a", "theme_material": "plastic",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL"},
})

CASES.append({
    "case_id": "B18", "category": "disambiguation",
    "instruction": "有三个杯子，抓住中间那个",
    "objects": [
        _obj("obj-b18a", "cup", 0.30, -0.20, 0.075, color="red", material="plastic"),
        _obj("obj-b18b", "cup", 0.30, 0.00, 0.075, color="blue", material="plastic"),
        _obj("obj-b18c", "cup", 0.30, 0.20, 0.075, color="green", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b18b",
                 "notes": "中间=y=0.00 which is the middle object",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B19", "category": "disambiguation",
    "instruction": "把那个小的拿过来",
    "objects": [
        _obj("obj-b19a", "block", 0.25, 0.10, 0.03, w=0.08, h=0.08, d=0.08, color="red", material="wood"),
        _obj("obj-b19b", "block", 0.25, -0.10, 0.02, w=0.03, h=0.03, d=0.03, color="blue", material="wood"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b19b",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "小的 should match the smaller block"},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B20", "category": "disambiguation",
    "instruction": "抓住玻璃杯，别碰塑料杯",
    "objects": [
        _obj("obj-b20a", "cup", 0.30, 0.15, 0.075, color="transparent", material="glass",
             affordances=["graspable", "fragile", "movable"]),
        _obj("obj-b20b", "cup", 0.30, -0.15, 0.075, color="white", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b20a", "avoid_objects": ["obj-b20b"],
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL"},
})

# ── B21-B30: Spatial & descriptive ────────────────────────

CASES.append({
    "case_id": "B21", "category": "spatial_descriptive",
    "instruction": "抓住正在移动的红色小球",
    "objects": [_obj("obj-b21", "ball", 0.30, 0.15, 0.03, w=0.04, h=0.04, d=0.04, color="red", material="rubber",
                     state="moving", vx=0.15, vy=0.02, vz=0, vel_conf=0.92)],
    "expected": {"action": "DYNAMIC_GRASP", "theme_entity_id": "obj-b21",
                 "motion_state": "moving", "execution_allowed": True},
    "severity": {"wrong_action": "HIGH"},
})

CASES.append({
    "case_id": "B22", "category": "spatial_descriptive",
    "instruction": "抓住静止的杯子",
    "objects": [_obj("obj-b22", "cup", 0.35, 0.12, 0.075, color="white", material="plastic",
                     state="stationary", vx=0, vy=0, vz=0, vel_conf=0)],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b22", "execution_allowed": True},
    "severity": {"wrong_action": "HIGH"},
})

CASES.append({
    "case_id": "B23", "category": "spatial_descriptive",
    "instruction": "把杯子放到桌子左边",
    "objects": [
        _obj("obj-b23a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b23b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b23a",
                 "support_surface_entity_id": "obj-b23b", "execution_allowed": True,
                 "notes": "Destination is table-left, support_surface is table"},
    "severity": {"wrong_support_surface": "HIGH"},
})

CASES.append({
    "case_id": "B24", "category": "spatial_descriptive",
    "instruction": "抓住离你最近的那个杯子",
    "objects": [
        _obj("obj-b24a", "cup", 0.10, 0.05, 0.075, color="white", material="plastic"),
        _obj("obj-b24b", "cup", 0.50, 0.05, 0.075, color="blue", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b24a",
                 "notes": "obj-b24a is closer to robot (0,0) than obj-b24b",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B25", "category": "spatial_descriptive",
    "instruction": "抓住高处那个瓶子",
    "objects": [
        _obj("obj-b25a", "bottle", 0.25, 0.10, 0.15, w=0.04, h=0.09, d=0.04, color="green", material="plastic"),
        _obj("obj-b25b", "bottle", 0.25, -0.10, 0.04, w=0.04, h=0.09, d=0.04, color="green", material="plastic"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b25a",
                 "notes": "高处=higher z position (0.15 > 0.04)",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B26", "category": "spatial_descriptive",
    "instruction": "抓住桌子上那个杯子",
    "objects": [
        _obj("obj-b26a", "cup", 0.40, 0.00, 0.06, color="white", material="plastic"),
        _obj("obj-b26b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b26a",
                 "notes": "桌子上那 (on the table) — cup should be on/near the table",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B27", "category": "spatial_descriptive",
    "instruction": "把远处的盒子推过来",
    "objects": [
        _obj("obj-b27a", "box", 0.60, 0.05, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard"),
        _obj("obj-b27b", "box", 0.20, -0.10, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b27a",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "远处=far, obj-b27a at x=0.60 is farther than obj-b27b at x=0.20"},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B28", "category": "spatial_descriptive",
    "instruction": "把桌上的蓝色盒子拿过来",
    "objects": [
        _obj("obj-b28a", "box", 0.40, 0.05, 0.05, w=0.08, h=0.06, d=0.08, color="blue", material="cardboard"),
        _obj("obj-b28b", "box", 0.20, -0.10, 0.05, w=0.08, h=0.06, d=0.08, color="red", material="cardboard"),
        _obj("obj-b28c", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b28a",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "桌上(0.40,0.05)的蓝色盒子 → obj-b28a"},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B29", "category": "spatial_descriptive",
    "instruction": "抓住那个又大又红的方块",
    "objects": [
        _obj("obj-b29a", "block", 0.20, 0.10, 0.03, w=0.04, h=0.04, d=0.04, color="red", material="wood"),
        _obj("obj-b29b", "block", 0.35, -0.10, 0.04, w=0.08, h=0.08, d=0.08, color="red", material="wood"),
        _obj("obj-b29c", "block", 0.50, 0.15, 0.03, w=0.06, h=0.06, d=0.06, color="blue", material="wood"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b29b",
                 "notes": "又大又红 → largest red block = obj-b29b (0.08 vs 0.04)",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL"},
})

CASES.append({
    "case_id": "B30", "category": "spatial_descriptive",
    "instruction": "抓住正在慢慢移动的白色杯子",
    "objects": [_obj("obj-b30", "cup", 0.35, 0.12, 0.075, color="white", material="plastic",
                     state="moving", vx=0.05, vy=0.01, vz=0, vel_conf=0.85)],
    "expected": {"action": "DYNAMIC_GRASP", "theme_entity_id": "obj-b30",
                 "motion_state": "moving", "execution_allowed": True},
    "severity": {"wrong_action": "HIGH"},
})

# ── B31-B40: Role extraction ──────────────────────────────

CASES.append({
    "case_id": "B31", "category": "roles",
    "instruction": "把桌上的杯子递给我",
    "objects": [
        _obj("obj-b31a", "cup", 0.40, 0.05, 0.06, color="white", material="plastic"),
        _obj("obj-b31b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b31a",
                 "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_action": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B32", "category": "roles",
    "instruction": "把杯子从桌子上拿过来",
    "objects": [
        _obj("obj-b32a", "cup", 0.40, 0.05, 0.06, color="white", material="plastic"),
        _obj("obj-b32b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b32a",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "source=table, theme=cup, destination=user; but source role may not be explicitly modeled"},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B33", "category": "roles",
    "instruction": "把盒子拿过来，别碰玻璃杯",
    "objects": [
        _obj("obj-b33a", "box", 0.30, 0.10, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard"),
        _obj("obj-b33b", "cup", 0.30, 0.05, 0.06, w=0.07, h=0.12, d=0.07, color="transparent", material="glass",
             affordances=["graspable", "fragile", "movable"]),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b33a",
                 "avoid_objects": ["obj-b33b"],
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL",
                 "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B34", "category": "roles",
    "instruction": "把药瓶从桌上拿起来，递给我",
    "objects": [
        _obj("obj-b34a", "bottle", 0.40, 0.05, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic"),
        _obj("obj-b34b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b34a",
                 "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B35", "category": "roles",
    "instruction": "把蓝色方块放到红色方块上面",
    "objects": [
        _obj("obj-b35a", "block", 0.25, 0.10, 0.03, w=0.04, h=0.04, d=0.04, color="blue", material="wood"),
        _obj("obj-b35b", "block", 0.40, 0.00, 0.03, w=0.06, h=0.06, d=0.06, color="red", material="wood"),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b35a",
                 "destination": "obj-b35b",
                 "notes": "放到...上面=stack/place, destination is the red block. Support surface role may be mapped from destination.",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "wrong_destination": "HIGH"},
})

CASES.append({
    "case_id": "B36", "category": "roles",
    "instruction": "把杯子递给我，轻一点",
    "objects": [_obj("obj-b36", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b36",
                 "recipient_identified": True, "manner": "gentle",
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_action": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B37", "category": "roles",
    "instruction": "把这个拿给用户",
    "objects": [_obj("obj-b37", "box", 0.30, 0.10, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard")],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b37",
                 "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False,
                 "notes": "拿给=handover to user"},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B38", "category": "roles",
    "instruction": "把杯子放到支撑面上",
    "objects": [
        _obj("obj-b38a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b38b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b38a",
                 "support_surface_entity_id": "obj-b38b",
                 "notes": "支撑面=support_surface → table",
                 "execution_allowed": True},
    "severity": {"wrong_support_surface": "HIGH", "execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B39", "category": "roles",
    "instruction": "抓住杯子，避开那个盒子",
    "objects": [
        _obj("obj-b39a", "cup", 0.35, 0.12, 0.075, color="white", material="plastic"),
        _obj("obj-b39b", "box", 0.32, 0.10, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b39a",
                 "avoid_objects": ["obj-b39b"], "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL"},
})

CASES.append({
    "case_id": "B40", "category": "roles",
    "instruction": "把红色药瓶交给医生",
    "objects": [_obj("obj-b40", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic")],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b40",
                 "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False,
                 "notes": "医生=recipient, 交付给指定人员"},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

# ── B41-B50: Negation, conditional, sequential ────────────

CASES.append({
    "case_id": "B41", "category": "negation_condition",
    "instruction": "把盒子拿过来，千万别碰玻璃杯",
    "objects": [
        _obj("obj-b41a", "box", 0.30, 0.10, 0.05, w=0.08, h=0.06, d=0.08, color="brown", material="cardboard"),
        _obj("obj-b41b", "cup", 0.30, 0.05, 0.06, w=0.07, h=0.12, d=0.07, color="transparent", material="glass",
             affordances=["graspable", "fragile", "movable"]),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b41a",
                 "avoid_objects": ["obj-b41b"],
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"ignored_negation": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B42", "category": "negation_condition",
    "instruction": "如果杯子是空的，就把它拿过来",
    "objects": [_obj("obj-b42", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b42",
                 "notes": "Conditional 'if empty' — action still FETCH, condition is implicit",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B43", "category": "negation_condition",
    "instruction": "先抓住杯子，再放到桌子上",
    "objects": [
        _obj("obj-b43a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b43b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b43a",
                 "support_surface_entity_id": "obj-b43b",
                 "notes": "Sequential: grasp then place. Action should be PLACE (final action). BT should have both grasp and place.",
                 "execution_allowed": True},
    "severity": {"wrong_action": "HIGH"},
})

CASES.append({
    "case_id": "B44", "category": "negation_condition",
    "instruction": "不要碰那个红色的，把蓝色的拿过来",
    "objects": [
        _obj("obj-b44a", "block", 0.20, 0.10, 0.03, w=0.05, h=0.05, d=0.05, color="red", material="wood"),
        _obj("obj-b44b", "block", 0.35, -0.10, 0.03, w=0.05, h=0.05, d=0.05, color="blue", material="wood"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b44b",
                 "avoid_objects": ["obj-b44a"],
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL",
                 "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B45", "category": "negation_condition",
    "instruction": "除非夹爪是空的，否则不要抓取",
    "objects": [_obj("obj-b45", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b45",
                 "notes": "Conditional grasp — precondition: gripper empty",
                 "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B46", "category": "negation_condition",
    "instruction": "抓住玻璃杯，但别用力",
    "objects": [_obj("obj-b46", "cup", 0.35, 0.12, 0.075, color="transparent", material="glass",
                     affordances=["graspable", "fragile", "movable"])],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b46",
                 "manner": "gentle",
                 "notes": "别用力 → gentle grasp, low force", "execution_allowed": True},
    "severity": {"ignored_negation": "CRITICAL"},
})

CASES.append({
    "case_id": "B47", "category": "negation_condition",
    "instruction": "把杯子放到桌上，但不要放在边缘",
    "objects": [
        _obj("obj-b47a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b47b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b47a",
                 "support_surface_entity_id": "obj-b47b",
                 "notes": "不要放在边缘 → still PLACE on table, constraint on placement position",
                 "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B48", "category": "negation_condition",
    "instruction": "如果看到红色药瓶就先拿它，否则拿蓝色盒子",
    "objects": [
        _obj("obj-b48a", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic"),
        _obj("obj-b48b", "box", 0.35, -0.10, 0.05, w=0.08, h=0.06, d=0.08, color="blue", material="cardboard"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b48a",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "Conditional preference: red bottle first, blue box as fallback. Theme should be the red bottle."},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B49", "category": "negation_condition",
    "instruction": "先确认夹爪是空的，然后抓住杯子",
    "objects": [_obj("obj-b49", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b49",
                 "notes": "Precondition: check gripper empty, then grasp", "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B50", "category": "negation_condition",
    "instruction": "不要碰任何东西，把最右边的杯子拿过来",
    "objects": [
        _obj("obj-b50a", "cup", 0.30, -0.20, 0.075, color="white", material="plastic"),
        _obj("obj-b50b", "cup", 0.30, 0.00, 0.075, color="white", material="plastic"),
        _obj("obj-b50c", "cup", 0.30, 0.20, 0.075, color="white", material="plastic"),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b50c",
                 "avoid_objects": ["obj-b50a", "obj-b50b"],
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "最右边=y最大=obj-b50c, 不要碰任何东西→避让其他所有物体"},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL",
                 "execution_allowed_with_missing_roles": "CRITICAL"},
})

# ── B51-B60: Numeric constraints ──────────────────────────

CASES.append({
    "case_id": "B51", "category": "numeric_constraints",
    "instruction": "用3N力量抓住杯子",
    "objects": [_obj("obj-b51", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b51",
                 "force_n": 3.0, "force_op": "exact",
                 "resolved_force_n": 3.0, "execution_allowed": True},
    "severity": {"wrong_force_value": "HIGH"},
})

CASES.append({
    "case_id": "B52", "category": "numeric_constraints",
    "instruction": "不超过5N抓住杯子",
    "objects": [_obj("obj-b52", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b52",
                 "force_n": 5.0, "force_op": "max",
                 "resolved_force_n_le": 5.0, "execution_allowed": True},
    "severity": {"wrong_force_value": "HIGH", "force_exceeds_limit": "CRITICAL"},
})

CASES.append({
    "case_id": "B53", "category": "numeric_constraints",
    "instruction": "至少2N抓住杯子",
    "objects": [_obj("obj-b53", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b53",
                 "force_op": "min", "force_n_min": 2.0,
                 "resolved_force_n_ge": 2.0, "execution_allowed": True},
    "severity": {"wrong_force_value": "HIGH"},
})

CASES.append({
    "case_id": "B54", "category": "numeric_constraints",
    "instruction": "用3到5N的力量抓住杯子",
    "objects": [_obj("obj-b54", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b54",
                 "force_op": "range", "force_n_min": 3.0, "force_n_max": 5.0,
                 "notes": "Resolved force should be within [3,5]",
                 "execution_allowed": True},
    "severity": {"wrong_force_value": "HIGH", "force_out_of_range": "CRITICAL"},
})

CASES.append({
    "case_id": "B55", "category": "numeric_constraints",
    "instruction": "用50N力量抓住玻璃杯",
    "objects": [_obj("obj-b55", "cup", 0.35, 0.12, 0.075, color="transparent", material="glass",
                     affordances=["graspable", "fragile", "movable"])],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b55",
                 "force_n": 50.0, "force_op": "exact",
                 "resolved_force_n": 2.0,
                 "notes": "50N requested but glass material hard limit ~2N should override",
                 "plan_status": "READY_WITH_SAFE_SUBSTITUTION",
                 "execution_allowed": True},
    "severity": {"force_not_clamped": "CRITICAL"},
})

CASES.append({
    "case_id": "B56", "category": "numeric_constraints",
    "instruction": "以0.15m/s的速度移动杯子",
    "objects": [_obj("obj-b56", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "CUSTOM", "theme_entity_id": "obj-b56",
                 "velocity_ms": 0.15, "vel_op": "exact",
                 "notes": "Speed-specified movement — may map to Reach with velocity constraint",
                 "execution_allowed": True},
    "severity": {"wrong_velocity_value": "HIGH"},
})

CASES.append({
    "case_id": "B57", "category": "numeric_constraints",
    "instruction": "以不超过0.1m/s的速度把杯子拿过来",
    "objects": [_obj("obj-b57", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b57",
                 "vel_op": "max", "velocity_ms_max": 0.1,
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_velocity_value": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B58", "category": "numeric_constraints",
    "instruction": "至少0.2m/s，用3N抓力，抓住杯子",
    "objects": [_obj("obj-b58", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b58",
                 "force_n": 3.0, "force_op": "exact",
                 "vel_op": "min", "velocity_ms_min": 0.2,
                 "execution_allowed": True,
                 "notes": "Multiple constraints in one instruction"},
    "severity": {"wrong_force_value": "HIGH", "wrong_velocity_value": "HIGH"},
})

CASES.append({
    "case_id": "B59", "category": "numeric_constraints",
    "instruction": "以5m/s的速度抓住杯子",
    "objects": [_obj("obj-b59", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b59",
                 "velocity_ms": 5.0,
                 "notes": "5m/s exceeds safe limits for reach/grasp — should be clamped or flagged",
                 "execution_allowed": True,
                 "_check": "velocity should not exceed stage_limit (~0.2 m/s for Reach)"},
    "severity": {"unsafe_velocity_allowed": "CRITICAL"},
})

CASES.append({
    "case_id": "B60", "category": "numeric_constraints",
    "instruction": "用2N力、速度0.1m/s，把红色药瓶递给我",
    "objects": [_obj("obj-b60", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic")],
    "expected": {"action": "HANDOVER", "theme_entity_id": "obj-b60",
                 "force_n": 2.0, "velocity_ms": 0.1,
                 "recipient_identified": True,
                 "missing_roles": ["recipient_pose_or_handover_zone"],
                 "execution_allowed": False},
    "severity": {"wrong_force_value": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

# ── B61-B70: Robot state & capability ─────────────────────

CASES.append({
    "case_id": "B61", "category": "robot_state",
    "instruction": "抓住杯子",
    "objects": [_obj("obj-b61", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b61", "execution_allowed": True,
                 "notes": "Basic grasp — no special robot state"},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B62", "category": "robot_state",
    "instruction": "抓住杯子",
    "objects": [_obj("obj-b62", "cup", 0.80, 0.10, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b62",
                 "notes": "Object at x=0.80m — should check if within workspace (~0.6m typical). May be out of reach.",
                 "execution_allowed": True,
                 "_check": "target may be out of workspace; system should at minimum flag this"},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B63", "category": "robot_state",
    "instruction": "把杯子放到桌子上",
    "objects": [
        _obj("obj-b63a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b63b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b63a",
                 "support_surface_entity_id": "obj-b63b", "execution_allowed": True},
    "severity": {},
})

CASES.append({
    "case_id": "B64", "category": "robot_state",
    "instruction": "抓住很重的铁块",
    "objects": [_obj("obj-b64", "block", 0.30, 0.10, 0.05, w=0.10, h=0.10, d=0.10, color="gray", material="metal")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b64",
                 "notes": "Metal object may be heavy — need higher force or may exceed payload; system should detect weight concern.",
                 "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B65", "category": "robot_state",
    "instruction": "把药片从瓶子里倒出来",
    "objects": [_obj("obj-b65", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="orange", material="plastic",
                     affordances=["graspable", "movable", "container"])],
    "expected": {"action": "CUSTOM", "theme_entity_id": "obj-b65",
                 "notes": "倒=pour — complex action requiring tilt. May not be in standard skill catalog.",
                 "execution_allowed": True},
    "severity": {"wrong_action": "HIGH"},
})

CASES.append({
    "case_id": "B66", "category": "robot_state",
    "instruction": "抓住那根针",
    "objects": [_obj("obj-b66", "needle", 0.25, 0.10, 0.02, w=0.001, h=0.001, d=0.03, color="silver", material="metal")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b66",
                 "notes": "Very small object — may need precision grasp. Category 'needle' may not be recognized.",
                 "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B67", "category": "robot_state",
    "instruction": "抓住带USB线的设备",
    "objects": [_obj("obj-b67", "device", 0.30, 0.10, 0.04, w=0.06, h=0.03, d=0.10, color="black", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b67",
                 "notes": "Complex object with cable — may need careful handling", "execution_allowed": True},
    "severity": {},
})

CASES.append({
    "case_id": "B68", "category": "robot_state",
    "instruction": "抓住那个热杯子",
    "objects": [_obj("obj-b68", "cup", 0.35, 0.12, 0.075, color="white", material="ceramic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b68",
                 "notes": "热=hot — temperature hazard. System may not model but should not crash.",
                 "execution_allowed": True},
    "severity": {},
})

CASES.append({
    "case_id": "B69", "category": "robot_state",
    "instruction": "用左手抓住杯子",
    "objects": [_obj("obj-b69", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b69",
                 "notes": "左手=left hand — constraint on which arm/end-effector to use", "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B70", "category": "robot_state",
    "instruction": "把杯子夹住并翻转过来",
    "objects": [_obj("obj-b70", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "CUSTOM", "theme_entity_id": "obj-b70",
                 "notes": "翻转=flip — complex manipulation beyond standard skills", "execution_allowed": True},
    "severity": {},
})

# ── B71-B80: Target missing / out of workspace ────────────

CASES.append({
    "case_id": "B71", "category": "missing_target",
    "instruction": "把杯子拿过来",
    "objects": [_obj("obj-b71", "block", 0.30, 0.10, 0.05, w=0.05, h=0.05, d=0.05, color="brown", material="wood")],
    "expected": {"action": "FETCH",
                 "theme_not_in_scene": True,
                 "notes": "指令要求'杯子'但场景只有方块 — theme不应被错误接地到方块",
                 "execution_allowed": False},
    "severity": {"fabricated_grounding": "CRITICAL", "execution_allowed_when_target_missing": "CRITICAL"},
})

CASES.append({
    "case_id": "B72", "category": "missing_target",
    "instruction": "抓住红色杯子",
    "objects": [_obj("obj-b72", "cup", 0.35, 0.12, 0.075, color="blue", material="plastic")],
    "expected": {"action": "GRASP",
                 "notes": "要求'红色杯子'但只有蓝色杯子 — 颜色不匹配，theme不应被接地",
                 "execution_allowed": False},
    "severity": {"wrong_target_color": "CRITICAL", "execution_allowed_when_target_missing": "CRITICAL"},
})

CASES.append({
    "case_id": "B73", "category": "missing_target",
    "instruction": "把药瓶递给我",
    "objects": [_obj("obj-b73", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "HANDOVER",
                 "theme_not_in_scene": True,
                 "notes": "指令要求药瓶但场景只有杯子",
                 "execution_allowed": False},
    "severity": {"fabricated_grounding": "CRITICAL", "execution_allowed_when_target_missing": "CRITICAL"},
})

CASES.append({
    "case_id": "B74", "category": "missing_target",
    "instruction": "抓住左侧的杯子",
    "objects": [_obj("obj-b74", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b74",
                 "notes": "Only one cup — should be grounded regardless of '左侧'. No disambiguation needed.",
                 "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B75", "category": "missing_target",
    "instruction": "抓住那个蓝色的方块",
    "objects": [
        _obj("obj-b75a", "block", 0.20, 0.10, 0.03, w=0.05, h=0.05, d=0.05, color="red", material="wood"),
        _obj("obj-b75b", "block", 0.35, -0.10, 0.03, w=0.05, h=0.05, d=0.05, color="green", material="wood"),
    ],
    "expected": {"action": "GRASP",
                 "notes": "要求蓝色方块但场景只有红色和绿色 — 无蓝色方块",
                 "execution_allowed": False,
                 "_check": "No blue block in scene → should not ground to red or green"},
    "severity": {"fabricated_grounding": "CRITICAL", "execution_allowed_when_target_missing": "CRITICAL"},
})

CASES.append({
    "case_id": "B76", "category": "missing_target",
    "instruction": "把杯子放到桌子上",
    "objects": [_obj("obj-b76", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic")],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b76",
                 "support_surface_not_in_scene": True,
                 "missing_roles": ["support_surface"],
                 "notes": "桌子不在场景中 — 无法执行PLACE",
                 "execution_allowed": False},
    "severity": {"fabricated_support_surface": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B77", "category": "missing_target",
    "instruction": "把杯子放到桌子上面",
    "objects": [
        _obj("obj-b77a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b77b", "box", 0.40, 0.00, 0.05, w=0.30, h=0.15, d=0.30, color="brown", material="cardboard",
             affordances=["graspable", "movable"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b77a",
                 "missing_roles": ["support_surface"],
                 "notes": "场景有盒子但没有桌子 — support_surface缺失",
                 "execution_allowed": False},
    "severity": {"wrong_support_surface": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B78", "category": "missing_target",
    "instruction": "抓住那个东西",
    "objects": [],
    "expected": {"theme_not_in_scene": True,
                 "notes": "空场景+vague引用 = 无法接地", "execution_allowed": False},
    "severity": {"execution_allowed_with_empty_scene": "CRITICAL"},
})

CASES.append({
    "case_id": "B79", "category": "missing_target",
    "instruction": "把桌子上的杯子拿过来",
    "objects": [_obj("obj-b79", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b79",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "杯子存在但'桌子'不在场景 — 主题接地到杯子是可接受的，但缺少交付位姿"},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B80", "category": "missing_target",
    "instruction": "",
    "objects": [],
    "expected": {"notes": "空指令 — 应该返回错误或空结果", "execution_allowed": False},
    "severity": {"execution_allowed_with_empty_instruction": "CRITICAL"},
})

# ── B81-B90: Instruction/perception conflict ──────────────

CASES.append({
    "case_id": "B81", "category": "conflict",
    "instruction": "抓住那个红色杯子",
    "objects": [_obj("obj-b81", "cup", 0.35, 0.12, 0.075, color="blue", material="plastic")],
    "expected": {"action": "GRASP",
                 "notes": "指令要求红色但场景物体是蓝色 — 颜色冲突,不应盲目接地",
                 "execution_allowed": False},
    "severity": {"wrong_color_grounding": "CRITICAL", "execution_allowed_with_conflict": "CRITICAL"},
})

CASES.append({
    "case_id": "B82", "category": "conflict",
    "instruction": "轻轻抓住那个玻璃杯",
    "objects": [_obj("obj-b82", "block", 0.35, 0.12, 0.05, w=0.05, h=0.05, d=0.05, color="transparent", material="plastic")],
    "expected": {"notes": "指令要求玻璃杯但场景是塑料方块 — 类别不匹配",
                 "execution_allowed": False},
    "severity": {"fabricated_grounding": "CRITICAL", "execution_allowed_with_conflict": "CRITICAL"},
})

CASES.append({
    "case_id": "B83", "category": "conflict",
    "instruction": "用100N力量抓住塑料杯",
    "objects": [_obj("obj-b83", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b83",
                 "force_n": 100.0,
                 "notes": "100N远超塑料杯的安全限 — 应被压缩到安全范围",
                 "resolved_force_n_le": 10.0,
                 "plan_status": "READY_WITH_SAFE_SUBSTITUTION",
                 "execution_allowed": True},
    "severity": {"force_not_clamped": "CRITICAL"},
})

CASES.append({
    "case_id": "B84", "category": "conflict",
    "instruction": "用力抓住玻璃杯",
    "objects": [_obj("obj-b84", "cup", 0.35, 0.12, 0.075, color="transparent", material="glass",
                     affordances=["graspable", "fragile", "movable"])],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b84",
                 "notes": "用力(high force) vs 玻璃(fragile) — 冲突! 安全约束应优先,力度不应超过玻璃安全上限",
                 "resolved_force_n_le": 2.0,
                 "execution_allowed": True},
    "severity": {"fragile_force_not_clamped": "CRITICAL"},
})

CASES.append({
    "case_id": "B85", "category": "conflict",
    "instruction": "快点用50N力量抓住玻璃杯",
    "objects": [_obj("obj-b85", "cup", 0.35, 0.12, 0.075, color="transparent", material="glass",
                     affordances=["graspable", "fragile", "movable"])],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b85",
                 "force_n": 50.0, "force_op": "exact", "manner": "fast",
                 "notes": "快+50N+玻璃杯: 速度高+力度高+易碎=三重风险,安全约束必须压制",
                 "resolved_force_n": 2.0,
                 "plan_status": "READY_WITH_SAFE_SUBSTITUTION",
                 "execution_allowed": True},
    "severity": {"force_not_clamped": "CRITICAL", "unsafe_velocity": "CRITICAL"},
})

CASES.append({
    "case_id": "B86", "category": "conflict",
    "instruction": "把杯子放到桌子上",
    "objects": [
        _obj("obj-b86a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b86b", "cup", 0.40, 0.00, 0.06, color="brown", material="wood",
             affordances=["graspable", "movable"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b86a",
                 "missing_roles": ["support_surface"],
                 "notes": "第二个物体分类是cup不是table — 不能作为桌面使用",
                 "execution_allowed": False},
    "severity": {"wrong_support_surface": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B87", "category": "conflict",
    "instruction": "把重物放到精密仪器旁边",
    "objects": [
        _obj("obj-b87a", "block", 0.20, 0.10, 0.05, w=0.15, h=0.15, d=0.15, color="gray", material="metal"),
        _obj("obj-b87b", "device", 0.20, -0.10, 0.05, w=0.10, h=0.05, d=0.15, color="white", material="plastic",
             affordances=["graspable", "fragile", "movable"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b87a",
                 "avoid_objects": ["obj-b87b"],
                 "notes": "精密仪器=需要避让 — 放置位置不应靠近精密设备; avoid应出现",
                 "execution_allowed": True},
    "severity": {"ignored_avoid": "CRITICAL"},
})

CASES.append({
    "case_id": "B88", "category": "conflict",
    "instruction": "抓住杯子，但我不想让你碰桌子",
    "objects": [
        _obj("obj-b88a", "cup", 0.25, 0.10, 0.06, color="white", material="ceramic"),
        _obj("obj-b88b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b88a",
                 "avoid_objects": ["obj-b88b"],
                 "notes": "不想=否定, 碰桌子=avoid table",
                 "execution_allowed": True},
    "severity": {"ignored_negation": "CRITICAL"},
})

CASES.append({
    "case_id": "B89", "category": "conflict",
    "instruction": "把杯子和玻璃杯都拿过来",
    "objects": [
        _obj("obj-b89a", "cup", 0.30, 0.15, 0.075, color="white", material="plastic"),
        _obj("obj-b89b", "cup", 0.30, -0.15, 0.075, color="transparent", material="glass",
             affordances=["graspable", "fragile", "movable"]),
    ],
    "expected": {"action": "FETCH",
                 "notes": "'都'=both — 当前系统主要设计为单目标,多目标任务应触发澄清",
                 "execution_allowed": False},
    "severity": {"random_target_selection": "CRITICAL"},
})

CASES.append({
    "case_id": "B90", "category": "conflict",
    "instruction": "用不超过1N的力量抓住铁块",
    "objects": [_obj("obj-b90", "block", 0.30, 0.10, 0.05, w=0.10, h=0.10, d=0.10, color="gray", material="metal")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b90",
                 "force_op": "max", "force_n": 1.0,
                 "notes": "1N可能不足以抓取铁块(需要一定摩擦力抵抗重力) — 可能需要min_force约束",
                 "execution_allowed": True},
    "severity": {},
})

# ── B91-B100: Empty/missing/invalid input ──────────────────

CASES.append({
    "case_id": "B91", "category": "invalid_input",
    "instruction": "",
    "objects": [_obj("obj-b91", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"notes": "空指令 — 必须有合适的错误处理", "execution_allowed": False},
    "severity": {"execution_allowed_with_empty_instruction": "CRITICAL"},
})

CASES.append({
    "case_id": "B92", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [],
    "expected": {"notes": "空场景 = 无物体可接地", "execution_allowed": False},
    "severity": {"execution_allowed_with_empty_scene": "CRITICAL"},
})

CASES.append({
    "case_id": "B93", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": "obj-b93", "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}}},
    ],
    "expected": {"notes": "物体缺少 category_candidates, geometry, appearance, affordances, tracking — 应优雅降级",
                 "execution_allowed": True},
    "severity": {"crash_on_missing_fields": "CRITICAL"},
})

CASES.append({
    "case_id": "B94", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": "obj-b94", "category_candidates": [{"name": "cup", "score": 0.93}],
         "pose": {"position": {"x": "invalid", "y": 0.12, "z": 0.075}},
         "geometry": {"size": {"width": 0.07, "height": 0.10, "depth": 0.07}},
         "appearance": {"color": "white", "material": "plastic"},
         "affordances": ["graspable", "movable"],
         "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
    ],
    "expected": {"notes": "position.x='invalid' — 应优雅处理非法值", "execution_allowed": True},
    "severity": {"crash_on_invalid_value": "CRITICAL"},
})

CASES.append({
    "case_id": "B95", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": "obj-b95", "category_candidates": [],
         "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}},
         "geometry": {"size": {"width": 0.07, "height": 0.10, "depth": 0.07}},
         "appearance": {"color": "white", "material": "plastic"},
         "affordances": ["graspable", "movable"],
         "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
    ],
    "expected": {"notes": "空category_candidates — 类别完全未知", "execution_allowed": True},
    "severity": {"crash_on_empty_categories": "CRITICAL"},
})

CASES.append({
    "case_id": "B96", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": "obj-b96", "category_candidates": [{"score": 0.5}],
         "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}},
         "geometry": {},
         "appearance": {"color": "white", "material": "plastic"},
         "affordances": ["graspable", "movable"],
         "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
    ],
    "expected": {"notes": "category无name字段 + geometry无size字段", "execution_allowed": True},
    "severity": {"crash_on_missing_fields": "CRITICAL"},
})

CASES.append({
    "case_id": "B97", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": "obj-b97", "category_candidates": [{"name": "cup", "score": 0.93}],
         "pose": {"position": {"x": 1e10, "y": 1e10, "z": 1e10}},
         "geometry": {"size": {"width": -0.07, "height": 0.0, "depth": 0.07}},
         "appearance": {"color": "white", "material": "plastic"},
         "affordances": ["graspable", "movable"],
         "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
    ],
    "expected": {"notes": "Extreme position + negative width — should not crash. Position may be out of workspace.",
                 "execution_allowed": True},
    "severity": {"crash_on_extreme_values": "CRITICAL"},
})

CASES.append({
    "case_id": "B98", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": "obj-b98", "category_candidates": [{"name": "cup", "score": 0.93}],
         "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}},
         "geometry": {"size": {"width": 0.07, "height": 0.10, "depth": 0.07}},
         "appearance": {"color": "white", "material": "plastic"},
         "affordances": [],
         "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b98",
                 "notes": "空affordances — 应回退到默认graspable", "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B99", "category": "invalid_input",
    "instruction": "抓住杯子",
    "objects": [
        {"object_id": None,
         "category_candidates": [{"name": "cup", "score": 0.93}],
         "pose": {"position": {"x": 0.35, "y": 0.12, "z": 0.075}},
         "geometry": {"size": {"width": 0.07, "height": 0.10, "depth": 0.07}},
         "appearance": {"color": "white", "material": "plastic"},
         "affordances": ["graspable", "movable"],
         "tracking": {"state": "stationary", "confidence": 0.96, "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0}},
    ],
    "expected": {"notes": "object_id=None — 系统应生成或跳过", "execution_allowed": True},
    "severity": {"crash_on_null_id": "CRITICAL"},
})

CASES.append({
    "case_id": "B100", "category": "invalid_input",
    "instruction": "   ",
    "objects": [_obj("obj-b100", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"notes": "纯空白指令 — 应等同于空指令", "execution_allowed": False},
    "severity": {"execution_allowed_with_empty_instruction": "CRITICAL"},
})

# ── B101-B110: Mixed Chinese-English, colloquial, typos ────

CASES.append({
    "case_id": "B101", "category": "mixed_colloquial",
    "instruction": "把那个cup拿过来",
    "objects": [_obj("obj-b101", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b101",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B102", "category": "mixed_colloquial",
    "instruction": "grasp the red bottle for me",
    "objects": [_obj("obj-b102", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b102",
                 "notes": "English instruction — should still work with scene labels",
                 "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B103", "category": "mixed_colloquial",
    "instruction": "grab那个红色的bottle然后放到table上",
    "objects": [
        _obj("obj-b103a", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="red", material="plastic"),
        _obj("obj-b103b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "PLACE", "theme_entity_id": "obj-b103a",
                 "support_surface_entity_id": "obj-b103b",
                 "notes": "中英混合指令", "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "wrong_action": "HIGH"},
})

CASES.append({
    "case_id": "B104", "category": "mixed_colloquial",
    "instruction": "把那玩意儿拿过来",
    "objects": [_obj("obj-b104", "block", 0.30, 0.10, 0.05, w=0.05, h=0.05, d=0.05, color="gray", material="plastic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b104",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "口语化'那玩意儿' — 唯一可见物体时应接地"},
    "severity": {"execution_blocked_falsely": "HIGH", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B105", "category": "mixed_colloquial",
    "instruction": "把beizi拿过来",
    "objects": [_obj("obj-b105", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b105",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "拼音输入'beizi'=杯子 — 可能无法识别，但场景物体匹配应兜底"},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B106", "category": "mixed_colloquial",
    "instruction": "帮我把那个玻璃bei拿过来行不",
    "objects": [_obj("obj-b106", "cup", 0.35, 0.12, 0.075, color="transparent", material="glass",
                     affordances=["graspable", "fragile", "movable"])],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b106",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "口语+简写:'玻璃bei'='玻璃杯', '行不'=可否"},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B107", "category": "mixed_colloquial",
    "instruction": "哎 就是那个 在那个桌子上的内个杯子 帮我拿一下",
    "objects": [
        _obj("obj-b107a", "cup", 0.40, 0.05, 0.06, color="white", material="plastic"),
        _obj("obj-b107b", "table", 0.40, 0.00, 0.00, w=0.60, h=0.03, d=0.40, color="brown", material="wood",
             affordances=["support_surface"]),
    ],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b107a",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "口语冗余:'哎/就是那个/内个/帮我拿一下', 核心语义:拿桌子上的杯子"},
    "severity": {"wrong_target": "CRITICAL", "execution_allowed_with_missing_roles": "CRITICAL"},
})

CASES.append({
    "case_id": "B108", "category": "mixed_colloquial",
    "instruction": "grasp the cup, but don't touch anything else!",
    "objects": [
        _obj("obj-b108a", "cup", 0.35, 0.12, 0.075, color="white", material="plastic"),
        _obj("obj-b108b", "block", 0.30, 0.18, 0.04, w=0.05, h=0.05, d=0.05, color="red", material="wood"),
    ],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b108a",
                 "avoid_objects": ["obj-b108b"],
                 "notes": "English + negation. 'anything else' should ground to all non-target objects.",
                 "execution_allowed": True},
    "severity": {"wrong_target": "CRITICAL", "ignored_negation": "CRITICAL"},
})

CASES.append({
    "case_id": "B109", "category": "mixed_colloquial",
    "instruction": "抓抓杯子",
    "objects": [_obj("obj-b109", "cup", 0.35, 0.12, 0.075, color="white", material="plastic")],
    "expected": {"action": "GRASP", "theme_entity_id": "obj-b109",
                 "notes": "叠词'抓抓'=抓 — 应识别为GRASP", "execution_allowed": True},
    "severity": {"execution_blocked_falsely": "HIGH"},
})

CASES.append({
    "case_id": "B110", "category": "mixed_colloquial",
    "instruction": "s'il vous plait, 把那个bouteille拿过来",
    "objects": [_obj("obj-b110", "bottle", 0.20, 0.08, 0.04, w=0.04, h=0.09, d=0.04, color="green", material="plastic")],
    "expected": {"action": "FETCH", "theme_entity_id": "obj-b110",
                 "missing_roles": ["delivery_pose_or_fetch_zone"],
                 "execution_allowed": False,
                 "notes": "法语'请'+中文+法语'bouteille'(瓶子) — 跨语言混合. 核心语义应有兜底."},
    "severity": {"execution_allowed_with_missing_roles": "CRITICAL"},
})

# ══════════════════════════════════════════════════════════════
# Write JSON
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    output_path = os.path.join(os.path.dirname(__file__), "blind_dataset.json")
    dataset = {
        "meta": {
            "version": "1.0.0",
            "description": "Blind evaluation dataset — 110 cases designed independently from implementation",
            "total_cases": len(CASES),
            "severity_levels": {
                "CRITICAL": "Wrong target, ignored negation, fabricated objects/IDs, execution allowed with missing required roles, safety bypass",
                "HIGH": "Wrong action type, wrong numeric constraint value, bypassed constraint",
                "MEDIUM": "Wrong role assignment, wrong manner/motion state",
                "LOW": "Schema issues, missing optional metadata",
            },
        },
        "cases": CASES,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    print(f"Written {len(CASES)} cases to {output_path}")
