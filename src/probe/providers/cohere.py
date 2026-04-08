"""Cohere embedding and reranking providers."""

from __future__ import annotations

import cohere
import numpy as np

from probe.models import RerankResult
from probe.providers.base import EmbeddingProvider, RerankProvider

INPUT_TYPE_MAP = {"document": "search_document", "query": "search_query"}


class CohereEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "embed-v4.0", dimensions: int = 1024):
        self._client = cohere.ClientV2(api_key=api_key)
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str], input_type: str = "document") -> np.ndarray:
        response = self._client.embed(
            model=self._model, texts=texts,
            input_type=INPUT_TYPE_MAP.get(input_type, input_type),
            embedding_types=["float"],
        )
        return np.array(response.embeddings.float_, dtype=np.float32)


class CohereRerank(RerankProvider):
    def __init__(self, api_key: str, model: str = "rerank-v3.5"):
        self._client = cohere.ClientV2(api_key=api_key)
        self._model = model

    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[RerankResult]:
        response = self._client.rerank(
            model=self._model, query=query, documents=documents,
            top_n=top_n or len(documents),
        )
        return [
            RerankResult(index=r.index, score=r.relevance_score, text=documents[r.index])
            for r in response.results
        ]
