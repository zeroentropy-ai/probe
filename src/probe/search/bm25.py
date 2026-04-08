"""BM25 search via SQLite FTS5."""

from __future__ import annotations

from probe.store.database import ProbeDB


class BM25Search:
    def __init__(self, db: ProbeDB):
        self._db = db

    def search(self, query: str, top_k: int = 30) -> list[tuple[int, float]]:
        results = self._db.search_fts(query, top_k=top_k)
        return [(r["chunk_id"], abs(float(r["rank"]))) for r in results]
