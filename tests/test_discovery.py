"""Tests for file discovery."""

from pathlib import Path

import pytest

from probe.indexer.discovery import compute_file_hash, discover_files


@pytest.fixture
def project_with_ignore(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "design.md").write_text("# Design")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hello')")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "output.js").write_text("compiled")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("lib")
    (tmp_path / ".gitignore").write_text("build/\nnode_modules/\n")
    (tmp_path / "secret.env").write_text("KEY=value")
    return tmp_path


class TestDiscoverFiles:
    def test_finds_supported_files(self, fixtures_dir: Path):
        files = discover_files([fixtures_dir])
        paths = {f.name for f in files}
        assert "design.md" in paths
        assert "auth.py" in paths
        assert "notes.txt" in paths

    def test_finds_text_files_without_known_extensions(self, tmp_path: Path):
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
        (tmp_path / "Dockerfile").write_text("FROM python:3.12\n")
        (tmp_path / "settings.local").write_text("feature=true\n")
        files = discover_files([tmp_path])
        names = {f.name for f in files}
        assert "Makefile" in names
        assert "Dockerfile" in names
        assert "settings.local" in names

    def test_skips_binary_files(self, tmp_path: Path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x00binary")
        (tmp_path / "doc.md").write_text("hello")
        files = discover_files([tmp_path])
        names = {f.name for f in files}
        assert "doc.md" in names
        assert "image.png" not in names

    def test_respects_gitignore(self, project_with_ignore: Path):
        files = discover_files([project_with_ignore])
        paths = {str(f.relative_to(project_with_ignore)) for f in files}
        assert "docs/design.md" in paths
        assert "src/main.py" in paths
        assert "build/output.js" not in paths
        assert "node_modules/lib.js" not in paths

    def test_respects_probeignore(self, tmp_path: Path):
        (tmp_path / "keep.md").write_text("keep")
        (tmp_path / "skip.md").write_text("skip")
        (tmp_path / ".probeignore").write_text("skip.md\n")
        files = discover_files([tmp_path])
        names = {f.name for f in files}
        assert "keep.md" in names
        assert "skip.md" not in names

    def test_respects_ignore_file_as_local_override(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text("local/\n")
        (tmp_path / ".ignore").write_text("!local/\n!local/**\n")
        (tmp_path / "local").mkdir()
        (tmp_path / "local" / "notes.local").write_text("index this local context\n")

        files = discover_files([tmp_path])

        paths = {str(f.relative_to(tmp_path)) for f in files}
        assert "local/notes.local" in paths

    def test_probeignore_has_highest_precedence(self, tmp_path: Path):
        (tmp_path / ".gitignore").write_text("local/\n")
        (tmp_path / ".ignore").write_text("!local/\n!local/**\n")
        (tmp_path / ".probeignore").write_text("local/\n")
        (tmp_path / "local").mkdir()
        (tmp_path / "local" / "notes.local").write_text("do not index\n")

        files = discover_files([tmp_path])

        paths = {str(f.relative_to(tmp_path)) for f in files}
        assert "local/notes.local" not in paths

    def test_skips_likely_secret_files_by_default(self, tmp_path: Path):
        (tmp_path / ".env.local.bak").write_text("DATABASE_URL=postgres://secret\n")
        (tmp_path / "private.pem").write_text("-----BEGIN PRIVATE KEY-----\nsecret\n")
        (tmp_path / "notes.md").write_text("# Safe\n")

        files = discover_files([tmp_path])

        names = {f.name for f in files}
        assert "notes.md" in names
        assert ".env.local.bak" not in names
        assert "private.pem" not in names


class TestFileHash:
    def test_deterministic_hash(self, tmp_path: Path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        h1 = compute_file_hash(f)
        h2 = compute_file_hash(f)
        assert h1 == h2
        assert len(h1) == 64

    def test_different_content_different_hash(self, tmp_path: Path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert compute_file_hash(f1) != compute_file_hash(f2)
