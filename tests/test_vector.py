"""Tests for numpy vector store."""

from pathlib import Path

import numpy as np
import pytest

from probe.search.vector import VectorStore


@pytest.fixture
def store(tmp_probe_dir: Path) -> VectorStore:
    return VectorStore(tmp_probe_dir / "vectors.npy", dimensions=4)


class TestVectorStore:
    def test_add_and_search(self, store: VectorStore):
        vectors = np.array([[1,0,0,0],[0,1,0,0],[0.7,0.7,0,0]], dtype=np.float32)
        store.add(chunk_ids=[10, 20, 30], vectors=vectors)
        query = np.array([0.9, 0.1, 0.0, 0.0], dtype=np.float32)
        results = store.search(query, top_k=2)
        assert len(results) == 2
        assert results[0][0] == 10
        assert results[0][1] > results[1][1]

    def test_save_and_load(self, store: VectorStore):
        vectors = np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32)
        store.add(chunk_ids=[1], vectors=vectors)
        store.save()
        loaded = VectorStore(store.path, dimensions=4)
        loaded.load()
        results = loaded.search(np.array([1,0,0,0], dtype=np.float32), top_k=1)
        assert results[0][0] == 1

    def test_clear(self, store: VectorStore):
        store.add(chunk_ids=[1], vectors=np.array([[1,0,0,0]], dtype=np.float32))
        store.clear()
        results = store.search(np.array([1,0,0,0], dtype=np.float32), top_k=1)
        assert len(results) == 0

    def test_save_after_clear_removes_vector_files(self, store: VectorStore):
        store.add(chunk_ids=[1], vectors=np.array([[1,0,0,0]], dtype=np.float32))
        store.save()

        store.clear()
        store.save()

        assert not store.path.exists()
        assert not store.path.with_suffix(".ids.npy").exists()

    def test_empty_search_returns_empty(self, store: VectorStore):
        results = store.search(np.array([1,0,0,0], dtype=np.float32), top_k=5)
        assert results == []
