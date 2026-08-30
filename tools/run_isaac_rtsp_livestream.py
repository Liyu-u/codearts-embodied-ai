"""Run the project's camera scene as a persistent Isaac Sim RTSP stream.

The runner intentionally owns only the video path.  It does not run the
ground-truth executor or expose the Isaac Sim editor UI.  Isaac Sim creates a
render product for the existing overhead camera and the official
``RTSPCameraHelper`` publishes H.264 over RTSP.

The script is designed for the offline Isaac Sim 6.0 container and accepts
Kit arguments through ``parse_known_args`` so callers can pass flags such as
``--/app/headless=true``.  It uses the official timeline-driven
``RTSPCameraHelper`` graph so the server receives frames in headless mode.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


CAMERA_PRIM = "/World/Sensors/overhead_rgbd"


def _stage(label: str) -> None:
    print(f"RTSP_STAGE {label}", flush=True)


def _enable_extensions(app) -> None:
    _stage("extensions_start")
    import omni.kit.app

    extension_manager = omni.kit.app.get_app().get_extension_manager()
    for extension in ("isaacsim.core.nodes", "isaacsim.streaming.rtsp"):
        extension_manager.set_extension_enabled_immediate(extension, True)
    _stage("extensions_enabled_before_update")
    app.update()
    _stage("extensions_ready")


def _spawn_scene_and_camera(app, fps: float, width: int, height: int):
    """Create the same labeled workcell camera used by the real camera path."""

    _stage("scene_imports_start")
    import omni.usd
    from pxr import Gf, UsdGeom
    from tools.run_isaac_camera_perception import _spawn_camera_scene

    _stage("scene_spawn_start")
    _spawn_camera_scene()
    _stage("scene_spawn_ready")
    stage = omni.usd.get_context().get_stage()
    camera = UsdGeom.Camera.Define(stage, CAMERA_PRIM)
    xform = UsdGeom.XformCommonAPI(camera.GetPrim())
    xform.SetTranslate(Gf.Vec3d(0.5, 0.0, 1.2))
    xform.SetRotate(Gf.Vec3f(0.0, 0.0, 0.0))
    camera.GetFocalLengthAttr().Set(24.0)
    camera.GetHorizontalApertureAttr().Set(9.6)
    _stage("usd_camera_created")
    # Commit the camera transform before the render product is created.
    _stage("camera_commit_start")
    app.update()
    _stage("camera_commit_ready")
    prim = stage.GetPrimAtPath(CAMERA_PRIM)
    if not prim or not prim.IsValid():
        raise RuntimeError(f"camera prim was not created: {CAMERA_PRIM}")
    return camera


def _attach_graph(width: int, height: int, port: int, mount_path: str):
    """Build the official timeline-driven RTSPCameraHelper graph."""

    _stage("graph_imports_start")
    import omni.graph.core as og

    _stage("graph_edit_start")
    og.Controller.edit(
        {"graph_path": "/RTSPGraph", "evaluator_name": "execution"},
        {
            og.Controller.Keys.CREATE_NODES: [
                ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                ("CreateRenderProduct", "isaacsim.core.nodes.IsaacCreateRenderProduct"),
                ("RTSPHelper", "isaacsim.streaming.rtsp.RTSPCameraHelper"),
            ],
            og.Controller.Keys.SET_VALUES: [
                ("CreateRenderProduct.inputs:cameraPrim", CAMERA_PRIM),
                ("CreateRenderProduct.inputs:width", int(width)),
                ("CreateRenderProduct.inputs:height", int(height)),
                ("RTSPHelper.inputs:port", int(port)),
                ("RTSPHelper.inputs:mountPath", mount_path),
                ("RTSPHelper.inputs:useRawEncoding", False),
                ("RTSPHelper.inputs:enabled", True),
            ],
            og.Controller.Keys.CONNECT: [
                ("OnPlaybackTick.outputs:tick", "CreateRenderProduct.inputs:execIn"),
                ("CreateRenderProduct.outputs:execOut", "RTSPHelper.inputs:execIn"),
                (
                    "CreateRenderProduct.outputs:renderProductPath",
                    "RTSPHelper.inputs:renderProductPath",
                ),
            ],
        },
    )
    _stage("graph_ready")
    return "/RTSPGraph"


def _port_is_listening(port: int) -> bool:
    """Inspect Linux TCP state without opening a dummy RTSP client session."""

    target = f":{int(port):04X}".upper()
    for proc_path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(proc_path, encoding="ascii") as handle:
                for line in handle:
                    fields = line.split()
                    if len(fields) < 4:
                        continue
                    local_address = fields[1].upper()
                    state = fields[3].upper()
                    if local_address.endswith(target) and state == "0A":
                        return True
        except OSError:
            continue
    return False


def _wait_until_listening(app, port: int, mount_path: str, timeout_s: float) -> None:
    _stage("rtsp_wait_start")
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        app.update()
        if _port_is_listening(port):
            print(f"RTSP_READY port={port} path={mount_path} camera={CAMERA_PRIM}", flush=True)
            return
        time.sleep(0.02)
    raise TimeoutError(f"RTSP server did not listen on 127.0.0.1:{port}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8554)
    parser.add_argument("--mount-path", default="/stream")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--startup-timeout", type=float, default=60.0)
    args, _ = parser.parse_known_args(argv)

    if not args.mount_path.startswith("/"):
        parser.error("--mount-path must start with '/'")
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in 1..65535")
    if args.width <= 0 or args.height <= 0 or args.fps <= 0:
        parser.error("width, height and fps must be positive")

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    _stage("simulation_app_ready")
    timeline = None
    stop_requested = False

    def request_stop(_signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    try:
        _enable_extensions(app)
        camera = _spawn_scene_and_camera(app, args.fps, args.width, args.height)
        graph_path = _attach_graph(
            args.width,
            args.height,
            args.port,
            args.mount_path,
        )
        # Keep strong references to the camera and graph path while the loop runs.
        _ = (camera, graph_path)
        _stage("graph_update_start")
        app.update()
        _stage("graph_update_ready")

        import omni.replicator.core as rep
        import omni.timeline
        import isaacsim.core.experimental.utils.app as app_utils

        timeline = omni.timeline.get_timeline_interface()
        if hasattr(timeline, "set_auto_update"):
            timeline.set_auto_update(True)
        rep.orchestrator.set_capture_on_play(True)
        app_utils.play()
        _stage(f"timeline_requested={timeline.is_playing()}")
        for _ in range(30):
            app.update()
        _stage(f"timeline_after_warmup={timeline.is_playing()}")
        _wait_until_listening(app, args.port, args.mount_path, args.startup_timeout)

        while app.is_running() and not stop_requested:
            app.update()
            time.sleep(0.01)
        return 0
    except Exception as exc:  # noqa: BLE001 - log a useful container failure
        print(f"RTSP_FAILED {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2
    finally:
        if timeline is not None:
            try:
                timeline.stop()
            except Exception:
                pass
        try:
            app.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
