"""Run one persistent Isaac Sim 6.0 world that consumes typed runtime jobs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class LiveWorldConfig:
    runtime_root: Path = DEFAULT_RUNTIME_ROOT
    streaming_experience: str = STREAMING_EXPERIENCE
    device: str = "cuda"
    idle_sleep_s: float = 0.05

    def __post_init__(self) -> None:
        if Path(self.runtime_root) != DEFAULT_RUNTIME_ROOT:
            raise ValueError(f"runtime_root is fixed at {DEFAULT_RUNTIME_ROOT}")
        if self.streaming_experience != STREAMING_EXPERIENCE:
            raise ValueError("the verified Isaac streaming experience cannot be replaced")
        if self.device not in {"cpu", "cuda", "cuda:0"}:
            raise ValueError("device must be cpu, cuda or cuda:0")
        if self.idle_sleep_s < 0:
            raise ValueError("idle_sleep_s must not be negative")


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
    ) -> None:
        self.app = app
        self.driver = driver
        self.scene = scene
        self.profile = profile
        self.provider_factory = provider_factory
        self.adapter_factory = adapter_factory

    @classmethod
    def create(cls, app: Any, config: LiveWorldConfig) -> "PersistentIsaacSession":
        from integration.adapters.executor import ExecutorAdapter
        from integration.config.loader import load_profile
        from modules.executor.isaac_driver import OmniDriver
        from modules.perception.isaac_ground_truth import IsaacGroundTruthProvider

        driver = OmniDriver(app, device=config.device)
        driver.connect(defer_start=True)
        scene = IsaacDynamicScene.create(app)
        driver.start()
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
        target_ids = task.get("target_ids") or []
        object_id = str(target_ids[0]) if target_ids else ""
        before = self.driver.read_object_pose(object_id) if object_id else None
        execution = adapter.run(strategy)
        after = self.driver.read_object_pose(object_id) if object_id else None
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
            "task_id": job["run_id"],
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
        {"headless": True}, experience=config.streaming_experience
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
        app.close()
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
) -> int:
    iterations = 0
    while app.is_running() and (
        max_iterations is None or iterations < max_iterations
    ):
        outcome = runtime_worker.process_once()
        world.step(render=True)
        iterations += 1
        if outcome.get("status") == "IDLE" and idle_sleep_s > 0:
            time.sleep(idle_sleep_s)
    return iterations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda", "cuda:0"), default="cuda")
    parser.add_argument("--idle-sleep-s", type=float, default=0.05)
    return parser


def main(argv: list[str] | None = None) -> int:
    args, _kit_args = build_parser().parse_known_args(argv)
    built = build_live_world(
        LiveWorldConfig(device=args.device, idle_sleep_s=args.idle_sleep_s)
    )
    print(
        json.dumps(
            {
                "status": "READY",
                "kit_instance_id": built.kit_instance_id,
                "world_id": built.world_id,
                "runtime_root": str(built.runtime_root),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        run_worker_loop(
            built.app,
            built.world,
            built.runtime_worker,
            idle_sleep_s=args.idle_sleep_s,
        )
    finally:
        built.world.shutdown()
        built.app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
