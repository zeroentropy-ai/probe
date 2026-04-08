"""Tests for the indexing pipeline."""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from probe.indexer.pipeline import IndexPipeline
from probe.store.database import ProbeDB
from probe.search.vector import VectorStore


@pytest.fixture
def mock_embedding_provider():
    provider = MagicMock()
    provider.dimensions = 4
    provider.embed.side_effect = lambda texts, **kw: np.random.randn(len(texts), 4).astype(np.float32)
    return provider


@pytest.fixture
def pipeline(tmp_probe_dir, mock_embedding_provider):
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    vector_store = VectorStore(tmp_probe_dir / "vectors.npy", dimensions=4)
    return IndexPipeline(db=db, vector_store=vector_store, embedding_provider=mock_embedding_provider)


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

    def test_vectors_saved(self, pipeline, fixtures_dir, tmp_probe_dir):
        pipeline.index([fixtures_dir])
        assert (tmp_probe_dir / "vectors.npy").exists()
