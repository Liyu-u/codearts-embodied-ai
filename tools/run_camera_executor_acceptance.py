"""Run the A/B-produced strategy through RGB-D camera perception and Isaac Sim C.

The online object poses used by the executor come from the live RTX camera.
USD/PhysX poses are collected only after execution as evaluation evidence; they
are never used to build the ``perception.v1`` input or to plan C actions.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _log(result_dir: Path, step: str, status: str, detail: str = "") -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    line = {"step": step, "status": status, "detail": detail}
    print(f"[CAMERA_C] {step}: {status} {detail}", flush=True)
    with open(result_dir / "progress.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        handle.flush()


def _warmup_camera(app: Any, frames: int = 30) -> None:
    """Allow RTX render products and semantic annotators to settle after scene setup."""

    for _ in range(max(int(frames), 0)):
        app.update()


def _manifest() -> list[dict[str, Any]]:
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
            "category": "桌子",
            "dimensions": {"x": 0.10, "y": 0.10, "z": 0.02},
            "attributes": {"display_name": "桌子", "purpose": "safe_placement", "support_surface": True},
            "execution": {"movable": False, "graspable": False, "valid_destination": True},
        },
    ]


def _attach_execution_capabilities(scene: dict[str, Any]) -> dict[str, Any]:
    """Attach static action capabilities without replacing camera poses.

    The RGB-D provider is intentionally responsible only for observed
    geometry/poses.  The executor still needs the static workcell affordance
    contract (for example, ``green_cube.graspable``) to pass its fail-closed
    guards.  Merge those capabilities at the adapter boundary; never use this
    map to fill or override an online camera pose.
    """
    capabilities = {
        item["id"]: dict(item.get("execution", {}))
        for item in _manifest()
        if item.get("id")
    }
    for item in scene.get("objects", []):
        object_id = item.get("id")
        if object_id in capabilities:
            item["execution"] = dict(capabilities[object_id])
    return scene


def _load_strategy(path: str | None) -> dict[str, Any]:
    if not path:
        return {
            "schema_version": "strategy.v1",
            "task_id": "isaac-camera-green-place",
            "code": None,
            "steps": [
                {"step_id": "s1", "action": "detect_object", "arguments": {"object_id": "green_cube"}},
                {"step_id": "s2", "action": "move_to_object", "arguments": {"object_id": "green_cube"}},
                {"step_id": "s3", "action": "grasp", "arguments": {"object_id": "green_cube"}},
                {"step_id": "s4", "action": "move_to_target", "arguments": {"destination_id": "zone_unstack_target"}},
                {"step_id": "s5", "action": "release", "arguments": {}},
            ],
        }
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(value, dict) and isinstance(value.get("strategy"), dict):
        return value["strategy"]
    if not isinstance(value, dict):
        raise ValueError("strategy 文件必须是 JSON 对象")
    return value


def _add_camera_scene() -> None:
    from isaacsim.core.experimental.prims import GeomPrim
    from pxr import Gf, UsdGeom, UsdLux
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    floor_path = "/World/Sensors/CameraFloor"
    floor = stage.DefinePrim(floor_path, "Cube")
    cube = UsdGeom.Cube(floor)
    cube.CreateSizeAttr(1.0)
    cube.AddTranslateOp().Set(Gf.Vec3d(0.5, 0.0, -0.005))
    cube.AddScaleOp().Set(Gf.Vec3f(1.0, 1.0, 0.01))
    GeomPrim(paths=floor_path, apply_collision_apis=True)

    dome = UsdLux.DomeLight.Define(stage, "/World/Sensors/CameraDomeLight")
    dome.CreateIntensityAttr(1200.0)
    dome.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))


def _add_semantics() -> None:
    try:
        from isaacsim.core.experimental.utils.semantics import add_labels

        for object_id in ("red_cube", "green_cube", "zone_unstack_target"):
            add_labels(f"/World/{object_id}", labels=[object_id], taxonomy="class")
    except ImportError:
        from omni.isaac.core.utils.semantics import add_update_semantics

        for object_id in ("red_cube", "green_cube", "zone_unstack_target"):
            add_update_semantics(f"/World/{object_id}", [("class", object_id)])


class CameraPoseDriver:
    """Forward motion to Isaac while exposing only live camera poses."""

    def __init__(self, real_driver: Any, provider: Any, app: Any, max_frames: int = 30) -> None:
        self._real_driver = real_driver
        self._provider = provider
        self._app = app
        self._max_frames = max_frames
        self.capture_count = 0
        self.capture_errors: list[str] = []
        self.last_scene: dict[str, Any] | None = None

    def _capture_scene(self) -> dict[str, Any]:
        last_error = ""
        for _ in range(self._max_frames):
            self._app.update()
            try:
                observation = self._provider.observe()
                if len(observation.get("objects", [])) < 3:
                    last_error = "camera frame contains fewer than three labeled objects"
                    continue
                from modules.perception.observation_normalizer import normalize_observation

                scene = normalize_observation(observation)
                _attach_execution_capabilities(scene)
                self.last_scene = scene
                self.capture_count += 1
                return scene
            except (RuntimeError, ValueError, KeyError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self.capture_errors.append(last_error)
        raise RuntimeError(last_error or "camera did not produce a valid frame")

    def read_object_pose(self, object_id: str) -> dict[str, float]:
        # The first executor action is detect_object.  Re-capturing there
        # would advance a just-reset controller before its first forward
        # tick; use the validated initial frame when available.  Callers
        # that need a fresh post-action frame explicitly invoke
        # ``_capture_scene``.
        scene = self.last_scene if self.last_scene is not None else self._capture_scene()
        for item in scene.get("objects", []):
            if item.get("id") == object_id:
                pose = item.get("pose") or {}
                return {axis: float(pose[axis]) for axis in ("x", "y", "z")}
        raise RuntimeError(f"camera did not observe object: {object_id}")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real_driver, name)


def _capture_initial(app: Any, provider: Any, result_dir: Path, frames: int) -> tuple[dict[str, Any], dict[str, Any]]:
    proxy = CameraPoseDriver.__new__(CameraPoseDriver)
    proxy._provider = provider
    proxy._app = app
    proxy._max_frames = frames
    proxy.capture_count = 0
    proxy.capture_errors = []
    proxy.last_scene = None
    scene = proxy._capture_scene()
    observation = provider.last_observation
    _write(result_dir / "camera_observation.json", observation)
    _write(result_dir / "perception.json", scene)
    return scene, {"proxy": proxy, "observation": observation}


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
    started = time.monotonic()
    try:
        _log(result_dir, "boot", "start", "creating headless SimulationApp")
        from isaacsim import SimulationApp

        app = SimulationApp({"headless": True})
        from integration.contract_validation import assert_contract
        from integration.adapters.executor import ExecutorAdapter
        from integration.config.loader import build_backend, load_profile
        from modules.perception.isaac_camera_real import IsaacCameraRealObservationProvider
        from tools.run_executor_acceptance import _spawn_objects
        from tools.run_isaac_camera_perception import _create_camera, _save_rgb
        from modules.executor.isaac_driver import FrankaPickPlaceDriver

        real_driver = FrankaPickPlaceDriver(app, device=args.device)
        real_driver.connect(defer_start=True)
        _log(result_dir, "connect", "done", f"FrankaPickPlaceDriver ({args.device})")
        _spawn_objects(include_dynamic=False)
        _add_camera_scene()
        _add_semantics()
        sensor, camera_model = _create_camera()
        provider = IsaacCameraRealObservationProvider(sensor, camera_model)
        real_driver.start()
        _log(result_dir, "scene", "done", "Isaac workcell, RTX camera and semantics started")

        # The provider keeps the online source strictly RGB-D + segmentation.
        initial_proxy = CameraPoseDriver(real_driver, provider, app, max_frames=max(args.frames, 1))
        scene = initial_proxy._capture_scene()
        observation = provider.last_observation
        assert_contract(observation, "perception_observation.1.0.0")
        assert_contract(scene, "perception.v1")
        _write(result_dir / "camera_observation.json", observation)
        _write(result_dir / "perception.json", scene)
        _save_rgb(sensor, result_dir)
        _log(result_dir, "perception", "done", f"RGB-D camera observed {len(scene['objects'])} objects")

        strategy = _load_strategy(args.strategy_file)
        profile = load_profile("sim")
        camera_driver = CameraPoseDriver(real_driver, provider, app, max_frames=max(args.frames, 1))
        backend = build_backend(profile, scene, driver=camera_driver)
        adapter = ExecutorAdapter(backend)
        before_physical = {object_id: real_driver.read_object_pose(object_id) for object_id in ("green_cube", "zone_unstack_target")}
        execution_started = time.monotonic()
        execution = adapter.run(strategy)
        execution["wall_ms"] = int((time.monotonic() - execution_started) * 1000)
        execution.setdefault("provenance", {}).update({
            "perception_backend": "isaac_camera_rgbd",
            "online_pose_source": "rgbd_depth_backprojection",
            "ground_truth_used_for_online_pose": False,
        })
        _write(result_dir / "execution.json", execution)

        post_scene = None
        try:
            post_scene = camera_driver._capture_scene()
            _write(result_dir / "camera_post.json", post_scene)
        except Exception as exc:  # post-frame evidence must not hide C status
            _log(result_dir, "post_perception", "error", f"{type(exc).__name__}: {exc}")
        after_physical = {object_id: real_driver.read_object_pose(object_id) for object_id in ("green_cube", "zone_unstack_target")}
        _write(result_dir / "evaluation.json", {
            "evaluation_only": True,
            "physical_before": before_physical,
            "physical_after": after_physical,
            "camera_initial": {item["id"]: item["pose"] for item in scene.get("objects", [])},
            "camera_post": {item["id"]: item["pose"] for item in (post_scene or {}).get("objects", [])},
        })
        _write(result_dir / "camera_metrics.json", {
            "schema_version": "isaac-camera-metrics.v1",
            "provider": provider.health(),
            "online_pose_source": "rgbd_depth_backprojection",
            "ground_truth_used_for_online_pose": False,
            "camera_capture_count_during_c": camera_driver.capture_count,
            "camera_capture_errors": camera_driver.capture_errors[-20:],
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })
        _log(result_dir, "report", "done", f"status={execution.get('status')}; camera_pose_reads={camera_driver.capture_count}")
        return 0 if execution.get("status") == "SUCCEEDED" else 2
    except Exception as exc:  # noqa: BLE001
        import traceback

        _write(result_dir / "camera_c_execution_error.json", {
            "status": "FAILED",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        })
        _log(result_dir, "fatal", "error", f"{type(exc).__name__}: {exc}")
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
