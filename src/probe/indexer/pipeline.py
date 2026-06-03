"""Indexing pipeline: discover -> extract -> chunk -> embed -> store."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from probe.indexer.chunkers import chunk_content
from probe.indexer.discovery import compute_file_hash, discover_files
from probe.indexer.extractors import classify_file_type, extract_content
from probe.models import Chunk
from probe.providers.base import EmbeddingProvider
from probe.search.vector import VectorStore
from probe.store.database import ProbeDB

DEFAULT_EMBED_BATCH_MAX_CHUNKS = 96
DEFAULT_EMBED_BATCH_MAX_BYTES = 4_500_000


@dataclass
class _PreparedFile:
    file_path: Path
    rel_path: str
    file_type: str
    file_hash: str
    mtime_ns: int
    size: int
    old_chunk_ids: list[int]
    chunks: list[Chunk]


@dataclass
class _EmbedItem:
    file_index: int
    chunk_index: int
    text: str
    byte_size: int


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

    def _env_int(self, name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    @property
    def _embed_batch_max_chunks(self) -> int:
        return self._env_int("PROBE_EMBED_BATCH_MAX_CHUNKS", DEFAULT_EMBED_BATCH_MAX_CHUNKS)

    @property
    def _embed_batch_max_bytes(self) -> int:
        return self._env_int("PROBE_EMBED_BATCH_MAX_BYTES", DEFAULT_EMBED_BATCH_MAX_BYTES)

    def _prepare_file(
        self, file_path: Path, rel_path: str, file_type: str,
        file_hash: str, mtime_ns: int, size: int, old_chunk_ids: list[int],
    ) -> _PreparedFile:
        """Extract and chunk a file without mutating DB/vector state."""
        content = extract_content(file_path)
        chunks = chunk_content(content, rel_path, file_type) if content.strip() else []
        return _PreparedFile(
            file_path=file_path,
            rel_path=rel_path,
            file_type=file_type,
            file_hash=file_hash,
            mtime_ns=mtime_ns,
            size=size,
            old_chunk_ids=old_chunk_ids,
            chunks=chunks,
        )

    def _embed_batch(
        self,
        items: list[_EmbedItem],
        vectors_by_file: dict[int, list[np.ndarray | None]],
        failed_files: dict[int, str],
    ) -> None:
        active = [item for item in items if item.file_index not in failed_files]
        if not active:
            return
        try:
            vectors = self.embedding_provider.embed(
                [item.text for item in active],
                input_type="document",
            )
            if len(vectors) != len(active):
                raise ValueError(
                    f"embedding provider returned {len(vectors)} vectors for "
                    f"{len(active)} chunks"
                )
        except Exception as exc:
            file_indexes = {item.file_index for item in active}
            if len(file_indexes) == 1:
                failed_files[next(iter(file_indexes))] = str(exc)
                return
            for file_index in sorted(file_indexes):
                self._embed_batch(
                    [item for item in active if item.file_index == file_index],
                    vectors_by_file,
                    failed_files,
                )
            return

        for item, vector in zip(active, vectors, strict=True):
            vectors_by_file[item.file_index][item.chunk_index] = vector

    def _embed_prepared_files(
        self,
        prepared_files: list[_PreparedFile],
    ) -> tuple[dict[int, list[np.ndarray]], dict[int, str]]:
        vectors_by_file: dict[int, list[np.ndarray | None]] = {
            file_index: [None] * len(prepared.chunks)
            for file_index, prepared in enumerate(prepared_files)
            if prepared.chunks
        }
        failed_files: dict[int, str] = {}
        max_chunks = self._embed_batch_max_chunks
        max_bytes = self._embed_batch_max_bytes
        items: list[_EmbedItem] = []

        for file_index, prepared in enumerate(prepared_files):
            for chunk_index, chunk in enumerate(prepared.chunks):
                byte_size = len(chunk.content.encode("utf-8"))
                if byte_size > max_bytes:
                    failed_files[file_index] = (
                        f"chunk exceeds PROBE_EMBED_BATCH_MAX_BYTES "
                        f"({byte_size} > {max_bytes})"
                    )
                    continue
                items.append(_EmbedItem(
                    file_index=file_index,
                    chunk_index=chunk_index,
                    text=chunk.content,
                    byte_size=byte_size,
                ))

        batch: list[_EmbedItem] = []
        batch_bytes = 0
        for item in items:
            if item.file_index in failed_files:
                continue
            would_exceed_chunks = len(batch) >= max_chunks
            would_exceed_bytes = batch and batch_bytes + item.byte_size > max_bytes
            if would_exceed_chunks or would_exceed_bytes:
                self._embed_batch(batch, vectors_by_file, failed_files)
                batch = []
                batch_bytes = 0
            batch.append(item)
            batch_bytes += item.byte_size
        if batch:
            self._embed_batch(batch, vectors_by_file, failed_files)

        successful_vectors: dict[int, list[np.ndarray]] = {}
        for file_index, vectors in vectors_by_file.items():
            if file_index in failed_files:
                continue
            if any(vector is None for vector in vectors):
                failed_files[file_index] = "embedding provider did not return all vectors"
                continue
            successful_vectors[file_index] = [vector for vector in vectors if vector is not None]
        return successful_vectors, failed_files

    def _store_prepared_file(
        self,
        prepared: _PreparedFile,
        vectors: list[np.ndarray] | None,
    ) -> int:
        old_vector_ids = list(self.vector_store._ids)
        old_vectors = (
            None
            if self.vector_store._vectors is None
            else self.vector_store._vectors.copy()
        )
        try:
            if prepared.old_chunk_ids:
                self.vector_store.delete(set(prepared.old_chunk_ids))
            self.db.delete_file(prepared.rel_path, commit=False)
            if not prepared.chunks:
                self.db.commit()
                return 0

            file_id = self.db.add_file(
                prepared.rel_path,
                prepared.file_hash,
                prepared.file_type,
                mtime_ns=prepared.mtime_ns,
                size=prepared.size,
                commit=False,
            )
            new_chunk_ids: list[int] = []
            for chunk in prepared.chunks:
                chunk_id = self.db.add_chunk(
                    file_id=file_id, chunk_index=chunk.chunk_index,
                    content=chunk.content, file_type=chunk.file_type,
                    char_start=chunk.char_start, char_end=chunk.char_end,
                    token_count=chunk.token_count, header_path=chunk.header_path,
                    symbol_name=chunk.symbol_name, page_number=chunk.page_number,
                    line_start=chunk.line_start, line_end=chunk.line_end,
                )
                new_chunk_ids.append(chunk_id)
            if vectors is None:
                raise ValueError("vectors required for non-empty file")
            self.vector_store.add(new_chunk_ids, np.array(vectors, dtype=np.float32))
            self.db.commit()
            return len(new_chunk_ids)
        except Exception:
            self.db.conn.rollback()
            self.vector_store._ids = old_vector_ids
            self.vector_store._vectors = old_vectors
            raise

    def index(self, paths: list[Path], full: bool = False) -> dict:
        files = discover_files(paths)

        files_indexed = 0
        files_skipped = 0
        chunks_created = 0
        files_removed = 0
        files_failed = 0
        failed_files: list[dict[str, str]] = []
        prepared_files: list[_PreparedFile] = []
        changed_vectors = False

        # Load existing vectors for incremental updates
        self.vector_store.load()

        # Clean up files that no longer exist on disk
        disk_rel_paths: set[str] = set()
        for file_path in files:
            disk_rel_paths.add(self._relative_path(file_path))

        for db_file in self.db.list_files():
            if db_file["path"] not in disk_rel_paths:
                old_ids = self.db.get_chunk_ids_for_file(db_file["path"])
                if old_ids:
                    self.vector_store.delete(set(old_ids))
                    changed_vectors = True
                self.db.delete_file(db_file["path"], commit=False)
                files_removed += 1
        if files_removed:
            self.db.commit()

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

            try:
                prepared = self._prepare_file(
                    file_path, rel_path, file_type, file_hash,
                    mtime_ns=stat.st_mtime_ns, size=stat.st_size,
                    old_chunk_ids=old_ids,
                )
            except Exception as exc:
                files_failed += 1
                failed_files.append({"path": rel_path, "error": str(exc)})
                continue

            if not prepared.chunks:
                try:
                    self._store_prepared_file(prepared, vectors=None)
                except Exception as exc:
                    files_failed += 1
                    failed_files.append({"path": rel_path, "error": str(exc)})
                else:
                    changed_vectors = True
                continue
            prepared_files.append(prepared)

        vectors_by_file, embedding_failures = self._embed_prepared_files(prepared_files)
        for file_index, error in embedding_failures.items():
            files_failed += 1
            failed_files.append({"path": prepared_files[file_index].rel_path, "error": error})

        for file_index, vectors in vectors_by_file.items():
            try:
                chunks_added = self._store_prepared_file(prepared_files[file_index], vectors)
            except Exception as exc:
                files_failed += 1
                failed_files.append({
                    "path": prepared_files[file_index].rel_path,
                    "error": str(exc),
                })
                continue
            if chunks_added:
                files_indexed += 1
                chunks_created += chunks_added
                changed_vectors = True

        if changed_vectors:
            self.vector_store.save()

        return {
            "files_indexed": files_indexed,
            "files_skipped": files_skipped,
            "chunks_created": chunks_created,
            "files_failed": files_failed,
            "failed_files": failed_files,
        }

    def refresh_changed(self, paths: list[Path]) -> dict:
        """Incrementally re-index files that changed since last index.

        Two-phase: (1) cheap stat sweep to detect candidates, (2) hash confirm
        and re-embed. Returns {added, changed, removed, failed, elapsed_ms}."""
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
                self.db.delete_file(db_file["path"], commit=False)
                removed += 1

        if deleted_chunk_ids:
            self.vector_store.delete(deleted_chunk_ids)
        if removed:
            self.db.commit()

        # Phase 2: hash-confirm each candidate and re-index if content actually changed.
        added = 0
        changed = 0
        failed = 0
        failed_files: list[dict[str, str]] = []
        prepared_files: list[_PreparedFile] = []
        prepared_existing_hashes: list[str | None] = []
        changed_vectors = bool(deleted_chunk_ids)

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

            try:
                prepared = self._prepare_file(
                    file_path, rel_path, file_type, file_hash,
                    mtime_ns=mtime_ns, size=size, old_chunk_ids=old_ids,
                )
            except Exception as exc:
                failed += 1
                failed_files.append({"path": rel_path, "error": str(exc)})
                continue

            if not prepared.chunks:
                try:
                    self._store_prepared_file(prepared, vectors=None)
                except Exception as exc:
                    failed += 1
                    failed_files.append({"path": rel_path, "error": str(exc)})
                else:
                    changed_vectors = True
                continue
            prepared_files.append(prepared)
            prepared_existing_hashes.append(existing_hash)

        vectors_by_file, embedding_failures = self._embed_prepared_files(prepared_files)
        for file_index, error in embedding_failures.items():
            failed += 1
            failed_files.append({"path": prepared_files[file_index].rel_path, "error": error})

        for file_index, vectors in vectors_by_file.items():
            try:
                chunks_added = self._store_prepared_file(prepared_files[file_index], vectors)
            except Exception as exc:
                failed += 1
                failed_files.append({
                    "path": prepared_files[file_index].rel_path,
                    "error": str(exc),
                })
                continue
            if chunks_added:
                if prepared_existing_hashes[file_index] is None:
                    added += 1
                else:
                    changed += 1
                changed_vectors = True

        if changed_vectors:
            self.vector_store.save()

        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        return {
            "added": added,
            "changed": changed,
            "removed": removed,
            "failed": failed,
            "failed_files": failed_files,
            "elapsed_ms": elapsed_ms,
        }
