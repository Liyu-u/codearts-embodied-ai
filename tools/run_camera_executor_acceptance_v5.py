"""Final camera C entry: keep the controller's ground plane and add only light."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_camera_executor_acceptance as camera_base
from tools import run_camera_executor_acceptance_v3 as enriched


def _add_camera_scene_without_extra_floor() -> None:
    from pxr import Gf, UsdLux
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    dome = UsdLux.DomeLight.Define(stage, "/World/Sensors/CameraDomeLight")
    dome.CreateIntensityAttr(1200.0)
    dome.CreateColorAttr(Gf.Vec3f(1.0, 1.0, 1.0))


camera_base._add_camera_scene = _add_camera_scene_without_extra_floor


if __name__ == "__main__":
    raise SystemExit(enriched.target.main(sys.argv[1:]))
