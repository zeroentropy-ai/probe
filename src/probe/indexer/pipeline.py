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

    def _index_file(
        self, file_path: Path, rel_path: str, file_type: str,
        file_hash: str, mtime_ns: int, size: int,
    ) -> tuple[list[str], list[int]]:
        """Index a single file: delete old chunks, extract, chunk, persist.
        Returns (new_chunk_texts, new_chunk_ids) so the caller can batch-embed.
        Raises on extract/chunk errors; caller decides what to do."""
        # Remove any existing vectors for this file before re-adding
        old_ids = self.db.get_chunk_ids_for_file(rel_path)
        self.db.delete_file(rel_path)

        content = extract_content(file_path)
        if not content.strip():
            # Still return old_ids so caller can delete from vector store
            return ([], [])

        chunks = chunk_content(content, rel_path, file_type)
        if not chunks:
            return ([], [])

        file_id = self.db.add_file(
            rel_path, file_hash, file_type,
            mtime_ns=mtime_ns, size=size,
        )
        new_chunk_texts: list[str] = []
        new_chunk_ids: list[int] = []
        for chunk in chunks:
            chunk_id = self.db.add_chunk(
                file_id=file_id, chunk_index=chunk.chunk_index,
                content=chunk.content, file_type=chunk.file_type,
                char_start=chunk.char_start, char_end=chunk.char_end,
                token_count=chunk.token_count, header_path=chunk.header_path,
                symbol_name=chunk.symbol_name, page_number=chunk.page_number,
            )
            new_chunk_texts.append(chunk.content)
            new_chunk_ids.append(chunk_id)
        self.db.commit()
        return (new_chunk_texts, new_chunk_ids)

    def index(self, paths: list[Path], full: bool = False) -> dict:
        files = discover_files(paths)

        files_indexed = 0
        files_skipped = 0
        chunks_created = 0
        files_removed = 0
        new_chunk_texts: list[str] = []
        new_chunk_ids: list[int] = []
        deleted_chunk_ids: set[int] = set()

        # Load existing vectors for incremental updates
        self.vector_store.load()

        # Clean up files that no longer exist on disk
        disk_rel_paths: set[str] = set()
        for file_path in files:
            try:
                disk_rel_paths.add(str(file_path.relative_to(Path.cwd())))
            except ValueError:
                disk_rel_paths.add(str(file_path))

        for db_file in self.db.list_files():
            if db_file["path"] not in disk_rel_paths:
                old_ids = self.db.get_chunk_ids_for_file(db_file["path"])
                deleted_chunk_ids.update(old_ids)
                self.db.delete_file(db_file["path"])
                files_removed += 1

        for file_path in files:
            file_hash = compute_file_hash(file_path)
            file_type = classify_file_type(file_path)
            stat = file_path.stat()

            try:
                rel_path = str(file_path.relative_to(Path.cwd()))
            except ValueError:
                rel_path = str(file_path)

            if not full:
                existing_hash = self.db.get_file_hash(rel_path)
                if existing_hash == file_hash:
                    files_skipped += 1
                    continue

            # Track old chunk IDs for vector deletion before re-adding
            old_ids = self.db.get_chunk_ids_for_file(rel_path)
            deleted_chunk_ids.update(old_ids)

            texts, ids = self._index_file(
                file_path, rel_path, file_type, file_hash,
                mtime_ns=stat.st_mtime_ns, size=stat.st_size,
            )
            if not texts:
                continue
            new_chunk_texts.extend(texts)
            new_chunk_ids.extend(ids)
            chunks_created += len(texts)
            files_indexed += 1

        # Remove vectors for deleted/changed files
        if deleted_chunk_ids:
            self.vector_store.delete(deleted_chunk_ids)

        # Embed only new chunks
        if new_chunk_texts:
            for i in range(0, len(new_chunk_texts), EMBED_BATCH_SIZE):
                batch_texts = new_chunk_texts[i:i + EMBED_BATCH_SIZE]
                batch_ids = new_chunk_ids[i:i + EMBED_BATCH_SIZE]
                vectors = self.embedding_provider.embed(batch_texts, input_type="document")
                self.vector_store.add(batch_ids, vectors)

        # Save if anything changed
        if new_chunk_texts or deleted_chunk_ids:
            self.vector_store.save()

        return {
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
            "chunks_created": chunks_created,
        }
