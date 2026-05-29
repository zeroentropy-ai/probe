"""Tests for probe smoke validation."""

import numpy as np

from probe.smoke import run_smoke


class FakeEmbeddingProvider:
    dimensions = 4

    def embed(self, texts, **kwargs):
        vectors = []
        for text in texts:
            if "rainbow handshake" in text.lower():
                vectors.append([1.0, 0.0, 0.0, 0.0])
            else:
                vectors.append([0.2, 0.8, 0.0, 0.0])
        return np.array(vectors, dtype=np.float32)


def test_smoke_default_temp_project_succeeds(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    report = run_smoke(embedding_provider=FakeEmbeddingProvider())

    assert report.status == "PASS"
    assert report.indexed_files >= 1
    assert report.search_result_count >= 1
    assert report.expected_file_matched is True


def test_smoke_current_project_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "README.md").write_text("# Smoke\n\nThe rainbow handshake lives here.\n")

    report = run_smoke(current=True, embedding_provider=FakeEmbeddingProvider())

    assert report.status == "PASS"
    assert report.project_path == str(tmp_path)
    assert report.expected_file_matched is True


def test_smoke_keep_preserves_temp_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    report = run_smoke(keep=True, embedding_provider=FakeEmbeddingProvider())

    assert report.status == "PASS"
    assert report.project_path is not None
    assert report.temp_project_kept is True


def test_smoke_claude_fails_when_claude_cli_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)

    report = run_smoke(claude=True, embedding_provider=FakeEmbeddingProvider())

    assert report.status == "FAIL"
    assert "Claude Code CLI not found" in report.error


def test_smoke_codex_fails_when_codex_cli_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)

    report = run_smoke(codex=True, embedding_provider=FakeEmbeddingProvider())

    assert report.status == "FAIL"
    assert "Codex CLI not found" in report.error
