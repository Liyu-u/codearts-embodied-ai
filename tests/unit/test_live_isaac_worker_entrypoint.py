from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from modules.executor.isaac_driver import OmniDriver
from tests.unit.test_cloud_relay_isaac_job import execute_job
from tests.unit.test_cloud_orchestrator import perception_document
from tools.run_live_isaac_worker import (
    DEFAULT_RUNTIME_ROOT,
    STREAMING_EXPERIENCE,
    IsaacDynamicScene,
    LiveWorldConfig,
    PersistentIsaacSession,
    build_live_world,
    run_worker_loop,
)


class FakeApp:
    def __init__(self) -> None:
        self.updates = 0
        self.closed = 0
        self.running = True

    def update(self) -> None:
        self.updates += 1

    def is_running(self) -> bool:
        return self.running

    def close(self) -> None:
        self.closed += 1


class FakeSession:
    def __init__(self) -> None:
        self.steps = 0

    def reset(self, _job):
        return None

    def prepare(self, job):
        return {"perception.json": perception_document(job["run_id"])}

    def execute(self, job):
        return {"execution.json": {}, "final_pose.json": {}}

    def step(self, *, render=True):
        self.steps += 1


class FakeRuntimeWorker:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.calls = 0

    def process_once(self):
        self.calls += 1
        return {"status": "IDLE"}


