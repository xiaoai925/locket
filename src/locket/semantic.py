"""Semantic conflict detection (spec §6) — a strategy-layer policy, not normative.

The spec fixes only how a conflict is *represented*; how it is *detected* is left to
implementations. Locket's choice: heuristic-first, LLM pluggable and off by default.

This module is pure logic. It takes one memory plus candidate memories (already scored
for similarity by the caller) and returns Findings. It never touches git, the store, or
the network — the store gathers candidates (e.g. via recall) and calls `judge()`.

The heuristic cannot truly tell "contradiction" from "agreement"; it only surfaces
candidates worth a human's confirmation (high overlap = likely duplicate; medium-high
overlap on the same topic/type with differing text = possible contradiction). Real
adjudication is delegated to a pluggable LLM via `LLMDetector`. Default is `NullDetector`
(off), so nothing is flagged unless an implementation opts in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .memory import Memory
from .search import tokenize


@dataclass
class Finding:
    other_id: str        # the existing memory this one conflicts with / duplicates
    kind: str            # "duplicate" | "contradiction"
    question: str        # a Chinese question for the owner to resolve
    confidence: float    # 0..1


class Detector(Protocol):
    def judge(self, memory: Memory, candidates: list[tuple[Memory, float]]) -> list[Finding]: ...


class NullDetector:
    """Default: never reports a conflict."""

    def judge(self, memory: Memory, candidates: list[tuple[Memory, float]]) -> list[Finding]:
        return []


def _text_tokens(m: Memory) -> set[str]:
    return set(tokenize(f"{m.title} {m.body}"))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _supersede_linked(a: Memory, b: Memory) -> bool:
    """True if either memory declares it supersedes the other (not a conflict then)."""
    a_sup = a.meta.get("supersedes") or []
    b_sup = b.meta.get("supersedes") or []
    return b.id in a_sup or a.id in b_sup


class HeuristicDetector:
    """Token-overlap heuristic. Cannot really judge "contradiction"; it only picks out
    candidates that need human confirmation: high overlap = likely duplicate; medium-high
    overlap + same topic/type + differing text = possible contradiction. Thresholds are
    tunable and deliberately conservative to keep noise down."""

    def __init__(self, *, duplicate_threshold: float = 0.85,
                 contradiction_threshold: float = 0.5):
        self.duplicate_threshold = duplicate_threshold
        self.contradiction_threshold = contradiction_threshold

    def judge(self, memory: Memory, candidates: list[tuple[Memory, float]]) -> list[Finding]:
        mem_tokens = _text_tokens(memory)
        findings: list[Finding] = []
        for other, _score in candidates:
            if other.id == memory.id:
                continue
            if _supersede_linked(memory, other):
                continue
            if other.about != memory.about:  # cross-topic isn't a contradiction
                continue
            jac = _jaccard(mem_tokens, _text_tokens(other))
            if jac >= self.duplicate_threshold:
                findings.append(Finding(
                    other_id=other.id, kind="duplicate", confidence=round(jac, 4),
                    question="这两条记忆可能是重复的，要合并吗？"))
            elif (self.contradiction_threshold <= jac < self.duplicate_threshold
                  and other.type == memory.type and other.body != memory.body):
                findings.append(Finding(
                    other_id=other.id, kind="contradiction", confidence=round(jac, 4),
                    question="关于同一主题有两条不同的记忆，哪条是对的？"))
        findings.sort(key=lambda f: f.confidence, reverse=True)
        return findings


class LLMDetector:
    """Pluggable LLM adjudication (off unless wired). Construction takes a callable
    `judge_fn(memory_text: str, candidates_texts: list[str]) -> list[dict]`, where each
    dict carries index/kind/question/confidence. Without a `judge_fn`, judge returns [].
    This class is only an adapter; it embeds no network or vendor call of its own."""

    def __init__(self, judge_fn: Callable[[str, list[str]], list[dict]] | None = None):
        self.judge_fn = judge_fn

    def judge(self, memory: Memory, candidates: list[tuple[Memory, float]]) -> list[Finding]:
        if self.judge_fn is None or not candidates:
            return []
        memory_text = f"{memory.title} {memory.body}".strip()
        candidates_texts = [f"{other.title} {other.body}".strip() for other, _ in candidates]
        results = self.judge_fn(memory_text, candidates_texts) or []
        findings: list[Finding] = []
        for item in results:
            try:
                idx = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(candidates):
                continue
            other = candidates[idx][0]
            try:
                conf = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            findings.append(Finding(
                other_id=other.id,
                kind=str(item.get("kind", "contradiction")),
                question=str(item.get("question", "")),
                confidence=conf))
        findings.sort(key=lambda f: f.confidence, reverse=True)
        return findings
