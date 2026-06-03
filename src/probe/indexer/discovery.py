"""File discovery with gitignore-style ignore support."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from pathspec import GitIgnoreSpec

TEXT_SAMPLE_BYTES = 8192
ALWAYS_IGNORE_DIRS = {".git", ".probe", "__pycache__", ".venv"}
ALWAYS_IGNORE_FILE_PATTERNS = {"*.pyc", "*.pyo"}
DEFAULT_SECRET_FILE_PATTERNS = {
    ".env",
    ".env.*",
    "*.env",
    "*.env.*",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
PDF_EXTENSIONS = {".pdf"}
OBVIOUS_BINARY_EXTENSIONS = {
    ".7z", ".a", ".avi", ".bmp", ".bz2", ".class", ".db", ".dll", ".dmg",
    ".dylib", ".eot", ".exe", ".gif", ".gz", ".ico", ".jar", ".jpeg", ".jpg",
    ".lockb", ".mov", ".mp3", ".mp4", ".o", ".obj", ".otf", ".png", ".pyc",
    ".pyo", ".rar", ".sqlite", ".sqlite3", ".tar", ".tgz", ".ttf", ".wasm",
    ".webm", ".webp", ".woff", ".woff2", ".xz", ".zip", ".zst",
}


@dataclass
class _IgnoreMatcher:
    """Match paths using gitignore syntax and ripgrep-like precedence."""

    root: Path
    gitignore_specs: list[tuple[Path, GitIgnoreSpec]] = field(default_factory=list)
    ignore_specs: list[tuple[Path, GitIgnoreSpec]] = field(default_factory=list)
    probeignore_specs: list[tuple[Path, GitIgnoreSpec]] = field(default_factory=list)

    def load_directory(self, directory: Path) -> None:
        self._load_file(directory / ".gitignore", self.gitignore_specs)
        self._load_file(directory / ".ignore", self.ignore_specs)
        self._load_file(directory / ".probeignore", self.probeignore_specs)

    def is_ignored(self, path: Path, *, is_dir: bool = False) -> bool:
        try:
            rel_parts = path.relative_to(self.root).parts
        except ValueError:
            return False

        if any(part in ALWAYS_IGNORE_DIRS for part in rel_parts):
            return True
        if not is_dir and any(
            fnmatch.fnmatch(path.name, pattern) for pattern in ALWAYS_IGNORE_FILE_PATTERNS
        ):
            return True
        if not is_dir and _is_sensitive_path(path):
            return True

        decision = self._category_decision(self.gitignore_specs, path, is_dir=is_dir)
        ignore_decision = self._category_decision(self.ignore_specs, path, is_dir=is_dir)
        if ignore_decision is not None:
            decision = ignore_decision
        probe_decision = self._category_decision(self.probeignore_specs, path, is_dir=is_dir)
        if probe_decision is not None:
            decision = probe_decision
        return bool(decision)

    def _load_file(
        self,
        path: Path,
        specs: list[tuple[Path, GitIgnoreSpec]],
    ) -> None:
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return
        specs.append((path.parent, GitIgnoreSpec.from_lines(lines)))

    def _category_decision(
        self,
        specs: list[tuple[Path, GitIgnoreSpec]],
        path: Path,
        *,
        is_dir: bool,
    ) -> bool | None:
        decision: bool | None = None
        for base_dir, spec in specs:
            try:
                rel = path.relative_to(base_dir).as_posix()
            except ValueError:
                continue
            if is_dir and not rel.endswith("/"):
                rel = f"{rel}/"
            match = spec.check_file(rel)
            if match.include is not None:
                decision = bool(match.include)
        return decision


def _looks_indexable(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return True
    if suffix in OBVIOUS_BINARY_EXTENSIONS:
        return False
    try:
        with path.open("rb") as f:
            sample = f.read(TEXT_SAMPLE_BYTES)
    except OSError:
        return False
    return b"\x00" not in sample


def _index_sensitive_files() -> bool:
    return os.environ.get("PROBE_INDEX_SECRET_FILES", "").lower() in {
        "1", "true", "yes", "on",
    }


def _is_sensitive_path(path: Path) -> bool:
    if _index_sensitive_files():
        return False
    name = path.name.lower()
    return any(fnmatch.fnmatch(name, pattern) for pattern in DEFAULT_SECRET_FILE_PATTERNS)


def discover_files(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in paths:
        if path.is_file():
            if not _is_sensitive_path(path) and _looks_indexable(path):
                result.append(path)
            continue

        if not path.is_dir():
            continue

        root = path.resolve()
        matcher = _IgnoreMatcher(root=root)

        for dirpath, dirnames, filenames in os.walk(root):
            current_dir = Path(dirpath)
            matcher.load_directory(current_dir)

            dirnames[:] = [
                dirname
                for dirname in sorted(dirnames)
                if not matcher.is_ignored(current_dir / dirname, is_dir=True)
            ]

            for filename in sorted(filenames):
                file_path = current_dir / filename
                if matcher.is_ignored(file_path):
                    continue
                if not _looks_indexable(file_path):
                    continue
                result.append(file_path)
    return result


def compute_file_hash(path: Path) -> str:
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
