"""RemoteChannel 单元测试：假命令执行器模拟认证失败/超时/传输失败/清理。"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.orchestrate.remote import CleanupResult, RemoteChannel
from tools.orchestrate.types import OrchestrationConfig


class FakeRunner:
    def __init__(self, plan: dict | None = None):
        self.plan = plan or {}
        self.calls: list[list[str]] = []

    def __call__(self, argv, timeout_s):
        self.calls.append(argv)
        marker = self.plan.get("marker", "ok")
        if marker == "auth_fail":
            return _result(255, "", "Permission denied (publickey)")
        if marker == "timeout":
            return _result(124, "", "Connection timed out")
        if marker == "transfer_fail":
            return _result(1, "", "scp: unexpected")
        if marker == "container":
            return _result(0, "abc123|codearts-run-isaac|nvcr.io/nvidia/isaac-sim:6.0.0\n", "")
        if marker == "rm_fail":
            if "docker rm" in " ".join(argv):
                return _result(1, "", "docker rm failed")
            return _result(0, "abc123|codearts-run-isaac|nvcr.io/nvidia/isaac-sim:6.0.0\n", "")
        return _result(0, "ok", "")


def _result(code, stdout, stderr):
    return SimpleNamespace(exit_code=code, stdout=stdout, stderr=stderr, ok=code == 0)


def _config(**overrides) -> OrchestrationConfig:
    values = dict(
        instruction="i",
        scene_id="s",
        server="10.0.0.1",
        port=5122,
        user="stu",
        remote_base="/data/stu/workspace",
        auth_mode="key",
        key_path=Path("id_rsa"),
    )
    values.update(overrides)
    return OrchestrationConfig(**values)


class RemoteChannelTest(unittest.TestCase):
    def test_run_command_injects_batch_mode(self):
        runner = FakeRunner()
        channel = RemoteChannel(_config(), command_runner=runner)
        result = channel.run_command("hostname", timeout_s=5)
        self.assertTrue(result.ok)
        argv = runner.calls[0]
        self.assertIn("-o", argv)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("hostname", argv)

    def test_auth_failure_marked_failed(self):
        runner = FakeRunner({"marker": "auth_fail"})
        channel = RemoteChannel(_config(), command_runner=runner)
        result = channel.run_command("hostname")
        self.assertFalse(result.ok)
        self.assertEqual(result.exit_code, 255)

    def test_connection_timeout(self):
        runner = FakeRunner({"marker": "timeout"})
        channel = RemoteChannel(_config(), command_runner=runner)
        result = channel.run_command("hostname")
        self.assertEqual(result.exit_code, 124)

    def test_upload_failure(self):
        runner = FakeRunner({"marker": "transfer_fail"})
        channel = RemoteChannel(_config(), command_runner=runner)
        local = Path(tempfile.gettempdir()) / "x.tar.gz"
        local.write_bytes(b"x")
        try:
            result = channel.upload(local, "/remote/x.tar.gz")
            self.assertFalse(result.ok)
        finally:
            local.unlink(missing_ok=True)

    def test_cleanup_containers_cleans_matching(self):
        runner = FakeRunner({"marker": "container"})
        channel = RemoteChannel(_config(), command_runner=runner)
        result: CleanupResult = channel.cleanup_containers(["/data/stu/workspace/codearts-run"])
        self.assertEqual(result.cleaned, ["abc123"])
        self.assertEqual(result.scanned, 1)

    def test_cleanup_warning_on_rm_failure(self):
        runner = FakeRunner({"marker": "rm_fail"})
        channel = RemoteChannel(_config(), command_runner=runner)
        result: CleanupResult = channel.cleanup_containers(["/data/stu/workspace/codearts-run"])
        self.assertEqual(result.cleaned, [])
        self.assertTrue(result.warnings)

    def test_cleanup_scan_failure(self):
        runner = FakeRunner({"marker": "auth_fail"})
        channel = RemoteChannel(_config(), command_runner=runner)
        result: CleanupResult = channel.cleanup_containers(["/data/stu/workspace"])
        self.assertTrue(result.warnings)


if __name__ == "__main__":
    unittest.main()