"""Local, derived vector index for semantic recall (P6, cognitive layer).

One SQLite file per device (lives alongside the other derived data under `.git/pmf/`),
holding one embedding per memory keyed by its id and content-addressed blob sha. This is
**derived, rebuildable data — never the source of truth**: the git repo is authoritative,
and this index can be dropped and regenerated at any time (e.g. after switching embedders).

Scope of this module: vector storage and nearest-neighbour search only. It does NOT touch
git, the network, or compute embeddings itself — callers embed text with `embed.py` and
hand the vectors in. Similarity is brute-force cosine over all rows in Python, which is
plenty for a personal/on-device memory store; swap in an ANN backend (faiss, sqlite-vec,
pgvector) here if a single user's memory ever grows large enough to need it.

Vectors are stored as little-endian float32 BLOBs (`struct.pack(f"<{dim}f", ...)`). The
index records the dimension and the embedder name it was built with; a dimension mismatch
(`dim_matches`) signals the caller to rebuild.
"""
from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

from .embed import cosine


@dataclass
class VHit:
    id: str      # memory id
    blob: str    # blob sha the vector was computed from (content-addressed, for caching)
    score: float  # cosine similarity, clamped to >= 0


class VectorIndex:
    def __init__(self, path: Path | str, dim: int) -> None:
        self.path = Path(path)
        self.dim = int(dim)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            "id TEXT PRIMARY KEY, blob TEXT NOT NULL, vec BLOB NOT NULL)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        self._db.execute("CREATE INDEX IF NOT EXISTS vectors_blob ON vectors(blob)")
        self._db.commit()
        # Record our dimension the first time; keep any prior value so dim_matches can
        # detect a mismatch and let the caller decide to rebuild.
        if self._meta_get("dim") is None:
            self._meta_set("dim", str(self.dim))

    # ---- meta helpers ------------------------------------------------------

    def _meta_get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _meta_set(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._db.commit()

    @property
    def built_at(self) -> str | None:
        return self._meta_get("built_at")

    @property
    def embedder_name(self) -> str | None:
        return self._meta_get("embedder")

    @property
    def stored_dim(self) -> int | None:
        raw = self._meta_get("dim")
        return int(raw) if raw is not None else None

    def dim_matches(self, dim: int) -> bool:
        stored = self.stored_dim
        return stored is not None and stored == int(dim)

    # ---- vector (de)serialization -----------------------------------------

    def _pack(self, vector: list[float]) -> bytes:
        if len(vector) != self.dim:
            raise ValueError(f"向量维度 {len(vector)} != 索引维度 {self.dim}")
        return struct.pack(f"<{self.dim}f", *vector)

    def _unpack(self, blob: bytes) -> list[float]:
        return list(struct.unpack(f"<{self.dim}f", blob))

    # ---- reads -------------------------------------------------------------

    def has(self, blob: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM vectors WHERE blob=? LIMIT 1", (blob,)).fetchone()
        return row is not None

    def all_ids(self) -> set[str]:
        return {r[0] for r in self._db.execute("SELECT id FROM vectors")}

    def search(self, query_vec: list[float], k: int = 10,
               allowed_ids: set[str] | None = None) -> list[VHit]:
        # A query embedded at a different dimension can't be compared; stay safe.
        if not query_vec or len(query_vec) != self.dim:
            return []
        hits: list[VHit] = []
        for mid, blob, vec in self._db.execute("SELECT id, blob, vec FROM vectors"):
            if allowed_ids is not None and mid not in allowed_ids:
                continue
            score = cosine(query_vec, self._unpack(vec))
            if score > 0.0:
                hits.append(VHit(mid, blob, round(score, 6)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    # ---- writes ------------------------------------------------------------

    def upsert(self, id: str, blob: str, vector: list[float]) -> None:
        packed = self._pack(vector)
        self._db.execute(
            "INSERT INTO vectors(id, blob, vec) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET blob=excluded.blob, vec=excluded.vec",
            (id, blob, packed))
        self._db.commit()

    def remove(self, ids: list[str]) -> None:
        if not ids:
            return
        self._db.executemany("DELETE FROM vectors WHERE id=?", [(i,) for i in ids])
        self._db.commit()

    def rebuild(self, rows: list[tuple[str, str, list[float]]], at: str,
                embedder_name: str) -> None:
        with self._db:  # single transaction
            self._db.execute("DELETE FROM vectors")
            self._db.executemany(
                "INSERT INTO vectors(id, blob, vec) VALUES(?, ?, ?)",
                [(mid, blob, self._pack(vec)) for mid, blob, vec in rows])
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES('built_at', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (at,))
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES('embedder', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (embedder_name,))
            self._db.execute(
                "INSERT INTO meta(key, value) VALUES('dim', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(self.dim),))

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM vectors")
            self._db.execute("DELETE FROM meta WHERE key IN ('built_at', 'embedder')")

    def close(self) -> None:
        self._db.close()
