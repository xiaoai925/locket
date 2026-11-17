"""Keyword search: BM25 over words (Latin) and character bigrams (CJK).

This is strategy-layer code; the spec leaves ranking to implementations.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+|[㐀-䶿一-鿿豈-﫿]+")


def tokenize(text: str) -> list[str]:
    tokens = []
    for match in _TOKEN.finditer(text.lower()):
        run = match.group()
        if run.isascii() or len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def bm25(query: str, docs: list[str], k1: float = 1.2, b: float = 0.75) -> list[tuple[int, float, int]]:
    """Return (doc index, score, number of distinct query tokens matched) for matching docs."""
    terms = set(tokenize(query))
    if not terms or not docs:
        return []
    counts = [Counter(tokenize(d)) for d in docs]
    lengths = [sum(c.values()) for c in counts]
    avg = (sum(lengths) / len(lengths)) or 1.0
    df = {t: sum(1 for c in counts if t in c) for t in terms}
    n = len(docs)
    results = []
    for i, c in enumerate(counts):
        score, matched = 0.0, 0
        for t in terms:
            f = c.get(t)
            if not f:
                continue
            matched += 1
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * lengths[i] / avg))
        if matched:
            results.append((i, score, matched))
    return results
