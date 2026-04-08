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


class TestOpenAIEmbedding:
    def test_embed_returns_array(self):
        import sys
        mock_openai = MagicMock()
        sys.modules["openai"] = mock_openai
        # Clear cached module so it re-imports with the mock
        sys.modules.pop("probe.providers.openai", None)
        try:
            from probe.providers.openai import OpenAIEmbedding
            mock_client = MagicMock()
            mock_openai.OpenAI.return_value = mock_client
            mock_response = MagicMock()
            mock_response.data = [
                MagicMock(embedding=[0.1] * 1536),
                MagicMock(embedding=[0.2] * 1536),
            ]
            mock_client.embeddings.create.return_value = mock_response
            provider = OpenAIEmbedding(api_key="test", dimensions=1536)
            result = provider.embed(["hello", "world"])
            assert result.shape == (2, 1536)
        finally:
            sys.modules.pop("openai", None)
            sys.modules.pop("probe.providers.openai", None)


class TestCohereEmbedding:
    def test_embed_returns_array(self):
        import sys
        mock_cohere = MagicMock()
        sys.modules["cohere"] = mock_cohere
        sys.modules.pop("probe.providers.cohere", None)
        try:
            from probe.providers.cohere import CohereEmbedding
            mock_client = MagicMock()
            mock_cohere.ClientV2.return_value = mock_client
            mock_response = MagicMock()
            mock_response.embeddings = MagicMock(float_=[[0.1] * 1024, [0.2] * 1024])
            mock_client.embed.return_value = mock_response
            provider = CohereEmbedding(api_key="test", dimensions=1024)
            result = provider.embed(["hello", "world"])
            assert result.shape == (2, 1024)
        finally:
            sys.modules.pop("cohere", None)
            sys.modules.pop("probe.providers.cohere", None)


class TestCohereRerank:
    def test_rerank_returns_results(self):
        import sys
        mock_cohere = MagicMock()
        sys.modules["cohere"] = mock_cohere
        sys.modules.pop("probe.providers.cohere", None)
        try:
            from probe.providers.cohere import CohereRerank
            mock_client = MagicMock()
            mock_cohere.ClientV2.return_value = mock_client
            mock_response = MagicMock()
            mock_response.results = [
                MagicMock(index=1, relevance_score=0.9),
                MagicMock(index=0, relevance_score=0.3),
            ]
            mock_client.rerank.return_value = mock_response
            provider = CohereRerank(api_key="test")
            results = provider.rerank("query", ["doc1", "doc2"])
            assert len(results) == 2
            assert results[0].score == 0.9
        finally:
            sys.modules.pop("cohere", None)
            sys.modules.pop("probe.providers.cohere", None)
