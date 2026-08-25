"""Real Isaac Sim RGB-D/semantic camera perception smoke runner.

This entrypoint creates the same stacking-cubes scene used by C, adds a fixed
overhead RTX camera, and writes both the formal camera observation and the
internal perception.v1 scene.  It never reads object poses from USD/PhysX for
the online result; those poses remain an evaluation-only concern.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _spawn_camera_scene() -> None:
    from tools.run_executor_acceptance import _spawn_objects

    _spawn_objects(include_dynamic=True)
    try:
        from isaacsim.core.experimental.utils.semantics import add_labels
    except ImportError:
        # Isaac Sim 5.x fallback kept for older deployment images.
        from omni.isaac.core.utils.semantics import add_update_semantics

        for object_id in ("red_cube", "green_cube", "zone_unstack_target"):
            add_update_semantics(f"/World/{object_id}", [("class", object_id)])
        return
    for object_id in ("red_cube", "green_cube", "zone_unstack_target"):
        add_labels(f"/World/{object_id}", labels=[object_id], taxonomy="class")


def _create_camera():
    import numpy as np
    import omni.usd
    from pxr import UsdGeom
    from isaacsim.sensors.experimental.rtx import CameraSensor, RtxCamera

    # Identity orientation means the USD camera looks down its local -Z axis.
    # The camera is above the workcell and uses a known pinhole model for
    # depth-to-world backprojection in IsaacCameraObservationProvider.
    camera = RtxCamera(
        "/World/Sensors/overhead_rgbd",
        tick_rate=15.0,
        positions=np.array([[0.5, 0.0, 1.2]], dtype=float),
        orientations=np.array([[1.0, 0.0, 0.0, 0.0]], dtype=float),
    )
    height, width = 480, 640

    # Isaac Sim 6.0 containers can expose USD camera optical attributes in
    # version-dependent units.  Keep the runtime camera optics unchanged and
    # use one explicit calibrated pinhole model for this 640x480 workcell
    # instead of trusting a version-dependent getter or mutating the view.
    # The 4:3 aperture ratio keeps square pixels: fx == fy == 1600 px.
    calibrated_focal = 24.0
    calibrated_horizontal_aperture = 9.6
    # Keep the pinhole values in pixel units; these are the values consumed by
    # the RGB-D backprojection code, independent of the runtime wrapper API.
    fx = width * calibrated_focal / calibrated_horizontal_aperture
    fy = fx
    cx, cy = width / 2.0 - 0.5, height / 2.0 - 0.5
    transform = np.eye(4, dtype=float)
    transform[:3, 3] = [0.5, 0.0, 1.2]
    try:
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath("/World/Sensors/overhead_rgbd")
        matrix = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        candidate = np.asarray([[float(matrix[row][col]) for col in range(4)] for row in range(4)], dtype=float)
        # RtxCamera may not have committed its batched position to USD until
        # the first app tick.  Do not accept an all-zero translation here.
        if np.isfinite(candidate).all() and float(candidate[2, 3]) > 0.5:
            transform = candidate
    except Exception:  # noqa: BLE001 - known pose fallback for old runtimes
        pass

    sensor = CameraSensor(
        camera,
        # Isaac Sim 6 follows the NumPy/OpenCV convention: (height, width).
        resolution=(height, width),
        annotators=[
            "rgb",
            "distance_to_image_plane",
            "semantic_segmentation",
            "instance_id_segmentation",
        ],
    )
    camera_model = {
        "fx": float(fx),
        "fy": float(fy),
        "cx": float(cx),
        "cy": float(cy),
        "image_width": width,
        "image_height": height,
        "intrinsics_source": "explicit_sim_calibration",
        "calibration": {
            "focal_length": calibrated_focal,
            "horizontal_aperture": calibrated_horizontal_aperture,
            "vertical_aperture": calibrated_horizontal_aperture * height / width,
        },
        "world_from_camera": transform.tolist(),
    }
    return sensor, camera_model


def _save_rgb(sensor, result_dir: Path) -> None:
    try:
        import numpy as np
        from PIL import Image

        data = sensor.get_data("rgb")
        rgb = data[0] if isinstance(data, tuple) else data
        if hasattr(rgb, "numpy"):
            rgb = rgb.numpy()
        rgb = np.asarray(rgb)
        if rgb.ndim == 3 and rgb.shape[-1] >= 3:
            Image.fromarray(rgb[..., :3].astype(np.uint8)).save(result_dir / "rgb.png")
    except Exception as exc:  # noqa: BLE001 - image preview is auxiliary evidence
        (result_dir / "rgb_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--frames", type=int, default=180)
    args, _ = parser.parse_known_args(argv)
    result_dir = Path(args.result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    print("[CAMERA] starting SimulationApp", flush=True)
    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import omni.timeline

        from integration.contract_validation import assert_contract
        from modules.perception.isaac_camera import IsaacCameraObservationProvider
        from modules.perception.observation_normalizer import normalize_observation

        _spawn_camera_scene()
        sensor, camera_model = _create_camera()
        provider = IsaacCameraObservationProvider(sensor, camera_model)
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()

        # RTX render products and semantic annotators need a few ticks after
        # the stage is built before their id-to-label map is complete.
        for _ in range(30):
            app.update()

        observation = None
        errors: list[str] = []
        for frame in range(max(args.frames, 1)):
            app.update()
            try:
                candidate = provider.observe()
            except (RuntimeError, ValueError) as exc:
                errors.append(f"frame={frame}: {type(exc).__name__}: {exc}")
                continue
            observation = candidate
            # A valid camera frame with all three labeled scene objects is the
            # acceptance target.  Keep looping briefly if RTX is still warming.
            if len(candidate.get("objects", [])) >= 3:
                break

        if observation is None:
            raise RuntimeError("camera did not produce a valid RGB/depth/segmentation frame")
        scene = normalize_observation(observation)
        assert_contract(observation, "perception_observation.1.0.0")
        assert_contract(scene, "perception.v1")

        _write(result_dir / "camera_observation.json", observation)
        _write(result_dir / "perception.json", scene)
        _write(
            result_dir / "camera_metrics.json",
            {
                "schema_version": "isaac-camera-metrics.v1",
                "frames_attempted": frame + 1,
                "provider": provider.health(),
                "errors": errors[-20:],
                "source": observation["source"],
                "online_pose_source": "rgbd_depth_backprojection",
                "ground_truth_used_for_online_pose": False,
            },
        )
        _save_rgb(sensor, result_dir)
        print(
            f"[CAMERA] success objects={len(observation['objects'])} "
            f"depth_valid={provider.last_metrics.get('depth_valid_ratio')}",
            flush=True,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        (result_dir / "camera_error.json").write_text(
            json.dumps(
                {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[CAMERA] failed: {type(exc).__name__}: {exc}", flush=True)
        return 2
    finally:
        try:
            app.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
