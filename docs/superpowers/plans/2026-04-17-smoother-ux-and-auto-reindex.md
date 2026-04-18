# Smoother UX & Auto Re-Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship probe v0.2.0 with (a) a `probe install`/`uninstall` command that registers probe as a user-scope MCP server in Claude Code via `claude mcp add`, and (b) refresh-before-search that incrementally re-indexes changed files before each CLI/MCP query.

**Architecture:** Refresh-before-search extends the existing incremental-index pipeline with a two-phase (stat → hash) algorithm gated by a TTL debounce, wired into both the CLI `search` command and the MCP `probe_search` tool. `probe install` shells out to `claude mcp add --scope user` rather than editing `~/.claude.json` directly, so Claude Code owns its config schema. Both features land together in 0.2.0 but are independent code paths.

**Tech Stack:** Python 3.10+, click (CLI), pytest + pytest-asyncio (tests), SQLite/FTS5 (existing), numpy (existing vector store), subprocess (shelling out to `claude`), threading (RefreshGate lock), the existing `zeroentropy`/`openai`/`cohere` provider abstraction.

**Spec:** `docs/superpowers/specs/2026-04-17-smoother-ux-and-auto-reindex-design.md`

**Task ordering rationale:** Bottom-up. DB schema first (Tasks 1), pipeline refactor (Task 2), then refresh primitives (Tasks 3–5), then integration into CLI and MCP (Tasks 6–7). The install command is independent so it slots in after (Tasks 8–12). Docs and release prep are last (Tasks 13–15).

---

## Task 1: Add `mtime_ns` and `size` columns to the `files` table

**Files:**
- Modify: `src/probe/store/database.py`
- Test: `tests/test_database.py`

The new refresh path needs cheap change-detection signals. We add two columns to the existing `files` table and apply them idempotently in `ProbeDB.initialize()`. Existing rows get defaults of 0; on first refresh they look "changed" and get backfilled with real values.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_files_table_has_mtime_ns_and_size_columns(tmp_probe_dir):
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    cols = {row[1] for row in db.conn.execute("PRAGMA table_info(files)").fetchall()}
    assert "mtime_ns" in cols
    assert "size" in cols
    db.close()


def test_initialize_is_idempotent(tmp_probe_dir):
    """Running initialize() twice must not error (ALTER TABLE would fail on second call)."""
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    db.initialize()  # second call should be a no-op
    db.close()


