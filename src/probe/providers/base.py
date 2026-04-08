"""Abstract base classes for embedding and reranking providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np

from probe.models import RerankResult


class EmbeddingProvider(ABC):
    """Base class for embedding providers."""

    @abstractmethod
    def embed(self, texts: list[str], input_type: str = "document") -> np.ndarray:
        """Embed a list of texts. Returns (n, dim) array."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the embedding dimensions."""


class RerankProvider(ABC):
    """Base class for reranking providers."""

    @abstractmethod
    def rerank(self, query: str, documents: list[str], top_n: int | None = None) -> list[RerankResult]:
        """Rerank documents by relevance to query. Returns sorted results."""
