from __future__ import annotations

import argparse
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
    STREAM_VIEW_MODES,
    STREAMING_EXPERIENCE,
    IsaacDynamicScene,
    LiveWorldConfig,
    PersistentIsaacSession,
    _check_stream_scene_prims,
    _configure_stream_view,
    _parse_vec3,
    _try_native_viewport,
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


class FakeStage:
    """Minimal stage double: GetPrimAtPath returns a valid prim or None."""

    def __init__(self, paths) -> None:
        self.paths = set(paths)

    def GetPrimAtPath(self, path):
        if path in self.paths:
            return types.SimpleNamespace(IsValid=lambda: True)
        return None


FULL_SCENE_PATHS = {
    "/World/robot",
    "/World/red_cube",
    "/World/green_cube",
    "/World/red_cube_left",
    "/World/red_cube_right",
    "/World/zone_unstack_target",
}


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
        # Production stream target: 1280x720 for WebRTC / OBS / HLS.
        self.assertEqual(app_calls[0][0]["width"], 1280)
        self.assertEqual(app_calls[0][0]["height"], 720)
        self.assertEqual(app_calls[0][0]["window_width"], 1280)
        self.assertEqual(app_calls[0][0]["window_height"], 720)
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
        self.assertIn(("execute", "task-run-001"), calls)
        self.assertEqual(perception["provenance"]["backend"], "isaac_ground_truth")
        self.assertEqual(artifacts["execution.json"]["input_strategy_sha256"], job["strategy_sha256"])
        self.assertEqual(
            artifacts["final_pose.json"]["run_id"],
            "run-001",
        )
        self.assertEqual(
            artifacts["final_pose.json"]["task_id"],
            "task-run-001",
        )

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
        instance.  connect(create_stage=False) must be used exactly once, the
        stream view must be configured on the same session, and reset must
        never rebuild the stage / SimulationApp / streaming."""
        app = FakeApp()
        connect_calls = []
        start_calls = []
        reset_calls = []
        view_calls = []

        class FakeOmniDriver:
            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs

            def connect(self, *, defer_start, create_stage):
                connect_calls.append({"defer_start": defer_start, "create_stage": create_stage})

            def start(self):
                start_calls.append(True)

            def reset_for_task(self):
                reset_calls.append("driver-reset")

            def shutdown(self):
                pass

        class FakeScene:
            @staticmethod
            def create(received_app):
                self.assertIs(received_app, app)
                return types.SimpleNamespace(reset=lambda _job: reset_calls.append("scene-reset"))

        def fake_configure_view(received_app, received_config):
            self.assertIs(received_app, app)
            self.assertEqual(received_config.stream_view_mode, "auto")
            view_calls.append(received_config.stream_view_mode)
            return {"mode": "viewport", "configured": True, "camera": "/OmniverseKit_Persp"}

        with (
            patch("modules.executor.isaac_driver.OmniDriver", FakeOmniDriver),
            patch("integration.config.loader.load_profile", return_value=object()),
            patch("tools.run_live_isaac_worker.IsaacDynamicScene", FakeScene),
            patch("tools.run_live_isaac_worker._configure_stream_view", side_effect=fake_configure_view),
        ):
            session = PersistentIsaacSession.create(app, LiveWorldConfig())
            session.reset({"run_id": "run-001", "task": {}})

        self.assertEqual(len(connect_calls), 1)
        self.assertIs(connect_calls[0]["defer_start"], True)
        self.assertIs(connect_calls[0]["create_stage"], False)
        self.assertEqual(len(start_calls), 1)
        # The stream view is configured once, after the scene exists.
        self.assertEqual(view_calls, ["auto"])
        self.assertTrue(session.stream_view_configured)
        self.assertEqual(session.stream_view_state["camera"], "/OmniverseKit_Persp")
        # reset() reuses the same stage/SimulationApp/streaming: no extra
        # connect, no start, no rebuild.
        self.assertEqual(len(connect_calls), 1)
        self.assertEqual(len(start_calls), 1)
        self.assertEqual(reset_calls, ["scene-reset", "driver-reset"])

    def test_stream_view_mode_defaults_to_auto_and_validated(self) -> None:
        self.assertEqual(LiveWorldConfig().stream_view_mode, "auto")
        for mode in STREAM_VIEW_MODES:
            self.assertEqual(LiveWorldConfig(stream_view_mode=mode).stream_view_mode, mode)
        with self.assertRaises(ValueError):
            LiveWorldConfig(stream_view_mode="bogus")

    def test_stream_camera_eye_target_defaults_and_validation(self) -> None:
        config = LiveWorldConfig()
        self.assertEqual(config.stream_camera_eye, (1.35, -1.45, 1.05))
        self.assertEqual(config.stream_camera_target, (0.40, 0.00, 0.25))
        self.assertEqual(
            LiveWorldConfig(stream_camera_eye=(2.0, -1.0, 0.5)).stream_camera_eye,
            (2.0, -1.0, 0.5),
        )
        self.assertEqual(
            LiveWorldConfig(stream_camera_target=(0.1, 0.2, 0.3)).stream_camera_target,
            (0.1, 0.2, 0.3),
        )
        with self.assertRaises(ValueError):
            LiveWorldConfig(stream_camera_eye=(1.0, 2.0))
        with self.assertRaises(ValueError):
            LiveWorldConfig(stream_camera_target=("a", "b", "c"))

    def test_parse_vec3_cli(self) -> None:
        self.assertEqual(_parse_vec3("1.35,-1.45,1.05"), (1.35, -1.45, 1.05))
        self.assertEqual(_parse_vec3(" 0.40, 0.00, 0.25 "), (0.40, 0.00, 0.25))
        with self.assertRaises(argparse.ArgumentTypeError):
            _parse_vec3("1,2")
        with self.assertRaises(argparse.ArgumentTypeError):
            _parse_vec3("a,b,c")

    def test_try_native_viewport_uses_isaac60_classmethod_api(self) -> None:
        """Isaac Sim 6.0 classmethod API: no get_instance() step; set_camera +
        set_camera_view(camera, *, eye=..., target=...) on the class, plus a
        best-effort set_resolution((1280, 720)) and 30 warm-up frames."""
        calls = []

        class FakeViewportManager:
            @classmethod
            def set_camera(cls, path):
                calls.append(("set_camera", path))

            @classmethod
            def set_camera_view(cls, camera, *, eye, target):
                calls.append(("set_camera_view", camera, eye, target))

            @classmethod
            def set_resolution(cls, resolution):
                calls.append(("set_resolution", resolution))

        rendering_manager = types.ModuleType("isaacsim.core.rendering_manager")
        rendering_manager.ViewportManager = FakeViewportManager
        core_pkg = types.ModuleType("isaacsim.core")
        core_pkg.rendering_manager = rendering_manager
        isaacsim_pkg = types.ModuleType("isaacsim")
        isaacsim_pkg.core = core_pkg

        app = FakeApp()
        with patch.dict(
            sys.modules,
            {
                "isaacsim": isaacsim_pkg,
                "isaacsim.core": core_pkg,
                "isaacsim.core.rendering_manager": rendering_manager,
            },
        ):
            result, error = _try_native_viewport(app, LiveWorldConfig())

        self.assertIsNone(error)
        self.assertEqual(result["mode"], "viewport")
        self.assertEqual(result["camera"], "/OmniverseKit_Persp")
        self.assertEqual(result["api"], "ViewportManager.set_camera_view")
        self.assertEqual(
            calls,
            [
                ("set_camera", "/OmniverseKit_Persp"),
                (
                    "set_camera_view",
                    "/OmniverseKit_Persp",
                    [1.35, -1.45, 1.05],
                    [0.40, 0.00, 0.25],
                ),
                ("set_resolution", (1280, 720)),
            ],
        )
        # The 6.0 classmethod API has no get_instance() step.
        self.assertFalse(hasattr(FakeViewportManager, "get_instance"))
        self.assertEqual(app.updates, 30)

    def test_try_native_viewport_reports_failure_cleanly(self) -> None:
        class BrokenViewportManager:
            @classmethod
            def set_camera(cls, path):
                raise RuntimeError("boom")

        rendering_manager = types.ModuleType("isaacsim.core.rendering_manager")
        rendering_manager.ViewportManager = BrokenViewportManager
        core_pkg = types.ModuleType("isaacsim.core")
        core_pkg.rendering_manager = rendering_manager
        isaacsim_pkg = types.ModuleType("isaacsim")
        isaacsim_pkg.core = core_pkg

        with patch.dict(
            sys.modules,
            {
                "isaacsim": isaacsim_pkg,
                "isaacsim.core": core_pkg,
                "isaacsim.core.rendering_manager": rendering_manager,
            },
        ):
            result, error = _try_native_viewport(FakeApp(), LiveWorldConfig())

        self.assertIsNone(result)
        self.assertIn("native ViewportManager failed", error)

    def test_check_stream_scene_prims_requires_robot_cube_and_target(self) -> None:
        ok, _reason = _check_stream_scene_prims(FakeStage(FULL_SCENE_PATHS))
        self.assertTrue(ok)
        ok, reason = _check_stream_scene_prims(FakeStage(FULL_SCENE_PATHS - {"/World/robot"}))
        self.assertFalse(ok)
        self.assertIn("/World/robot", reason)
        ok, reason = _check_stream_scene_prims(
            FakeStage(FULL_SCENE_PATHS - {"/World/red_cube", "/World/green_cube",
                                          "/World/red_cube_left", "/World/red_cube_right"})
        )
        self.assertFalse(ok)
        self.assertIn("red_cube", reason)
        ok, reason = _check_stream_scene_prims(FakeStage(FULL_SCENE_PATHS - {"/World/zone_unstack_target"}))
        self.assertFalse(ok)
        self.assertIn("zone_unstack_target", reason)

    def test_configure_stream_view_auto_prefers_native_viewport(self) -> None:
        import io
        from contextlib import redirect_stdout

        usd_calls = []
        with (
            patch("tools.run_live_isaac_worker._get_stream_stage", return_value=FakeStage(FULL_SCENE_PATHS)),
            patch(
                "tools.run_live_isaac_worker._try_native_viewport",
                return_value=({"mode": "viewport", "camera": "/OmniverseKit_Persp", "api": "manager.set_camera_view"}, None),
            ),
            patch(
                "tools.run_live_isaac_worker._try_usd_camera_fallback",
                side_effect=lambda _a: usd_calls.append(True) or (None, "must not be used"),
            ),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="auto"))

        output = buffer.getvalue()
        self.assertTrue(state["configured"])
        self.assertEqual(state["mode"], "viewport")
        self.assertIn("STREAM_VIEW_READY mode=viewport camera=/OmniverseKit_Persp", output)
        self.assertEqual(usd_calls, [])
        self.assertNotIn("STREAM_READY", output)

    def test_configure_stream_view_auto_falls_back_to_usd_camera(self) -> None:
        import io
        from contextlib import redirect_stdout

        with (
            patch("tools.run_live_isaac_worker._get_stream_stage", return_value=FakeStage(FULL_SCENE_PATHS)),
            patch("tools.run_live_isaac_worker._try_native_viewport", return_value=(None, "no API")),
            patch(
                "tools.run_live_isaac_worker._try_usd_camera_fallback",
                return_value=({"mode": "usd-camera", "camera": "/World/Camera", "api": "ensure_stream_camera"}, None),
            ),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="auto"))

        output = buffer.getvalue()
        self.assertTrue(state["configured"])
        self.assertEqual(state["mode"], "usd-camera")
        self.assertIn("STREAM_VIEW_READY mode=usd-camera camera=/World/Camera", output)
        self.assertIn("falling back to usd-camera", output)
        self.assertNotIn("STREAM_READY", output)

    def test_configure_stream_view_auto_failure_keeps_worker_running(self) -> None:
        import io
        from contextlib import redirect_stdout

        with (
            patch("tools.run_live_isaac_worker._get_stream_stage", return_value=FakeStage(FULL_SCENE_PATHS)),
            patch("tools.run_live_isaac_worker._try_native_viewport", return_value=(None, "native broke")),
            patch("tools.run_live_isaac_worker._try_usd_camera_fallback", return_value=(None, "usd broke")),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="auto"))

        output = buffer.getvalue()
        self.assertFalse(state["configured"])
        # auto reports the fallback error (the last attempt) as the reason.
        self.assertIn("falling back to usd-camera", output)
        self.assertIn("STREAM_VIEW_FAILED reason=usd broke", output)
        self.assertNotIn("STREAM_VIEW_READY", output)
        self.assertNotIn("STREAM_READY", output)

    def test_configure_stream_view_viewport_mode_never_falls_back(self) -> None:
        import io
        from contextlib import redirect_stdout

        usd_calls = []
        with (
            patch("tools.run_live_isaac_worker._get_stream_stage", return_value=FakeStage(FULL_SCENE_PATHS)),
            patch("tools.run_live_isaac_worker._try_native_viewport", return_value=(None, "native broke")),
            patch(
                "tools.run_live_isaac_worker._try_usd_camera_fallback",
                side_effect=lambda _a: usd_calls.append(True) or (None, "must not be used"),
            ),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="viewport"))

        self.assertFalse(state["configured"])
        self.assertIn("STREAM_VIEW_FAILED", buffer.getvalue())
        self.assertEqual(usd_calls, [])

    def test_configure_stream_view_usd_camera_mode_skips_native(self) -> None:
        import io
        from contextlib import redirect_stdout

        native_calls = []
        with (
            patch("tools.run_live_isaac_worker._get_stream_stage", return_value=FakeStage(FULL_SCENE_PATHS)),
            patch(
                "tools.run_live_isaac_worker._try_native_viewport",
                side_effect=lambda _a: native_calls.append(True) or (None, "must not be used"),
            ),
            patch(
                "tools.run_live_isaac_worker._try_usd_camera_fallback",
                return_value=({"mode": "usd-camera", "camera": "/World/Camera", "api": "ensure_stream_camera"}, None),
            ),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="usd-camera"))

        self.assertTrue(state["configured"])
        self.assertEqual(state["mode"], "usd-camera")
        self.assertEqual(native_calls, [])
        self.assertIn("STREAM_VIEW_READY mode=usd-camera", buffer.getvalue())

    def test_configure_stream_view_off_touches_nothing(self) -> None:
        import io
        from contextlib import redirect_stdout

        native_calls = []
        usd_calls = []
        with (
            patch(
                "tools.run_live_isaac_worker._try_native_viewport",
                side_effect=lambda _a: native_calls.append(True) or (None, "must not be used"),
            ),
            patch(
                "tools.run_live_isaac_worker._try_usd_camera_fallback",
                side_effect=lambda _a: usd_calls.append(True) or (None, "must not be used"),
            ),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="off"))

        self.assertFalse(state["configured"])
        self.assertEqual(state["mode"], "off")
        self.assertIn("STREAM_VIEW_OFF", buffer.getvalue())
        self.assertEqual(native_calls, [])
        self.assertEqual(usd_calls, [])

    def test_configure_stream_view_fails_when_scene_prims_missing(self) -> None:
        import io
        from contextlib import redirect_stdout

        native_calls = []
        with (
            patch("tools.run_live_isaac_worker._get_stream_stage", return_value=FakeStage(set())),
            patch(
                "tools.run_live_isaac_worker._try_native_viewport",
                side_effect=lambda _a: native_calls.append(True) or (None, "must not be used"),
            ),
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                state = _configure_stream_view(FakeApp(), LiveWorldConfig(stream_view_mode="auto"))

        self.assertFalse(state["configured"])
        self.assertIn("STREAM_VIEW_FAILED reason=scene prims not ready", buffer.getvalue())
        self.assertEqual(native_calls, [])

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
