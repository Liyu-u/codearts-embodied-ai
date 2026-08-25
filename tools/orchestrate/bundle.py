"""tools/orchestrate/bundle.py —— 白名单打包与敏感文件校验。

打包前扫描待打包路径，命中敏感文件名模式即抛出 ``SensitiveFileError``
终止上传，满足 spec 4.3.2 / 5.1.1.7。
"""

from __future__ import annotations

import io
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

SENSITIVE_PATTERNS = [
    re.compile(r"^\.env(\.|$)"),
    re.compile(r"_llm\.env$"),
    re.compile(r"\.csv$", re.IGNORECASE),
    re.compile(r"(ak|sk|secret|password|credential|private[_-]?key)", re.IGNORECASE),
    re.compile(r"\.pem$|\.key$", re.IGNORECASE),
]

# 默认打包白名单：源码/契约/清单目录（相对仓库根）。
DEFAULT_ALLOWED_PATHS = [
    "contracts",
    "integration",
    "modules",
    "tools",
    "testdata",
]


class SensitiveFileError(ValueError):
    pass


@dataclass
class BundleBuilder:
    repo_root: Path
    allowed_paths: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_PATHS)
    )
    extra_files: list[Path] = field(default_factory=list)

    def _scan_paths(self, paths: list[Path]) -> list[Path]:
        files: list[Path] = []
        for path in paths:
            if path.is_dir():
                files.extend(item for item in path.rglob("*") if item.is_file())
            elif path.is_file():
                files.append(path)
        return files

    @staticmethod
    def _is_sensitive(name: str) -> str | None:
        for pattern in SENSITIVE_PATTERNS:
            if pattern.search(name):
                return pattern.pattern
        return None

    def scan_for_sensitive_files(self) -> list[str]:
        """返回命中的敏感文件相对路径清单；为空表示可安全打包。"""
        collect: list[Path] = []
        for rel in self.allowed_paths:
            candidate = self.repo_root / rel
            if candidate.exists():
                collect.append(candidate)
        collect.extend(self.extra_files)
        hits: list[str] = []
        for file_path in self._scan_paths(collect):
            name = file_path.name
            if self._is_sensitive(name):
                try:
                    rel = file_path.relative_to(self.repo_root)
                except ValueError:
                    rel = file_path
                hits.append(str(rel))
        return hits

    def build(self, target: Path) -> Path:
        """打包为 tar.gz 到 ``target``，敏感文件命中时抛出错误。"""
        hits = self.scan_for_sensitive_files()
        if hits:
            raise SensitiveFileError(
                "检测到敏感文件，已终止上传: " + "; ".join(sorted(hits))
            )
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        entries: list[tuple[Path, Path]] = []
        for rel in self.allowed_paths:
            candidate = self.repo_root / rel
            if candidate.exists():
                entries.append((candidate, Path(rel)))
        for extra in self.extra_files:
            entries.append((extra, Path(extra.name)))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for source, arcname in entries:
                archive.add(source, arcname=arcname, recursive=True)
        target.write_bytes(buffer.getvalue())
        return target

    def manifest(self) -> list[str]:
        """列出打包后的条目（用于验证不含敏感项）。"""
        entries: list[tuple[Path, Path]] = []
        for rel in self.allowed_paths:
            candidate = self.repo_root / rel
            if candidate.exists():
                entries.append((candidate, Path(rel)))
        names: list[str] = []
        for source, arcname in entries:
            if source.is_dir():
                names.extend(
                    str(Path(arcname) / item.relative_to(source))
                    for item in source.rglob("*")
                    if item.is_file()
                )
            else:
                names.append(str(arcname))
        return names