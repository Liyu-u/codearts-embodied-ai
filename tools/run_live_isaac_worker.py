"""Run one persistent Isaac Sim 6.0 world that consumes typed runtime jobs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

# The worker is invoked as tools/run_live_isaac_worker.py inside the
# container; put the repo root on sys.path so the tools package resolves.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.live_worker.runtime import LiveRuntimeWorker
from tools.relay.runtime_protocol import RuntimeLayout


DEFAULT_RUNTIME_ROOT = Path("/data/stu_01/workspace/live-runtime")
STREAMING_EXPERIENCE = "/isaac-sim/apps/isaacsim.exp.full.streaming.kit"
# How to configure the streaming Perspective View:
#   auto      -> native viewport API first, usd-camera fallback (production)
#   viewport  -> native viewport API only
#   usd-camera-> /World/Camera + look-at + viewport binding only
#   off       -> never touch camera/view (round-1 diagnostic isolation)
STREAM_VIEW_MODES = ("auto", "viewport", "usd-camera", "off")
# Frames to let viewport/stage transforms apply after view configuration.
# This is NOT a stream-readiness wait.
STREAM_VIEW_WARMUP_FRAMES = 30
# Production stream framing: covers Franka, red/green cubes and the target
# zone in one level, horizontal (Z-up) view.  Overridable via CLI.
DEFAULT_STREAM_CAMERA_EYE = (1.35, -1.45, 1.05)
DEFAULT_STREAM_CAMERA_TARGET = (0.40, 0.00, 0.25)


@dataclass(frozen=True, slots=True)
class LiveWorldConfig:
    runtime_root: Path = DEFAULT_RUNTIME_ROOT
    streaming_experience: str = STREAMING_EXPERIENCE
    device: str = "cuda"
    idle_sleep_s: float = 0.05
    stream_view_mode: str = "auto"
    stream_camera_eye: tuple[float, float, float] = DEFAULT_STREAM_CAMERA_EYE
    stream_camera_target: tuple[float, float, float] = DEFAULT_STREAM_CAMERA_TARGET

    def __post_init__(self) -> None:
        if Path(self.runtime_root) != DEFAULT_RUNTIME_ROOT:
            raise ValueError(f"runtime_root is fixed at {DEFAULT_RUNTIME_ROOT}")
        if self.streaming_experience != STREAMING_EXPERIENCE:
            raise ValueError("the verified Isaac streaming experience cannot be replaced")
        if self.device not in {"cpu", "cuda", "cuda:0"}:
            raise ValueError("device must be cpu, cuda or cuda:0")
        if self.idle_sleep_s < 0:
            raise ValueError("idle_sleep_s must not be negative")
        if self.stream_view_mode not in STREAM_VIEW_MODES:
            raise ValueError(
                f"stream_view_mode must be one of {STREAM_VIEW_MODES}, "
                f"got {self.stream_view_mode!r}"
            )
        for name, value in (
            ("stream_camera_eye", self.stream_camera_eye),
            ("stream_camera_target", self.stream_camera_target),
        ):
            if (
                len(value) != 3
                or not all(isinstance(component, (int, float)) for component in value)
            ):
                raise ValueError(f"{name} must be a 3-component numeric tuple")


@dataclass(frozen=True, slots=True)
class BuiltLiveWorld:
    app: Any
    world: Any
    runtime_worker: LiveRuntimeWorker
    runtime_root: Path
    kit_instance_id: str
    world_id: str


class IsaacDynamicScene:
    BASE_POSES: dict[str, dict[str, float]] = {
        "red_cube": {"x": 0.65, "y": -0.20, "z": 0.0258},
        "green_cube": {"x": 0.50, "y": 0.00, "z": 0.0258},
        "red_cube_left": {"x": 0.46, "y": -0.14, "z": 0.0258},
        "red_cube_right": {"x": 0.60, "y": -0.04, "z": 0.0258},
        "zone_unstack_target": {"x": 0.45, "y": 0.10, "z": 0.02575},
    }

    def __init__(self, app: Any, dynamic_handles: Mapping[str, Any]) -> None:
        self.app = app
        self.dynamic_handles = dict(dynamic_handles)

    @staticmethod
    def _scene_object_ids(job: Mapping[str, Any]) -> tuple[str, ...]:
        initial_ids = tuple(
            str(object_id)
            for object_id in dict(job.get("initial_scene_poses") or {})
            if object_id != "zone_unstack_target"
        )
        selected = (
            initial_ids
            if {"red_cube_left", "red_cube_right"}.issubset(initial_ids)
            else ("red_cube", "green_cube")
        )
        requested = str(job.get("object_id") or "")
        if requested and requested not in selected:
            selected = (*selected, requested)
        return tuple(dict.fromkeys(selected))

    @classmethod
    def create(cls, app: Any) -> "IsaacDynamicScene":
        import numpy as np
        from isaacsim.core.experimental.objects import Cube
        from isaacsim.core.experimental.prims import GeomPrim, RigidPrim

        dynamic: dict[str, Any] = {}
        colors = {
            "red_cube": "red",
            "green_cube": "green",
            "red_cube_left": "red",
            "red_cube_right": "red",
        }
        for object_id, color in colors.items():
            pose = cls.BASE_POSES[object_id]
            size = 0.04 if color == "red" else 0.0515
            cube = Cube(
                paths=f"/World/{object_id}",
                positions=(pose["x"], pose["y"], pose["z"]),
                orientations=(1.0, 0.0, 0.0, 0.0),
                sizes=1.0,
                scales=(size, size, size),
                colors=color,
            )
            GeomPrim(paths=cube.paths, apply_collision_apis=True)
            dynamic[object_id] = RigidPrim(paths=cube.paths)

        target = cls.BASE_POSES["zone_unstack_target"]
        marker = Cube(
            paths="/World/zone_unstack_target",
            positions=(target["x"], target["y"], target["z"]),
            orientations=(1.0, 0.0, 0.0, 0.0),
            sizes=1.0,
            scales=(0.10, 0.10, 0.02),
            colors="gray",
        )
        GeomPrim(paths=marker.paths, apply_collision_apis=True)
        app.update()
        return cls(app, dynamic)

    def reset(self, job: Mapping[str, Any]) -> None:
        import numpy as np

        poses = deepcopy(self.BASE_POSES)
        for object_id, pose in dict(job.get("initial_scene_poses") or {}).items():
            if object_id in poses and isinstance(pose, Mapping):
                poses[object_id] = {
                    axis: float(pose[axis]) for axis in ("x", "y", "z")
                }
        active_ids = frozenset(self._scene_object_ids(job))
        for object_id, handle in self.dynamic_handles.items():
            pose = (
                poses[object_id]
                if object_id in active_ids
                else {"x": 2.0, "y": 2.0, "z": -1.0}
            )
            handle.set_world_poses(
                positions=np.asarray([[pose["x"], pose["y"], pose["z"]]], dtype=float),
                orientations=np.asarray([[1.0, 0.0, 0.0, 0.0]], dtype=float),
            )
        for _ in range(5):
            self.app.update()

    def manifest(self, job: Mapping[str, Any]) -> list[dict[str, Any]]:
        from tools.run_ground_truth_executor_acceptance import build_ground_truth_manifest

        return build_ground_truth_manifest(
            self._scene_object_ids(job), ("zone_unstack_target",)
        )


def _ensure_stream_camera(app: Any) -> bool:
    """Point the WebRTC stream at the workspace via /World/Camera.

    The camera is framed on the Franka + cube workspace (eye -> target) so the
    client's initial view shows the scene.  A bare camera prim looks straight
    down at the ground (default -Z orientation in a Z-up stage), which made the
    stream show a zoomed-in close-up of empty ground.  Runs on the SAME stage:
    no create_new_stage, no new SimulationApp, no WebRTC restart, no timeline
    change.  Returns True only when the camera prim + look-at transform +
    viewport binding all applied.
    """
    try:
        import omni.kit.app  # noqa: F401  (ensures omni modules are importable)
        from isaacsim.core.utils.stage import get_current_stage
        from omni.kit.viewport.utility import get_active_viewport
        from pxr import Gf, UsdGeom

        stage = get_current_stage()
        camera_path = "/World/Camera"
        if not stage.GetPrimAtPath(camera_path):
            camera = UsdGeom.Camera.Define(stage, camera_path)
        else:
            camera = UsdGeom.Camera(stage.GetPrimAtPath(camera_path))
        camera.GetFocalLengthAttr().Set(23.0)
        # Frame the workspace: camera above/beside the origin, looking at the
        # cubes' area so Franka + targets fill the view.
        eye = Gf.Vec3d(1.30, -1.55, 0.95)
        target = Gf.Vec3d(0.35, 0.0, 0.25)
        up = Gf.Vec3d(0.0, 0.0, 1.0)
        forward = target - eye
        forward = forward / (forward.GetLength() or 1.0)
        z_axis = -forward  # cameras look along their local -Z
        x_axis = Gf.Cross(up, z_axis)  # Gf.Cross is a module function, not a method
        x_axis = x_axis / (x_axis.GetLength() or 1.0)
        y_axis = Gf.Cross(z_axis, x_axis)
        matrix = Gf.Matrix4d(
            x_axis[0], y_axis[0], z_axis[0], eye[0],
            x_axis[1], y_axis[1], z_axis[1], eye[1],
            x_axis[2], y_axis[2], z_axis[2], eye[2],
            0.0, 0.0, 0.0, 1.0,
        )
        xformable = UsdGeom.Xformable(camera)
        xformable.ClearXformOpOrder()
        xformable.AddTransformOp().Set(matrix)
        viewport = get_active_viewport()
        viewport.camera_path = camera_path
        app.update()
        print("[worker] stream camera ready", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[worker] camera setup skipped, using default viewport: {exc}", flush=True)
        return False


def _prim_valid(stage: Any, path: str) -> bool:
    """True when a USD prim exists at path on stage (works for fake stages too)."""
    if stage is None:
        return False
    try:
        prim = stage.GetPrimAtPath(path)
        if not prim:
            return False
        return bool(getattr(prim, "IsValid", lambda: True)())
    except Exception:  # noqa: BLE001
        return False


def _check_stream_scene_prims(stage: Any) -> tuple[bool, str]:
    """Verify the prims the stream must show exist before STREAM_VIEW_READY:
    the robot, at least one cube, and the target zone."""
    missing = []
    if not _prim_valid(stage, "/World/robot"):
        missing.append("/World/robot")
    if not any(
        _prim_valid(stage, path)
        for path in (
            "/World/red_cube",
            "/World/green_cube",
            "/World/red_cube_left",
            "/World/red_cube_right",
        )
    ):
        missing.append("/World/{red,green,red_cube_left,red_cube_right}_cube")
    if not _prim_valid(stage, "/World/zone_unstack_target"):
        missing.append("/World/zone_unstack_target")
    if missing:
        return False, "missing prims: " + ", ".join(missing)
    return True, ""


def _get_stream_stage() -> Any:
    from isaacsim.core.utils.stage import get_current_stage

    return get_current_stage()


def _try_native_viewport(
    app: Any, config: LiveWorldConfig
) -> tuple[dict[str, Any] | None, str | None]:
    """Configure the streaming Perspective View via Isaac Sim 6.0 native API.

    Official 6.0 classmethod API (there is NO get_instance() step):
      ViewportManager.set_camera("/OmniverseKit_Persp")
      ViewportManager.set_camera_view("/OmniverseKit_Persp", eye=..., target=...)
    set_resolution is best-effort only and must never fail camera config.
    Never rebuilds the stage / SimulationApp / WebRTC session / timeline.
    """
    try:
        from isaacsim.core.rendering_manager import ViewportManager
    except Exception as exc:  # noqa: BLE001
        return None, f"isaacsim.core.rendering_manager unavailable: {exc}"

    camera_path = "/OmniverseKit_Persp"
    eye = list(config.stream_camera_eye)
    target = list(config.stream_camera_target)
    try:
        ViewportManager.set_camera(camera_path)
        ViewportManager.set_camera_view(
            camera_path,
            eye=eye,
            target=target,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"native ViewportManager failed: {exc}"

    try:
        ViewportManager.set_resolution((1280, 720))
    except Exception:  # noqa: BLE001
        # resolution failure must not fail camera configuration
        pass

    for _ in range(STREAM_VIEW_WARMUP_FRAMES):
        app.update()

    return (
        {
            "mode": "viewport",
            "camera": camera_path,
            "api": "ViewportManager.set_camera_view",
        },
        None,
    )


def _try_usd_camera_fallback(app: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Fallback: /World/Camera + explicit look-at transform + viewport binding.

    Same invariants as the native path: no new stage, no new SimulationApp,
    no WebRTC restart, no experience change, timeline untouched.
    """
    try:
        ok = _ensure_stream_camera(app)
    except Exception as exc:  # noqa: BLE001
        return None, f"usd-camera setup raised: {exc}"
    if not ok:
        return None, "usd-camera setup reported failure"
    return {"mode": "usd-camera", "camera": "/World/Camera", "api": "ensure_stream_camera"}, None


