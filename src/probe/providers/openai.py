"""OpenAI embedding provider."""

from __future__ import annotations

import numpy as np
from openai import OpenAI

from probe.providers.base import EmbeddingProvider


class OpenAIEmbedding(EmbeddingProvider):
    def __init__(self, api_key: str, model: str = "text-embedding-3-large", dimensions: int = 1536):
        self._client = OpenAI(api_key=api_key)
        self._model = model
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str], input_type: str = "document") -> np.ndarray:
        response = self._client.embeddings.create(
            model=self._model, input=texts, dimensions=self._dimensions,
        )
        return np.array([item.embedding for item in response.data], dtype=np.float32)
