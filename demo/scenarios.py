"""Demo-only perception fixtures used by the local frontend server.

The fixtures intentionally stay outside ``modules/`` so the demo cannot
silently change the production perception contract.  They are all converted
to the same perception.v1 shape before entering the real pipeline.
"""

from __future__ import annotations

from copy import deepcopy


def _cube(object_id: str, color: str, x: float, z: float = 0.04) -> dict:
    names = {"red": "红色方块", "green": "绿色方块", "blue": "蓝色方块"}
    return {
        "id": object_id,
        "category": names.get(color, "方块"),
        "pose": {"x": x, "y": 0.0, "z": z},
        "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
        "attributes": {"display_name": names.get(color, "方块"), "color": color},
        "execution": {"movable": True, "graspable": True},
    }


def _target(*, valid_destination: bool = True) -> dict:
    return {
        "id": "zone_unstack_target",
        "category": "桌子",
        "pose": {"x": 0.4, "y": 0.0, "z": 0.03},
        "attributes": {
            "purpose": "safe_placement" if valid_destination else "blocked_placement",
            "display_name": "桌子",
        },
        "execution": {
            "movable": False,
            "graspable": False,
            "valid_destination": valid_destination,
        },
    }


def _scene(scene_id: str, objects: list[dict]) -> dict:
    return {
        "schema_version": "perception.v1",
        "scene_id": scene_id,
        "coordinate_frame": "world",
        "objects": objects,
        "execution_context": {"backend": "mock", "scene_revision": "demo-1"},
    }