def _configure_stream_view(app: Any, config: LiveWorldConfig) -> dict[str, Any]:
    """Configure the streaming Perspective View (native first, usd-camera
    fallback).  Never rebuilds the stage / SimulationApp / WebRTC session.

    Prints STREAM_VIEW_READY / STREAM_VIEW_FAILED / STREAM_VIEW_OFF.
    STREAM_VIEW_READY only means camera/view configuration succeeded -- it is
    NOT a WebRTC media readiness claim (that is judged by real client video).
    The worker keeps running even when the view cannot be configured
    (configured=false), it never fakes success.
    """
    mode = config.stream_view_mode
    if mode == "off":
        print("[worker] STREAM_VIEW_OFF (camera/view untouched)", flush=True)
        return {"mode": "off", "configured": False, "camera": None, "error": None}

    try:
        stage = _get_stream_stage()
    except Exception as exc:  # noqa: BLE001
        reason = f"get_current_stage failed: {exc}"
        print(f"[worker] STREAM_VIEW_FAILED reason={reason}", flush=True)
        return {"mode": mode, "configured": False, "camera": None, "error": reason}

    ok, missing = _check_stream_scene_prims(stage)
    if not ok:
        reason = f"scene prims not ready: {missing}"
        print(f"[worker] STREAM_VIEW_FAILED reason={reason}", flush=True)
        return {"mode": mode, "configured": False, "camera": None, "error": reason}

    result: dict[str, Any] | None = None
    error: str | None = None
    if mode in ("auto", "viewport"):
        result, error = _try_native_viewport(app, config)
    if result is None and mode in ("auto", "usd-camera"):
        if mode == "auto":
            print(
                f"[worker] native viewport unavailable ({error}); "
                "falling back to usd-camera",
                flush=True,
            )
        result, error = _try_usd_camera_fallback(app)

    if result is None:
        reason = error or "no stream view method succeeded"
        print(f"[worker] STREAM_VIEW_FAILED reason={reason}", flush=True)
        return {"mode": mode, "configured": False, "camera": None, "error": reason}

    print(
        f"[worker] STREAM_VIEW_READY mode={result['mode']} "
        f"camera={result['camera']} "
        f"eye={','.join(str(x) for x in config.stream_camera_eye)} "
        f"target={','.join(str(x) for x in config.stream_camera_target)} "
        f"api={result['api']}",
        flush=True,
    )
    return {
        "mode": result["mode"],
        "configured": True,
        "camera": result["camera"],
        "error": None,
    }


