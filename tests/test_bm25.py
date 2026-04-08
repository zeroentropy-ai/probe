"""Tests for BM25 search wrapper."""

from pathlib import Path
import pytest
from probe.search.bm25 import BM25Search
from probe.store.database import ProbeDB


@pytest.fixture
def bm25(tmp_probe_dir: Path) -> BM25Search:
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    file_id = db.add_file("test.md", "hash", "markdown")
    db.add_chunk(file_id, 0, "OAuth PKCE authentication flow with Auth0", "markdown", 0, 42, 7)
    db.add_chunk(file_id, 1, "Rate limiting uses a sliding window algorithm", "markdown", 43, 88, 7)
    db.add_chunk(file_id, 2, "Authentication tokens are stored in Redis", "markdown", 89, 130, 7)
    return BM25Search(db)


class TestBM25Search:
    def test_basic_search(self, bm25: BM25Search):
        results = bm25.search("authentication", top_k=10)
        assert len(results) >= 1
        for chunk_id, score in results:
            assert isinstance(chunk_id, int)
            assert isinstance(score, float)

    def test_respects_top_k(self, bm25: BM25Search):
        results = bm25.search("authentication", top_k=1)
        assert len(results) == 1

    def test_no_results_for_unrelated_query(self, bm25: BM25Search):
        results = bm25.search("kubernetes deployment", top_k=10)
        assert len(results) == 0
