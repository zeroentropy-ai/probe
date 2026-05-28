"""SQLite store for probe: files, chunks, and FTS5 full-text search."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class ProbeDB:
    """SQLite database for storing indexed files and chunks."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def initialize(self) -> None:
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                hash TEXT NOT NULL,
                file_type TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                file_type TEXT NOT NULL,
                char_start INTEGER NOT NULL,
                char_end INTEGER NOT NULL,
                token_count INTEGER NOT NULL,
                header_path TEXT,
                symbol_name TEXT,
                page_number INTEGER,
                line_start INTEGER,
                line_end INTEGER,
                FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE CASCADE
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                content,
                content='chunks',
                content_rowid='id',
                tokenize='porter unicode61'
            );

            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
            END;

            CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
            END;
        """)
        # Migration: add mtime_ns and size columns for refresh-before-search.
        # ALTER TABLE is idempotent via try/except on duplicate-column.
        for ddl in [
            "ALTER TABLE files ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE files ADD COLUMN size INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE chunks ADD COLUMN line_start INTEGER",
            "ALTER TABLE chunks ADD COLUMN line_end INTEGER",
        ]:
            try:
                self.conn.execute(ddl)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
        # Re-enable foreign keys after executescript (it issues an implicit COMMIT)
        self.conn.execute("PRAGMA foreign_keys=ON")

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

    def get_file_hash(self, path: str) -> str | None:
        row = self.conn.execute("SELECT hash FROM files WHERE path = ?", (path,)).fetchone()
        return row["hash"] if row else None

    def get_chunk_ids_for_file(self, path: str) -> list[int]:
        """Get all chunk IDs belonging to a file."""
        rows = self.conn.execute(
            "SELECT c.id FROM chunks c JOIN files f ON c.file_id = f.id WHERE f.path = ?",
            (path,),
        ).fetchall()
        return [row[0] for row in rows]

    def delete_file(self, path: str) -> None:
        self.conn.execute("DELETE FROM files WHERE path = ?", (path,))
        self.conn.commit()

    def list_files(self) -> list[dict]:
        rows = self.conn.execute("SELECT path, hash, file_type, indexed_at FROM files").fetchall()
        return [dict(row) for row in rows]

    def add_chunk(self, file_id, chunk_index, content, file_type, char_start, char_end,
                  token_count, header_path=None, symbol_name=None, page_number=None,
                  line_start=None, line_end=None) -> int:
        cursor = self.conn.execute(
            """INSERT INTO chunks
               (file_id, chunk_index, content, file_type, char_start, char_end,
                token_count, header_path, symbol_name, page_number, line_start, line_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (file_id, chunk_index, content, file_type, char_start, char_end,
             token_count, header_path, symbol_name, page_number, line_start, line_end),
        )
        return cursor.lastrowid

    def commit(self) -> None:
        """Commit the current transaction."""
        self.conn.commit()

    def get_chunk(self, chunk_id: int) -> dict | None:
        row = self.conn.execute(
            """SELECT c.*, f.path as file_path
               FROM chunks c JOIN files f ON c.file_id = f.id
               WHERE c.id = ?""",
            (chunk_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_all_chunks(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT c.*, f.path as file_path
               FROM chunks c JOIN files f ON c.file_id = f.id
               ORDER BY c.id"""
        ).fetchall()
        return [dict(row) for row in rows]

    def search_fts(self, query: str, top_k: int = 30) -> list[dict]:
        # Sanitize query: wrap each term in double quotes to escape FTS5 operators
        sanitized = " ".join(
            f'"{term}"' for term in query.split() if term.strip()
        )
        if not sanitized:
            return []
        try:
            rows = self.conn.execute(
                """SELECT chunks_fts.rowid as chunk_id, rank
                   FROM chunks_fts
                   WHERE chunks_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (sanitized, top_k),
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []

    def get_stats(self) -> dict:
        total_files = self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total_chunks = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        type_rows = self.conn.execute(
            "SELECT file_type, COUNT(*) as cnt FROM files GROUP BY file_type"
        ).fetchall()
        file_types = {row["file_type"]: row["cnt"] for row in type_rows}
        last_row = self.conn.execute(
            "SELECT indexed_at FROM files ORDER BY indexed_at DESC LIMIT 1"
        ).fetchone()
        last_indexed = last_row["indexed_at"] if last_row else None
        return {
            "total_files": total_files,
            "total_chunks": total_chunks,
            "file_types": file_types,
            "last_indexed": last_indexed,
        }

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

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
