"""Tests for the indexing pipeline."""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from probe.indexer.pipeline import IndexPipeline
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB


@pytest.fixture
def mock_embedding_provider():
    provider = MagicMock()
    provider.dimensions = 4
    provider.embed.side_effect = (
        lambda texts, **kw: np.random.randn(len(texts), 4).astype(np.float32)
    )
    return provider


@pytest.fixture
def pipeline(tmp_probe_dir, mock_embedding_provider):
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    vector_store = VectorStore(tmp_probe_dir / "vectors.npy", dimensions=4)
    return IndexPipeline(
        db=db, vector_store=vector_store, embedding_provider=mock_embedding_provider
    )


class TestIndexPipeline:
    def test_index_directory(self, pipeline, fixtures_dir):
        stats = pipeline.index([fixtures_dir])
        assert stats["files_indexed"] > 0
        assert stats["chunks_created"] > 0
        assert len(pipeline.db.list_files()) > 0
        assert len(pipeline.db.get_all_chunks()) > 0

    def test_incremental_index_skips_unchanged(self, pipeline, fixtures_dir):
        pipeline.index([fixtures_dir])
        stats1 = pipeline.db.get_stats()
        stats = pipeline.index([fixtures_dir])
        assert stats["files_skipped"] > 0
        stats2 = pipeline.db.get_stats()
        assert stats1["total_chunks"] == stats2["total_chunks"]

    def test_full_reindex(self, pipeline, fixtures_dir):
        pipeline.index([fixtures_dir])
        stats = pipeline.index([fixtures_dir], full=True)
        assert stats["files_skipped"] == 0
        assert stats["files_indexed"] > 0

    def test_embedding_provider_called(self, pipeline, fixtures_dir, mock_embedding_provider):
        pipeline.index([fixtures_dir])
        assert mock_embedding_provider.embed.called

    def test_index_skips_files_that_fail_extraction(self, pipeline, tmp_path):
        (tmp_path / "good.md").write_text("# Good\n\nSearchable project context.")
        (tmp_path / "broken.pdf").write_text("not actually a pdf")

        stats = pipeline.index([tmp_path])

        assert stats["files_indexed"] == 1
        assert stats["files_failed"] == 1
        assert len(stats["failed_files"]) == 1
        assert Path(stats["failed_files"][0]["path"]).name == "broken.pdf"
        assert {Path(f["path"]).name for f in pipeline.db.list_files()} == {"good.md"}

    def test_vectors_saved(self, pipeline, fixtures_dir, tmp_probe_dir):
        pipeline.index([fixtures_dir])
        assert (tmp_probe_dir / "vectors.npy").exists()

    def test_refresh_no_changes(self, pipeline, fixtures_dir, mock_embedding_provider):
        pipeline.index([fixtures_dir])
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([fixtures_dir])

        assert stats["added"] == 0
        assert stats["changed"] == 0
        assert stats["removed"] == 0
        assert "elapsed_ms" in stats
        # Phase 2 never runs for unchanged files, so no new embed calls.
        assert mock_embedding_provider.embed.call_count == 0

    def test_refresh_detects_deleted_file(self, pipeline, fixtures_dir, tmp_path):
        # Copy fixtures into a temp dir so we can delete from it safely
        import shutil

        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])
        assert len(pipeline.db.list_files()) > 0

        # Delete one file
        target = work / "notes.txt"
        target.unlink()

        stats = pipeline.refresh_changed([work])
        assert stats["removed"] == 1
        paths = {f["path"] for f in pipeline.db.list_files()}
        assert "notes.txt" not in paths

    def test_refresh_new_file(self, pipeline, fixtures_dir, tmp_path,
                              mock_embedding_provider):
        import shutil

        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])

        # Add a new file
        (work / "new.md").write_text("# New\nSome content about fresh things.")
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([work])
        assert stats["added"] == 1
        assert stats["changed"] == 0
        paths = {f["path"] for f in pipeline.db.list_files()}
        assert str(work / "new.md") in paths
        assert mock_embedding_provider.embed.call_count >= 1

    def test_refresh_edited_file(self, pipeline, fixtures_dir, tmp_path,
                                 mock_embedding_provider):
        import shutil
        import time

        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])

        # Modify an existing file (content change)
        target = work / "README.md"
        time.sleep(0.01)  # ensure mtime advances on coarse filesystems
        target.write_text(target.read_text() + "\n\nNew paragraph about something.")
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([work])
        assert stats["changed"] == 1
        assert stats["added"] == 0
        assert mock_embedding_provider.embed.call_count >= 1

    def test_refresh_touched_file_not_reembedded(self, pipeline, fixtures_dir,
                                                  tmp_path, mock_embedding_provider):
        """mtime changes but content doesn't: hash confirms no real change, no embed."""
        import os
        import shutil
        import time

        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])

        target = work / "README.md"
        # Bump mtime without changing content
        new_time = time.time() + 10
        os.utime(target, (new_time, new_time))
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([work])
        # Phase 1 flags it, phase 2 confirms via hash, updates mtime, no embed.
        assert stats["changed"] == 0
        assert stats["added"] == 0
        assert mock_embedding_provider.embed.call_count == 0

    def test_refresh_keeps_existing_index_when_reindex_fails(
        self, pipeline, tmp_path, monkeypatch,
    ):
        target = tmp_path / "notes.txt"
        target.write_text("stable searchable context")
        pipeline.index([tmp_path])

        target.write_text("changed content")

        def fail_extract(path):
            if path == target:
                raise OSError("cannot read")
            raise AssertionError(f"unexpected path: {path}")

        monkeypatch.setattr("probe.indexer.pipeline.extract_content", fail_extract)

        stats = pipeline.refresh_changed([tmp_path])

        assert stats["failed"] == 1
        assert len(stats["failed_files"]) == 1
        assert Path(stats["failed_files"][0]["path"]).name == "notes.txt"
        assert {Path(f["path"]).name for f in pipeline.db.list_files()} == {"notes.txt"}
        chunks = pipeline.db.get_all_chunks()
        assert len(chunks) == 1
        assert chunks[0]["content"] == "stable searchable context"

    def test_root_dir_stores_project_relative_paths_when_cwd_differs(
        self, tmp_path, mock_embedding_provider, monkeypatch,
    ):
        """MCP can launch outside the project but still index project-relative paths."""
        launch_dir = tmp_path / "launcher"
        launch_dir.mkdir()
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "docs").mkdir()
        (project_dir / "docs" / "guide.md").write_text("# Guide\n\nProject setup details.")
        probe_dir = project_dir / ".probe"
        probe_dir.mkdir()

        monkeypatch.chdir(launch_dir)
        db = ProbeDB(probe_dir / "probe.db")
        db.initialize()
        vector_store = VectorStore(probe_dir / "vectors.npy", dimensions=4)
        pipeline = IndexPipeline(
            db=db,
            vector_store=vector_store,
            embedding_provider=mock_embedding_provider,
            root_dir=project_dir,
        )

        stats = pipeline.index([project_dir])

        assert stats["files_indexed"] == 1
        assert {f["path"] for f in db.list_files()} == {"docs/guide.md"}
