"""Tests for the SQLite store."""

from pathlib import Path

import pytest

from probe.store.database import ProbeDB


@pytest.fixture
def db(tmp_probe_dir: Path) -> ProbeDB:
    db = ProbeDB(tmp_probe_dir / "probe.db")
    db.initialize()
    return db


class TestFileOperations:
    def test_add_file(self, db: ProbeDB):
        db.add_file("docs/design.md", "abc123", "markdown")
        files = db.list_files()
        assert len(files) == 1
        assert files[0]["path"] == "docs/design.md"
        assert files[0]["hash"] == "abc123"
        assert files[0]["file_type"] == "markdown"

    def test_get_file_hash(self, db: ProbeDB):
        db.add_file("docs/design.md", "abc123", "markdown")
        assert db.get_file_hash("docs/design.md") == "abc123"
        assert db.get_file_hash("nonexistent.md") is None

    def test_delete_file(self, db: ProbeDB):
        db.add_file("docs/design.md", "abc123", "markdown")
        db.delete_file("docs/design.md")
        assert db.list_files() == []

    def test_delete_file_cascades_chunks(self, db: ProbeDB):
        file_id = db.add_file("docs/design.md", "abc123", "markdown")
        db.add_chunk(file_id=file_id, chunk_index=0, content="hello world",
                     file_type="markdown", char_start=0, char_end=11, token_count=2)
        assert len(db.get_all_chunks()) == 1
        db.delete_file("docs/design.md")
        assert len(db.get_all_chunks()) == 0


class TestChunkOperations:
    def test_add_and_get_chunks(self, db: ProbeDB):
        file_id = db.add_file("docs/design.md", "abc123", "markdown")
        chunk_id = db.add_chunk(file_id=file_id, chunk_index=0,
                                content="## Auth\nOAuth PKCE flow", file_type="markdown",
                                char_start=0, char_end=24, token_count=5,
                                header_path="Authentication > OAuth Flow",
                                line_start=10, line_end=11)
        chunks = db.get_all_chunks()
        assert len(chunks) == 1
        assert chunks[0]["content"] == "## Auth\nOAuth PKCE flow"
        assert chunks[0]["header_path"] == "Authentication > OAuth Flow"
        assert chunks[0]["id"] == chunk_id
        assert chunks[0]["line_start"] == 10
        assert chunks[0]["line_end"] == 11

    def test_get_chunk_by_id(self, db: ProbeDB):
        file_id = db.add_file("src/auth.py", "def456", "code")
        chunk_id = db.add_chunk(file_id=file_id, chunk_index=0,
                                content="class OAuthHandler:", file_type="code",
                                char_start=0, char_end=19, token_count=3,
                                symbol_name="OAuthHandler")
        chunk = db.get_chunk(chunk_id)
        assert chunk is not None
        assert chunk["symbol_name"] == "OAuthHandler"
        assert chunk["file_path"] == "src/auth.py"


class TestFTS:
    def test_fts_search(self, db: ProbeDB):
        file_id = db.add_file("docs/design.md", "abc123", "markdown")
        db.add_chunk(file_id=file_id, chunk_index=0,
                     content="OAuth PKCE authentication flow with Auth0",
                     file_type="markdown", char_start=0, char_end=42, token_count=7)
        db.add_chunk(file_id=file_id, chunk_index=1,
                     content="Rate limiting uses sliding window algorithm",
                     file_type="markdown", char_start=43, char_end=86, token_count=6)
        results = db.search_fts("authentication", top_k=10)
        assert len(results) == 1
        assert results[0]["chunk_id"] is not None

    def test_fts_returns_top_k(self, db: ProbeDB):
        file_id = db.add_file("docs/d.md", "h", "markdown")
        for i in range(20):
            db.add_chunk(file_id=file_id, chunk_index=i,
                         content=f"authentication method {i}",
                         file_type="markdown", char_start=i*30, char_end=(i+1)*30, token_count=3)
        results = db.search_fts("authentication", top_k=5)
        assert len(results) == 5


class TestStats:
    def test_get_stats(self, db: ProbeDB):
        file_id = db.add_file("docs/design.md", "abc123", "markdown")
        db.add_chunk(file_id=file_id, chunk_index=0, content="hello",
                     file_type="markdown", char_start=0, char_end=5, token_count=1)
        stats = db.get_stats()
        assert stats["total_files"] == 1
        assert stats["total_chunks"] == 1
        assert stats["file_types"]["markdown"] == 1


class TestMtimeAndSize:
    def test_files_table_has_mtime_ns_and_size_columns(self, tmp_probe_dir: Path):
        db = ProbeDB(tmp_probe_dir / "probe.db")
        db.initialize()
        cols = {row[1] for row in db.conn.execute("PRAGMA table_info(files)").fetchall()}
        assert "mtime_ns" in cols
        assert "size" in cols
        db.close()

    def test_initialize_is_idempotent(self, tmp_probe_dir: Path):
        """Running initialize() twice must not error (ALTER TABLE would fail on second call)."""
        db = ProbeDB(tmp_probe_dir / "probe.db")
        db.initialize()
        db.initialize()  # second call should be a no-op
        db.close()

    def test_chunks_table_has_line_range_columns(self, tmp_probe_dir: Path):
        db = ProbeDB(tmp_probe_dir / "probe.db")
        db.initialize()
        cols = {row[1] for row in db.conn.execute("PRAGMA table_info(chunks)").fetchall()}
        assert "line_start" in cols
        assert "line_end" in cols
        db.close()

    def test_add_file_accepts_mtime_and_size(self, tmp_probe_dir: Path):
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
