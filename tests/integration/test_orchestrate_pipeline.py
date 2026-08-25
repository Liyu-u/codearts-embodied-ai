"""一键编排集成测试：Mock 闭环、BLOCKED 分类、传输重试、敏感拦截、清理。

默认测试入口不访问外网：PREPARE 使用规则引擎，EXECUTE 使用 Mock 后端，
远程路径通过假命令执行器注入。
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.orchestrate.orchestrator import orchestrate
from tools.orchestrate.types import OrchestrationConfig, exit_code_for


class FakeRunner:
    """远程命令假执行器：默认模拟一次成功的容器执行闭环。"""

    def __init__(self, mode: str = "success"):
        self.mode = mode
        self.calls: list[list[str]] = []
        self.uploaded_bundle: Path | None = None

    def __call__(self, argv, timeout_s):
        self.calls.append(argv)
        joined = " ".join(argv)
        if "docker ps" in joined:
            if self.mode == "rm_fail":
                return _result(0, "abc|isaac-x|nvcr.io/nvidia/isaac-sim:6.0.0\n", "")
            return _result(0, "", "")
        if "docker rm" in joined:
            return _result(1 if self.mode == "rm_fail" else 0, "", "")
        if self.mode == "auth_fail":
            return _result(255, "", "Permission denied (publickey)")
        if self.mode == "container_fail":
            return _result(1, "", "CONTAINER_EXITED")
        if "scp" in joined and "execution.json" not in joined:
            return _result(0, "", "")
        if "scp" in joined:
            return _result(0, "", "")
        if "execution.json" in argv:
            return _result(0, "REPORT_READY\n", "")
        if "mkdir" in joined or "rm -f" in joined or "tar -xzf" in joined:
            return _result(0, "", "")
        if "hostname" in joined:
            return _result(0, "ok\n", "")
        return _result(0, "", "")


def _result(code, stdout, stderr):
    return SimpleNamespace(
        exit_code=code, stdout=stdout, stderr=stderr, ok=code == 0
    )


def _config(**overrides) -> OrchestrationConfig:
    values = dict(
        instruction="把绿色方块放到桌子上",
        scene_id="stacking_cubes",
        server="10.0.0.1",
        port=5122,
        user="stu",
        remote_base="/data/stu/workspace",
        auth_mode="key",
        key_path=Path("id_rsa"),
        backend="mock",
    )
    values.update(overrides)
    return OrchestrationConfig(**values)


class OrchestrateMockLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "run_mock"

    def tearDown(self):
        self.tmp.cleanup()

    def test_mock_full_loop_succeeds_with_all_artifacts(self):
        config = _config(out_dir=self.out)
        result = orchestrate(config)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertIsNone(result.failure_class)
        self.assertEqual(exit_code_for(result.failure_class), 0)
        for name in ["remote-isaac-run", "stage-report", "task", "strategy", "execution", "feedback"]:
            self.assertIn(name, result.artifact_paths, name)
            self.assertTrue(result.artifact_paths[name].exists(), name)
        run_artifact = json.loads(
            (self.out / "remote-isaac-run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(run_artifact["status"], "SUCCEEDED")

    def test_blocked_instruction_returns_runner(self):
        config = _config(
            instruction="把红色方块放到绿色方块上", out_dir=self.out
        )
        result = orchestrate(config)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.failure_class, "runner")
        self.assertNotEqual(exit_code_for(result.failure_class), 0)
        stage_names = [s.stage for s in result.stages]
        self.assertIn("PREPARE", stage_names)
        self.assertIn("CLEANUP", stage_names)


class OrchestrateRemotePathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "run_remote"
        self._prev_mode = __import__("os").environ.get("CODEARTS_STRATEGY_MODE")
        __import__("os").environ["CODEARTS_STRATEGY_MODE"] = "off"

    def tearDown(self):
        import os

        if self._prev_mode is not None:
            os.environ["CODEARTS_STRATEGY_MODE"] = self._prev_mode
        else:
            os.environ.pop("CODEARTS_STRATEGY_MODE", None)
        self.tmp.cleanup()

    def test_transport_auth_failure_after_retries(self):
        runner = FakeRunner(mode="auth_fail")
        config = _config(
            backend="remote_isaac",
            out_dir=self.out,
            transport_retries=1,
        )
        result = orchestrate(config, command_runner=runner)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.failure_class, "transport_auth")
        self.assertEqual(exit_code_for(result.failure_class), 10)

    def test_sensitive_bundle_blocks_before_remote_command(self):
        runner = FakeRunner()
        bundle_root = Path(self.tmp.name) / "bundle_root"
        (bundle_root / "integration").mkdir(parents=True)
        (bundle_root / "integration" / ".env").write_text("KEY=x", encoding="utf-8")
        (bundle_root / "integration" / "a.py").write_text("x=1", encoding="utf-8")

        from tools.orchestrate.bundle import BundleBuilder, SensitiveFileError

        builder = BundleBuilder(repo_root=bundle_root, allowed_paths=["integration"])
        with self.assertRaises(SensitiveFileError):
            builder.build(Path(self.tmp.name) / "bundle.tar.gz")

    def test_cleanup_runs_on_failure_path(self):
        runner = FakeRunner(mode="auth_fail")
        config = _config(
            backend="remote_isaac",
            out_dir=self.out,
            transport_retries=0,
        )
        result = orchestrate(config, command_runner=runner)
        stage_names = [s.stage for s in result.stages]
        self.assertIn("CLEANUP", stage_names)
        self.assertEqual(result.failure_class, "transport_auth")


if __name__ == "__main__":
    unittest.main()