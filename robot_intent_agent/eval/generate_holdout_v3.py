#!/usr/bin/env python3
"""
Generate holdout_v3 evaluation dataset — 120+ cases across 14 categories.

This dataset is FROZEN after generation. Expected values are specified
independently — NOT derived from current system output.

Usage:
    python -m robot_intent_agent.eval.generate_holdout_v3

Output:
    robot_intent_agent/eval/holdout_v3.json
    robot_intent_agent/eval/holdout_v3.sha256
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════════════════════
# Object templates — reusable scene object definitions
# ══════════════════════════════════════════════════════════════

def _obj(oid: str, category: str, x: float, y: float, z: float,
         w: float, h: float, d: float, color: str = "unknown",
         material: str = "plastic", affordances: List[str] | None = None,
         state: str = "stationary") -> Dict[str, Any]:
    """Create a perception-style object entry."""
    affs = affordances or ["graspable", "movable"]
    return {
        "object_id": oid,
        "category_candidates": [{"name": category, "score": 0.95}],
        "pose": {"position": {"x": x, "y": y, "z": z}},
        "geometry": {"size": {"width": w, "height": h, "depth": d}},
        "appearance": {"color": color, "material": material},
        "affordances": affs,
        "tracking": {"state": state, "confidence": 0.9,
                     "velocity": {"x": 0, "y": 0, "z": 0}, "velocity_confidence": 0},
    }


# ══════════════════════════════════════════════════════════════
# Dataset builder
# ══════════════════════════════════════════════════════════════

def build_holdout_v3() -> Dict[str, Any]:
    """Build the complete holdout_v3 dataset."""
    cases: List[Dict[str, Any]] = []
    case_id = [0]

    def _next_id() -> str:
        case_id[0] += 1
        return f"HV{case_id[0]:04d}"

    # ── 1. simple_action (12 cases) ──────────────────────────
    cat = "simple_action"
    # HV0001: Basic grasp
    cases.append({
        "case_id": _next_id(), "category": cat,
        "instruction": "抓住杯子",
        "objects": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
        "expected": {
            "accepted_actions": ["GRASP"],
            "required_semantics": {"theme_category": "cup"},
            "required_roles": ["theme"],
        },
    })
    # HV0002: Fetch
    cases.append({
        "case_id": _next_id(), "category": cat,
        "instruction": "把盒子拿过来",
        "objects": [_obj("box-1", "box", 0.25, -0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")],
        "expected": {
            "accepted_actions": ["FETCH"],
            "required_semantics": {"theme_category": "box"},
            "required_roles": ["theme"],
        },
    })
    # HV0003: Place
    cases.append({
        "case_id": _next_id(), "category": cat,
        "instruction": "把杯子放到桌子上",
        "objects": [
            _obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
            _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood",
                 ["fixed", "support_surface"], "stationary"),
        ],
        "expected": {
            "accepted_actions": ["PLACE"],
            "required_semantics": {"theme_category": "cup", "support_surface_category": "table"},
            "required_roles": ["theme", "support_surface"],
        },
    })
    # HV0004: Handover
    cases.append({
        "case_id": _next_id(), "category": cat,
        "instruction": "把药瓶递给用户",
        "objects": [_obj("bottle-1", "medicine_bottle", 0.20, 0.05, 0.04, 0.04, 0.08, 0.04, "white")],
        "expected": {
            "accepted_actions": ["HANDOVER"],
            "required_semantics": {"theme_category": "medicine_bottle", "recipient_present": True},
            "required_roles": ["theme", "recipient"],
        },
    })
    # HV0005: Dynamic grasp
    cases.append({
        "case_id": _next_id(), "category": cat,
        "instruction": "抓住正在移动的红色小球",
        "objects": [_obj("ball-1", "ball", 0.35, 0.00, 0.02, 0.03, 0.03, 0.03, "red", "rubber",
                         ["graspable", "movable"], "moving")],
        "expected": {
            "accepted_actions": ["DYNAMIC_GRASP", "GRASP"],
            "required_semantics": {"theme_category": "ball", "theme_color": "red"},
            "required_roles": ["theme"],
        },
    })
    # HV0006-0012: More simple actions
    for i, (instr, objs_list, exp_action, theme_cat) in enumerate([
        ("抓住蓝色的方块", [_obj("block-blue", "block", 0.30, 0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood")], "GRASP", "block"),
        ("把托盘上的杯子拿过来", [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
                              _obj("tray-1", "tray", 0.00, 0.00, 0.00, 0.30, 0.02, 0.20, "gray", "plastic", ["fixed", "support_surface"])], "FETCH", "cup"),
        ("把书放到书架上", [_obj("book-1", "book", 0.20, 0.05, 0.02, 0.15, 0.02, 0.20, "blue", "paper"),
                         _obj("shelf-1", "shelf", 0.00, -0.30, 0.00, 0.40, 0.02, 0.25, "brown", "wood", ["fixed", "support_surface"])], "PLACE", "book"),
        ("抓住瓶子", [_obj("bottle-1", "bottle", 0.25, -0.05, 0.05, 0.05, 0.12, 0.05, "green", "glass")], "GRASP", "bottle"),
        ("把设备推过来", [_obj("device-1", "device", 0.40, 0.00, 0.04, 0.10, 0.06, 0.08, "gray", "metal")], "FETCH", "device"),
        ("抓住金属零件", [_obj("metal-1", "metal_part", 0.30, 0.05, 0.03, 0.04, 0.03, 0.06, "silver", "metal")], "GRASP", "metal_part"),
        ("把针捡起来", [_obj("needle-1", "needle", 0.15, 0.00, 0.005, 0.002, 0.001, 0.03, "silver", "metal")], "GRASP", "needle"),
    ]):
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr,
            "objects": objs_list,
            "expected": {
                "accepted_actions": [exp_action],
                "required_semantics": {"theme_category": theme_cat},
                "required_roles": ["theme"],
            },
        })

    # ── 2. role_binding (12 cases) ───────────────────────────
    cat = "role_binding"
    role_cases = [
        ("把蓝色方块放到红色方块上面", "PLACE",
         [_obj("block-blue", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood"),
          _obj("block-red", "block", 0.20, 0.10, 0.08, 0.05, 0.05, 0.05, "red", "wood")],
         {"theme_category": "block", "theme_color": "blue", "support_surface_color": "red"}),
        ("把杯子从托盘上拿过来交给用户", "HANDOVER",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("tray-1", "tray", 0.00, 0.00, 0.00, 0.30, 0.02, 0.20, "gray", "plastic", ["fixed", "support_surface"])],
         {"theme_category": "cup", "recipient_present": True}),
        ("把杯子放到桌子左边", "PLACE",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"])],
         {"theme_category": "cup", "support_surface_category": "table"}),
        ("把红色药瓶递给用户", "HANDOVER",
         [_obj("bottle-red", "medicine_bottle", 0.20, 0.05, 0.04, 0.04, 0.08, 0.04, "red")],
         {"theme_category": "medicine_bottle", "theme_color": "red", "recipient_present": True}),
        ("抓住桌上的杯子", "GRASP",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"])],
         {"theme_category": "cup"}),
        ("把柜子里的设备搬出来", "TRANSFER",
         [_obj("device-1", "device", 0.40, 0.00, 0.04, 0.10, 0.06, 0.08, "gray", "metal"),
          _obj("cabinet-1", "cabinet", 0.00, -0.50, 0.00, 0.60, 0.80, 0.40, "gray", "metal", ["fixed"])],
         {"theme_category": "device", "source_present": True}),
        ("把托盘端到厨房", "TRANSFER",
         [_obj("tray-1", "tray", 0.00, 0.00, 0.00, 0.30, 0.02, 0.20, "gray", "plastic", ["support_surface", "movable"])],
         {"theme_category": "tray"}),
        ("把杯子端给客人", "HANDOVER",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         {"theme_category": "cup", "recipient_present": True}),
        ("把药瓶放回架子上", "PLACE",
         [_obj("bottle-1", "medicine_bottle", 0.20, 0.05, 0.04, 0.04, 0.08, 0.04, "white"),
          _obj("shelf-1", "shelf", 0.00, -0.30, 0.00, 0.40, 0.02, 0.25, "brown", "wood", ["fixed", "support_surface"])],
         {"theme_category": "medicine_bottle", "support_surface_category": "shelf"}),
        ("帮我拿一下那个盒子", "FETCH",
         [_obj("box-1", "box", 0.25, -0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")],
         {"theme_category": "box"}),
        ("请把杯子放到台面上", "PLACE",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"])],
         {"theme_category": "cup", "support_surface_category": "table"}),
        ("递一下那个玻璃杯", "HANDOVER",
         [_obj("cup-glass", "glass_cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "transparent", "glass")],
         {"theme_category": "glass_cup", "recipient_present": True}),
    ]
    for instr, exp_action, objs, semantics in role_cases:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": [exp_action],
                "required_semantics": semantics,
                "required_roles": list(semantics.keys()) if "recipient_present" not in semantics else ["theme", "recipient"],
            },
        })

    # ── 3. multi_object (12 cases) ───────────────────────────
    cat = "multi_object"
    multi_cases = [
        ("抓住红色杯子", "GRASP", "red",
         [_obj("cup-red", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "red"),
          _obj("cup-blue", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "blue")]),
        ("抓住蓝色杯子", "GRASP", "blue",
         [_obj("cup-red", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "red"),
          _obj("cup-blue", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "blue")]),
        ("把大盒子拿过来", "FETCH", "large",
         [_obj("box-big", "box", 0.20, 0.10, 0.05, 0.10, 0.08, 0.10, "brown", "cardboard"),
          _obj("box-small", "box", 0.35, -0.10, 0.03, 0.04, 0.04, 0.04, "brown", "cardboard")]),
        ("抓住那个小的", "GRASP", "small",
         [_obj("block-big", "block", 0.20, 0.10, 0.03, 0.08, 0.08, 0.08, "red", "wood"),
          _obj("block-small", "block", 0.35, -0.10, 0.02, 0.03, 0.03, 0.03, "red", "wood")]),
        ("抓住左边的杯子", "GRASP", "left",
         [_obj("cup-left", "cup", 0.30, 0.20, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-right", "cup", 0.30, -0.20, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住右边的瓶子", "GRASP", "right",
         [_obj("bottle-left", "bottle", 0.30, 0.15, 0.05, 0.05, 0.12, 0.05, "green", "glass"),
          _obj("bottle-right", "bottle", 0.30, -0.15, 0.05, 0.05, 0.12, 0.05, "green", "glass")]),
        ("抓住那个红色的方块，不要蓝色的", "GRASP", "red",
         [_obj("block-red", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-blue", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood")]),
        ("抓住前面那个瓶子", "GRASP", "front",
         [_obj("bottle-front", "bottle", 0.40, 0.00, 0.05, 0.05, 0.12, 0.05, "green", "glass"),
          _obj("bottle-back", "bottle", 0.20, 0.00, 0.05, 0.05, 0.12, 0.05, "green", "glass")]),
        ("抓住近处的杯子", "GRASP", "near",
         [_obj("cup-near", "cup", 0.20, 0.05, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-far", "cup", 0.50, -0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住远处的盒子", "GRASP", "far",
         [_obj("box-near", "box", 0.20, 0.05, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard"),
          _obj("box-far", "box", 0.55, -0.20, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")]),
        ("抓住那个玻璃杯，不是塑料的", "GRASP", "glass",
         [_obj("cup-glass", "glass_cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "transparent", "glass"),
          _obj("cup-plastic", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white", "plastic")]),
        ("抓住移动中的球，不是静止的", "DYNAMIC_GRASP", "moving",
         [_obj("ball-moving", "ball", 0.30, 0.10, 0.02, 0.03, 0.03, 0.03, "red", "rubber", ["graspable", "movable"], "moving"),
          _obj("ball-static", "ball", 0.30, -0.10, 0.02, 0.03, 0.03, 0.03, "blue", "rubber")]),
    ]
    for instr, exp_action, key_attr, objs in multi_cases:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": [exp_action],
                "required_semantics": {"disambiguation_key": key_attr},
                "required_roles": ["theme"],
            },
        })

    # ── 4. size_color_spatial (12 cases) ─────────────────────
    cat = "size_color_spatial"
    scs_cases = [
        ("抓住最大的瓶子", "GRASP",
         [_obj("bottle-s", "bottle", 0.20, 0.10, 0.03, 0.03, 0.06, 0.03, "green", "plastic"),
          _obj("bottle-m", "bottle", 0.35, 0.00, 0.04, 0.04, 0.09, 0.04, "green", "plastic"),
          _obj("bottle-l", "bottle", 0.50, -0.10, 0.05, 0.05, 0.12, 0.05, "green", "plastic")]),
        ("抓住最小的瓶子", "GRASP",
         [_obj("bottle-s", "bottle", 0.20, 0.10, 0.03, 0.03, 0.06, 0.03, "green", "plastic"),
          _obj("bottle-m", "bottle", 0.35, 0.00, 0.04, 0.04, 0.09, 0.04, "green", "plastic"),
          _obj("bottle-l", "bottle", 0.50, -0.10, 0.05, 0.05, 0.12, 0.05, "green", "plastic")]),
        ("抓住那个又大又红的方块", "GRASP",
         [_obj("block-big-red", "block", 0.20, 0.10, 0.03, 0.08, 0.08, 0.08, "red", "wood"),
          _obj("block-small-red", "block", 0.35, -0.10, 0.02, 0.03, 0.03, 0.03, "red", "wood")]),
        ("抓住高处的那个杯子", "GRASP",
         [_obj("cup-high", "cup", 0.30, 0.10, 0.15, 0.07, 0.10, 0.07, "white"),
          _obj("cup-low", "cup", 0.30, -0.10, 0.05, 0.07, 0.10, 0.07, "white")]),
        ("抓住最前面的方块", "GRASP",
         [_obj("block-front", "block", 0.45, 0.00, 0.03, 0.05, 0.05, 0.05, "blue", "wood"),
          _obj("block-back", "block", 0.15, 0.00, 0.03, 0.05, 0.05, 0.05, "blue", "wood")]),
        ("把透明的杯子拿过来", "FETCH",
         [_obj("cup-clear", "glass_cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "transparent", "glass"),
          _obj("cup-opaque", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white", "plastic")]),
        ("抓住深色的那个瓶子", "GRASP",
         [_obj("bottle-dark", "bottle", 0.30, 0.10, 0.05, 0.05, 0.12, 0.05, "black", "glass"),
          _obj("bottle-light", "bottle", 0.30, -0.10, 0.05, 0.05, 0.12, 0.05, "white", "plastic")]),
        ("抓住那个白色的方块", "GRASP",
         [_obj("block-white", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "white", "wood"),
          _obj("block-black", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "black", "wood")]),
        ("抓住那个金属的而不是塑料的", "GRASP",
         [_obj("cup-metal", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "silver", "metal"),
          _obj("cup-plastic", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white", "plastic")]),
        ("抓住最远的那个杯子", "GRASP",
         [_obj("cup-near", "cup", 0.15, 0.05, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-mid", "cup", 0.30, 0.00, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-far", "cup", 0.55, -0.15, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住低处最近的那个杯子", "GRASP",
         [_obj("cup-low-near", "cup", 0.20, 0.05, 0.03, 0.07, 0.10, 0.07, "white"),
          _obj("cup-high-far", "cup", 0.50, -0.10, 0.15, 0.07, 0.10, 0.07, "white")]),
        ("把黄色杯子放到蓝色杯子旁边", "PLACE",
         [_obj("cup-yellow", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "yellow"),
          _obj("cup-blue", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "blue")],
         {"theme_color": "yellow"}),
    ]
    for item in scs_cases:
        instr, exp_action = item[0], item[1]
        objs = item[2]
        extra_semantics = item[3] if len(item) > 3 else {}
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": [exp_action],
                "required_semantics": {"multi_attribute_disambiguation": True, **extra_semantics},
                "required_roles": ["theme"],
            },
        })

    # ── 5. ordinal_reference (10 cases) ──────────────────────
    cat = "ordinal_reference"
    ord_cases = [
        ("抓住第一个杯子", "first",
         [_obj("cup-1", "cup", 0.20, 0.20, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, 0.00, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-3", "cup", 0.40, -0.20, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住第二个杯子", "second",
         [_obj("cup-1", "cup", 0.20, 0.20, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, 0.00, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-3", "cup", 0.40, -0.20, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住第三个杯子", "third",
         [_obj("cup-1", "cup", 0.20, 0.20, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, 0.00, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-3", "cup", 0.40, -0.20, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住中间那个杯子", "middle",
         [_obj("cup-1", "cup", 0.20, 0.20, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, 0.00, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-3", "cup", 0.40, -0.20, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住最后一个盒子", "last",
         [_obj("box-1", "box", 0.20, 0.20, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard"),
          _obj("box-2", "box", 0.30, 0.00, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard"),
          _obj("box-3", "box", 0.40, -0.20, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")]),
        ("把第一个方块放到第二个方块上面", "first",
         [_obj("block-1", "block", 0.20, 0.15, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-2", "block", 0.20, 0.15, 0.08, 0.05, 0.05, 0.05, "blue", "wood")]),
        ("抓住右数第二个瓶子", "second_from_right",
         [_obj("bottle-1", "bottle", 0.30, 0.20, 0.05, 0.05, 0.12, 0.05, "green", "glass"),
          _obj("bottle-2", "bottle", 0.30, 0.00, 0.05, 0.05, 0.12, 0.05, "green", "glass"),
          _obj("bottle-3", "bottle", 0.30, -0.20, 0.05, 0.05, 0.12, 0.05, "green", "glass")]),
        ("抓住中间的方块", "middle",
         [_obj("block-l", "block", 0.20, 0.15, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-m", "block", 0.30, 0.00, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-r", "block", 0.40, -0.15, 0.03, 0.05, 0.05, 0.05, "red", "wood")]),
        ("抓住首个遇到的瓶子", "first",
         [_obj("bottle-near", "bottle", 0.20, 0.05, 0.05, 0.05, 0.12, 0.05, "green", "glass"),
          _obj("bottle-far", "bottle", 0.50, -0.10, 0.05, 0.05, 0.12, 0.05, "green", "glass")]),
        ("把最后面的那个盒子递给我", "last",
         [_obj("box-front", "box", 0.40, 0.00, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard"),
          _obj("box-back", "box", 0.10, 0.00, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")]),
    ]
    for instr, ord_key, objs in ord_cases:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": ["GRASP", "FETCH", "PLACE"],
                "required_semantics": {"ordinal_reference": ord_key},
                "required_roles": ["theme"],
            },
        })

    # ── 6-14: Remaining categories ──
    # Generate remaining cases programmatically
    _add_negation_cases(cases, _next_id)
    _add_if_else_cases(cases, _next_id)
    _add_unless_cases(cases, _next_id)
    _add_sequence_cases(cases, _next_id)
    _add_numeric_cases(cases, _next_id)
    _add_robot_state_cases(cases, _next_id)
    _add_ambiguity_cases(cases, _next_id)
    _add_missing_role_cases(cases, _next_id)
    _add_conflicting_input_cases(cases, _next_id)

    return {
        "meta": {
            "name": "holdout_v3",
            "version": "3.0.0",
            "created": "2026-07-21",
            "description": "Final holdout evaluation dataset — FROZEN after generation",
            "total_cases": len(cases),
            "categories": list(set(c["category"] for c in cases)),
        },
        "cases": cases,
        "metamorphic_tests": _build_metamorphic_tests(),
    }


def _add_negation_cases(cases, _next_id):
    cat = "negation"
    neg_objs = [
        _obj("box-1", "box", 0.30, 0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard"),
        _obj("cup-glass", "glass_cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "transparent", "glass", ["graspable", "fragile", "movable"]),
    ]
    neg_objs2 = [
        _obj("block-red", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
        _obj("block-blue", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood"),
    ]
    neg_objs3 = [
        _obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
        _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"]),
    ]
    for instr, objs, avoid_cat in [
        ("把盒子拿过来，别碰玻璃杯", neg_objs, "glass_cup"),
        ("不要碰玻璃杯，把盒子拿过来", neg_objs, "glass_cup"),
        ("千万别碰玻璃杯，去拿盒子", neg_objs, "glass_cup"),
        ("绕开玻璃杯，把盒子取过来", neg_objs, "glass_cup"),
        ("避开玻璃杯抓盒子", neg_objs, "glass_cup"),
        ("不要碰那个红色的，把蓝色的拿过来", neg_objs2, "red_block"),
        ("别碰红色的方块，拿蓝色的", neg_objs2, "red_block"),
        ("禁止接触红色方块，只拿蓝色的", neg_objs2, "red_block"),
        ("抓住杯子，但我不想让你碰桌子", neg_objs3, "table"),
        ("把杯子拿过来，但不要碰桌子", neg_objs3, "table"),
        ("移动时避开桌子抓杯子", neg_objs3, "table"),
        ("抓住杯子，禁止触碰桌子", neg_objs3, "table"),
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": ["FETCH", "GRASP"],
                "required_semantics": {"avoid_present": True, "avoid_category": avoid_cat},
                "forbidden_semantics": {"theme_is_avoided": True},
                "required_roles": ["theme"],
            },
        })

def _add_if_else_cases(cases, _next_id):
    cat = "if_else"
    for instr, objs in [
        ("如果看到红色药瓶就先拿它，否则拿蓝色盒子",
         [_obj("bottle-red", "medicine_bottle", 0.20, 0.10, 0.04, 0.04, 0.08, 0.04, "red"),
          _obj("box-blue", "box", 0.35, -0.10, 0.04, 0.06, 0.06, 0.06, "blue", "cardboard")]),
        ("如果杯子在桌子上就抓取，否则不抓",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"])]),
        ("如果看到红色药瓶就抓取",
         [_obj("bottle-red", "medicine_bottle", 0.20, 0.10, 0.04, 0.04, 0.08, 0.04, "red")]),
        ("看到红色方块就抓，要不就抓蓝色的",
         [_obj("block-red", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-blue", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood")]),
        ("如果夹爪为空就抓杯子",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("只有杯子可见时才抓取",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("如果红色药瓶可见就递给我，否则递蓝色盒子",
         [_obj("bottle-red", "medicine_bottle", 0.20, 0.10, 0.04, 0.04, 0.08, 0.04, "red"),
          _obj("box-blue", "box", 0.35, -0.10, 0.04, 0.06, 0.06, 0.06, "blue", "cardboard")]),
        ("如果设备已经归位就抓取零件",
         [_obj("metal-1", "metal_part", 0.30, 0.05, 0.03, 0.04, 0.03, 0.06, "silver", "metal")]),
        ("如果目标在移动就不要抓，否则抓取",
         [_obj("ball-1", "ball", 0.35, 0.00, 0.02, 0.03, 0.03, 0.03, "red", "rubber", ["graspable", "movable"], "moving")]),
        ("如果前面没有障碍就移动到目标",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": ["GRASP", "FETCH", "HANDOVER"],
                "required_semantics": {"conditional_present": True},
                "accepted_plan_statuses": ["NEEDS_CLARIFICATION", "READY", "READY_WITH_SAFE_SUBSTITUTION"],
            },
        })

def _add_unless_cases(cases, _next_id):
    cat = "unless"
    for instr in [
        "除非夹爪是空的，否则不要抓取",
        "除非夹爪已抓取，否则继续尝试",
        "除非设备已归位，否则不能移动",
        "除非确认安全，否则不要执行",
        "除非看到目标，要不然不要动",
        "除非已经定位，不然先搜索",
        "除非夹爪为空，否则不要抓任何东西",
        "除非目标静止，否则等待",
        "除非工作区域已清理，否则先清理",
        "除非收到确认信号，否则保持等待",
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr,
            "objects": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
            "expected": {
                "accepted_actions": ["GRASP", "CUSTOM"],
                "required_semantics": {"conditional_present": True, "type": "UNLESS"},
                "accepted_plan_statuses": ["NEEDS_CLARIFICATION", "BLOCKED", "READY"],
            },
        })

def _add_sequence_cases(cases, _next_id):
    cat = "sequence"
    seq_objs = [
        _obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
        _obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"]),
    ]
    for instr in [
        "先抓住杯子再放到桌子上",
        "抓住杯子然后放到桌子上",
        "先确认位置再抓取",
        "拿起杯子然后递给我",
        "打开夹爪以后再抓取",
        "先检查状态然后执行任务",
        "等待目标稳定后再抓取",
        "抓住方块并堆叠到另一个上面",
        "先移动到安全高度再接近目标",
        "把杯子拿起来以后放到托盘上",
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": seq_objs,
            "expected": {
                "accepted_actions": ["GRASP", "PLACE", "FETCH", "HANDOVER", "CUSTOM"],
                "required_semantics": {"sequence_present": True},
                "accepted_plan_statuses": ["NEEDS_CLARIFICATION", "READY", "READY_WITH_SAFE_SUBSTITUTION"],
            },
        })

def _add_numeric_cases(cases, _next_id):
    cat = "numeric_constraints"
    for instr, force_val, force_op in [
        ("用5N抓住杯子", 5.0, "exact"),
        ("以不超过3N的力度抓取玻璃杯", 3.0, "max"),
        ("至少用2N的力量抓住物体", 2.0, "min"),
        ("用0.15m/s的速度移动杯子", None, None),
        ("以不超过0.2m/s的速度搬运", None, None),
        ("用3N到8N的力量抓取", None, "range"),
        ("轻轻地抓住杯子", None, None),
        ("慢慢移动到目标", None, None),
        ("快速抓取物体", None, None),
        ("以最大力度抓取", None, "max"),
    ]:
        semantics = {"numeric_constraint_present": True}
        if force_val is not None:
            semantics["force_value"] = force_val
        if force_op is not None:
            semantics["force_operator"] = force_op
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr,
            "objects": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white", "glass", ["graspable", "fragile", "movable"])],
            "expected": {
                "accepted_actions": ["GRASP", "FETCH"],
                "required_semantics": semantics,
            },
        })

def _add_robot_state_cases(cases, _next_id):
    cat = "robot_state"
    for instr, expected_semantics in [
        ("先确认夹爪是空的，然后抓住杯子", {"robot_state_check": "gripper_empty"}),
        ("确认夹爪已张开再抓取", {"robot_state_check": "gripper_open"}),
        ("只有归位后才能执行任务", {"robot_state_check": "is_homed"}),
        ("检查夹爪是否已经抓住物体", {"robot_state_check": "gripper_has_object"}),
        ("确认目标静止后再抓取", {"robot_state_check": "object_stable"}),
        ("等目标停止移动再抓", {"robot_state_check": "object_moving"}),
        ("确认能力可用后执行", {"robot_state_check": "capability_available"}),
        ("检查工作空间是否安全", {"robot_state_check": "workspace_safe"}),
        ("确认夹爪为空并且已归位", {"robot_state_check": "gripper_empty_and_homed"}),
        ("等所有条件满足后再执行", {"robot_state_check": "all_conditions"}),
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr,
            "objects": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
            "expected": {
                "accepted_actions": ["GRASP", "CUSTOM"],
                "required_semantics": expected_semantics,
                "accepted_plan_statuses": ["NEEDS_CLARIFICATION", "READY", "READY_WITH_SAFE_SUBSTITUTION"],
            },
        })

def _add_ambiguity_cases(cases, _next_id):
    cat = "ambiguity"
    for instr, objs in [
        ("抓住杯子",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住那个杯子",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("把盒子拿过来",
         [_obj("box-1", "box", 0.20, 0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard"),
          _obj("box-2", "box", 0.35, -0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")]),
        ("抓住那个方块",
         [_obj("block-1", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-2", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood")]),
        ("抓住那个东西",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("box-1", "box", 0.35, -0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")]),
        ("把这个拿过来",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("box-1", "box", 0.35, -0.10, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")]),
        ("抓住中间那个",
         [_obj("cup-1", "cup", 0.20, 0.15, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, 0.00, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-3", "cup", 0.40, -0.15, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-4", "cup", 0.50, -0.30, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("把那个拿给我",
         [_obj("bottle-1", "bottle", 0.20, 0.05, 0.04, 0.04, 0.08, 0.04, "white"),
          _obj("cup-1", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")]),
        ("抓住那边的那个",
         [_obj("block-1", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
          _obj("block-2", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood")]),
        ("随便拿一个杯子过来",
         [_obj("cup-red", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "red"),
          _obj("cup-blue", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "blue")]),
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_actions": ["GRASP", "FETCH"],
                "required_semantics": {"ambiguous": True},
                "accepted_plan_statuses": ["NEEDS_CLARIFICATION", "BLOCKED"],
            },
        })

def _add_missing_role_cases(cases, _next_id):
    cat = "missing_role"
    for instr, missing in [
        ("把杯子放到上面", "support_surface"),
        ("把东西递给他", "theme"),
        ("放到桌子上", "theme"),
        ("抓住它", "theme"),
        ("移动一下", "theme"),
        ("把它放好", "theme_and_destination"),
        ("拿过来", "theme"),
        ("递给用户", "theme"),
        ("搬到那边去", "theme_and_destination"),
        ("帮我放一下", "theme_and_support_surface"),
    ]:
        objs = [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")]
        if "support_surface" in missing or "destination" in missing:
            objs.append(_obj("table-1", "table", 0.00, 0.00, 0.00, 0.50, 0.03, 0.30, "brown", "wood", ["fixed", "support_surface"]))
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_plan_statuses": ["NEEDS_CLARIFICATION", "BLOCKED"],
                "required_semantics": {"missing_role": missing},
                "forbidden_semantics": {"execution_allowed": True},
            },
        })

def _add_conflicting_input_cases(cases, _next_id):
    cat = "conflicting_input"
    for instr, objs, conflict_type in [
        ("用100N的力量抓住玻璃杯",
         [_obj("cup-glass", "glass_cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "transparent", "glass", ["graspable", "fragile", "movable"])],
         "force_vs_fragility"),
        ("用50N抓住精密仪器",
         [_obj("device-1", "device", 0.40, 0.00, 0.04, 0.10, 0.06, 0.08, "gray", "metal", ["graspable", "fragile", "movable"])],
         "force_vs_fragility"),
        ("把杯子放到杯子上",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         "support_surface_infeasible"),
        ("抓住杯子并避开杯子",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
          _obj("cup-2", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         "theme_avoid_conflict"),
        ("以0.5m/s的速度抓取",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         "velocity_exceeds_limit"),
        ("把重物放到精密仪器旁边",
         [_obj("weight-1", "weight", 0.20, 0.10, 0.04, 0.10, 0.08, 0.10, "black", "metal", ["graspable", "movable"]),
          _obj("device-1", "device", 0.40, 0.00, 0.04, 0.10, 0.06, 0.08, "gray", "metal", ["graspable", "fragile", "movable"])],
         "heavy_near_fragile"),
        ("抓住不存在的物体",
         [], "no_objects"),
        ("用-5N的力量抓取",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         "negative_force"),
        ("用NaN牛顿抓取",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         "invalid_numeric"),
        ("同时抓住和释放同一个杯子",
         [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
         "contradictory_actions"),
    ]:
        cases.append({
            "case_id": _next_id(), "category": cat,
            "instruction": instr, "objects": objs,
            "expected": {
                "accepted_plan_statuses": ["BLOCKED", "NEEDS_CLARIFICATION"],
                "required_semantics": {"conflict_type": conflict_type},
                "forbidden_semantics": {"execution_allowed": True},
            },
        })


def _build_metamorphic_tests() -> List[Dict[str, Any]]:
    """Build metamorphic test variants."""
    return [
        {
            "id": "META_NEG_001",
            "description": "Negation variants — same core semantics",
            "base_instruction": "不要碰花瓶",
            "objects": [_obj("vase-1", "vase", 0.30, 0.10, 0.10, 0.08, 0.15, 0.08, "white", "ceramic", ["graspable", "fragile", "movable"]),
                       _obj("cup-1", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")],
            "variants": [
                "别碰花瓶",
                "绕开花瓶",
                "花瓶绝对不能接触",
                "移动时避开白色花瓶",
                "禁止接触花瓶",
            ],
            "expected_invariant": {"avoid_present": True, "avoid_category": "vase"},
        },
        {
            "id": "META_SWAP_001",
            "description": "Color swap — negation must follow object, not keyword position",
            "base_instruction": "不要碰红色方块，把蓝色方块拿来",
            "objects": [_obj("block-red", "block", 0.20, 0.10, 0.03, 0.05, 0.05, 0.05, "red", "wood"),
                       _obj("block-blue", "block", 0.35, -0.10, 0.03, 0.05, 0.05, 0.05, "blue", "wood")],
            "variants": [
                "不要碰蓝色方块，把红色方块拿来",
            ],
            "expected_invariant_swap": {"first": {"theme_color": "blue", "avoid_color": "red"},
                                        "second": {"theme_color": "red", "avoid_color": "blue"}},
        },
        {
            "id": "META_ORDER_001",
            "description": "Object order invariance",
            "base_instruction": "抓住红色杯子",
            "objects_original": [_obj("cup-blue", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "blue"),
                                _obj("cup-red", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "red")],
            "objects_reversed": [_obj("cup-red", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "red"),
                                _obj("cup-blue", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "blue")],
            "expected_invariant": {"theme_color": "red"},
        },
        {
            "id": "META_EXTRA_001",
            "description": "Extra unrelated object — result unchanged",
            "base_instruction": "抓住杯子",
            "objects_base": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
            "objects_with_extra": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
                                  _obj("box-extra", "box", 0.50, -0.30, 0.04, 0.06, 0.06, 0.06, "brown", "cardboard")],
            "expected_invariant": {"theme_category": "cup"},
        },
        {
            "id": "META_AMBIG_001",
            "description": "Add identical object → ambiguous if no disambiguation",
            "base_instruction": "抓住杯子",
            "objects_unambiguous": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white")],
            "objects_ambiguous": [_obj("cup-1", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "white"),
                                 _obj("cup-2", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "white")],
            "unambiguous_expected": {"accepted_plan_statuses": ["READY", "READY_WITH_SAFE_SUBSTITUTION"]},
            "ambiguous_expected": {"accepted_plan_statuses": ["NEEDS_CLARIFICATION", "BLOCKED"]},
        },
        {
            "id": "META_COLOR_SWAP_001",
            "description": "Swap object colors → grounding must follow facts",
            "base_instruction": "抓住红色杯子",
            "objects_red_left": [_obj("cup-red", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "red"),
                                _obj("cup-blue", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "blue")],
            "objects_colors_swapped": [_obj("cup-blue", "cup", 0.30, 0.10, 0.075, 0.07, 0.10, 0.07, "blue"),
                                      _obj("cup-red", "cup", 0.30, -0.10, 0.075, 0.07, 0.10, 0.07, "red")],
            "expected_different": True,
        },
    ]


# ══════════════════════════════════════════════════════════════
# Main — generate and freeze
# ══════════════════════════════════════════════════════════════

def main():
    output_dir = Path(__file__).parent
    dataset = build_holdout_v3()

    # Write dataset
    output_path = output_dir / "holdout_v3.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    # Compute SHA256
    with open(output_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()

    sha_path = output_dir / "holdout_v3.sha256"
    with open(sha_path, "w", encoding="utf-8") as f:
        f.write(f"{sha}  holdout_v3.json\n")

    print(f"Generated {output_path}")
    print(f"  Total cases: {len(dataset['cases'])}")
    print(f"  Categories: {sorted(set(c['category'] for c in dataset['cases']))}")
    print(f"  Metamorphic tests: {len(dataset['metamorphic_tests'])}")
    print(f"  SHA256: {sha}")
    print(f"\nHOLDOUT V3 IS NOW FROZEN. Do not modify expected values.")
    print(f"SHA256 recorded at: {sha_path}")


if __name__ == "__main__":
    main()
