"""服务器端：C 模块 Isaac Sim 执行后端验收（新 executor 接线）。

用 ``ExecutorAdapter.from_profile(sim)`` + ``OmniDriver`` 跑一条真实 pick-and-place 策略，
输出 ``execution.v1``（含方块前后位姿、关节轨迹、安全事件、provenance）。

与旧 ``run_isaac_pickplace.py`` 的区别：本脚本走的是新集成到 main 的 executor 链路
（IsaacSimBackend → StrategyInterpreter → execution.v1），而不是直接调用 FrankaPickPlace。

运行（容器内，离线）：
    /isaac-sim/python.sh run_executor_acceptance.py --result-dir /workspace/results -- --/app/headless=true

已知限制（见 docs/服务器联调验收指南.md）：OmniDriver.move_to 是“循环直到收敛”的差分 IK，
CPU 物理下远距离移动每帧耗时指数增长；本脚本默认用较短步距的小范围移动，
若整段 pick-and-place 过慢，请改走 FrankaPickPlace（tools/run_isaac_pickplace.py）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def log(result_dir: Path, step: str, status: str, detail: str = "") -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    line = {"step": step, "status": status, "detail": detail}
    print(f"[ACCEPT] {step}: {status} {detail}", flush=True)
    with open(result_dir / "progress.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        handle.flush()


def _spawn_objects() -> None:
    """在 /World 下创建与 perception object_id 一致的方块 prim。

    对象 id 必须与 perception 场景一致（red_cube / green_cube / zone_unstack_target），
    因为 OmniDriver.read_object_pose 通过 ``/World/{object_id}`` 回读位姿。
    """
    from isaacsim.core.utils.prims import create_prim
    from isaacsim.core.utils.stage import get_current_stage
    from pxr import Gf, PhysxSchema, UsdGeom

    stage = get_current_stage()
    specs = [
        # (id, 类型, 中心位姿, 尺寸, 颜色)
        ("red_cube", "Cube", (0.25, 0.0, 0.04), (0.04, 0.04, 0.04), (1.0, 0.0, 0.0)),
        ("green_cube", "Cube", (0.25, 0.0, 0.12), (0.04, 0.04, 0.04), (0.0, 1.0, 0.0)),
        ("zone_unstack_target", "Cube", (0.40, 0.0, 0.03), (0.10, 0.10, 0.02), (0.7, 0.7, 0.7)),
    ]
    for object_id, prim_type, pos, scale, color in specs:
        path = f"/World/{object_id}"
        create_prim(prim_path=path, prim_type=prim_type, position=pos, scale=scale)
        prim = stage.GetPrimAtPath(path)
        if prim is None:
            continue
        # 颜色
        geom = UsdGeom.Gprim(prim)
        geom.GetDisplayColorAttr().Set([Gf.Vec3f(*color)])
        # 刚体 + 碰撞（与 scene_builder.py 一致）
        try:
            PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
            rigid = PhysxSchema.PhysxRigidBodyAPI(prim)
            rigid.GetRigidBodyEnabledAttr().Set(True)
            rigid.GetMassAttr().Set(0.15)
            geom.GetCollisionEnabledAttr().Set(True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] physics setup skipped for {object_id}: {exc}", flush=True)


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--placement-mode", default="direct", choices=["direct", "stack_on"])
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
        from modules.executor.isaac_driver import OmniDriver

        # 1) 驱动连接：官方 Franka + 差分 IK + CPU 物理
        driver = OmniDriver(app, device="cpu")
        driver.connect()
        log(rd, "connect", "done", "OmniDriver connected (Franka + CPU physics)")

        # 2) 场景物体（prim 路径与 perception object_id 一致）
        _spawn_objects()
        app.update()
        log(rd, "scene", "done", "objects spawned")

        # 3) perception 场景（用 mock 场景的 object 元数据；id 与 /World/{id} 对齐）
        from integration.adapters import perception

        scene = perception.run({"scene_id": "stacking_cubes", "backend": "mock"})

        # 4) 后端 + 适配器（sim profile → IsaacSimBackend）
        profile = load_profile("sim")
        backend = build_backend(profile, scene, driver=driver)
        adapter = ExecutorAdapter(backend)
        log(rd, "backend", "done", "ExecutorAdapter(sim/isaac backend) built")

        # 5) 执行。对象位姿取后端逻辑状态（_release 会更新 _objects）；
        #    物理抓取的真实位移证据见 tools/run_isaac_pickplace.py（FrankaPickPlace）。
        strategy = _strategy(args.placement_mode)
        before = backend.snapshot()["objects"]["green_cube"]["pose"]
        start = time.monotonic()
        execution = adapter.run(strategy)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        after = backend.snapshot()["objects"]["green_cube"]["pose"]

        execution["cube_before"] = before
        execution["cube_after"] = after
        execution["cube_moved_m"] = (
            (after["x"] - before["x"]) ** 2
            + (after["y"] - before["y"]) ** 2
            + (after["z"] - before["z"]) ** 2
        ) ** 0.5
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
