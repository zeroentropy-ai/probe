"""Tests for file discovery."""

from pathlib import Path
import pytest
from probe.indexer.discovery import discover_files, compute_file_hash


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

    def test_filters_unsupported_extensions(self, tmp_path: Path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
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