class PersistentIsaacSession:
    def __init__(
        self,
        *,
        app: Any,
        driver: Any,
        scene: Any,
        profile: Any,
        provider_factory: Callable[..., Any],
        adapter_factory: Callable[[Any, dict[str, Any], Any], Any],
        stream_view_state: dict[str, Any] | None = None,
    ) -> None:
        self.app = app
        self.driver = driver
        self.scene = scene
        self.profile = profile
        self.provider_factory = provider_factory
        self.adapter_factory = adapter_factory
        self.stream_view_state = stream_view_state or {}

    @property
    def stream_view_configured(self) -> bool:
        return bool(self.stream_view_state.get("configured"))

    @classmethod
    def create(cls, app: Any, config: LiveWorldConfig) -> "PersistentIsaacSession":
        from integration.adapters.executor import ExecutorAdapter
        from integration.config.loader import load_profile
        from modules.executor.isaac_driver import OmniDriver
        from modules.perception.isaac_ground_truth import IsaacGroundTruthProvider

        driver = OmniDriver(app, device=config.device)
        # STAGE LIFECYCLE INVARIANT (live worker):
        # The streaming Kit created this SimulationApp / USD context / Hydra
        # renderer / WebRTC session.  They must stay the SAME instance for the
        # whole worker lifetime, so NEVER call stage_utils.create_new_stage()
        # here (create_stage=False).  Rebuilding the stage closes the streaming
        # stage (World0 -> World1) and breaks the Hydra/RTX renderer, which
        # shows up as 'HydraEngine rtx failed creating scene renderer' and
        # NVST_R_BUSY / black WebRTC.  Only the batch runner rebuilds stages.
        driver.connect(defer_start=True, create_stage=False)
        # Franka / ground / light are created inside connect on the SAME
        # streaming stage; cubes + target are created here, also on that stage.
        scene = IsaacDynamicScene.create(app)
        driver.start()  # timeline play + reset + ~10 updates
        for _ in range(STREAM_VIEW_WARMUP_FRAMES):
            app.update()
        # Configure the Perspective Streaming View AFTER the scene exists and
        # the timeline is playing; then let the view transform apply a few
        # frames.  30 frames is NOT a stream-readiness wait.
        stream_view_state = _configure_stream_view(app, config)
        for _ in range(STREAM_VIEW_WARMUP_FRAMES):
            app.update()
        profile = load_profile("sim")
        return cls(
            app=app,
            driver=driver,
            scene=scene,
            profile=profile,
            provider_factory=IsaacGroundTruthProvider,
            adapter_factory=lambda profile, perception, received_driver: ExecutorAdapter.from_profile(
                profile, perception, driver=received_driver
            ),
            stream_view_state=stream_view_state,
        )

    def reset(self, job: dict[str, Any]) -> None:
        self.scene.reset(job)
        self.driver.reset_for_task()

    def prepare(self, job: dict[str, Any]) -> dict[str, Any]:
        provider = self.provider_factory(
            driver=self.driver,
            scene_id=str(job["scene_id"]),
            manifest=self.scene.manifest(job),
        )
        perception = provider.observe()
        perception["provenance"] = {
            "backend": "isaac_ground_truth",
            "run_id": job["run_id"],
            "source": "persistent_live_worker",
        }
        return {"perception.json": perception}

    def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        perception = deepcopy(job["perception"])
        strategy = deepcopy(job["strategy"])
        adapter = self.adapter_factory(self.profile, perception, self.driver)
        task = job.get("task") or {}
        task_id = str(task.get("task_id") or "")
        if not task_id:
            raise ValueError("ISAAC_EXECUTE task_id is required")
        target_ids = task.get("target_ids") or []
        object_id = str(target_ids[0]) if target_ids else ""
        before = self.driver.read_object_pose(object_id) if object_id else None
        execution = adapter.run(strategy)
        if execution.get("task_id") != task_id:
            raise ValueError("executor task_id drift")
        # Post-action pose is only meaningful for a SUCCEEDED run.  After a
        # SAFE_STOP (or any failure that engaged emergency stop) the driver is
        # e-stopped and read_object_pose raises "OmniDriver is in emergency
        # stop state", which must NOT overwrite the real execution evidence —
        # previously that crash dropped execution.json entirely and surfaced
        # only as the secondary "execution.json is missing".
        after = None
        if object_id and execution.get("status") == "SUCCEEDED":
            after = self.driver.read_object_pose(object_id)
        execution["object_id"] = object_id or None
        execution["object_before"] = before
        execution["object_after"] = after
        execution["input_strategy_sha256"] = job["strategy_sha256"]
        execution.setdefault("provenance", {}).update(
            {
                "backend": "isaac",
                "perception_backend": "isaac_ground_truth",
                "run_id": job["run_id"],
            }
        )
        final_pose = {
            "run_id": job["run_id"],
            "task_id": task_id,
            "object_id": object_id or None,
            "pose": after,
            "goal_reached": execution.get("status") == "SUCCEEDED",
            "provenance": {
                "backend": "isaac",
                "source": "live_usd_physx_driver",
            },
        }
        return {"execution.json": execution, "final_pose.json": final_pose}

    def step(self, *, render: bool = True) -> None:
        # Drive one Kit frame.  Plain app.update() keeps the livestream
        # encoder fed (verified on the live server); render=True is not part
        # of the stable SimulationApp.update() signature.
        del render
        self.app.update()

    def shutdown(self) -> None:
        self.driver.shutdown()


