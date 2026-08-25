"""BundleBuilder 单元测试：敏感文件拦截、白名单打包、清单不含敏感项。"""

import tempfile
import unittest
import tarfile
from pathlib import Path

from tools.orchestrate.bundle import BundleBuilder, SensitiveFileError


def _make_tree(root: Path, files: list[str]) -> None:
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")


class BundleBuilderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "contracts").mkdir()
        (self.root / "contracts" / "x.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sensitive_env_file_detected(self):
        _make_tree(self.root, ["integration/secret/.env", "integration/a.py"])
        builder = BundleBuilder(
            repo_root=self.root, allowed_paths=["integration"]
        )
        hits = builder.scan_for_sensitive_files()
        self.assertTrue(any("\.env" in h for h in hits))

    def test_build_raises_on_sensitive_file(self):
        _make_tree(self.root, ["integration/.env", "integration/b.py"])
        builder = BundleBuilder(
            repo_root=self.root, allowed_paths=["integration"]
        )
        with self.assertRaises(SensitiveFileError) as ctx:
            builder.build(self.root / "out.tar.gz")
        self.assertIn("敏感文件", str(ctx.exception))

    def test_csv_and_llm_env_detected(self):
        _make_tree(
            self.root,
            [
                "integration/a.py",
                "integration/data.csv",
                "integration/tracecoder_llm.env",
            ],
        )
        builder = BundleBuilder(
            repo_root=self.root, allowed_paths=["integration"]
        )
        hits = builder.scan_for_sensitive_files()
        self.assertEqual(len(hits), 2)

    def test_whitelist_excludes_outside_dirs(self):
        _make_tree(
            self.root,
            [
                "integration/a.py",
                "modules/b.py",
                "docs/keep.md",
            ],
        )
        builder = BundleBuilder(
            repo_root=self.root, allowed_paths=["integration"]
        )
        manifest = builder.manifest()
        self.assertTrue(any(m.endswith("a.py") for m in manifest))
        self.assertFalse(any("modules" in m for m in manifest))
        self.assertFalse(any("docs" in m for m in manifest))

    def test_manifest_clean_after_build(self):
        _make_tree(self.root, ["integration/a.py", "contracts/z.json"])
        builder = BundleBuilder(
            repo_root=self.root,
            allowed_paths=["integration", "contracts"],
        )
        target = self.root / "bundle.tar.gz"
        builder.build(target)
        self.assertTrue(target.exists())
        with tarfile.open(target, "r:gz") as archive:
            names = archive.getnames()
        self.assertTrue(any(n.endswith("a.py") for n in names))
        self.assertTrue(any(n.endswith("z.json") for n in names))
        for name in names:
            self.assertNotIn(".env", name)
            self.assertNotIn(".csv", name)


if __name__ == "__main__":
    unittest.main()