"""服务器端：C 模块 Isaac Sim 执行后端验收（新 executor 接线）。

用 ``ExecutorAdapter.from_profile(sim)`` + ``FrankaPickPlaceDriver`` 跑一条真实 pick-and-place 策略，
输出 ``execution.v1``（含方块前后位姿、关节轨迹、安全事件、provenance）。

与旧 ``run_isaac_pickplace.py`` 的区别：本脚本走的是新集成到 main 的 executor 链路
（IsaacSimBackend → StrategyInterpreter → execution.v1），并通过 MotionDriver 适配官方 FrankaPickPlace，
而不是在验收脚本中绕过 executor 直接调用控制器。

运行（容器内，离线）：
    /isaac-sim/python.sh run_executor_acceptance.py --result-dir /workspace/results -- --/app/headless=true

控制器遵循 Isaac Sim 官方 Franka 示例：每帧发送完整末端目标位姿，
再回读真实末端状态判断收敛；若远程环境仍无法在超时内完成，
应先检查物理设备和 Isaac Sim 日志，而不是用逻辑位姿代替物理结果。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path


def log(result_dir: Path, step: str, status: str, detail: str = "") -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    line = {"step": step, "status": status, "detail": detail}
    print(f"[ACCEPT] {step}: {status} {detail}", flush=True)
    with open(result_dir / "progress.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        handle.flush()


def _spawn_objects(
    *,
    include_dynamic: bool = True,
    cube_position: tuple[float, float, float] = (0.50, 0.0, 0.0258),
    red_position: tuple[float, float, float] = (0.65, -0.20, 0.0258),
    target_position: tuple[float, float, float] = (0.45, 0.10, 0.02575),
    dynamic_object_id: str = "green_cube",
    object_positions: dict | None = None,
) -> list[tuple[object, tuple[float, float, float]]]:
    """在 /World 下创建与 perception object_id 一致的方块 prim。

    对象 id 必须与 perception 场景一致（red_cube / green_cube / zone_unstack_target），
    因为 OmniDriver.read_object_pose 通过 ``/World/{object_id}`` 回读位姿。
    """
    from isaacsim.core.utils.stage import get_current_stage
    from isaacsim.core.experimental.objects import Cube
    from isaacsim.core.experimental.prims import GeomPrim, RigidPrim
    from pxr import Gf, Usd, UsdGeom

    stage = get_current_stage()
    supplied_positions = dict(object_positions or {})

    def position_for(object_id: str, fallback: tuple[float, float, float]) -> tuple[float, float, float]:
        value = supplied_positions.get(object_id)
        if isinstance(value, dict):
            return (float(value["x"]), float(value["y"]), float(value["z"]))
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(float(item) for item in value)
        return tuple(float(item) for item in fallback)

    specs = [
        # (id, 中心位姿, 尺寸, 颜色)
        # 采用官方 FrankaPickPlace 已验证的可达工作区，避免把靠近
        # 机器人基座边界的夹具坐标误判为 IK/物理失败。
        ("red_cube", position_for("red_cube", red_position), (0.04, 0.04, 0.04), "red"),
        # Keep the dynamic cube identical to the official FrankaPickPlace
        # example (51.5 mm).  The controller's grasp/lift clearance is tuned
        # against this geometry.
        ("green_cube", position_for("green_cube", cube_position), (0.0515, 0.0515, 0.0515), "green"),
        ("zone_unstack_target", position_for("zone_unstack_target", target_position), (0.10, 0.10, 0.02), "gray"),
    ]
    known_ids = {item[0] for item in specs}
    for object_id in supplied_positions:
        if object_id in known_ids or object_id == "zone_unstack_target":
            continue
        text = str(object_id)
        color = next((name for name in ("red", "green", "blue") if name in text), "green")
        size = 0.04 if color == "red" else 0.0515
        specs.append((text, position_for(text, cube_position), (size, size, size), color))
    if dynamic_object_id not in {item[0] for item in specs}:
        specs.append((dynamic_object_id, position_for(dynamic_object_id, cube_position), (0.0515, 0.0515, 0.0515), "green"))
    dynamic_objects = []

    def set_display_color(path: str, color: str) -> None:
        # The official FrankaPickPlace controller creates green_cube before
        # this helper runs.  Do not construct a second experimental Cube
        # view for an existing dynamic body: on Isaac Sim 6 that can mutate
        # the body's view/physics state and break the first grasp contact.
        # Set USD displayColor directly on the existing GPrims instead.
        colors = {
            "red": Gf.Vec3f(1.0, 0.0, 0.0),
            "green": Gf.Vec3f(0.0, 1.0, 0.0),
            "blue": Gf.Vec3f(0.0, 0.25, 1.0),
            "gray": Gf.Vec3f(0.45, 0.45, 0.45),
        }
        display_color = colors[color]
        root = stage.GetPrimAtPath(path)
        for prim in Usd.PrimRange(root):
            if not prim or not prim.IsA(UsdGeom.Gprim):
                continue
            gprim = UsdGeom.Gprim(prim)
            attr = gprim.GetDisplayColorAttr()
            if not attr:
                attr = gprim.CreateDisplayColorAttr()
            attr.Set([display_color])

    for object_id, pos, scale, color in specs:
        if object_id == dynamic_object_id and not include_dynamic:
            if stage.GetPrimAtPath(f"/World/{object_id}"):
                set_display_color(f"/World/{object_id}", color)
            continue
        path = f"/World/{object_id}"
        if object_id == dynamic_object_id:
            cube = Cube(
                paths=path,
                positions=pos,
                orientations=(1.0, 0.0, 0.0, 0.0),
                sizes=1.0,
                scales=scale,
                colors=color,
            )
            GeomPrim(paths=cube.paths, apply_collision_apis=True)
            set_display_color(path, color)
            dynamic_objects.append((RigidPrim(paths=cube.paths), pos))
        else:
            # Use plain USD geometry for static scene markers.  The official
            # controller has only one experimental dynamic Cube; constructing
            # additional experimental objects can put PhysX on a very slow
            # tensor initialization path even when collisions are disabled.
            prim = stage.DefinePrim(path, "Cube")
            geom = UsdGeom.Cube(prim)
            geom.CreateSizeAttr(1.0)
            geom.AddTranslateOp().Set(Gf.Vec3d(*pos))
            geom.AddScaleOp().Set(Gf.Vec3f(*scale))
            set_display_color(path, color)
        prim = stage.GetPrimAtPath(path)
        if prim is None:
            raise RuntimeError(f"failed to create Isaac Sim prim {path}")
    return dynamic_objects


def _strategy(placement_mode: str) -> dict:
    """一条五步原子动作策略（direct 或 stack_on 放置）。"""
    move_to_target_args = {"destination_id": "zone_unstack_target"}
    if placement_mode == "stack_on":
        move_to_target_args = {"destination_id": "red_cube", "placement_mode": "stack_on"}
    return {
        "schema_version": "strategy.v1",
        "task_id": f"isaac-executor-{placement_mode}",
        "code": None,
        "steps": [
            {"step_id": "s1", "action": "detect_object", "arguments": {"object_id": "green_cube"}},
            {"step_id": "s2", "action": "move_to_object", "arguments": {"object_id": "green_cube"}},
            {"step_id": "s3", "action": "grasp", "arguments": {"object_id": "green_cube"}},
            {"step_id": "s4", "action": "move_to_target", "arguments": move_to_target_args},
            {"step_id": "s5", "action": "release", "arguments": {}},
        ],
    }


def _load_strategy(strategy_file: str | None, placement_mode: str) -> dict:
    """Load a strategy produced by A/B, or use the built-in acceptance plan."""

    if not strategy_file:
        return _strategy(placement_mode)
    path = Path(strategy_file)
    strategy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(strategy, dict):
        raise ValueError("strategy file must contain a JSON object")
    # The live A/B preparation report stores scene/task/strategy together;
    # accepting that envelope keeps the hand-off auditable without rewriting
    # the generated strategy on disk.
    if isinstance(strategy.get("strategy"), dict):
        strategy = strategy["strategy"]
    return strategy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--placement-mode", default="direct", choices=["direct", "stack_on"])
    parser.add_argument(
        "--strategy-file",
        default=None,
        help="optional strategy.v1 JSON generated by the live A/B stages",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("ISAAC_SIM_DEVICE", "cpu"),
        choices=["cpu", "cuda", "cuda:0"],
        help="Isaac Sim physics device (default: ISAAC_SIM_DEVICE or cpu)",
    )
    parser.add_argument(
        "--skip-static-markers",
        action="store_true",
        help="diagnostic mode: do not add red/target USD markers",
    )
    args, _ = parser.parse_known_args(argv)
    rd = Path(args.result_dir)
    rd.mkdir(parents=True, exist_ok=True)

    log(rd, "boot", "start", "creating SimulationApp")
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    log(rd, "boot", "done", "SimulationApp created")

    try:
        # 仓库根目录（integration/、modules/ 所在层）加入 sys.path，不写死容器路径。
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

        from integration.adapters.executor import ExecutorAdapter
        from integration.config.loader import build_backend, load_profile
        from modules.executor.isaac_driver import FrankaPickPlaceDriver

        # 1) 驱动连接：复用 NVIDIA 官方 FrankaPickPlace 控制循环。
        # 该控制器已经在同一服务器、同一 Isaac Sim 镜像上验证过完整
        # pick-and-place；这里仅适配到集成 executor 的 MotionDriver 接口。
        driver = FrankaPickPlaceDriver(app, device=args.device)
        driver.connect(defer_start=True)
        log(rd, "connect", "done", f"FrankaPickPlaceDriver connected ({args.device} physics; start deferred)")

        # 2) 场景物体（prim 路径与 perception object_id 一致）
        # 官方控制器已经创建 green_cube；这里只补充静态 perception 标记，
        # 避免第二个实验版动态 Cube 触发额外的 PhysX tensor 初始化。
        if not args.skip_static_markers:
            _spawn_objects(include_dynamic=False)
            log(rd, "scene", "done", "objects spawned")
        else:
            log(rd, "scene", "done", "static markers skipped")
        driver.start()
        log(rd, "start", "done", "official FrankaPickPlace reset after timeline start")

        # 3) perception 场景（用 mock 场景的 object 元数据；id 与 /World/{id} 对齐）
        from integration.adapters import perception

        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})
        # The stock mock scene is intentionally generic.  For this real Isaac
        # acceptance profile, align only the destination metadata with the
        # official controller's fixed physical target; object identity and all
        # other perception fields remain unchanged.
        for item in scene.get("objects", []):
            if item.get("id") == "zone_unstack_target":
                item["pose"] = {"x": 0.45, "y": 0.10, "z": 0.02575}
        # Keep the pre-action scene read-free.  NVIDIA's controller resets the
        # dynamic cube and immediately enters its forward loop; an eager
        # GeomPrim tensor read here can force an expensive transform sync before
        # the first physics tick.  The mock scene is aligned with the explicit
        # USD spawn coordinates; post-action pose checks below remain physical.

        # 4) 后端 + 适配器（sim profile → IsaacSimBackend）
        profile = load_profile("sim")
        backend = build_backend(profile, scene, driver=driver)
        adapter = ExecutorAdapter(backend)
        log(rd, "backend", "done", "ExecutorAdapter(sim/isaac backend) built")

        # 5) 执行。前后位姿都从 Isaac Sim prim 回读；真实驱动的 release
        #    还会在后端内部执行一次释放后位姿校验，不能用逻辑状态冒充物理证据。
        strategy = _load_strategy(args.strategy_file, args.placement_mode)
        # s1 performs a real perception read and updates the backend object;
        # use the same physical pose for the displacement evidence rather than
        # the mock scene's illustrative coordinates.
        before = driver.read_object_pose("green_cube")
        start = time.monotonic()
        execution = adapter.run(strategy)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        try:
            after = driver.read_object_pose("green_cube")
            physical_pose_error = None
        except Exception as exc:  # noqa: BLE001
            after = None
            physical_pose_error = f"{type(exc).__name__}: {exc}"

        execution["cube_before"] = before
        execution["cube_after"] = after
        if after is not None:
            execution["cube_moved_m"] = (
                (after["x"] - before["x"]) ** 2
                + (after["y"] - before["y"]) ** 2
                + (after["z"] - before["z"]) ** 2
            ) ** 0.5
        else:
            execution["cube_moved_m"] = None
            execution["physical_pose_error"] = physical_pose_error
            execution["status"] = "FAILED"
            execution["reason"] = "PHYSICAL_POSE_UNAVAILABLE"
        execution["wall_ms"] = elapsed_ms

        (rd / "execution.json").write_text(
            json.dumps(execution, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        log(rd, "report", "done", f"execution.json written status={execution['status']}")
    except Exception as exc:  # noqa: BLE001
        import traceback

        log(rd, "fatal", "error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return 2
    finally:
        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
