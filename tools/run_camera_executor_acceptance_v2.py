"""Execute an A/B strategy with live RGB-D camera poses in Isaac Sim."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_camera_executor_acceptance as base


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--strategy-file")
    parser.add_argument("--device", default="cuda", choices=["cpu", "cuda", "cuda:0"])
    parser.add_argument("--frames", type=int, default=60)
    args, _ = parser.parse_known_args(argv)
    result_dir = Path(args.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)
    app = None
    real_driver = None
    camera_driver = None
    provider = None
    captured: dict[str, dict] = {}
    started = time.monotonic()
    try:
        base._log(result_dir, "boot", "start", "creating headless SimulationApp")
        from isaacsim import SimulationApp

        app = SimulationApp({"headless": True})
        from integration.contract_validation import assert_contract
        from integration.adapters.executor import ExecutorAdapter
        from integration.config.loader import build_backend, load_profile
        from modules.executor.isaac_driver import FrankaPickPlaceDriver
        from modules.perception.isaac_camera_real import IsaacCameraRealObservationProvider
        from tools.run_executor_acceptance import _spawn_objects
        from tools.run_isaac_camera_perception import _create_camera, _save_rgb

        real_driver = FrankaPickPlaceDriver(app, device=args.device)
        real_driver.connect(defer_start=True)
        base._log(result_dir, "connect", "done", f"FrankaPickPlaceDriver ({args.device})")
        _spawn_objects(include_dynamic=False)
        base._add_camera_scene()
        base._add_semantics()
        sensor, camera_model = _create_camera()
        provider = IsaacCameraRealObservationProvider(sensor, camera_model)
        original_observe = provider.observe

        def observe_and_keep():
            observation = original_observe()
            captured["observation"] = observation
            return observation

        provider.observe = observe_and_keep
        # Warm the RTX products while the scene is still static.  Starting the
        # Franka controller first can advance the cube during warm-up and make
        # the initial three-object camera frame nondeterministic.
        base._warmup_camera(app, 30)
        real_driver.start()
        base._log(result_dir, "scene", "done", "Isaac workcell, RTX camera and semantics started")
        initial_camera = base.CameraPoseDriver(real_driver, provider, app, max_frames=max(args.frames, 1))
        scene = initial_camera._capture_scene()
        observation = captured.get("observation")
        if not observation:
            raise RuntimeError("camera observation was not captured")
        assert_contract(observation, "perception_observation.1.0.0")
        assert_contract(scene, "perception.v1")
        base._write(result_dir / "camera_observation.json", observation)
        base._write(result_dir / "perception.json", scene)
        _save_rgb(sensor, result_dir)
        base._log(result_dir, "perception", "done", f"RGB-D camera observed {len(scene['objects'])} objects")

        strategy = base._load_strategy(args.strategy_file)
        profile = load_profile("sim")
        camera_driver = base.CameraPoseDriver(real_driver, provider, app, max_frames=max(args.frames, 1))
        camera_driver.last_scene = scene
        backend = build_backend(profile, scene, driver=camera_driver)
        adapter = ExecutorAdapter(backend)
        # Calibrate the official controller immediately after the static first
        # camera frame and before adapter.run() enters event 0.  The backend
        # repeats the idempotent setter at phase 4 as a guard for other
        # real-driver integrations, but does not change an equal target.
        reset_for_control = getattr(real_driver, "reset_for_control", None)
        if callable(reset_for_control):
            reset_for_control()
        destination = next(
            (item for item in scene.get("objects", []) if item.get("id") == "zone_unstack_target"),
            None,
        )
        setter = getattr(real_driver, "set_target_pose", None)
        if callable(setter) and isinstance(destination, dict):
            setter(destination.get("pose") or {})
        # Do not read the dynamic body's USD/PhysX pose before the first
        # controller tick.  On Isaac Sim 6 GPU physics, that eager sync can
        # invalidate the controller's freshly reset contact state and make
        # the otherwise official grasp fail.  The initial RGB-D frame is the
        # permitted online pre-action observation; the post-action D check
        # below is the authoritative physical verification.
        before_physical = {
            item["id"]: dict(item["pose"])
            for item in scene.get("objects", [])
            if item.get("id") in ("green_cube", "zone_unstack_target")
        }
        execution_started = time.monotonic()
        execution = adapter.run(strategy)
        execution["wall_ms"] = int((time.monotonic() - execution_started) * 1000)
        execution.setdefault("provenance", {}).update({
            "perception_backend": "isaac_camera_rgbd",
            "online_pose_source": "rgbd_depth_backprojection",
            "ground_truth_used_for_online_pose": False,
        })
        base._write(result_dir / "execution.json", execution)
        diagnostics = getattr(real_driver, "diagnostics", None)
        if callable(diagnostics):
            base._write(result_dir / "controller_diagnostics.json", diagnostics())

        post_scene = None
        try:
            post_scene = camera_driver._capture_scene()
            base._write(result_dir / "camera_post.json", post_scene)
        except Exception as exc:
            base._log(result_dir, "post_perception", "error", f"{type(exc).__name__}: {exc}")
        release_pose = None
        for step in reversed(execution.get("steps", [])):
            verification = step.get("verification") if isinstance(step, dict) else None
            if (
                isinstance(verification, dict)
                and verification.get("verified")
                and isinstance(verification.get("object_pose"), dict)
            ):
                release_pose = dict(verification["object_pose"])
                break
        after_physical = {
            "green_cube": release_pose or real_driver.read_object_pose("green_cube"),
            "zone_unstack_target": real_driver.read_object_pose("zone_unstack_target"),
        }
        base._write(result_dir / "evaluation.json", {
            "evaluation_only": True,
            "physical_before_source": "rgbd_camera_initial",
            "physical_after_source": "physx_controller_release_verification" if release_pose else "usd_stage_post_action",
            "physical_before": before_physical,
            "physical_after": after_physical,
            "camera_initial": {item["id"]: item["pose"] for item in scene.get("objects", [])},
            "camera_post": {item["id"]: item["pose"] for item in (post_scene or {}).get("objects", [])},
        })
        base._write(result_dir / "camera_metrics.json", {
            "schema_version": "isaac-camera-metrics.v1",
            "provider": provider.health(),
            "online_pose_source": "rgbd_depth_backprojection",
            "ground_truth_used_for_online_pose": False,
            "camera_capture_count_during_c": camera_driver.capture_count,
            "camera_capture_errors": camera_driver.capture_errors[-20:],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })
        base._log(result_dir, "report", "done", f"status={execution.get('status')}; camera_pose_reads={camera_driver.capture_count}")
        return 0 if execution.get("status") == "SUCCEEDED" else 2
    except Exception as exc:
        import traceback

        base._write(result_dir / "camera_c_execution_error.json", {
            "status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "camera_health": provider.health() if provider is not None else None,
            "last_camera_observation": captured.get("observation"),
        })
        base._log(result_dir, "fatal", "error", f"{type(exc).__name__}: {exc}")
        return 2
    finally:
        if real_driver is not None:
            try:
                real_driver.shutdown()
            except Exception:
                pass
        if app is not None:
            try:
                app.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
