"""Indexing pipeline: discover -> extract -> chunk -> embed -> store."""

from __future__ import annotations

from pathlib import Path

from probe.indexer.chunkers import chunk_content
from probe.indexer.discovery import compute_file_hash, discover_files
from probe.indexer.extractors import classify_file_type, extract_content
from probe.providers.base import EmbeddingProvider
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB

EMBED_BATCH_SIZE = 96


class IndexPipeline:
    def __init__(
        self, db: ProbeDB, vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
    ):
        self.db = db
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider

    def index(self, paths: list[Path], full: bool = False) -> dict:
        files = discover_files(paths)

        files_indexed = 0
        files_skipped = 0
        chunks_created = 0

        for file_path in files:
            file_hash = compute_file_hash(file_path)
            file_type = classify_file_type(file_path)

            # Make path relative to cwd for consistent storage
            try:
                rel_path = str(file_path.relative_to(Path.cwd()))
            except ValueError:
                rel_path = str(file_path)

            if not full:
                existing_hash = self.db.get_file_hash(rel_path)
                if existing_hash == file_hash:
                    files_skipped += 1
                    continue

            self.db.delete_file(rel_path)

            content = extract_content(file_path)
            if not content.strip():
                continue

            chunks = chunk_content(content, rel_path, file_type)
            if not chunks:
                continue

            file_id = self.db.add_file(rel_path, file_hash, file_type)
            for chunk in chunks:
                self.db.add_chunk(
                    file_id=file_id, chunk_index=chunk.chunk_index,
                    content=chunk.content, file_type=chunk.file_type,
                    char_start=chunk.char_start, char_end=chunk.char_end,
                    token_count=chunk.token_count, header_path=chunk.header_path,
                    symbol_name=chunk.symbol_name, page_number=chunk.page_number,
                )
                chunks_created += 1
            files_indexed += 1

        # Re-embed ALL chunks to keep vectors in sync
        all_db_chunks = self.db.get_all_chunks()
        if all_db_chunks:
            self.vector_store.clear()
            all_texts = [c["content"] for c in all_db_chunks]
            all_ids = [c["id"] for c in all_db_chunks]

            for i in range(0, len(all_texts), EMBED_BATCH_SIZE):
                batch_texts = all_texts[i:i + EMBED_BATCH_SIZE]
                batch_ids = all_ids[i:i + EMBED_BATCH_SIZE]
                vectors = self.embedding_provider.embed(batch_texts, input_type="document")
                self.vector_store.add(batch_ids, vectors)
            self.vector_store.save()

        return {
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
            "chunks_created": chunks_created,
        }