def _simulation_app_factory(config: dict[str, Any], *, experience: str):
    from isaacsim import SimulationApp

    return SimulationApp(config, experience=experience)


def build_live_world(
    config: LiveWorldConfig,
    *,
    simulation_app_factory: Callable[..., Any] | None = None,
    session_factory: Callable[[Any, LiveWorldConfig], Any] | None = None,
    runtime_worker_factory: Callable[..., LiveRuntimeWorker] = LiveRuntimeWorker,
    id_factory: Callable[[], str] = lambda: uuid4().hex,
) -> BuiltLiveWorld:
    app_factory = simulation_app_factory or _simulation_app_factory
    app = app_factory(
        {
            # Production target: 1280x720 for WebRTC / OBS / HLS.
            "headless": True,
            "width": 1280,
            "height": 720,
            "window_width": 1280,
            "window_height": 720,
        },
        experience=config.streaming_experience,
    )
    kit_instance_id = f"kit-{id_factory()}"
    world_id = f"world-{id_factory()}"
    try:
        world = (session_factory or PersistentIsaacSession.create)(app, config)
        worker = runtime_worker_factory(
            RuntimeLayout(config.runtime_root),
            execute=world.execute,
            prepare=world.prepare,
            reset=world.reset,
            worker_instance_id=kit_instance_id,
            world_id=world_id,
        )
    except Exception:
        import traceback

        # Print the real failure before any teardown: closing the app on a
        # partially-initialized streaming Kit can itself abort the process.
        traceback.print_exc()
        try:
            app.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return BuiltLiveWorld(
        app=app,
        world=world,
        runtime_worker=worker,
        runtime_root=config.runtime_root,
        kit_instance_id=kit_instance_id,
        world_id=world_id,
    )


