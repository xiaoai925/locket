"""Pluggable text embedding for semantic recall (P6, cognitive layer).

The engine never hard-depends on a model: the default `HashingEmbedder` is pure stdlib,
deterministic and offline, so the vector pipeline runs anywhere — including on a phone in
Termux with no network. Higher-quality semantic backends (a local model, or a cloud API
such as Claude) plug in behind the same `Embedder` interface; when none is configured and
nothing is available, recall falls back to pure BM25.

Honest limitation: the hashing embedder captures surface token overlap, not deep meaning,
so it will not match "交通工具" to "特斯拉". It exists to make the whole path work offline
and testable; swap in a real on-device model for true semantic quality. The vector index
(vector.py) stores whatever dimension the active embedder produces, keyed by content hash,
and is rebuildable, so switching embedders just means a reindex.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol, runtime_checkable

from .search import tokenize


@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, text: str) -> list[float]: ...


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors (0 if either is empty/zero or dims differ)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot))  # inputs are L2-normalized, so dot == cosine


class HashingEmbedder:
    """Deterministic offline embedding: hash each token (and char bigrams for CJK) into a
    fixed-dim bag, tf-weighted, then L2-normalize. Pure stdlib, no model, no network.

    Semantically shallow (overlap-based) but a working, reproducible vector for the whole
    pipeline — and the only option that is guaranteed to run on-device offline."""
    name = "hashing-v1"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        toks = tokenize(text)  # words + CJK bigrams, already lowercased
        # add char trigrams for latin words to blur minor spelling differences
        extra = []
        for w in re.findall(r"[a-z0-9]{4,}", text.lower()):
            extra += [w[i:i + 3] for i in range(len(w) - 2)]
        return toks + extra

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        feats = self._features(text or "")
        if not feats:
            return vec
        for feat in feats:
            h = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "big") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0  # signed hashing reduces collisions
            vec[idx] += sign
        return _l2_normalize(vec)


class CallableEmbedder:
    """Adapter wrapping any `fn(text)->list[float]` as an Embedder: a local model,
    an ONNX/GGUF runner, or a cloud API client. Vectors are L2-normalized here so
    cosine() stays a plain dot product regardless of backend."""

    def __init__(self, fn, dim: int, name: str = "callable"):
        self._fn = fn
        self.dim = dim
        self.name = name

    def embed(self, text: str) -> list[float]:
        vec = list(self._fn(text or ""))
        if len(vec) != self.dim:
            raise ValueError(f"embedder {self.name} 返回维度 {len(vec)} != 声明的 {self.dim}")
        return _l2_normalize([float(x) for x in vec])


def default_embedder() -> Embedder:
    """The always-available offline embedder. Callers may inject a better one."""
    return HashingEmbedder()
