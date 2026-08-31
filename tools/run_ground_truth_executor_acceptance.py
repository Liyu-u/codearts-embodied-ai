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
import random
import sys
import time
import uuid
from dataclasses import replace
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


def build_ground_truth_manifest(
    object_ids: tuple[str, ...] = ("red_cube", "green_cube"),
    destination_ids: tuple[str, ...] = ("zone_unstack_target",),
) -> list[dict]:
    """Build a semantic manifest for the live objects in one Isaac scene.

    The pose is deliberately not included here: ``IsaacGroundTruthProvider``
    reads every pose from USD/PhysX.  Keeping this helper pure makes it
    possible to validate multi-object manifests before starting Isaac Sim.
    """
    manifest: list[dict] = []
    seen: set[str] = set()
    color_names = {"red": "红色", "green": "绿色", "blue": "蓝色"}
    for object_id in object_ids:
        object_id = str(object_id)
        if not object_id or object_id in seen:
            continue
        seen.add(object_id)
        color = next((key for key in color_names if key in object_id), "green")
        size = 0.04 if color == "red" else 0.0515
        display = f"{color_names[color]}方块"
        manifest.append({
            "id": object_id,
            "category": display,
            "dimensions": {"x": size, "y": size, "z": size},
            "attributes": {"display_name": display, "color": color},
            "execution": {
                "movable": True,
                "graspable": True,
                **({"stackable_destination": True, "valid_destination": True} if color == "red" else {}),
            },
        })
    for destination_id in destination_ids:
        destination_id = str(destination_id)
        if not destination_id or destination_id in seen:
            continue
        seen.add(destination_id)
        manifest.append({
            "id": destination_id,
            "category": "放置区域",
            "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02},
            "attributes": {"display_name": "放置区域", "purpose": "safe_placement"},
            "execution": {"movable": False, "graspable": False, "valid_destination": True},
        })
    return manifest


def _manifest(
    object_ids: tuple[str, ...] = ("red_cube", "green_cube"),
    destination_ids: tuple[str, ...] = ("zone_unstack_target",),
) -> list[dict]:
    """Semantic manifest; poses are always read from the live driver."""
    return build_ground_truth_manifest(object_ids, destination_ids)


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


