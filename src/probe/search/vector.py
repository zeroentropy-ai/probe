"""Numpy-based vector store with cosine similarity search."""

from __future__ import annotations

from pathlib import Path

import numpy as np


class VectorStore:
    def __init__(self, path: Path, dimensions: int):
        self.path = path
        self.dimensions = dimensions
        self._ids: list[int] = []
        self._vectors: np.ndarray | None = None

    def add(self, chunk_ids: list[int], vectors: np.ndarray) -> None:
        if self._vectors is None:
            self._vectors = vectors.astype(np.float32)
        else:
            self._vectors = np.vstack([self._vectors, vectors.astype(np.float32)])
        self._ids.extend(chunk_ids)

    def search(self, query: np.ndarray, top_k: int = 30) -> list[tuple[int, float]]:
        if self._vectors is None or len(self._ids) == 0:
            return []

        query = query.astype(np.float32).flatten()
        norms = np.linalg.norm(self._vectors, axis=1)
        query_norm = np.linalg.norm(query)

        valid = (norms > 0) & (query_norm > 0)
        scores = np.zeros(len(self._ids), dtype=np.float32)
        scores[valid] = (self._vectors[valid] @ query) / (norms[valid] * query_norm)

        k = min(top_k, len(self._ids))
        top_indices = np.argpartition(scores, -k)[-k:]
        top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        return [(self._ids[i], float(scores[i])) for i in top_indices]

    def save(self) -> None:
        if self._vectors is not None:
            np.save(str(self.path), self._vectors)
            ids_path = self.path.with_suffix(".ids.npy")
            np.save(str(ids_path), np.array(self._ids, dtype=np.int64))

    def load(self) -> None:
        if self.path.exists():
            self._vectors = np.load(str(self.path))
            ids_path = self.path.with_suffix(".ids.npy")
            if ids_path.exists():
                self._ids = np.load(str(ids_path)).tolist()

    def delete(self, chunk_ids: set[int]) -> None:
        """Delete vectors by chunk IDs."""
        if self._vectors is None or not chunk_ids:
            return
        keep = [i for i, cid in enumerate(self._ids) if cid not in chunk_ids]
        if not keep:
            self.clear()
            return
        self._vectors = self._vectors[keep]
        self._ids = [self._ids[i] for i in keep]

    def clear(self) -> None:
        self._ids = []
        self._vectors = None