SCENARIOS: dict[str, dict] = {
    "stacking_cubes": {
        "name": "叠放方块（成功）",
        "description": "绿色方块在红色方块上，桌面目标区有效，可完整跑通 A→B→C→D。",
        "focus": "全链路基准",
        "instruction": "把绿色方块放到桌子上",
        "expected": "SUCCEEDED",
        "scene": _scene("stacking_cubes", [_cube("red_cube", "red", 0.25), _cube("green_cube", "green", 0.25, 0.12), _target()]),
    },
    "single_red_cube": {
        "name": "单方块（成功）",
        "description": "只有一个红色方块，适合演示最短的确定性抓取放置流程。",
        "focus": "A/B/C/D 正常路径",
        "instruction": "把红色方块放到桌子上",
        "expected": "SUCCEEDED",
        "scene": _scene("single_red_cube", [_cube("red_cube", "red", 0.22), _target()]),
    },
    "ambiguous_red_cubes": {
        "name": "同名方块（安全阻断）",
        "description": "场景里有两个红色方块，意图无法唯一绑定时应停在 A，不进入执行。",
        "focus": "A：目标消歧与安全门禁",
        "instruction": "把红色方块放到桌子上",
        "expected": "BLOCKED",
        "scene": _scene("ambiguous_red_cubes", [_cube("red_cube_left", "red", 0.2), _cube("red_cube_right", "red", 0.3), _target()]),
    },
    "no_destination": {
        "name": "缺少目标区（安全阻断）",
        "description": "目标物存在但没有可放置目的地，用于展示约束不足时的安全门禁。",
        "focus": "A：目的地约束检查",
        "instruction": "把红色方块放到安全区",
        "expected": "BLOCKED",
        "scene": _scene("no_destination", [_cube("red_cube", "red", 0.22)]),
    },
    "target_not_found": {
        "name": "目标不存在（澄清）",
        "description": "指令要求蓝色方块，但场景中只有红色方块；A 应要求澄清，不得猜测替代目标。",
        "focus": "A：目标绑定失败",
        "instruction": "把蓝色方块放到桌子上",
        "expected": "BLOCKED",
        "scene": _scene("target_not_found", [_cube("red_cube", "red", 0.22), _target()]),
    },
    "unsupported_push": {
        "name": "推送动作（策略阻断）",
        "description": "A 能理解为 push，但第一阶段 B 只允许 pick_and_place，因此在策略门禁处阻断。",
        "focus": "B：能力边界与策略门禁",
        "instruction": "把红色方块推到桌子上",
        "expected": "BLOCKED",
        "scene": _scene("unsupported_push", [_cube("red_cube", "red", 0.22), _target()]),
    },
    "tracecoder_repair": {
        "name": "TraceCoder 修复后重试（成功）",
        "description": "初始策略故意不带恢复逻辑；C 首次抓取失败后由 D TraceCoder 生成 patch，再交给 C 重试成功。",
        "focus": "D：TraceCoder 策略修复 + C 重试",
        "instruction": "把绿色方块放到桌子上",
        "expected": "SUCCEEDED",
        "tracecoder_repair": True,
        "executor_failures": {"grasp": 1},
        "scene": _scene("tracecoder_repair", [_cube("red_cube", "red", 0.25), _cube("green_cube", "green", 0.25, 0.12), _target()]),
    },
    "grasp_retry_success": {
        "name": "抓取失败后恢复（成功）",
        "description": "B 已经带有动作级恢复逻辑；C 第一次抓取故障后在同一次执行中进入 recovery_1 并成功。",
        "focus": "C：动作级故障恢复 + D：结果复核",
        "instruction": "把绿色方块放到桌子上",
        "expected": "SUCCEEDED",
        "executor_failures": {"grasp": 1},
        "scene": _scene("grasp_retry_success", [_cube("red_cube", "red", 0.25), _cube("green_cube", "green", 0.25, 0.12), _target()]),
    },
    "grasp_safe_stop": {
        "name": "抓取持续失败（安全停止）",
        "description": "主抓取和恢复抓取都失败，C 进入 SAFE_STOP，D 判定不可自动重试。",
        "focus": "C/D：安全停止与不可重试反馈",
        "instruction": "把绿色方块放到桌子上",
        "expected": "SAFE_STOP",
        "executor_failures": {"grasp": 2},
        "scene": _scene("grasp_safe_stop", [_cube("red_cube", "red", 0.25), _cube("green_cube", "green", 0.25, 0.12), _target()]),
    },
    "invalid_destination": {
        "name": "目标区不可放置（执行失败）",
        "description": "A/B 可以形成任务和策略，但 C 发现目标区没有 valid_destination 能力，D 保留失败诊断。",
        "focus": "C：执行能力校验 + D：失败诊断",
        "instruction": "把红色方块放到桌子上",
        "expected": "FAILED",
        "scene": _scene("invalid_destination", [_cube("red_cube", "red", 0.22), _target(valid_destination=False)]),
    },
    "sorting_workcell": {
        "name": "多色方块分拣工作站（综合）",
        "description": "三个待分拣方块和三个定位托盘组成一个小型分拣工位，支持多条目标明确的抓取放置指令。",
        "focus": "综合场景：多目标、多目的地和安全阻断",
        "instruction": "把红色方块放到红色托盘里",
        "expected": "SUCCEEDED",
        "commands": [
            {
                "instruction": "把红色方块放到红色托盘里",
                "expected": "SUCCEEDED",
                "target_id": "red_sort_cube",
                "destination_id": "left_sort_tray",
            },
            {
                "instruction": "把绿色方块放到绿色托盘里",
                "expected": "SUCCEEDED",
                "target_id": "green_sort_cube",
                "destination_id": "middle_sort_tray",
            },
            {
                "instruction": "把蓝色方块放到蓝色托盘里",
                "expected": "SUCCEEDED",
                "target_id": "blue_sort_cube",
                "destination_id": "right_sort_tray",
            },
            {
                "instruction": "把黄色方块放到红色托盘里",
                "expected": "BLOCKED",
            },
            {
                "instruction": "把方块放到红色托盘里",
                "expected": "BLOCKED",
            },
            {
                "instruction": "把红色方块推到红色托盘里",
                "expected": "BLOCKED",
            },
        ],
        # The canonical version is served by modules/perception's Mock adapter.
        "scene": _scene(
            "sorting_workcell",
            [
                _cube("red_sort_cube", "red", 0.16),
                _cube("green_sort_cube", "green", 0.24),
                _cube("blue_sort_cube", "blue", 0.32),
                {
                    "id": "left_sort_tray",
                    "category": "红色托盘",
                    "pose": {"x": 0.16, "y": 0.22, "z": 0.03},
                    "dimensions": {"x": 0.12, "y": 0.12, "z": 0.03},
                    "attributes": {
                        "display_name": "红色托盘",
                        "color": "red",
                        "purpose": "sorting_destination",
                        "slot": "left",
                    },
                    "execution": {
                        "movable": False,
                        "graspable": False,
                        "valid_destination": True,
                    },
                },
                {
                    "id": "middle_sort_tray",
                    "category": "绿色托盘",
                    "pose": {"x": 0.24, "y": 0.22, "z": 0.03},
                    "dimensions": {"x": 0.12, "y": 0.12, "z": 0.03},
                    "attributes": {
                        "display_name": "绿色托盘",
                        "color": "green",
                        "purpose": "sorting_destination",
                        "slot": "middle",
                    },
                    "execution": {
                        "movable": False,
                        "graspable": False,
                        "valid_destination": True,
                    },
                },
                {
                    "id": "right_sort_tray",
                    "category": "蓝色托盘",
                    "pose": {"x": 0.32, "y": 0.22, "z": 0.03},
                    "dimensions": {"x": 0.12, "y": 0.12, "z": 0.03},
                    "attributes": {
                        "display_name": "蓝色托盘",
                        "color": "blue",
                        "purpose": "sorting_destination",
                        "slot": "right",
                    },
                    "execution": {
                        "movable": False,
                        "graspable": False,
                        "valid_destination": True,
                    },
                },
            ],
        ),
    },
}


def list_scenarios() -> list[dict]:
    return [
        {
            "id": scene_id,
            "name": item["name"],
            "description": item["description"],
            "focus": item.get("focus", "闭环演示"),
            "instruction": item["instruction"],
            "expected": item["expected"],
            "commands": item.get("commands", []),
        }
        for scene_id, item in SCENARIOS.items()
    ]


def get_scenario(scene_id: str) -> dict:
    try:
        return deepcopy(SCENARIOS[scene_id])
    except KeyError as exc:
        raise ValueError(f"unsupported demo scene: {scene_id}") from exc
