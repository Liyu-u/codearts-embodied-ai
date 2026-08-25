"""Canonical real Isaac Sim RGB-D camera perception runner."""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.perception import isaac_camera as base  # noqa: E402
from modules.perception.isaac_camera_real import IsaacCameraRealObservationProvider  # noqa: E402
from tools import run_isaac_camera_perception as runner  # noqa: E402


base.IsaacCameraObservationProvider = IsaacCameraRealObservationProvider

_original_spawn_camera_scene = runner._spawn_camera_scene


def _spawn_camera_scene_with_workcell() -> None:
    _original_spawn_camera_scene()
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


runner._spawn_camera_scene = _spawn_camera_scene_with_workcell


if __name__ == "__main__":
    raise SystemExit(runner.main())
