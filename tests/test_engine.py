"""Tests for the context engine."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from probe.models import ContextResponse, RerankResult
from probe.search.engine import ContextEngine
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB


@pytest.fixture
def populated_db(tmp_probe_dir):
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    fid = db.add_file("docs/auth.md", "h1", "markdown")
    db.add_chunk(fid, 0, "OAuth PKCE authentication flow with Auth0", "markdown", 0, 42, 7,
                 header_path="Authentication > OAuth Flow", line_start=4, line_end=4)
    db.add_chunk(fid, 1, "Rate limiting uses sliding window algorithm", "markdown", 43, 87, 7,
                 header_path="API Design > Rate Limiting")
    fid2 = db.add_file("src/auth.py", "h2", "code")
    db.add_chunk(fid2, 0, "class OAuthHandler:\n    pass", "code", 0, 28, 5,
                 symbol_name="OAuthHandler")
    return db


@pytest.fixture
def populated_vector_store(tmp_probe_dir):
    store = VectorStore(tmp_probe_dir / "vectors.npy", dimensions=4)
    vectors = np.array([[1,0,0,0],[0,1,0,0],[0.8,0.2,0,0]], dtype=np.float32)
    store.add([1, 2, 3], vectors)
    return store


@pytest.fixture
def mock_embedding():
    provider = MagicMock()
    provider.embed.return_value = np.array([[0.9, 0.1, 0.0, 0.0]], dtype=np.float32)
    return provider


@pytest.fixture
def mock_reranker():
    provider = MagicMock()
    provider.rerank.side_effect = lambda query, docs, **kw: [
        RerankResult(index=i, score=1.0 - i * 0.1, text=doc) for i, doc in enumerate(docs)
    ]
    return provider


@pytest.fixture
def engine(populated_db, populated_vector_store, mock_embedding, mock_reranker):
    return ContextEngine(db=populated_db, vector_store=populated_vector_store,
                         embedding_provider=mock_embedding, rerank_provider=mock_reranker)


class TestContextEngine:
    def test_search_returns_results(self, engine):
        response = engine.search("authentication")
        assert isinstance(response, ContextResponse)
        assert len(response.results) > 0
        assert response.query == "authentication"

    def test_results_have_scores(self, engine):
        response = engine.search("authentication")
        for result in response.results:
            assert result.score > 0
            assert result.file is not None

    def test_respects_top_k(self, engine):
        response = engine.search("authentication", top_k=1)
        assert len(response.results) <= 1

    def test_respects_max_tokens(self, engine):
        response = engine.search("authentication", max_tokens=10)
        assert response.total_tokens <= 20

    def test_reranker_called(self, engine, mock_reranker):
        engine.search("authentication")
        assert mock_reranker.rerank.called

    def test_no_rerank_option(self, engine, mock_reranker):
        engine.search("authentication", rerank=False)
        assert not mock_reranker.rerank.called

    def test_sources_searched_count(self, engine):
        response = engine.search("authentication")
        assert response.sources_searched == 3

    def test_results_include_line_ranges(self, engine):
        response = engine.search("authentication")
        auth_result = next(r for r in response.results if r.file == "docs/auth.md")
        assert auth_result.line_start == 4
        assert auth_result.line_end == 4
