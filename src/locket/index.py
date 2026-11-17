"""Derived SQLite read index for Locket (ARCHITECTURE.md §8, plan A).

The git repository of Markdown files is the source of truth. This index is
**derived data**: it lives under `.git/pmf/`, mirrors whatever `main` currently
holds, and can be thrown away and rebuilt from the repository at any time. Its
only job is to answer reads (list / filter / lookup) without parsing every
memory file on every call (the O(N) cost of walking the tree).

This module is pure SQLite + JSON. It never touches git and never touches the
network — callers hand it already-parsed `Memory` objects and blob shas.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .memory import Memory

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    blob       TEXT,
    path       TEXT,
    type       TEXT,
    about      TEXT,
    title      TEXT,
    importance REAL,
    valid_to   TEXT,
    meta_json  TEXT NOT NULL,
    body       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS memories_about ON memories(about);
CREATE INDEX IF NOT EXISTS memories_type  ON memories(type);
CREATE INDEX IF NOT EXISTS memories_blob  ON memories(blob);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_COLUMNS = ("id", "blob", "path", "type", "about", "title",
            "importance", "valid_to", "meta_json", "body")


class Index:
    """A rebuildable SQLite mirror of the memories in main's tree."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._db: sqlite3.Connection | None = None

    # ---- connection / schema (lazy) ---------------------------------------

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self.path), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.executescript(_SCHEMA)
            self._db.commit()
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # ---- bookkeeping ------------------------------------------------------

    @property
    def built_at(self) -> str | None:
        """The main commit this index currently reflects, or None if never built."""
        row = self.db.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
        return row["value"] if row else None

    def _set_built_at(self, at: str | None) -> None:
        if at is None:
            self.db.execute("DELETE FROM meta WHERE key = 'built_at'")
        else:
            self.db.execute(
                "INSERT INTO meta(key, value) VALUES('built_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (at,))

    # ---- row (de)serialisation --------------------------------------------

    @staticmethod
    def _row(blob: str, m: Memory) -> tuple:
        return (
            m.id, blob, m.path, m.type, m.about, m.title or None,
            m.importance, m.meta.get("valid_to"),
            json.dumps(m.meta, ensure_ascii=False), m.body,
        )

    @staticmethod
    def _memory(row: sqlite3.Row) -> Memory:
        return Memory(meta=json.loads(row["meta_json"]), body=row["body"], path=row["path"])

    def _upsert(self, rows: list[tuple]) -> None:
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _COLUMNS if c != "id")
        self.db.executemany(
            f"INSERT INTO memories ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}", rows)

    # ---- writes -----------------------------------------------------------

    def rebuild(self, entries: list[tuple[str, Memory]], at: str) -> None:
        """Replace every row with `entries` (each (blob_sha, memory)); set built_at."""
        with self.db:
            self.db.execute("DELETE FROM memories")
            self._upsert([self._row(blob, m) for blob, m in entries])
            self._set_built_at(at)

    def apply(self, upsert: list[tuple[str, Memory]], remove_ids: list[str], at: str) -> None:
        """Upsert the given rows (by id), delete `remove_ids`, then set built_at."""
        with self.db:
            if remove_ids:
                self.db.executemany("DELETE FROM memories WHERE id = ?",
                                    [(mid,) for mid in remove_ids])
            if upsert:
                self._upsert([self._row(blob, m) for blob, m in upsert])
            self._set_built_at(at)

    def clear(self) -> None:
        """Drop all rows and forget which commit we reflected."""
        with self.db:
            self.db.execute("DELETE FROM memories")
            self._set_built_at(None)

    # ---- reads ------------------------------------------------------------

    def all(self, *, about: str | None = None, type: str | None = None,
            include_inactive: bool = True) -> list[Memory]:
        clauses, params = [], []
        if about is not None:
            clauses.append("about = ?")
            params.append(about)
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.execute(f"SELECT meta_json, body, path FROM memories{where}", params).fetchall()
        result = [self._memory(r) for r in rows]
        if not include_inactive:
            result = [m for m in result if m.active]
        return result

    def by_blobs(self, shas: list[str]) -> dict[str, Memory]:
        if not shas:
            return {}
        found: dict[str, Memory] = {}
        # chunk to stay under SQLite's variable limit on very large inputs
        for i in range(0, len(shas), 500):
            chunk = shas[i:i + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = self.db.execute(
                f"SELECT blob, meta_json, body, path FROM memories WHERE blob IN ({placeholders})",
                chunk).fetchall()
            for r in rows:
                found[r["blob"]] = self._memory(r)
        return found

    def get_by_suffix(self, suffix: str, *, include_inactive: bool = True) -> list[Memory]:
        rows = self.db.execute(
            "SELECT meta_json, body, path FROM memories WHERE UPPER(id) LIKE ?",
            ("%" + suffix.upper(),)).fetchall()
        result = [self._memory(r) for r in rows]
        if not include_inactive:
            result = [m for m in result if m.active]
        return result
