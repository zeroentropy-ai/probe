"""File discovery with .gitignore/.probeignore support."""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path

SUPPORTED_EXTENSIONS = {
    ".md", ".mdx", ".txt", ".pdf",
    ".py", ".js", ".ts", ".tsx", ".jsx",
    ".go", ".rs", ".java",
    ".yaml", ".yml", ".json",
    ".rst", ".adoc", ".tex",
}


def _parse_ignore_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    patterns = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            patterns.append(line)
    return patterns


def _is_ignored(file_path: Path, base_dir: Path, patterns: list[str]) -> bool:
    rel = str(file_path.relative_to(base_dir))
    for pattern in patterns:
        if pattern.endswith("/"):
            dir_pattern = pattern.rstrip("/")
            if any(part == dir_pattern for part in file_path.relative_to(base_dir).parts):
                return True
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(file_path.name, pattern):
            return True
    return False


def discover_files(paths: list[Path]) -> list[Path]:
    result = []
    for path in paths:
        if path.is_file():
            if path.suffix in SUPPORTED_EXTENSIONS:
                result.append(path)
            continue

        ignore_patterns = []
        ignore_patterns.extend(_parse_ignore_file(path / ".gitignore"))
        ignore_patterns.extend(_parse_ignore_file(path / ".probeignore"))
        ignore_patterns.extend([".git/", ".probe/", "__pycache__/", ".venv/", "*.pyc"])

        for file_path in sorted(path.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix not in SUPPORTED_EXTENSIONS:
                continue
            if _is_ignored(file_path, path, ignore_patterns):
                continue
            result.append(file_path)
    return result


def compute_file_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
