"""Context Engine: hybrid retrieval -> RRF -> rerank -> context assembly."""

from __future__ import annotations

import tiktoken

from probe.models import ContextResponse, SearchResult
from probe.providers.base import EmbeddingProvider, RerankProvider
from probe.search.bm25 import BM25Search
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB

_enc = tiktoken.get_encoding("cl100k_base")
RRF_K = 60


class ContextEngine:
    def __init__(
        self, db: ProbeDB, vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        rerank_provider: RerankProvider | None = None,
    ):
        self.db = db
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.rerank_provider = rerank_provider
        self._bm25 = BM25Search(db)

    def search(self, query: str, top_k: int = 10, max_tokens: int = 4096,
               file_types: list[str] | None = None, rerank: bool = True,
               threshold: float = 0.0) -> ContextResponse:
        stats = self.db.get_stats()

        # Step 1: Hybrid retrieval
        bm25_results = self._bm25.search(query, top_k=30)
        query_vec = self.embedding_provider.embed([query], input_type="query")[0]
        vector_results = self.vector_store.search(query_vec, top_k=30)

        # Step 2: RRF merge
        merged = self._rrf_merge(bm25_results, vector_results)

        # Filter by file type
        if file_types:
            filtered = []
            for chunk_id, score in merged:
                chunk = self.db.get_chunk(chunk_id)
                if chunk and chunk["file_type"] in file_types:
                    filtered.append((chunk_id, score))
            merged = filtered

        # Fetch chunk data
        candidates = []
        for chunk_id, rrf_score in merged:
            chunk = self.db.get_chunk(chunk_id)
            if chunk:
                candidates.append((chunk, rrf_score))

        if not candidates:
            return ContextResponse(query=query, sources_searched=stats["total_chunks"])

        # Step 3: Reranking
        if rerank and self.rerank_provider and candidates:
            docs = [c["content"] for c, _ in candidates]
            reranked = self.rerank_provider.rerank(query, docs)
            scored_candidates = []
            for rr in reranked:
                chunk, _ = candidates[rr.index]
                scored_candidates.append((chunk, rr.score))
            candidates = scored_candidates
        else:
            candidates.sort(key=lambda x: x[1], reverse=True)

        # Step 4: Filter by threshold
        if threshold > 0:
            candidates = [(c, s) for c, s in candidates if s >= threshold]

        # Step 5: Deduplicate
        candidates = self._deduplicate(candidates)

        # Step 6: Trim to top_k and token budget
        results: list[SearchResult] = []
        total_tokens = 0
        for chunk, score in candidates[:top_k]:
            chunk_tokens = len(_enc.encode(chunk["content"]))
            if total_tokens + chunk_tokens > max_tokens and results:
                break
            results.append(SearchResult(
                score=round(score, 4), file=chunk["file_path"],
                file_type=chunk["file_type"], content=chunk["content"],
                char_range=(chunk["char_start"], chunk["char_end"]),
                header_path=chunk.get("header_path"),
                symbol_name=chunk.get("symbol_name"),
                page_number=chunk.get("page_number"),
                line_start=chunk.get("line_start"),
                line_end=chunk.get("line_end"),
            ))
            total_tokens += chunk_tokens

        return ContextResponse(query=query, results=results, total_tokens=total_tokens,
                               sources_searched=stats["total_chunks"])

    def _rrf_merge(self, bm25_results, vector_results):
        scores: dict[int, float] = {}
        for rank, (chunk_id, _) in enumerate(bm25_results):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (RRF_K + rank + 1)
        for rank, (chunk_id, _) in enumerate(vector_results):
            scores[chunk_id] = scores.get(chunk_id, 0) + 1.0 / (RRF_K + rank + 1)
        return sorted(scores.items(), key=lambda x: x[1], reverse=True)

    def _deduplicate(self, candidates):
        kept = []
        for chunk, score in candidates:
            is_dup = False
            for existing, _ in kept:
                if self._text_overlap(chunk["content"], existing["content"]) > 0.8:
                    is_dup = True
                    break
            if not is_dup:
                kept.append((chunk, score))
        return kept

    @staticmethod
    def _text_overlap(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        words_a = set(a.lower().split())
        words_b = set(b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        return len(intersection) / min(len(words_a), len(words_b))