def test_add_file_accepts_mtime_and_size(tmp_probe_dir):
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    file_id = db.add_file("foo.md", "abc123", "markdown", mtime_ns=1700000000000000000, size=42)
    assert file_id > 0
    row = db.conn.execute(
        "SELECT mtime_ns, size FROM files WHERE id = ?", (file_id,)
    ).fetchone()
    assert row["mtime_ns"] == 1700000000000000000
    assert row["size"] == 42
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database.py::test_files_table_has_mtime_ns_and_size_columns tests/test_database.py::test_initialize_is_idempotent tests/test_database.py::test_add_file_accepts_mtime_and_size -v`

Expected: 3 failures — columns don't exist yet; `add_file` doesn't accept `mtime_ns`/`size`.

- [ ] **Step 3: Implement migration in `initialize()`**

Edit `src/probe/store/database.py`. After the `executescript` block in `initialize()` (after line 73, before the `PRAGMA foreign_keys=ON` at line 75), add:

```python
        # Migration: add mtime_ns and size columns for refresh-before-search.
        # ALTER TABLE is idempotent via try/except on duplicate-column.
        for ddl in [
            "ALTER TABLE files ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE files ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
```

- [ ] **Step 4: Update `add_file()` to accept the new columns**

Replace the current `add_file` method (around lines 77–84):

```python
    def add_file(self, path: str, hash: str, file_type: str,
                 mtime_ns: int = 0, size: int = 0) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = self.conn.execute(
            """INSERT INTO files (path, hash, file_type, indexed_at, mtime_ns, size)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (path, hash, file_type, now, mtime_ns, size),
        )
        self.conn.commit()
        return cursor.lastrowid
```

- [ ] **Step 5: Add helper to read and update mtime/size**

Append to `ProbeDB` (before `close()`):

```python
    def get_file_signature(self, path: str) -> tuple[str, int, int] | None:
        """Return (hash, mtime_ns, size) for a file, or None if not indexed."""
        row = self.conn.execute(
            "SELECT hash, mtime_ns, size FROM files WHERE path = ?", (path,),
        ).fetchone()
        if not row:
            return None
        return (row["hash"], row["mtime_ns"], row["size"])

    def update_file_signature(self, path: str, mtime_ns: int, size: int) -> None:
        """Update mtime_ns/size without touching hash or re-indexing."""
        self.conn.execute(
            "UPDATE files SET mtime_ns = ?, size = ? WHERE path = ?",
            (mtime_ns, size, path),
        )
        self.conn.commit()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_database.py -v`

Expected: All green, including the three new tests.

- [ ] **Step 7: Commit**

```bash
git add src/probe/store/database.py tests/test_database.py
git commit -m "feat(store): add mtime_ns and size columns to files table for refresh detection"
```

---

## Task 2: Extract `_index_file()` helper in `IndexPipeline` (prerequisite refactor)

**Files:**
- Modify: `src/probe/indexer/pipeline.py`
- Test: `tests/test_pipeline.py` (no changes; existing tests must continue to pass)

`IndexPipeline.index()` currently contains the full per-file work inline. To let `refresh_changed()` reuse the same logic in Task 5, we extract the per-file portion into a private `_index_file()` helper. Pure refactor — behavior unchanged. Existing pipeline tests are the regression harness.

- [ ] **Step 1: Verify existing pipeline tests pass before the refactor (establish baseline)**

Run: `pytest tests/test_pipeline.py -v`

Expected: 5 tests pass. If any fail, stop and fix before refactoring.

- [ ] **Step 2: Add `_index_file()` helper**

Edit `src/probe/indexer/pipeline.py`. Add this method inside `IndexPipeline` (place it directly after `__init__`):

```python
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
```

- [ ] **Step 3: Rewrite `index()` to use `_index_file()`**

Replace the current `index()` method body (currently ~90 lines) with the version below. The key change: inside the per-file loop, we call `_index_file()` instead of doing extract/chunk/persist inline.

```python
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
```

- [ ] **Step 4: Run tests to verify no regression**

Run: `pytest tests/test_pipeline.py -v`

Expected: All 5 existing tests still green.

- [ ] **Step 5: Commit**

```bash
git add src/probe/indexer/pipeline.py
git commit -m "refactor(indexer): extract _index_file() helper from IndexPipeline.index()"
```

---

## Task 3: Add `RefreshGate` class

**Files:**
- Create: `src/probe/indexer/refresh_gate.py`
- Test: `tests/test_refresh_gate.py`

A small debounce primitive. One instance per process. TTL configurable via `PROBE_REFRESH_TTL` env var: unset → 5.0s default, `0` → always refresh, `-1` → never refresh.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_refresh_gate.py`:

```python
"""Tests for the refresh-before-search debounce gate."""

import threading

import pytest

from probe.indexer.refresh_gate import RefreshGate


class TestRefreshGate:
    def test_default_ttl_allows_first_refresh(self):
        gate = RefreshGate(ttl_seconds=5.0)
        assert gate.should_refresh() is True

    def test_mark_blocks_within_ttl(self, monkeypatch):
        """After marking, subsequent should_refresh within TTL returns False."""
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])

        gate = RefreshGate(ttl_seconds=5.0)
        assert gate.should_refresh() is True
        gate.mark()
        now[0] = 102.0  # 2s later, within 5s TTL
        assert gate.should_refresh() is False

    def test_refresh_allowed_after_ttl(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])

        gate = RefreshGate(ttl_seconds=5.0)
        gate.mark()
        now[0] = 106.0  # 6s later, past TTL
        assert gate.should_refresh() is True

    def test_ttl_zero_always_allows(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: now[0])

        gate = RefreshGate(ttl_seconds=0.0)
        gate.mark()  # marking has no effect
        assert gate.should_refresh() is True
        assert gate.should_refresh() is True

    def test_ttl_negative_never_allows(self):
        gate = RefreshGate(ttl_seconds=-1.0)
        assert gate.should_refresh() is False

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("PROBE_REFRESH_TTL", "0")
        gate = RefreshGate.from_env()
        # With ttl=0, always allowed
        gate.mark()
        assert gate.should_refresh() is True

    def test_env_var_disabled(self, monkeypatch):
        monkeypatch.setenv("PROBE_REFRESH_TTL", "-1")
        gate = RefreshGate.from_env()
        assert gate.should_refresh() is False

    def test_env_var_absent_defaults_to_5s(self, monkeypatch):
        monkeypatch.delenv("PROBE_REFRESH_TTL", raising=False)
        gate = RefreshGate.from_env()
        assert gate._ttl == 5.0

    def test_concurrent_should_refresh_is_safe(self):
        """Ensure no race when multiple threads hit should_refresh simultaneously."""
        gate = RefreshGate(ttl_seconds=5.0)
        results: list[bool] = []
        def worker():
            results.append(gate.should_refresh())
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        # Without the lock this could throw; we just verify no exceptions and
        # that we got one result per thread.
        assert len(results) == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_refresh_gate.py -v`

Expected: All 9 fail — module doesn't exist.

- [ ] **Step 3: Implement `RefreshGate`**

Create `src/probe/indexer/refresh_gate.py`:

```python
"""Debounce gate for refresh-before-search."""

from __future__ import annotations

import os
import threading
import time


class RefreshGate:
    """Guards refresh-before-search from running more than once per TTL window.

    TTL semantics:
    - positive: refresh allowed once per `ttl_seconds` window
    - 0: always allowed (mark() is a no-op)
    - negative: never allowed (refresh fully disabled)
    """

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._ttl = ttl_seconds
        self._last_refresh = 0.0  # monotonic
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "RefreshGate":
        raw = os.environ.get("PROBE_REFRESH_TTL")
        if raw is None:
            return cls(ttl_seconds=5.0)
        try:
            return cls(ttl_seconds=float(raw))
        except ValueError:
            # Malformed value: fall back to default rather than crashing the search.
            return cls(ttl_seconds=5.0)

    def should_refresh(self) -> bool:
        if self._ttl < 0:
            return False
        if self._ttl == 0:
            return True
        with self._lock:
            return (time.monotonic() - self._last_refresh) >= self._ttl

    def mark(self) -> None:
        if self._ttl == 0:
            return
        with self._lock:
            self._last_refresh = time.monotonic()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_refresh_gate.py -v`

Expected: All 9 pass.

- [ ] **Step 5: Commit**

```bash
git add src/probe/indexer/refresh_gate.py tests/test_refresh_gate.py
git commit -m "feat(indexer): add RefreshGate debounce primitive for refresh-before-search"
```

---

## Task 4: Implement `refresh_changed()` — phase 1 (stat sweep + deletion)

**Files:**
- Modify: `src/probe/indexer/pipeline.py`
- Test: `tests/test_pipeline.py`

Walk files, compare mtime/size to DB, detect new/changed/deleted. Don't hash or re-embed yet — that's Task 5. This isolates the stat-sweep logic so we can test it independently.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py` (at the bottom of `TestIndexPipeline`):

```python
    def test_refresh_no_changes(self, pipeline, fixtures_dir, mock_embedding_provider):
        pipeline.index([fixtures_dir])
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([fixtures_dir])

        assert stats["added"] == 0
        assert stats["changed"] == 0
        assert stats["removed"] == 0
        assert "elapsed_ms" in stats
        # Phase 2 never runs for unchanged files, so no new embed calls.
        assert mock_embedding_provider.embed.call_count == 0

    def test_refresh_detects_deleted_file(self, pipeline, fixtures_dir, tmp_path):
        # Copy fixtures into a temp dir so we can delete from it safely
        import shutil
        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])
        assert len(pipeline.db.list_files()) > 0

        # Delete one file
        target = work / "notes.txt"
        target.unlink()

        stats = pipeline.refresh_changed([work])
        assert stats["removed"] == 1
        paths = {f["path"] for f in pipeline.db.list_files()}
        assert "notes.txt" not in paths
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py::TestIndexPipeline::test_refresh_no_changes tests/test_pipeline.py::TestIndexPipeline::test_refresh_detects_deleted_file -v`

Expected: `AttributeError: 'IndexPipeline' object has no attribute 'refresh_changed'`.

- [ ] **Step 3: Add phase-1-only `refresh_changed()` stub**

Append to `IndexPipeline` in `src/probe/indexer/pipeline.py`:

```python
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
            try:
                rel_path = str(file_path.relative_to(Path.cwd()))
            except ValueError:
                rel_path = str(file_path)
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

        # Phase 2: not yet implemented (Task 5).
        added = 0
        changed = 0
        # TEMP until Task 5: assume all candidates are "changed" with no real work done.
        # (We intentionally leave this zero so Task 5 can flip it on when hashing is added.)

        if deleted_chunk_ids:
            self.vector_store.save()

        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        return {"added": added, "changed": changed, "removed": removed, "elapsed_ms": elapsed_ms}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py::TestIndexPipeline::test_refresh_no_changes tests/test_pipeline.py::TestIndexPipeline::test_refresh_detects_deleted_file -v`

Expected: Both pass. (Note: `test_refresh_no_changes` passes because in the existing fixtures nothing has changed between `index()` and `refresh_changed()`, so candidates is empty even though phase 2 isn't implemented.)

- [ ] **Step 5: Commit**

```bash
git add src/probe/indexer/pipeline.py tests/test_pipeline.py
git commit -m "feat(indexer): add refresh_changed() phase 1 (stat sweep + deletion)"
```

---

## Task 5: Implement `refresh_changed()` — phase 2 (hash confirm + re-index)

**Files:**
- Modify: `src/probe/indexer/pipeline.py`
- Test: `tests/test_pipeline.py`

For each file flagged by phase 1, compute SHA-256. If hash matches DB, it was a metadata-only change — update mtime/size, skip embed. If hash differs (or it's a new file), re-index via `_index_file()`.

- [ ] **Step 1: Write the failing tests**

Append to `TestIndexPipeline` in `tests/test_pipeline.py`:

```python
    def test_refresh_new_file(self, pipeline, fixtures_dir, tmp_path,
                              mock_embedding_provider):
        import shutil
        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])

        # Add a new file
        (work / "new.md").write_text("# New\nSome content about fresh things.")
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([work])
        assert stats["added"] == 1
        assert stats["changed"] == 0
        paths = {f["path"] for f in pipeline.db.list_files()}
        assert "new.md" in paths
        assert mock_embedding_provider.embed.call_count >= 1

    def test_refresh_edited_file(self, pipeline, fixtures_dir, tmp_path,
                                 mock_embedding_provider):
        import shutil, time
        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])

        # Modify an existing file (content change)
        target = work / "README.md"
        time.sleep(0.01)  # ensure mtime advances on coarse filesystems
        target.write_text(target.read_text() + "\n\nNew paragraph about something.")
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([work])
        assert stats["changed"] == 1
        assert stats["added"] == 0
        assert mock_embedding_provider.embed.call_count >= 1

    def test_refresh_touched_file_not_reembedded(self, pipeline, fixtures_dir,
                                                  tmp_path, mock_embedding_provider):
        """mtime changes but content doesn't: hash confirms no real change, no embed."""
        import shutil, os, time
        work = tmp_path / "work"
        shutil.copytree(fixtures_dir, work)
        pipeline.index([work])

        target = work / "README.md"
        # Bump mtime without changing content
        new_time = time.time() + 10
        os.utime(target, (new_time, new_time))
        mock_embedding_provider.embed.reset_mock()

        stats = pipeline.refresh_changed([work])
        # Phase 1 flags it, phase 2 confirms via hash, updates mtime, no embed.
        assert stats["changed"] == 0
        assert stats["added"] == 0
        assert mock_embedding_provider.embed.call_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py::TestIndexPipeline::test_refresh_new_file tests/test_pipeline.py::TestIndexPipeline::test_refresh_edited_file tests/test_pipeline.py::TestIndexPipeline::test_refresh_touched_file_not_reembedded -v`

Expected: 3 failures — phase 2 isn't implemented, so `added`/`changed` are always 0 and new files aren't indexed.

- [ ] **Step 3: Replace the phase-2 stub with real logic**

In `src/probe/indexer/pipeline.py`, inside `refresh_changed()`, replace the block that currently reads:

```python
        # Phase 2: not yet implemented (Task 5).
        added = 0
        changed = 0
        # TEMP until Task 5: assume all candidates are "changed" with no real work done.
        # (We intentionally leave this zero so Task 5 can flip it on when hashing is added.)
```

with this:

```python
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
```

The final `if deleted_chunk_ids: self.vector_store.save()` block below needs to catch candidate_deleted_ids too. Update it to:

```python
        if deleted_chunk_ids or candidate_deleted_ids or new_chunk_texts:
            self.vector_store.save()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`

Expected: All pipeline tests pass, including the three new phase-2 tests.

- [ ] **Step 5: Commit**

```bash
git add src/probe/indexer/pipeline.py tests/test_pipeline.py
git commit -m "feat(indexer): implement refresh_changed() phase 2 (hash confirm + reindex)"
```

---

## Task 6: Wire refresh-before-search into CLI `search` command

**Files:**
- Modify: `src/probe/cli.py`
- Test: `tests/test_cli.py`

Before calling `engine.search`, instantiate a module-level `RefreshGate` from env and run `refresh_changed` if the gate allows it. Print a dim summary line if anything changed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` in the `TestCLI` class:

```python
    def test_search_calls_refresh_when_gate_allows(self, runner, monkeypatch, tmp_path):
        """When PROBE_REFRESH_TTL=0, every search invocation triggers refresh_changed."""
        import os
        from unittest.mock import MagicMock, patch

        os.environ["PROBE_REFRESH_TTL"] = "0"
        os.environ["ZEROENTROPY_API_KEY"] = "test"
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".probe").mkdir()

        # Mock the pieces so we don't hit disk/API, just observe refresh is called.
        with patch("probe.cli._build_providers") as mock_build, \
             patch("probe.indexer.pipeline.IndexPipeline.refresh_changed") as mock_refresh, \
             patch("probe.search.engine.ContextEngine.search") as mock_search, \
             patch("probe.store.database.ProbeDB.get_stats",
                   return_value={"total_files": 1, "total_chunks": 1, "file_types": {}, "last_indexed": None}):
            mock_build.return_value = (MagicMock(), None)
            mock_refresh.return_value = {"added": 0, "changed": 0, "removed": 0, "elapsed_ms": 10}
            mock_search.return_value = MagicMock(results=[], total_tokens=0, sources_searched=0, query="x")

            result = runner.invoke(main, ["search", "x"])
            assert mock_refresh.called, f"refresh_changed was not called. Output: {result.output}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli.py::TestCLI::test_search_calls_refresh_when_gate_allows -v`

Expected: Fails — `refresh_changed` is never called (not wired into `search` yet).

- [ ] **Step 3: Wire the gate into `search`**

In `src/probe/cli.py`, add at the top with the other imports:

```python
from probe.indexer.refresh_gate import RefreshGate
```

Then, inside the `search` command function, after `vector_store.load()` and before `embedding, reranker = _build_providers(config)`, add:

```python
    # Refresh-before-search: update index if files changed since last index.
    gate = RefreshGate.from_env()
    if gate.should_refresh():
        from probe.indexer.pipeline import IndexPipeline
        embedding_for_refresh, _ = _build_providers(config)
        pipeline = IndexPipeline(
            db=db, vector_store=vector_store,
            embedding_provider=embedding_for_refresh,
        )
        try:
            refresh_stats = pipeline.refresh_changed([Path.cwd()])
            gate.mark()
            total_changed = refresh_stats["added"] + refresh_stats["changed"] + refresh_stats["removed"]
            if total_changed > 0:
                console.print(
                    f"[dim]Refreshed: +{refresh_stats['added']} "
                    f"±{refresh_stats['changed']} -{refresh_stats['removed']} "
                    f"({refresh_stats['elapsed_ms']}ms)[/dim]"
                )
        except Exception as e:
            console.print(
                f"[yellow]Warning: refresh failed ({e}); using stale index.[/yellow]"
            )
```

Note: the later `embedding, reranker = _build_providers(config)` call stays as-is; we build providers twice here but the construction is cheap and the duplication makes the refresh block self-contained. Simplify later if profiling shows it matters.

- [ ] **Step 4: Run the new test and all CLI tests**

Run: `pytest tests/test_cli.py -v`

Expected: All pass including the new `test_search_calls_refresh_when_gate_allows`.

- [ ] **Step 5: Commit**

```bash
git add src/probe/cli.py tests/test_cli.py
git commit -m "feat(cli): wire refresh-before-search into search command"
```

---

## Task 7: Wire refresh-before-search into MCP `probe_search` + add `refreshed` field

**Files:**
- Modify: `src/probe/mcp/server.py`
- Test: `tests/test_mcp.py`

Replace the existing "auto-index if empty" block with a unified `refresh_changed` call. Return a `refreshed` field in every response. Handle refresh errors by embedding an `error` string in the field; don't block search.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np


def test_probe_search_returns_refreshed_field(tmp_path, monkeypatch):
    """probe_search JSON response should always include a refreshed field."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".probe").mkdir()
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
    monkeypatch.setenv("PROBE_REFRESH_TTL", "0")

    server = create_mcp_server()
    # Find the registered probe_search tool
    tool = server._tool_manager._tools["probe_search"]  # FastMCP internal

    fake_response = MagicMock()
    fake_response.query = "x"
    fake_response.results = []
    fake_response.total_tokens = 0
    fake_response.sources_searched = 0

    with patch("probe.search.engine.ContextEngine.search", return_value=fake_response), \
         patch("probe.indexer.pipeline.IndexPipeline.refresh_changed",
               return_value={"added": 0, "changed": 2, "removed": 0, "elapsed_ms": 50}), \
         patch("probe.mcp.server._build_providers",
               return_value=(MagicMock(dimensions=4,
                                       embed=MagicMock(return_value=np.zeros((1,4), dtype=np.float32))), None)):
        result_json = tool.fn(query="x")

    data = json.loads(result_json)
    assert "refreshed" in data
    assert data["refreshed"] == {"added": 0, "changed": 2, "removed": 0, "elapsed_ms": 50}


def test_probe_search_refresh_error_does_not_block_search(tmp_path, monkeypatch):
    """A failing refresh surfaces an error field but search still runs."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".probe").mkdir()
    monkeypatch.setenv("ZEROENTROPY_API_KEY", "test")
    monkeypatch.setenv("PROBE_REFRESH_TTL", "0")

    server = create_mcp_server()
    tool = server._tool_manager._tools["probe_search"]

    fake_response = MagicMock()
    fake_response.query = "x"
    fake_response.results = []
    fake_response.total_tokens = 0
    fake_response.sources_searched = 0

    with patch("probe.search.engine.ContextEngine.search", return_value=fake_response), \
         patch("probe.indexer.pipeline.IndexPipeline.refresh_changed",
               side_effect=RuntimeError("rate limited")), \
         patch("probe.mcp.server._build_providers",
               return_value=(MagicMock(dimensions=4,
                                       embed=MagicMock(return_value=np.zeros((1,4), dtype=np.float32))), None)):
        result_json = tool.fn(query="x")

    data = json.loads(result_json)
    assert "refreshed" in data
    assert "error" in data["refreshed"]
    assert "rate limited" in data["refreshed"]["error"]
    # Search still ran
    assert data["query"] == "x"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp.py -v`

Expected: Two failures — `refreshed` field is not present in the response.

- [ ] **Step 3: Update `probe_search` in `src/probe/mcp/server.py`**

Replace the body of `probe_search` (currently lines 131–172, starting at `@server.tool()\n    def probe_search(`) with:

```python
    @server.tool()
    def probe_search(
        query: str, top_k: int = 10, max_tokens: int = 4096,
        file_types: list[str] | None = None,
    ) -> str:
        """Search project knowledge (docs, specs, code) and return curated, reranked context.
        Use this when you need to understand how something works, find requirements,
        or locate relevant code and documentation."""
        from probe.indexer.pipeline import IndexPipeline
        from probe.indexer.refresh_gate import RefreshGate

        config = state.config
        vector_store = VectorStore(
            state.probe_dir / "vectors.npy",
            dimensions=config.embedding_dimensions,
        )

        # Unified refresh (replaces the old "auto-index if empty" path — when the
        # DB is empty, every file is "new" so phase 2 indexes the whole project).
        refreshed_info: dict = {"added": 0, "changed": 0, "removed": 0, "elapsed_ms": 0}
        gate = RefreshGate.from_env()
        if gate.should_refresh():
            try:
                embedding_for_refresh, _ = _build_providers(config)
                pipeline = IndexPipeline(
                    db=state.db, vector_store=vector_store,
                    embedding_provider=embedding_for_refresh,
                )
                refreshed_info = pipeline.refresh_changed([Path.cwd()])
                gate.mark()
                if refreshed_info["added"] + refreshed_info["changed"] + refreshed_info["removed"] > 0:
                    state.invalidate()
            except Exception as e:
                refreshed_info = {
                    "added": 0, "changed": 0, "removed": 0, "elapsed_ms": 0,
                    "error": str(e),
                }

        engine = state.get_engine()
        response = engine.search(
            query=query, top_k=top_k, max_tokens=max_tokens, file_types=file_types,
        )
        return json.dumps({
            "query": response.query,
            "results": [
                {"score": r.score, "file": r.file, "type": r.file_type,
                 "header_path": r.header_path, "symbol": r.symbol_name,
                 "page": r.page_number, "content": r.content,
                 "char_range": list(r.char_range)}
                for r in response.results
            ],
            "total_tokens": response.total_tokens,
            "sources_searched": response.sources_searched,
            "refreshed": refreshed_info,
        }, indent=2)
```

- [ ] **Step 4: Run MCP tests**

Run: `pytest tests/test_mcp.py -v`

Expected: All 4 tests pass (2 original + 2 new).

- [ ] **Step 5: Update the server instructions to mention the refresh behavior**

In `src/probe/mcp/server.py`, update the `MCP_INSTRUCTIONS` constant. Replace the existing "IMPORTANT" paragraph:

```python
IMPORTANT: On first use in a project, call probe_status first. If it shows 0 indexed files, \
call probe_index to build the search index before searching. This only needs to happen once \
per project.
```

with:

```python
IMPORTANT: probe auto-indexes on first search and incrementally refreshes on every \
subsequent search (within a debounce window), so you normally do not need to call \
probe_index manually. Every probe_search response includes a `refreshed` field with \
counts of files that were newly indexed, changed, or removed.
```

- [ ] **Step 6: Run all tests**

Run: `pytest -v`

Expected: All green.

- [ ] **Step 7: Commit**

```bash
git add src/probe/mcp/server.py tests/test_mcp.py
git commit -m "feat(mcp): wire refresh-before-search into probe_search and add refreshed field"
```

---

## Task 8: Add `probe install` skeleton + claude-CLI detection

**Files:**
- Modify: `src/probe/cli.py`
- Test: `tests/test_cli.py`

Register the new `install` subcommand. First step of the install flow: detect whether the `claude` CLI is on PATH.

- [ ] **Step 1: Write the failing test**

Append to `TestCLI` in `tests/test_cli.py`:

```python
    def test_install_exits_when_claude_not_on_path(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None if name == "claude" else "/usr/bin/" + name)
        result = runner.invoke(main, ["install"])
        assert result.exit_code == 1
        assert "Claude Code CLI not found" in result.output

    def test_install_command_exists(self, runner):
        result = runner.invoke(main, ["install", "--help"])
        assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py::TestCLI::test_install_exits_when_claude_not_on_path tests/test_cli.py::TestCLI::test_install_command_exists -v`

Expected: Both fail — `install` subcommand doesn't exist.

- [ ] **Step 3: Add the `install` subcommand with just the claude-detection step**

In `src/probe/cli.py`, add at the top imports:

```python
import shutil
import subprocess
```

Then add this new command (place it below the existing `mcp` command, near the end of the file):

```python
@main.command()
@click.option("--api-key", default=None, help="ZeroEntropy API key (skip prompt).")
@click.option("--no-embed-key", is_flag=True,
              help="Register without embedding API key (rely on shell env).")
@click.option("--force", is_flag=True, help="Skip already-installed confirmation.")
def install(api_key, no_embed_key, force):
    """Register probe as a user-scope MCP server in Claude Code."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]Claude Code CLI not found.[/red] "
            "Install it from the official Claude Code documentation, then rerun `probe install`."
        )
        sys.exit(1)

    console.print("[bold]probe install — coming in the next tasks...[/bold]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py::TestCLI::test_install_exits_when_claude_not_on_path tests/test_cli.py::TestCLI::test_install_command_exists -v`

Expected: Both pass.

- [ ] **Step 5: Commit**

```bash
git add src/probe/cli.py tests/test_cli.py
git commit -m "feat(cli): add probe install command skeleton with claude-CLI detection"
```

---

## Task 9: `probe install` — API key resolution

**Files:**
- Modify: `src/probe/cli.py`
- Test: `tests/test_cli.py`

Three paths: `--api-key` flag, `ZEROENTROPY_API_KEY` env confirm, interactive prompt. Re-prompt up to 3 times on empty input.

- [ ] **Step 1: Write the failing tests**

Append to `TestCLI`:

```python
    def test_install_uses_api_key_flag(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name in ("claude", "probe") else None)
        # Stub claude-mcp-get to "not installed" (exit 1) and claude-mcp-add to success.
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0
            r.stdout = b""
            r.stderr = b""
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--api-key", "sk-test-123"])
        # No key prompt appears in output (non-interactive via flag)
        assert "Enter your ZeroEntropy API key" not in result.output

    def test_install_uses_env_key_by_default(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name in ("claude", "probe") else None)
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "env-key-xyz")
        captured = {}
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0
            r.stdout = b""
            r.stderr = b""
            if "add" in cmd:
                captured["cmd"] = cmd
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # Hit enter at the confirm prompt → default yes
        result = runner.invoke(main, ["install"], input="\n")
        assert result.exit_code == 0
        assert "Use $ZEROENTROPY_API_KEY from environment?" in result.output
        # Verify the env key ended up in the add args
        assert "ZEROENTROPY_API_KEY=env-key-xyz" in " ".join(captured["cmd"])

    def test_install_rejects_empty_key_after_retries(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name in ("claude", "probe") else None)
        monkeypatch.delenv("ZEROENTROPY_API_KEY", raising=False)
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R()
            r.returncode = 1
            r.stdout = b""
            r.stderr = b""
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # Give empty input 3 times (4 newlines = 4 empty tries, hit the cap)
        result = runner.invoke(main, ["install"], input="\n\n\n\n")
        assert result.exit_code == 1
        assert "API key required" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k "install_uses_api_key_flag or install_uses_env_key or install_rejects_empty_key" -v`

Expected: All 3 fail — key resolution logic isn't implemented.

- [ ] **Step 3: Implement key resolution**

Replace the body of the `install` command in `src/probe/cli.py` with:

```python
def install(api_key, no_embed_key, force):
    """Register probe as a user-scope MCP server in Claude Code."""
    claude_bin = shutil.which("claude")
    if not claude_bin:
        console.print(
            "[red]Claude Code CLI not found.[/red] "
            "Install it from the official Claude Code documentation, then rerun `probe install`."
        )
        sys.exit(1)

    # Check if already installed
    get_result = subprocess.run(
        [claude_bin, "mcp", "get", "probe", "--scope", "user"],
        capture_output=True,
    )
    if get_result.returncode == 0:
        if not force:
            if not click.confirm("probe is already registered. Reinstall?", default=False):
                console.print("No changes made.")
                return
        subprocess.run(
            [claude_bin, "mcp", "remove", "probe", "--scope", "user"],
            capture_output=True,
        )

    # Resolve API key
    resolved_key: str | None = None
    if not no_embed_key:
        if api_key:
            resolved_key = api_key
        else:
            env_key = os.environ.get("ZEROENTROPY_API_KEY")
            if env_key and click.confirm(
                "Use $ZEROENTROPY_API_KEY from environment?", default=True,
            ):
                resolved_key = env_key
            else:
                for _ in range(3):
                    entered = click.prompt(
                        "Enter your ZeroEntropy API key",
                        hide_input=True, default="", show_default=False,
                    )
                    if entered.strip():
                        resolved_key = entered.strip()
                        break
                else:
                    console.print("[red]API key required.[/red]")
                    sys.exit(1)

    # Resolve probe argv
    probe_bin = shutil.which("probe")
    if probe_bin:
        probe_argv = [probe_bin, "mcp"]
    else:
        probe_argv = [sys.executable, "-m", "probe.cli", "mcp"]
        console.print(
            f"[yellow]Note: probe binary not on PATH; using {sys.executable} -m probe.cli. "
            "If you move this Python env, rerun `probe install`.[/yellow]"
        )

    # Build and run claude mcp add
    add_cmd = [claude_bin, "mcp", "add", "--scope", "user", "--transport", "stdio"]
    if resolved_key:
        add_cmd += ["-e", f"ZEROENTROPY_API_KEY={resolved_key}"]
    add_cmd += ["probe", "--", *probe_argv]

    add_result = subprocess.run(add_cmd, capture_output=True)
    if add_result.returncode != 0:
        console.print(f"[red]claude mcp add failed:[/red]\n{add_result.stderr.decode()}")
        sys.exit(1)

    console.print(
        "[green]✓ probe installed at user scope.[/green]\n"
        "  Open any project in Claude Code and ask a question — probe will auto-index on first search.\n"
        "  To uninstall: probe uninstall"
    )
```

- [ ] **Step 4: Run the three new tests**

Run: `pytest tests/test_cli.py -k "install_uses_api_key_flag or install_uses_env_key or install_rejects_empty_key" -v`

Expected: All 3 pass.

- [ ] **Step 5: Commit**

```bash
git add src/probe/cli.py tests/test_cli.py
git commit -m "feat(cli): probe install — API key resolution (flag, env, prompt)"
```

---

## Task 10: `probe install` — `--no-embed-key` flag + already-installed handling

**Files:**
- Modify: `src/probe/cli.py` (small tweaks; most logic already there from Task 9)
- Test: `tests/test_cli.py`

The key resolution code from Task 9 already respects `--no-embed-key` (we only resolve a key if `not no_embed_key`). And we already check "already installed". This task is *just* adding the tests to pin down that behavior and catch regressions.

- [ ] **Step 1: Write the failing tests**

Append to `TestCLI`:

```python
    def test_install_no_embed_key_omits_env(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name in ("claude", "probe") else None)
        captured = {}
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R()
            r.returncode = 1 if "get" in cmd else 0
            r.stdout = b""; r.stderr = b""
            if "add" in cmd: captured["cmd"] = cmd
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["install", "--no-embed-key"])
        assert result.exit_code == 0
        joined = " ".join(captured["cmd"])
        assert "ZEROENTROPY_API_KEY=" not in joined
        assert "-e" not in captured["cmd"]

    def test_install_already_registered_cancels_without_force(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name in ("claude", "probe") else None)
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R()
            # "get" succeeds → already registered
            r.returncode = 0 if "get" in cmd else 0
            r.stdout = b""; r.stderr = b""
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # Hit enter → default "no"
        result = runner.invoke(main, ["install"], input="\n")
        assert result.exit_code == 0
        assert "already registered" in result.output
        assert "No changes made" in result.output

    def test_install_force_skips_confirmation(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name in ("claude", "probe") else None)
        monkeypatch.setenv("ZEROENTROPY_API_KEY", "k")
        seen = []
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R()
            r.returncode = 0  # "get" says installed; "remove" and "add" both succeed
            r.stdout = b""; r.stderr = b""
            seen.append(cmd[:3])
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        # No stdin — would fail if confirm prompted
        result = runner.invoke(main, ["install", "--force"], input="\n")
        assert result.exit_code == 0
        # We should have called get, remove, add
        assert any("remove" in cmd for cmd in seen)
        assert any("add" in cmd for cmd in seen)
```

- [ ] **Step 2: Run tests to verify they pass (key resolution code from Task 9 already handles these cases)**

Run: `pytest tests/test_cli.py -k "install_no_embed_key or install_already_registered or install_force" -v`

Expected: All 3 pass without code changes — the behavior was already built in Task 9. If any fail, patch the bug before committing.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli.py
git commit -m "test(cli): cover --no-embed-key, already-installed, and --force paths"
```

---

## Task 11: `probe uninstall` command

**Files:**
- Modify: `src/probe/cli.py`
- Test: `tests/test_cli.py`

Counterpart to `install`. `claude mcp remove probe --scope user`; with `--purge`, also delete `.probe/` in cwd.

- [ ] **Step 1: Write the failing tests**

Append to `TestCLI`:

```python
    def test_uninstall_calls_claude_mcp_remove(self, runner, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name == "claude" else None)
        seen = []
        def fake_run(cmd, *a, **kw):
            class R: pass
            r = R(); r.returncode = 0; r.stdout = b""; r.stderr = b""
            seen.append(cmd)
            return r
        monkeypatch.setattr("subprocess.run", fake_run)

        result = runner.invoke(main, ["uninstall"])
        assert result.exit_code == 0
        assert any("remove" in cmd for cmd in seen)

    def test_uninstall_purge_deletes_dot_probe(self, runner, monkeypatch, tmp_path):
        monkeypatch.setattr("shutil.which", lambda name: "/fake/" + name if name == "claude" else None)
        monkeypatch.setattr("subprocess.run", lambda *a, **k: type("R", (), {"returncode": 0, "stdout": b"", "stderr": b""})())
        monkeypatch.chdir(tmp_path)
        probe_dir = tmp_path / ".probe"
        probe_dir.mkdir()
        (probe_dir / "probe.db").write_text("dummy")

        result = runner.invoke(main, ["uninstall", "--purge"])
        assert result.exit_code == 0
        assert not probe_dir.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -k "uninstall" -v`

Expected: Failures — `uninstall` subcommand doesn't exist.

- [ ] **Step 3: Add the `uninstall` command**

In `src/probe/cli.py`, after the `install` command, add:

```python
@main.command()
@click.option("--purge", is_flag=True, help="Also delete .probe/ from cwd.")
def uninstall(purge):
    """Unregister probe from Claude Code."""
    claude_bin = shutil.which("claude")
    if claude_bin:
        subprocess.run(
            [claude_bin, "mcp", "remove", "probe", "--scope", "user"],
            capture_output=True,
        )
        # Ignore errors: "not found" is fine.

    if purge:
        probe_dir = Path.cwd() / ".probe"
        if probe_dir.exists():
            shutil.rmtree(probe_dir, ignore_errors=True)
            console.print(f"[dim]Deleted {probe_dir}[/dim]")

    console.print("[green]✓ probe uninstalled.[/green]")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -k "uninstall" -v`

Expected: Both pass.

- [ ] **Step 5: Commit**

```bash
git add src/probe/cli.py tests/test_cli.py
git commit -m "feat(cli): add probe uninstall command with --purge"
```

---

## Task 12: Update README for the new install flow

**Files:**
- Modify: `README.md`

Replace the manual `.mcp.json` snippet with the one-command flow. Keep manual JSON as a fallback for CI/advanced users.

- [ ] **Step 1: Update the "Quick Start" section**

In `README.md`, find the Quick Start section (currently lines 17–31). Replace:

```markdown
## Quick Start

```bash
# 1. Get a free API key at https://dashboard.zeroentropy.dev
# 2. Install
pip install probe-search

# 3. Set your API key
export ZEROENTROPY_API_KEY="ze_xxx"

# 4. Index and search
probe index .
probe search "how does authentication work"
```

Or run without installing:

```bash
uvx probe-search search "how does authentication work"
```
```

with:

```markdown
## Quick Start

```bash
# 1. Get a free API key at https://dashboard.zeroentropy.dev
# 2. Install
pip install probe-search

# 3. Register probe with Claude Code (one-time, machine-wide)
probe install

# Now open any project in Claude Code and ask a question —
# probe will auto-index on first search and refresh on subsequent ones.
```

For CLI-only use:

```bash
export ZEROENTROPY_API_KEY="ze_xxx"
probe index .
probe search "how does authentication work"
```

The index auto-refreshes before each search; set `PROBE_REFRESH_TTL=0` to
force refresh every time, or `-1` to disable refresh.
```

- [ ] **Step 2: Update the "MCP Server Setup" section**

Change the heading and opening from:

```markdown
## MCP Server Setup (Claude Code, Cursor)

Add a `.mcp.json` file to your project root:
```

to:

```markdown
## MCP Server Setup (Claude Code, Cursor)

**Claude Code users**: `probe install` (see Quick Start) does this automatically.

**For Cursor or advanced use**: add a `.mcp.json` file to your project root:
```

(Keep the existing JSON block unchanged below it.)

- [ ] **Step 3: Add install/uninstall rows to the CLI reference table**

In the "CLI Reference" section, add these rows to the table (alphabetically near the top):

```markdown
| `probe install` | Register probe as a user-scope MCP server in Claude Code |
| `probe uninstall [--purge]` | Unregister probe; `--purge` also deletes `.probe/` in cwd |
```

- [ ] **Step 4: Update the "What's NOT in v1" section**

Replace the first bullet:

```markdown
- File system watcher for auto-reindexing on changes
```

with:

```markdown
- Real-time filesystem watcher (refresh-before-search handles typical edit volumes fine)
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: update README for probe install flow and refresh-before-search"
```

---

## Task 13: Version bump + CHANGELOG

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/probe/__init__.py`
- Create: `CHANGELOG.md`

- [ ] **Step 1: Bump version in `pyproject.toml`**

In `pyproject.toml`, change:

```toml
version = "0.1.0"
```

to:

```toml
version = "0.2.0"
```

- [ ] **Step 2: Bump version in `src/probe/__init__.py`**

Change:

```python
__version__ = "0.1.0"
```

to:

```python
__version__ = "0.2.0"
```

- [ ] **Step 3: Create `CHANGELOG.md`**

Create `CHANGELOG.md` at the repo root:

```markdown
# Changelog

All notable changes to probe are documented here.

## 0.2.0 — 2026-04-TBD

### Added
- `probe install` / `probe uninstall` for one-command Claude Code integration via `claude mcp add --scope user`.
- Refresh-before-search: the index incrementally updates when files change, driven by a two-phase (stat → hash) algorithm with a TTL debounce (`PROBE_REFRESH_TTL`, default 5s).
- `refreshed` field on every `probe_search` MCP response with counts of added/changed/removed files.

### Changed
- MCP `probe_search` no longer needs a separate `probe_index` call — auto-index is subsumed into the unified refresh path.
- `files` table schema gains `mtime_ns` and `size` columns (migration is automatic and backwards-compatible).

### Removed
- The "File system watcher" roadmap item (refresh-before-search covers the same UX need without a daemon).
```

- [ ] **Step 4: Run the version test to confirm the bump**

Run: `pytest tests/test_cli.py::TestCLI::test_version -v`

Expected: Fails — test asserts `"0.1.0" in result.output`. This is the test that needs updating.

- [ ] **Step 5: Update the version test**

In `tests/test_cli.py`, find `test_version` and change:

```python
assert "0.1.0" in result.output
```

to:

```python
assert "0.2.0" in result.output
```

Run again: `pytest tests/test_cli.py::TestCLI::test_version -v` — expected to pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/probe/__init__.py CHANGELOG.md tests/test_cli.py
git commit -m "chore: bump version to 0.2.0 and add CHANGELOG"
```

---

## Task 14: Run full test suite and lint

**Files:** (none)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -v`

Expected: All tests pass. Note total count: baseline was roughly 40 tests; we add ~20 new ones → ~60 passing.

- [ ] **Step 2: Run ruff**

Run: `ruff check src/ tests/`

Expected: No errors. If any, fix inline and re-run.

- [ ] **Step 3: If anything's off, fix and commit before moving on**

```bash
git add -A
git commit -m "fix: address lint / test issues from full-suite run"
```

(Skip the commit if nothing changed.)

---

## Task 15: Manual end-to-end validation

**Files:** (none — manual steps)

Validate against the toy project at `/Users/dilawar/tmp/probe-toy`.

- [ ] **Step 1: Reinstall probe from the working-tree**

```bash
uv tool install --reinstall /Users/dilawar/github.com/dilawarm/probe
export PATH="/Users/dilawar/.local/bin:$PATH"
probe --version  # should print 0.2.0
```

- [ ] **Step 2: Run `probe install` with env var present**

```bash
export ZEROENTROPY_API_KEY="ze_dJhRwyBuhyPTUFkt"
probe install
# Accept the default (use env var)
```

Confirm: `claude mcp list` shows `probe` under user scope.

- [ ] **Step 3: Open a new Claude Code session in the toy project and observe auto-index**

```bash
cd /Users/dilawar/tmp/probe-toy
rm -rf .probe  # force a clean first-search
claude
```

Ask: "How does authentication work in this project?"

Expect: Claude invokes `probe_search`; the response includes a non-zero `refreshed.added` (because the DB was empty) and returns hits from both `docs/design/auth.md` and `src/auth/oauth.py`.

- [ ] **Step 4: Edit a file and observe refresh**

With the same Claude session still open (or a new one), in another terminal:

```bash
echo "\n## New section about SSO" >> /Users/dilawar/tmp/probe-toy/docs/design/auth.md
```

Ask Claude a follow-up: "What does the auth doc say about SSO?"

Expect: `probe_search` response has `refreshed: {"changed": 1, ...}` and results include the new SSO section.

- [ ] **Step 5: `probe uninstall`**

```bash
probe uninstall
claude mcp list | grep probe || echo "probe is gone"
```

Expect: probe is no longer listed.

- [ ] **Step 6: Final commit of any validation-script helpers (if you added any)**

Usually nothing to commit at this step. Ship it.

---

## Self-Review (performed by plan author)

**Spec coverage:**

- Feature A §2.1 command shape → Tasks 8, 9, 10, 11 ✓
- Feature A §2.2 algorithm (all 6 steps) → Tasks 8, 9 ✓
- Feature A §2.3 shell-out rationale → reflected in Task 9 implementation comments
- Feature A §2.4 error handling table → Tasks 8 (claude missing), 9 (API key retry) ✓
- Feature A §2.5 uninstall → Task 11 ✓
- Feature B §3.1 contract → Task 4 (skeleton) + Task 5 (fleshed out)
- Feature B §3.2 two-phase algorithm → Tasks 4 (phase 1) + 5 (phase 2)
- Feature B §3.3 DB migration → Task 1
- Feature B §3.4 RefreshGate + TTL env var → Task 3
- Feature B §3.5 integration points (CLI + MCP) → Tasks 6 + 7
- Feature B §3.6 failure modes → Task 7 includes the error-path test
- §4.1 unit tests → embedded in Tasks 1, 3, 4, 5, 6, 7, 8, 9, 10, 11
- §4.2 manual validation → Task 15
- §5 docs → Task 12
- §7 rollout → Task 13

**Placeholder scan:** Only `2026-04-TBD` in the CHANGELOG (intentional — release date unknown). All code steps contain full code; no "TODO" or "similar to". Task 4 contains a temporary marker (`# TEMP until Task 5`) in the stub that gets replaced in Task 5 Step 3 — called out explicitly.

**Type consistency:** `refreshed` dict keys (`added`, `changed`, `removed`, `elapsed_ms`, optional `error`) are consistent across Tasks 4, 5, 6, 7. `refresh_changed()` signature is stable. `RefreshGate.from_env()` / `should_refresh()` / `mark()` used identically across Tasks 6 and 7.

**Ambiguity:** The Task 4 "note" explains why `test_refresh_no_changes` passes even with phase 2 unimplemented — readers won't assume phase 2 exists early.
