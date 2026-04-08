"""ZeroEntropy embedding and reranking providers."""

from __future__ import annotations

import numpy as np
from zeroentropy import ZeroEntropy

from probe.models import RerankResult
from probe.providers.base import EmbeddingProvider, RerankProvider


class ZeroEntropyEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "zembed-1", dimensions: int = 1280):
        self._client = ZeroEntropy(api_key=api_key)
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str], input_type: str = "document") -> np.ndarray:
        response = self._client.models.embed(
            model=self._model, input=texts, input_type=input_type, dimensions=self._dimensions,
        )
        return np.array([item.embedding for item in response.results], dtype=np.float32)


class ZeroEntropyRerank(RerankProvider):
    def __init__(self, api_key: str, model: str = "zerank-2"):
        self._client = ZeroEntropy(api_key=api_key)
        self._model = model

    def rerank(
        self, query: str, documents: list[str], top_n: int | None = None,
    ) -> list[RerankResult]:
        kwargs: dict = {"model": self._model, "query": query, "documents": documents}
        if top_n is not None:
            kwargs["top_n"] = top_n
        response = self._client.models.rerank(**kwargs)
        return [
            RerankResult(index=r.index, score=r.relevance_score, text=documents[r.index])
            for r in response.results
        ]