class LiveIsaacEntrypointTests(unittest.TestCase):
    def test_scene_manifest_matches_normal_and_two_red_scenario_membership(self) -> None:
        handles = {name: object() for name in ("red_cube", "green_cube", "red_cube_left", "red_cube_right")}
        scene = IsaacDynamicScene(FakeApp(), handles)
        normal = scene.manifest(
            {
                "object_id": "red_cube",
                "destination_id": "zone_unstack_target",
                "initial_scene_poses": {},
            }
        )
        two_red = scene.manifest(
            {
                "object_id": "red_cube_right",
                "destination_id": "zone_unstack_target",
                "initial_scene_poses": {
                    "red_cube_left": {"x": 0.46, "y": -0.14, "z": 0.0258},
                    "red_cube_right": {"x": 0.60, "y": -0.04, "z": 0.0258},
                },
            }
        )

        self.assertEqual([item["id"] for item in normal], ["red_cube", "green_cube", "zone_unstack_target"])
        self.assertEqual([item["id"] for item in two_red], ["red_cube_left", "red_cube_right", "zone_unstack_target"])

    def test_build_constructs_one_streaming_app_and_fixed_runtime_worker(self) -> None:
        app = FakeApp()
        app_calls = []
        session = FakeSession()
        worker_instances = []

        def app_factory(config, *, experience):
            app_calls.append((config, experience))
            return app

        def session_factory(received_app, _config):
            self.assertIs(received_app, app)
            return session

        def worker_factory(*args, **kwargs):
            value = FakeRuntimeWorker(*args, **kwargs)
            worker_instances.append(value)
            return value

        built = build_live_world(
            LiveWorldConfig(),
            simulation_app_factory=app_factory,
            session_factory=session_factory,
            runtime_worker_factory=worker_factory,
            id_factory=lambda: "stable-id",
        )

        self.assertEqual(len(app_calls), 1)
        self.assertEqual(app_calls[0][1], STREAMING_EXPERIENCE)
        self.assertTrue(app_calls[0][0]["headless"])
        self.assertEqual(built.runtime_root, DEFAULT_RUNTIME_ROOT)
        self.assertEqual(worker_instances[0].args[0].root, DEFAULT_RUNTIME_ROOT)
        self.assertEqual(worker_instances[0].kwargs["worker_instance_id"], "kit-stable-id")
        self.assertEqual(worker_instances[0].kwargs["world_id"], "world-stable-id")

    def test_loop_steps_same_world_without_closing_app_or_launching_processes(self) -> None:
        app = FakeApp()
        session = FakeSession()
        worker = FakeRuntimeWorker()
        with patch("subprocess.run", side_effect=AssertionError("no subprocess")), patch(
            "subprocess.Popen", side_effect=AssertionError("no subprocess")
        ):
            iterations = run_worker_loop(
                app,
                session,
                worker,
                max_iterations=3,
                idle_sleep_s=0,
            )

        self.assertEqual(iterations, 3)
        self.assertEqual(worker.calls, 3)
        self.assertEqual(session.steps, 3)
        self.assertEqual(app.closed, 0)

    def test_session_reuses_driver_for_ground_truth_and_executor_evidence(self) -> None:
        calls = []

        class Driver:
            def reset_for_task(self):
                calls.append("driver-reset")

            def read_object_pose(self, object_id):
                return {"x": 0.5, "y": 0.1, "z": 0.03, "object_id": object_id}

        class Scene:
            def reset(self, job):
                calls.append(("scene-reset", job["run_id"]))

            def manifest(self, _job):
                return [{"id": "red_cube", "category": "cube"}]

        class Provider:
            def observe(self):
                calls.append("observe")
                return perception_document("run-001")

        class Adapter:
            def run(self, strategy):
                calls.append(("execute", strategy["task_id"]))
                return {
                    "schema_version": "execution.v1",
                    "task_id": strategy["task_id"],
                    "status": "SUCCEEDED",
                    "steps": [],
                    "provenance": {"backend": "isaac"},
                }

        driver = Driver()
        session = PersistentIsaacSession(
            app=FakeApp(),
            driver=driver,
            scene=Scene(),
            profile=object(),
            provider_factory=lambda **kwargs: Provider(),
            adapter_factory=lambda profile, perception, received_driver: Adapter(),
        )
        prepare = {
            "schema_version": "cloud-job.v1",
            "job_type": "ISAAC_PREPARE_AND_PERCEIVE",
            "run_id": "run-001",
            "case_id": "multi-red-001",
            "scene_id": "multi_object_stacking",
        }
        session.reset(prepare)
        perception = session.prepare(prepare)["perception.json"]
        job = execute_job("run-001")
        job["perception"] = perception

        artifacts = session.execute(job)

        self.assertEqual(calls[:3], [("scene-reset", "run-001"), "driver-reset", "observe"])
        self.assertIn(("execute", "run-001"), calls)
        self.assertEqual(perception["provenance"]["backend"], "isaac_ground_truth")
        self.assertEqual(artifacts["execution.json"]["input_strategy_sha256"], job["strategy_sha256"])
        self.assertEqual(artifacts["final_pose.json"]["task_id"], "run-001")

    def test_omni_driver_reset_reuses_existing_franka_and_clears_stop_state(self) -> None:
        app = FakeApp()

        class Franka:
            def __init__(self):
                self.resets = 0

            def reset_to_default_pose(self):
                self.resets += 1

        timeline = types.SimpleNamespace(play_calls=0)

        def play():
            timeline.play_calls += 1

        timeline.play = play
        timeline_module = types.ModuleType("omni.timeline")
        timeline_module.get_timeline_interface = lambda: timeline
        omni_module = types.ModuleType("omni")
        omni_module.timeline = timeline_module
        driver = OmniDriver(app)
        driver._connected = True
        driver._started = True
        driver._stopped = True
        driver._franka = Franka()
        with patch.dict(sys.modules, {"omni": omni_module, "omni.timeline": timeline_module}):
            driver.reset_for_task()

        self.assertEqual(driver._franka.resets, 1)
        self.assertFalse(driver._stopped)
        self.assertGreaterEqual(app.updates, 1)
        self.assertEqual(timeline.play_calls, 1)

    def test_persistent_session_never_rebuilds_the_streaming_stage(self) -> None:
        """Regression: the live worker must keep the streaming app's
        SimulationApp / USD context / Hydra renderer / WebRTC session as ONE
        instance.  It must call driver.connect(create_stage=False) — never
        stage_utils.create_new_stage() — and must not touch the custom stream
        camera by default (round 1 isolation)."""
        app = FakeApp()
        connect_calls = []
        start_calls = []

        class FakeOmniDriver:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def connect(self, *, defer_start, create_stage):
                connect_calls.append({"defer_start": defer_start, "create_stage": create_stage})

            def start(self):
                start_calls.append(True)

            def shutdown(self):
                pass

        class FakeScene:
            @staticmethod
            def create(received_app):
                self.assertIs(received_app, app)
                return object()

        camera_calls = []
        with (
            patch("modules.executor.isaac_driver.OmniDriver", FakeOmniDriver),
            patch("integration.config.loader.load_profile", return_value=object()),
            patch("tools.run_live_isaac_worker.IsaacDynamicScene", FakeScene),
            patch(
                "tools.run_live_isaac_worker._ensure_stream_camera",
                side_effect=lambda a: camera_calls.append(a),
            ),
        ):
            PersistentIsaacSession.create(app, LiveWorldConfig())

        self.assertEqual(len(connect_calls), 1)
        self.assertIs(connect_calls[0]["defer_start"], True)
        self.assertIs(connect_calls[0]["create_stage"], False)
        self.assertEqual(len(start_calls), 1)
        # Round 1 isolation: the custom stream camera is OFF by default.
        self.assertEqual(camera_calls, [])

    def test_stream_camera_switch_is_off_by_default_and_validated(self) -> None:
        self.assertIs(LiveWorldConfig().stream_camera, False)
        self.assertIs(LiveWorldConfig(stream_camera=True).stream_camera, True)
        with self.assertRaises(ValueError):
            LiveWorldConfig(stream_camera="yes")

    def test_loop_prints_warmup_elapsed_only_never_stream_ready(self) -> None:
        import io
        from contextlib import redirect_stdout

        app = FakeApp()
        session = FakeSession()
        worker = FakeRuntimeWorker()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            iterations = run_worker_loop(
                app,
                session,
                worker,
                max_iterations=3,
                idle_sleep_s=0,
                warmup_marker_after_s=0.0,
                warmup_marker_every_s=1e9,
            )
        output = buffer.getvalue()
        self.assertEqual(iterations, 3)
        self.assertEqual(worker.calls, 3)
        self.assertEqual(session.steps, 3)
        # Honest semantics: elapsed time only, never a readiness claim.
        self.assertIn("STREAM_WARMUP_ELAPSED", output)
        self.assertNotIn("STREAM_READY", output)
        self.assertNotIn("WORKER_READY", output)

    def test_runtime_root_cannot_be_redirected_to_an_arbitrary_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                LiveWorldConfig(runtime_root=Path(directory))


if __name__ == "__main__":
    unittest.main()