def _formal_metrics(execution: dict) -> dict:
    """把一次真实仿真执行结果整理成可直接统计的指标。"""
    steps = execution.get("steps") or []
    by_action = {
        str(step.get("action")): step
        for step in steps
        if isinstance(step, dict) and step.get("action")
    }
    grasp_step = by_action.get("grasp") or {}
    release_step = by_action.get("release") or {}
    release_verification = release_step.get("verification") or {}
    task_status = execution.get("status")
    grasp_success = grasp_step.get("status") == "SUCCESS"
    placement_success = (
        release_step.get("status") == "SUCCESS"
        and release_verification.get("verified") is True
    )
    recovery_attempts = int(execution.get("recovery_attempts") or 0)
    safety_events = execution.get("safety_events") or []
    return {
        "task_success": task_status == "SUCCEEDED" and grasp_success and placement_success,
        "grasp_success": grasp_success,
        "placement_success": placement_success,
        "safe_stop_count": 1 if task_status == "SAFE_STOP" else 0,
        "safety_event_count": len(safety_events),
        "failure_recovery_success": (
            task_status == "SUCCEEDED" if recovery_attempts > 0 else None
        ),
        "recovery_attempts": recovery_attempts,
        "execution_time_ms": execution.get("wall_ms"),
        "prewarm_time_ms": (execution.get("run_conditions") or {}).get("prewarm", {}).get("elapsed_ms"),
        "strategy_contract_passed": (
            (execution.get("provenance") or {}).get("validation") or {}
        ).get("passed"),
        "world_state_verified": release_verification.get("verified"),
        "development_debug_time_min": None,
        "development_debug_time_status": "not_measured_in_runtime_acceptance",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--strategy-file")
    parser.add_argument("--strategy-wait-s", type=float, default=0.0)
    parser.add_argument("--task-config")
    parser.add_argument("--placement-mode", default="direct", choices=["direct", "stack_on"])
    parser.add_argument("--device", default=os.environ.get("ISAAC_SIM_DEVICE", "cpu"), choices=["cpu", "cuda", "cuda:0"])
    parser.add_argument("--gpu-index", default="1")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--experiment-run-id")
    parser.add_argument("--variant-id", default="V0_RULE_BASELINE")
    parser.add_argument("--case-id", default="real-isaac-default")
    parser.add_argument("--category", default="real_isaac")
    parser.add_argument("--expected-status")
    args, _ = parser.parse_known_args(argv)
    result_dir = Path(args.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    from tools.real_isaac_experiment import (
        FailureInjectingDriver,
        SafetyInjectingDriver,
        load_experiment_config,
        select_execution_strategy,
        select_case,
        variant_runtime,
        wait_for_strategy_file,
    )

    experiment_config = None
    experiment_case = None
    if args.task_config:
        experiment_config = load_experiment_config(args.task_config)
        experiment_case = select_case(experiment_config, args.case_id)
    effective_seed = args.seed
    if effective_seed is None and experiment_config is not None:
        effective_seed = int(experiment_config["seed"])
    if effective_seed is not None:
        random.seed(effective_seed)
    runtime = variant_runtime(args.variant_id)
    effective_category = (experiment_case or {}).get("category", args.category)
    effective_expected_status = (
        args.expected_status
        or (experiment_case or {}).get("expected_status")
        or "SUCCEEDED"
    )
    task_run_id = args.experiment_run_id or uuid.uuid4().hex
    task_id = f"{args.variant_id}-{args.case_id}-{task_run_id}"

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

        global_config = (experiment_config or {}).get("global", {})
        initial_poses = dict(global_config.get("initial_scene_poses") or {})
        initial_poses.update((experiment_case or {}).get("initial_scene_poses") or {})
        active_object_id = str((experiment_case or {}).get("object_id") or "green_cube")
        destination_id = str((experiment_case or {}).get("destination_id") or "zone_unstack_target")
        cube_pose = initial_poses.get(active_object_id) or initial_poses.get("green_cube") or {
            "x": 0.50, "y": 0.0, "z": 0.0258
        }
        target_pose = initial_poses.get(destination_id) or initial_poses.get("zone_unstack_target") or {
            "x": 0.45, "y": 0.10, "z": 0.02575
        }
        cube_position = (
            float(cube_pose["x"]), float(cube_pose["y"]), float(cube_pose["z"])
        )
        target_position = (
            float(target_pose["x"]) - 0.05,
            float(target_pose["y"]) - 0.05,
            0.03,
        )
        driver = FrankaPickPlaceDriver(
            app,
            device=args.device,
            cube_position=cube_position,
            target_position=target_position,
            dynamic_object_id=active_object_id,
        )
        driver.connect(defer_start=True)
        _log(result_dir, "connect", "done", f"FrankaPickPlaceDriver ({args.device})")
        _spawn_objects(
            include_dynamic=False,
            cube_position=cube_position,
            dynamic_object_id=active_object_id,
            object_positions=initial_poses,
            target_position=(
                float(target_pose["x"]),
                float(target_pose["y"]),
                float(target_pose["z"]),
            ),
        )
        driver.start()
        _log(result_dir, "scene", "done", "Isaac scene spawned and started")

        object_ids = tuple(
            object_id for object_id in initial_poses
            if object_id != destination_id
        )
        if active_object_id not in object_ids:
            object_ids = (*object_ids, active_object_id)
        destination_ids = (destination_id,)
        provider = IsaacGroundTruthProvider(
            driver,
            scene_id=str(global_config.get("scene_id") or (experiment_case or {}).get("scene_id") or "stacking_cubes"),
            manifest=_manifest(object_ids=object_ids, destination_ids=destination_ids),
        )
        scene = isaac_perception.run(provider)
        _write(result_dir / "perception.json", scene)
        _log(result_dir, "perception", "done", "live USD/PhysX ground truth captured")

        profile = load_profile("sim")
        warmup_steps = int(global_config.get("prewarm_forward_steps", 1))
        warmup = driver.warmup_for_control(forward_steps=warmup_steps)
        _log(
            result_dir,
            "prewarm",
            "done",
            f"forward_steps={warmup['forward_steps']}; elapsed_ms={warmup['elapsed_ms']}; reset_after_warmup=true",
        )
        if not runtime["safety_gate_enabled"]:
            from modules.executor.safety import WorkspaceLimits

            profile = replace(
                profile,
                safety=replace(
                    profile.safety,
                    workspace=WorkspaceLimits(
                        x_min=-100.0, x_max=100.0,
                        y_min=-100.0, y_max=100.0,
                        z_min=-100.0, z_max=100.0,
                    ),
                    collision_check=False,
                    fail_closed_on_error=False,
                ),
            )
        failure_driver = FailureInjectingDriver(
            driver,
            (experiment_case or {}).get("failure_injection") or {},
        )
        execution_driver = SafetyInjectingDriver(
            failure_driver,
            (experiment_case or {}).get("safety_injection") or {},
        )
        backend = build_backend(profile, scene, driver=execution_driver)
        adapter = ExecutorAdapter(backend)
        if experiment_case is not None:
            external_strategy = None
            if args.strategy_file:
                external_strategy = (
                    wait_for_strategy_file(args.strategy_file, timeout_s=args.strategy_wait_s)
                    if args.strategy_wait_s > 0
                    else _load_strategy(args.strategy_file, args.placement_mode)
                )
            strategy = select_execution_strategy(
                experiment_case,
                args.variant_id,
                external_strategy=external_strategy,
                live_perception=scene,
                task_id=task_id,
            )
        else:
            strategy = _load_strategy(args.strategy_file, args.placement_mode)
            strategy["task_id"] = task_id
        _write(result_dir / "strategy.json", strategy)
        before = driver.read_object_pose(active_object_id)
        started = time.monotonic()
        if (experiment_case or {}).get("execution_mode") == "gate_only":
            execution = {
                "schema_version": "execution.v1",
                "task_id": task_id,
                "status": "BLOCKED",
                "steps": [],
                "trajectory_points": [],
                "total_duration_ms": 0,
                "stop_reason": (experiment_case or {}).get(
                    "safety_reason", "SAFETY_GATE"
                ),
                "recovery_attempts": 0,
                "recovery_exhausted": False,
                "safety_events": [],
                "reason": (experiment_case or {}).get(
                    "safety_reason", "SAFETY_GATE"
                ),
            }
        else:
            execution = adapter.run(strategy)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            after = driver.read_object_pose(active_object_id)
            physical_pose_error = None
        except Exception as exc:  # noqa: BLE001
            # A fail-closed execution intentionally stops the driver before
            # the post-action pose check. Preserve the execution.v1 result
            # instead of replacing the real safety outcome with a wrapper
            # exception and losing the report entirely.
            after = None
            physical_pose_error = f"{type(exc).__name__}: {exc}"
        execution["object_id"] = active_object_id
        execution["object_before"] = before
        execution["object_after"] = after
        # Retain the historical fields for consumers of the v1 reports; for
        # a red/multi-object case these aliases still point to the selected
        # dynamic object and are accompanied by object_id above.
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
            # Preserve SAFE_STOP when the executor already stopped safely;
            # only downgrade an otherwise successful result because the
            # physical postcondition could not be verified.
            if execution.get("status") == "SUCCEEDED":
                execution["status"] = "FAILED"
                execution["reason"] = "PHYSICAL_POSE_UNAVAILABLE"
        execution["wall_ms"] = elapsed_ms
        execution["driver_diagnostics"] = driver.diagnostics()
        execution["failure_injection"] = {
            "requested": (experiment_case or {}).get("failure_injection") or {},
            "applied": list(failure_driver.injection_log),
        }
        execution["safety_injection"] = {
            "requested": (experiment_case or {}).get("safety_injection") or {},
            "applied": list(execution_driver.injection_log),
        }
        execution.setdefault("provenance", {})["perception_backend"] = "isaac_ground_truth"
        execution["run_conditions"] = {
            "backend": profile.backend,
            "device": args.device,
            "gpu_index": args.gpu_index,
            "protocol_version": (experiment_config or {}).get("protocol_version", "unknown"),
            "action_timeout_s": profile.safety.motion.action_timeout_s,
            "scene_id": scene.get("scene_id"),
            "scene_revision": scene.get("execution_context", {}).get("scene_revision"),
            "initial_scene_poses": {
                item["id"]: item.get("pose")
                for item in scene.get("objects", [])
                if isinstance(item, dict) and item.get("id") and item.get("pose")
            },
            "randomness": {
                "mode": "deterministic_fixed_scene",
                "seed": effective_seed,
                "note": "固定任务配置；Python随机源已设定相同种子",
            },
            "prewarm": warmup,
            "experiment": {
                "variant_id": args.variant_id,
                "case_id": args.case_id,
                "category": effective_category,
                "expected_status": effective_expected_status,
                "declared_modules": runtime["modules"],
                "variant_name": runtime["name"],
                "safety_gate_enabled": runtime["safety_gate_enabled"],
                "repair_enabled": runtime["repair_enabled"],
                "simulation_only": runtime["simulation_only"],
                "execution_mode": (experiment_case or {}).get("execution_mode", "physical"),
                "task_config": args.task_config,
                "execution_scope": "版本配置直接决定策略是否带故障修复；C阶段在真实Isaac Sim中执行",
            },
        }
        execution["variant_id"] = args.variant_id
        from tools.live_intelligent_e2e import strategy_digest

        execution["input_strategy_sha256"] = strategy_digest(strategy)
        execution["case_id"] = args.case_id
        execution["category"] = effective_category
        execution["expected_status"] = effective_expected_status
        execution["case_outcome_correct"] = execution.get("status") == effective_expected_status
        execution["formal_metrics"] = _formal_metrics(execution)
        _write(
            result_dir / "final_pose.json",
            {"object_id": active_object_id, "pose": after, "source": "isaac_ground_truth"},
        )
        _write(result_dir / "execution.json", execution)
        _log(
            result_dir,
            "report",
            "done",
            f"status={execution.get('status')}; stop_reason={execution.get('stop_reason')}",
        )
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
