"""Demo-only perception fixtures used by the local frontend server.

The fixtures intentionally stay outside ``modules/`` so the demo cannot
silently change the production perception contract.  They are all converted
to the same perception.v1 shape before entering the real pipeline.
"""

from __future__ import annotations

from copy import deepcopy

from modules.perception.mock_scene import get_mock_scene
from modules.perception.spatial_context import enrich_spatial_context


def _cube(object_id: str, color: str, x: float, z: float = 0.04, y: float = 0.0) -> dict:
    names = {"red": "红色方块", "green": "绿色方块", "blue": "蓝色方块"}
    return {
        "id": object_id,
        "category": names.get(color, "方块"),
        "pose": {"x": x, "y": y, "z": z},
        "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
        "attributes": {"display_name": names.get(color, "方块"), "color": color},
        "execution": {"movable": True, "graspable": True},
    }


def _target(*, valid_destination: bool = True) -> dict:
    return {
        "id": "zone_unstack_target",
        "category": "桌子",
        "pose": {"x": 0.4, "y": 0.0, "z": 0.03},
        "dimensions": {"x": 0.50, "y": 0.05, "z": 0.50},
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


def _stack_base(object_id: str = "red_cube") -> dict:
    base = _cube(object_id, "red", 0.25)
    base["execution"]["stackable_destination"] = True
    base["execution"]["valid_destination"] = True
    base["attributes"]["purpose"] = "stack_base"
    return base


def _scene(scene_id: str, objects: list[dict]) -> dict:
    scene = {
        "schema_version": "perception.v1",
        "scene_id": scene_id,
        "coordinate_frame": "world",
        "objects": objects,
        "execution_context": {"backend": "mock", "scene_revision": "1"},
        "relations": [],
    }
    return enrich_spatial_context(scene)


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
    "standalone_grasp": {
        "name": "单独抓取（成功）",
        "description": "只执行感知、接近和抓取，验证 pick/grasp 不会被强制拼接成放置动作。",
        "focus": "A/B/C：单独抓取闭环",
        "instruction": "抓起绿色方块",
        "expected": "SUCCEEDED",
        "scene": _scene(
            "standalone_grasp",
            [_cube("red_cube", "red", 0.22), _cube("green_cube", "green", 0.30)],
        ),
    },
    "transfer_green": {
        "name": "搬运方块（成功）",
        "description": "A 输出 transfer，B 复用抓取—搬运—释放原子策略，C 使用既有执行源。",
        "focus": "A/B/C：transfer 约束复用",
        "instruction": "把绿色方块搬运到桌子上",
        "expected": "SUCCEEDED",
        "scene": _scene(
            "transfer_green",
            [_cube("green_cube", "green", 0.22), _target()],
        ),
    },
    "fetch_to_table": {
        "name": "取物到目标区（成功）",
        "description": "A 在同时出现取物和明确桌面落点时输出 fetch，B/C 复用有边界的抓取搬运源。",
        "focus": "A/B/C：fetch 交付位置约束",
        "instruction": "把红色方块拿过来放到桌子上",
        "expected": "SUCCEEDED",
        "scene": _scene(
            "fetch_to_table",
            [_cube("red_cube", "red", 0.22), _target()],
        ),
    },
    "stack_green_on_red": {
        "name": "方块堆叠（成功）",
        "description": "A 输出 stack，B 在既有 move_to_target 上显式使用 stack_on，C 按尺寸计算安全落点。",
        "focus": "A/B/C/D：stack 约束与几何落点",
        "instruction": "把绿色方块叠到红色方块上",
        "expected": "SUCCEEDED",
        "scene": _scene(
            "stack_green_on_red",
            [_stack_base(), _cube("green_cube", "green", 0.22, 0.12)],
        ),
    },
    "ambiguous_red_cubes": {
        "name": "同名方块（安全阻断）",
        "description": "场景里有两个红色方块，意图无法唯一绑定时应停在 A，不进入执行。",
        "focus": "A：目标消歧与安全门禁",
        "instruction": "把红色方块放到桌子上",
        "expected": "BLOCKED",
        "commands": [
            {"instruction": "把红色方块放到桌子上", "expected": "BLOCKED"},
            {"instruction": "把左边红色方块放到桌子上", "expected": "SUCCEEDED", "target_id": "red_cube_left"},
            {"instruction": "把右边红色方块放到桌子上", "expected": "SUCCEEDED", "target_id": "red_cube_right"},
        ],
        "scene": _scene("ambiguous_red_cubes", [_cube("red_cube_left", "red", 0.2, y=-0.1), _cube("red_cube_right", "red", 0.2, y=0.1), _target()]),
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
        "description": "A 能理解为 push，但当前 C 没有推送接触/路径执行源，因此在策略门禁处阻断。",
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
        "name": "目标区不可放置（安全阻断）",
        "description": "A 在执行门禁阶段发现目标区没有 valid_destination 能力，阻断后续策略和执行。",
        "focus": "C：执行能力校验 + D：失败诊断",
        "instruction": "把红色方块放到桌子上",
        "expected": "BLOCKED",
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
                _cube("red_sort_cube", "red", 0.16, y=-0.18),
                _cube("green_sort_cube", "green", 0.16, y=0.0),
                _cube("blue_sort_cube", "blue", 0.16, y=0.18),
                {
                    "id": "left_sort_tray",
                    "category": "红色托盘",
                    "pose": {"x": 0.32, "y": -0.18, "z": 0.03},
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
                    "pose": {"x": 0.32, "y": 0.0, "z": 0.03},
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
                    "pose": {"x": 0.32, "y": 0.18, "z": 0.03},
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

# The two canonical perception fixtures are owned by the perception module.
# Reuse those exact payloads in the catalog so the preset image, the P-stage
# response, and the C-stage initial scene cannot drift apart.
for _canonical_scene_id in ("stacking_cubes", "sorting_workcell"):
    SCENARIOS[_canonical_scene_id]["scene"] = get_mock_scene(_canonical_scene_id)


def list_scenarios() -> list[dict]:
    return [
        {
            "id": scene_id,
            "name": item["name"],
            "description": item["description"],
            "focus": item.get("focus", "闭环演示"),
            "instruction": item["instruction"],
            "expected": item["expected"],
            "commands": deepcopy(item.get("commands", [])),
            "scene": deepcopy(item["scene"]),
        }
        for scene_id, item in SCENARIOS.items()
    ]


def get_scenario(scene_id: str) -> dict:
    try:
        return deepcopy(SCENARIOS[scene_id])
    except KeyError as exc:
        raise ValueError(f"unsupported demo scene: {scene_id}") from exc