def run_worker_loop(
    app: Any,
    world: Any,
    runtime_worker: Any,
    *,
    max_iterations: int | None = None,
    idle_sleep_s: float = 0.05,
    warmup_marker_after_s: float = 180.0,
    warmup_marker_every_s: float = 60.0,
) -> int:
    iterations = 0
    started_at = time.monotonic()
    next_marker_at = warmup_marker_after_s
    note_printed = False
    while app.is_running() and (
        max_iterations is None or iterations < max_iterations
    ):
        try:
            outcome = runtime_worker.process_once()
        except Exception as exc:  # noqa: BLE001
            # A transient job error must never tear down the streaming app.
            print(f"[worker] process_once error (continuing): {exc}", flush=True)
            outcome = {"status": "IDLE"}
        try:
            world.step(render=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[worker] step error (continuing): {exc}", flush=True)
        iterations += 1
        elapsed = time.monotonic() - started_at
        if elapsed >= next_marker_at:
            if not note_printed:
                # Honest semantics: this is ELAPSED TIME ONLY, never a
                # readiness claim.  Stream readiness is judged by the
                # operator seeing real media in the WebRTC client.
                note_printed = True
                print(
                    "[worker] STREAM_WARMUP note: markers report elapsed time "
                    "only, NOT stream readiness; open the WebRTC client and "
                    "check for real media. A single NVST_R_BUSY during app "
                    "load is expected and is not a failure (one client at a "
                    "time; do not spam reconnect)",
                    flush=True,
                )
            print(
                f"[worker] STREAM_WARMUP_ELAPSED {elapsed:.0f}s",
                flush=True,
            )
            next_marker_at += warmup_marker_every_s
        if outcome.get("status") == "IDLE" and idle_sleep_s > 0:
            time.sleep(idle_sleep_s)
    return iterations


def _parse_vec3(value: str) -> tuple[float, float, float]:
    """Parse 'X,Y,Z' into a 3-float tuple for camera framing CLI args."""
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"expected 3 comma-separated floats, got {value!r}"
        )
    try:
        return tuple(float(part) for part in parts)  # type: ignore[return-value]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected 3 comma-separated floats, got {value!r}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda", "cuda:0"), default="cuda")
    parser.add_argument("--idle-sleep-s", type=float, default=0.05)
    parser.add_argument(
        "--stream-view-mode",
        choices=STREAM_VIEW_MODES,
        default=None,
        help="how to configure the streaming Perspective View (default auto = "
        "native viewport API first, usd-camera fallback)",
    )
    parser.add_argument(
        "--stream-camera",
        action="store_true",
        help="DEPRECATED alias for --stream-view-mode usd-camera",
    )
    parser.add_argument(
        "--stream-camera-eye",
        type=_parse_vec3,
        default=None,
        metavar="X,Y,Z",
        help=f"camera eye, default {','.join(str(x) for x in DEFAULT_STREAM_CAMERA_EYE)}",
    )
    parser.add_argument(
        "--stream-camera-target",
        type=_parse_vec3,
        default=None,
        metavar="X,Y,Z",
        help=f"camera target, default {','.join(str(x) for x in DEFAULT_STREAM_CAMERA_TARGET)}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args, _kit_args = build_parser().parse_known_args(argv)
    stream_view_mode = args.stream_view_mode or (
        "usd-camera" if args.stream_camera else "auto"
    )
    config = LiveWorldConfig(
        device=args.device,
        idle_sleep_s=args.idle_sleep_s,
        stream_view_mode=stream_view_mode,
    )
    if args.stream_camera_eye is not None:
        config = replace(config, stream_camera_eye=args.stream_camera_eye)
    if args.stream_camera_target is not None:
        config = replace(config, stream_camera_target=args.stream_camera_target)
    built = build_live_world(config)
    print(
        json.dumps(
            {
                # WORKER_READY means the job loop / runtime is up.  It is NOT
                # a streaming readiness claim.
                "status": "WORKER_READY",
                "kit_instance_id": built.kit_instance_id,
                "world_id": built.world_id,
                "runtime_root": str(built.runtime_root),
                "stream_view_configured": bool(
                    getattr(built.world, "stream_view_configured", False)
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    print(
        "[worker] WORKER_READY != STREAM_VIEW_READY != WEBRTC MEDIA READY; "
        "the stream is judged by real video in the WebRTC client, never by "
        "elapsed time alone",
        flush=True,
    )
    try:
        run_worker_loop(
            built.app,
            built.world,
            built.runtime_worker,
            idle_sleep_s=args.idle_sleep_s,
            warmup_marker_after_s=180.0,
            warmup_marker_every_s=60.0,
        )
    finally:
        built.world.shutdown()
        built.app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
