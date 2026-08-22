"""Isaac Sim acceptance entrypoint using live ground-truth perception.

This is the first real P->C path: the scene is created in Isaac Sim, poses are
read through the live MotionDriver, the resulting perception.v1 snapshot is
persisted, and the existing Isaac executor consumes that snapshot. A/B can
continue to provide the strategy file; no Mock scene is used for C input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _log(result_dir: Path, step: str, status: str, detail: str = "") -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    line = {"step": step, "status": status, "detail": detail}
    print(f"[GROUND_TRUTH] {step}: {status} {detail}", flush=True)
    with open(result_dir / "progress.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        handle.flush()


def _manifest() -> list[dict]:
    """Semantic manifest; poses are always read from the live driver."""
    return [
        {
            "id": "red_cube",
            "category": "红色方块",
            "dimensions": {"x": 0.04, "y": 0.04, "z": 0.04},
            "attributes": {"display_name": "红色方块", "color": "red"},
            "execution": {"movable": True, "graspable": True, "stackable_destination": True, "valid_destination": True},
        },
        {
            "id": "green_cube",
            "category": "绿色方块",
            "dimensions": {"x": 0.0515, "y": 0.0515, "z": 0.0515},
            "attributes": {"display_name": "绿色方块", "color": "green"},
            "execution": {"movable": True, "graspable": True},
        },
        {
            "id": "zone_unstack_target",
            "category": "放置区域",
            "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02},
            "attributes": {"display_name": "放置区域", "purpose": "safe_placement"},
            "execution": {"movable": False, "graspable": False, "valid_destination": True},
        },
    ]


def _load_strategy(path: str | None, placement_mode: str) -> dict:
    if path:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("strategy"), dict):
            return value["strategy"]
        return value
    return {
        "schema_version": "strategy.v1",
        "task_id": f"isaac-ground-truth-{placement_mode}",
        "code": None,
        "steps": [
            {"step_id": "s1", "action": "detect_object", "arguments": {"object_id": "green_cube"}},
            {"step_id": "s2", "action": "move_to_object", "arguments": {"object_id": "green_cube"}},
            {"step_id": "s3", "action": "grasp", "arguments": {"object_id": "green_cube"}},
            {"step_id": "s4", "action": "move_to_target", "arguments": {"destination_id": "zone_unstack_target"}},
            {"step_id": "s5", "action": "release", "arguments": {}},
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--strategy-file")
    parser.add_argument("--placement-mode", default="direct", choices=["direct", "stack_on"])
    parser.add_argument("--device", default=os.environ.get("ISAAC_SIM_DEVICE", "cpu"), choices=["cpu", "cuda", "cuda:0"])
    args, _ = parser.parse_known_args(argv)
    result_dir = Path(args.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    _log(result_dir, "boot", "start", "creating SimulationApp")
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    driver = None
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from integration.adapters import isaac_perception
        from integration.adapters.executor import ExecutorAdapter
        from integration.config.loader import build_backend, load_profile
        from modules.executor.isaac_driver import FrankaPickPlaceDriver
        from modules.perception.isaac_ground_truth import IsaacGroundTruthProvider
        from tools.run_executor_acceptance import _spawn_objects

        driver = FrankaPickPlaceDriver(app, device=args.device)
        driver.connect(defer_start=True)
        _log(result_dir, "connect", "done", f"FrankaPickPlaceDriver ({args.device})")
        _spawn_objects(include_dynamic=False)
        driver.start()
        _log(result_dir, "scene", "done", "Isaac scene spawned and started")

        provider = IsaacGroundTruthProvider(driver, scene_id="stacking_cubes", manifest=_manifest())
        scene = isaac_perception.run(provider)
        _write(result_dir / "perception.json", scene)
        _log(result_dir, "perception", "done", "live USD/PhysX ground truth captured")

        backend = build_backend(load_profile("sim"), scene, driver=driver)
        adapter = ExecutorAdapter(backend)
        strategy = _load_strategy(args.strategy_file, args.placement_mode)
        before = driver.read_object_pose("green_cube")
        started = time.monotonic()
        execution = adapter.run(strategy)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        after = driver.read_object_pose("green_cube")
        execution["cube_before"] = before
        execution["cube_after"] = after
        execution["cube_moved_m"] = ((after["x"] - before["x"]) ** 2 + (after["y"] - before["y"]) ** 2 + (after["z"] - before["z"]) ** 2) ** 0.5
        execution["wall_ms"] = elapsed_ms
        execution.setdefault("provenance", {})["perception_backend"] = "isaac_ground_truth"
        _write(result_dir / "execution.json", execution)
        _log(result_dir, "report", "done", f"status={execution.get('status')}")
        return 0 if execution.get("status") == "SUCCEEDED" else 2
    except Exception as exc:  # noqa: BLE001
        import traceback

        _log(result_dir, "fatal", "error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        return 2
    finally:
        if driver is not None:
            try:
                driver.shutdown()
            except Exception:
                pass
        try:
            app.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
