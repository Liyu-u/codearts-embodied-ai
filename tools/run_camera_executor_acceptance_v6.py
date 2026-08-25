"""Camera C runner that preserves evaluation evidence after a safe stop."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import run_camera_executor_acceptance_v5 as final_runner


_original_read_pose = final_runner.enriched.target.main.__globals__["FrankaPickPlaceDriver"].read_object_pose if "FrankaPickPlaceDriver" in final_runner.enriched.target.main.__globals__ else None


def _install_evaluation_pose_read() -> None:
    from modules.executor.isaac_driver import FrankaPickPlaceDriver

    original = FrankaPickPlaceDriver.read_object_pose

    def read_object_pose(self, object_id: str):
        try:
            return original(self, object_id)
        except Exception:
            if not getattr(self, "_stopped", False):
                raise
            from pxr import UsdGeom
            import omni.usd

            stage = omni.usd.get_context().get_stage()
            prim = stage.GetPrimAtPath(f"/World/{object_id}")
            if not prim or not prim.IsValid():
                raise
            pos = UsdGeom.XformCache().GetLocalToWorldTransform(prim).ExtractTranslation()
            return {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2])}

    FrankaPickPlaceDriver.read_object_pose = read_object_pose


_install_evaluation_pose_read()


if __name__ == "__main__":
    raise SystemExit(final_runner.enriched.target.main(sys.argv[1:]))
