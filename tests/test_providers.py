"""Tests for providers."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from probe.providers.zeroentropy import ZeroEntropyEmbedding, ZeroEntropyRerank
from probe.models import RerankResult


class TestZeroEntropyEmbedding:
    @patch("probe.providers.zeroentropy.ZeroEntropy")
    def test_embed_returns_array(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024), MagicMock(embedding=[0.2] * 1024)]
        mock_client.models.embed.return_value = mock_response

        provider = ZeroEntropyEmbedding(api_key="test-key", dimensions=1024)
        result = provider.embed(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 1024)

    @patch("probe.providers.zeroentropy.ZeroEntropy")
    def test_embed_passes_input_type(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
        mock_client.models.embed.return_value = mock_response

        provider = ZeroEntropyEmbedding(api_key="test-key")
        provider.embed(["query text"], input_type="query")
        call_kwargs = mock_client.models.embed.call_args[1]
        assert call_kwargs["input_type"] == "query"

    def test_dimensions_property(self):
        with patch("probe.providers.zeroentropy.ZeroEntropy"):
            provider = ZeroEntropyEmbedding(api_key="test", dimensions=512)
            assert provider.dimensions == 512


class TestZeroEntropyRerank:
    @patch("probe.providers.zeroentropy.ZeroEntropy")
    def test_rerank_returns_sorted_results(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_response = MagicMock()
        mock_response.results = [
            MagicMock(index=1, relevance_score=0.95),
            MagicMock(index=0, relevance_score=0.42),
        ]
        mock_client.models.rerank.return_value = mock_response

        provider = ZeroEntropyRerank(api_key="test-key")
        results = provider.rerank("authentication", ["doc about cats", "doc about auth"])
        assert len(results) == 2
        assert results[0].score == 0.95
        assert results[0].index == 1
        assert results[0].text == "doc about auth"
