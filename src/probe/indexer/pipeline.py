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
        root_dir: Path | None = None,
    ):
        self.db = db
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.root_dir = (root_dir or Path.cwd()).resolve()

    def _relative_path(self, file_path: Path) -> str:
        try:
            return str(file_path.resolve().relative_to(self.root_dir))
        except ValueError:
            return str(file_path)

    def _index_file(
        self, file_path: Path, rel_path: str, file_type: str,
        file_hash: str, mtime_ns: int, size: int,
    ) -> tuple[list[str], list[int]]:
        """Index a single file: delete old chunks, extract, chunk, persist.
        Returns (new_chunk_texts, new_chunk_ids) so the caller can batch-embed.
        Raises on extract/chunk errors; caller decides what to do."""
        # Remove any existing DB rows for this file before re-adding
        self.db.delete_file(rel_path)

        content = extract_content(file_path)
        if not content.strip():
            # File is now empty; caller (which tracked old chunk IDs before
            # calling) handles vector-store deletion; we just signal "nothing new".
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
                line_start=chunk.line_start, line_end=chunk.line_end,
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
            disk_rel_paths.add(self._relative_path(file_path))

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

            rel_path = self._relative_path(file_path)

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

    def refresh_changed(self, paths: list[Path]) -> dict:
        """Incrementally re-index files that changed since last index.

        Two-phase: (1) cheap stat sweep to detect candidates, (2) hash confirm
        and re-embed. Returns {added, changed, removed, elapsed_ms}."""
        import time as _time
        t0 = _time.monotonic()

        files = discover_files(paths)
        self.vector_store.load()

        # Phase 1: stat sweep and bucket files.
        disk_rel_paths: set[str] = set()
        candidates: list[tuple[Path, str, str, int, int, str | None]] = []
        # tuple: (file_path, rel_path, file_type, mtime_ns, size, existing_hash_or_None)

        for file_path in files:
            rel_path = self._relative_path(file_path)
            disk_rel_paths.add(rel_path)

            try:
                stat = file_path.stat()
            except FileNotFoundError:
                continue

            sig = self.db.get_file_signature(rel_path)
            if sig is None:
                # New file
                candidates.append((
                    file_path, rel_path, classify_file_type(file_path),
                    stat.st_mtime_ns, stat.st_size, None,
                ))
                continue
            existing_hash, existing_mtime, existing_size = sig
            if existing_mtime == stat.st_mtime_ns and existing_size == stat.st_size:
                continue  # unchanged — skip
            candidates.append((
                file_path, rel_path, classify_file_type(file_path),
                stat.st_mtime_ns, stat.st_size, existing_hash,
            ))

        # Deletions
        removed = 0
        deleted_chunk_ids: set[int] = set()
        for db_file in self.db.list_files():
            if db_file["path"] not in disk_rel_paths:
                deleted_chunk_ids.update(self.db.get_chunk_ids_for_file(db_file["path"]))
                self.db.delete_file(db_file["path"])
                removed += 1

        if deleted_chunk_ids:
            self.vector_store.delete(deleted_chunk_ids)

        # Phase 2: hash-confirm each candidate and re-index if content actually changed.
        added = 0
        changed = 0
        new_chunk_texts: list[str] = []
        new_chunk_ids: list[int] = []
        candidate_deleted_ids: set[int] = set()

        for file_path, rel_path, file_type, mtime_ns, size, existing_hash in candidates:
            try:
                file_hash = compute_file_hash(file_path)
            except (FileNotFoundError, PermissionError):
                continue

            if existing_hash is not None and file_hash == existing_hash:
                # Metadata-only change (e.g., `touch`): update sig, skip re-embed.
                self.db.update_file_signature(rel_path, mtime_ns, size)
                continue

            # Real content change (or new file): re-index.
            old_ids = self.db.get_chunk_ids_for_file(rel_path)
            candidate_deleted_ids.update(old_ids)

            texts, ids = self._index_file(
                file_path, rel_path, file_type, file_hash,
                mtime_ns=mtime_ns, size=size,
            )
            if not texts:
                continue
            new_chunk_texts.extend(texts)
            new_chunk_ids.extend(ids)
            if existing_hash is None:
                added += 1
            else:
                changed += 1

        if candidate_deleted_ids:
            self.vector_store.delete(candidate_deleted_ids)

        # Batch-embed new chunks
        if new_chunk_texts:
            for i in range(0, len(new_chunk_texts), EMBED_BATCH_SIZE):
                batch_texts = new_chunk_texts[i:i + EMBED_BATCH_SIZE]
                batch_ids = new_chunk_ids[i:i + EMBED_BATCH_SIZE]
                vectors = self.embedding_provider.embed(batch_texts, input_type="document")
                self.vector_store.add(batch_ids, vectors)

        if deleted_chunk_ids or candidate_deleted_ids or new_chunk_texts:
            self.vector_store.save()

        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        return {"added": added, "changed": changed, "removed": removed, "elapsed_ms": elapsed_ms}
