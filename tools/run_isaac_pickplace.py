"""真实 Isaac Sim pick-and-place 验收 —— 用官方 FrankaPickPlace 输出 execution.v1。

跑官方 FrankaPickPlace（差分 IK + CPU，已验证稳定 453ms/帧），采集：
- 方块 before/after 世界位姿；
- 每帧关节轨迹（抽样）；
- 总耗时。
输出 execution.v1 格式 JSON。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def mark(result_dir: Path, name: str, status: str, detail: str = "") -> None:
    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    line = {"step": name, "status": status, "detail": detail}
    print(f"[PROBE] {name}: {status} {detail}", flush=True)
    with open(result_dir / "progress.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        handle.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    args, _ = parser.parse_known_args(argv)
    rd = Path(args.result_dir)
    rd.mkdir(parents=True, exist_ok=True)

    mark(rd, "boot", "start", "creating SimulationApp")
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    mark(rd, "boot", "done", "SimulationApp created")

    try:
        import numpy as np
        import omni.kit.app

        omni.kit.app.get_app().get_extension_manager().set_extension_enabled_immediate(
            "isaacsim.robot.experimental.manipulators.examples", True
        )
        import omni.timeline
        from isaacsim.core.simulation_manager import SimulationManager
        from isaacsim.robot.experimental.manipulators.examples.franka import (
            FrankaPickPlace,
        )

        SimulationManager.set_physics_sim_device("cpu")
        app.update()

        pick_place = FrankaPickPlace()
        pick_place.setup_scene()
        mark(rd, "setup", "done", "FrankaPickPlace scene ready")

        cube_before = pick_place.cube.get_world_poses()[0].numpy()[0].tolist()

        omni.timeline.get_timeline_interface().play()
        app.update()
        mark(rd, "play", "done", "timeline playing")

        reset_needed = True
        frames = 0
        start = time.monotonic()
        done = False
        trajectory: list[dict] = []
        while app.is_running():
            if SimulationManager.is_simulating():
                if reset_needed:
                    pick_place.reset()
                    reset_needed = False
                pick_place.forward("damped-least-squares")
                frames += 1
                if frames % 10 == 0:
                    dof = pick_place.robot.get_dof_positions()
                    joints = dof.numpy().reshape(-1).tolist() if hasattr(dof, "numpy") else list(dof)
                    trajectory.append(
                        {
                            "timestamp_ms": int((time.monotonic() - start) * 1000),
                            "joint_positions": joints,
                        }
                    )
            if pick_place.is_done():
                done = True
                break
            app.update()
            if frames >= 2000:
                break
        elapsed_ms = int((time.monotonic() - start) * 1000)

        cube_after = pick_place.cube.get_world_poses()[0].numpy()[0].tolist()
        mark(rd, "run", "done", f"frames={frames} done={done} elapsed_ms={elapsed_ms}")

        execution = {
            "schema_version": "execution.v1",
            "task_id": "isaac-pickplace-real",
            "status": "SUCCEEDED" if done else "FAILED",
            "steps": [
                {
                    "step_id": "pick_place_real",
                    "phase": "main",
                    "action": "pick_and_place",
                    "arguments": {"task": "FrankaPickPlace"},
                    "status": "SUCCESS" if done else "FAILED",
                    "reason": None if done else "not done within frame limit",
                    "duration_ms": elapsed_ms,
                }
            ],
            "trajectory_points": trajectory,
            "total_duration_ms": elapsed_ms,
            "safety_events": [],
            "cube_before": cube_before,
            "cube_after": cube_after,
            "cube_moved_m": float(
                np.linalg.norm(np.asarray(cube_after[:3]) - np.asarray(cube_before[:3]))
            ),
        }
        (rd / "execution.json").write_text(
            json.dumps(execution, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        mark(rd, "report", "done", "execution.json written")
    except Exception as exc:  # noqa: BLE001
        import traceback

        mark(rd, "fatal", "error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return 2
    finally:
        import os

        os._exit(0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
