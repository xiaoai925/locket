#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark: SQLite derived index vs. parsing every memory file (ARCHITECTURE.md §8, plan A).

Shows that serving memories from the derived SQLite index is much faster than the
current read path, which parses every memory's markdown on each read.

Run (本机真解释器):
  set PYTHONUTF8=1
  C:/Users/aiqia/AppData/Local/Programs/Python/Python312/python.exe scripts/bench_index.py [N]
or, with py launcher:
  py -3 scripts/bench_index.py [N]

N defaults to 5000. The script is self-contained: it injects <repo>/src onto
sys.path, touches neither git nor store.py, and skips cleanly (exit 0) if the
index module is not written yet.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Make `locket` importable without installing: add <repo>/src to sys.path.
_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from locket.ids import new_ulid  # noqa: E402
from locket.memory import Memory, parse  # noqa: E402

try:
    from locket.index import Index  # noqa: E402
except Exception as exc:  # noqa: BLE001 - module may not be written yet
    print(f"index.py 尚未就绪，跳过（{exc.__class__.__name__}: {exc}）")
    sys.exit(0)


# ---- synthetic data ---------------------------------------------------------

_ABOUTS = ["self", "project:si-ag", "project:locket", "world"]
_TYPES = ["fact", "preference", "event", "procedure", "summary"]
_WORDS_ZH = ["用户", "偏好", "项目", "记忆", "开发", "环境", "同步", "冲突",
             "合并", "检索", "向量", "数据库", "性能", "存储", "索引", "分支"]
_WORDS_EN = ["python", "windows", "git", "sqlite", "recall", "merge", "vector",
             "index", "memory", "cloud", "gitea", "token", "embedding", "cache"]


def _body(i: int, rng: random.Random) -> str:
    zh = "".join(rng.sample(_WORDS_ZH, rng.randint(3, 6)))
    en = " ".join(rng.sample(_WORDS_EN, rng.randint(2, 5)))
    return f"第{i}条记忆：{zh}，相关 {en}。"


def make_memories(n: int, seed: int = 7) -> list[tuple[str, Memory]]:
    """Return [(fake_blob_sha, Memory)]; blob sha is a stand-in, the index just stores it."""
    rng = random.Random(seed)
    entries: list[tuple[str, Memory]] = []
    for i in range(n):
        about = _ABOUTS[i % len(_ABOUTS)]
        mtype = _TYPES[i % len(_TYPES)]
        mid = new_ulid()
        meta = {"pmf": "0.2", "id": mid, "type": mtype, "about": about,
                "created": "2026-11-03T10:00:00+08:00"}
        if i % 3 == 0:
            meta["title"] = f"标题{i}"
        if i % 4 == 0:
            meta["tags"] = rng.sample(_WORDS_EN, 2)
        if i % 5 == 0:
            meta["importance"] = round(rng.random(), 2)
        if i % 11 == 0:  # some expired, to exercise the active filter
            meta["valid_to"] = "2000-01-01"
        title = meta.get("title") or "m"
        path = f"{about.replace(':', 's/')}/{title}-{mid[-8:].lower()}.md"
        entries.append((f"{i:040x}", Memory(meta, _body(i, rng), path)))
    return entries


# ---- timing -----------------------------------------------------------------

def _timeit(fn, repeat: int = 3) -> tuple[float, object]:
    """Return (best seconds over `repeat` runs, last result)."""
    best = float("inf")
    result = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        best = min(best, time.perf_counter() - start)
    return best, result


def baseline_parse_all(entries: list[tuple[str, Memory]]) -> float:
    """Current read path cost: dump then parse every memory's markdown."""
    blobs = [m.dumps() for _sha, m in entries]

    def run():
        return [parse(b) for b in blobs]

    secs, _ = _timeit(run)
    return secs


def bench_index(entries: list[tuple[str, Memory]], tmp: Path) -> dict:
    idx = Index(tmp / "index.sqlite")

    rebuild_secs, _ = _timeit(lambda: idx.rebuild(entries, "deadbeef"), repeat=3)
    all_secs, all_rows = _timeit(lambda: idx.all(include_inactive=True))

    shas = [sha for sha, _ in entries]
    sample = random.Random(1).sample(shas, min(50, len(shas)))
    by_blobs_secs, _ = _timeit(lambda: idx.by_blobs(sample))

    suffix = entries[len(entries) // 2][1].id[-8:]
    suffix_secs, _ = _timeit(lambda: idx.get_by_suffix(suffix))

    return {
        "rebuild": rebuild_secs,
        "all": all_secs,
        "all_count": len(all_rows),
        "by_blobs": by_blobs_secs,
        "get_by_suffix": suffix_secs,
        "built_at": idx.built_at,
    }


def main(argv: list[str]) -> int:
    n = int(argv[1]) if len(argv) > 1 else 5000
    print(f"生成 {n} 条合成记忆……")
    entries = make_memories(n)

    base = baseline_parse_all(entries)
    tmp = Path(tempfile.mkdtemp(prefix="locket-bench-"))
    try:
        idx = bench_index(entries, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    speedup = base / idx["all"] if idx["all"] > 0 else float("inf")
    ms = lambda s: f"{s * 1000:9.2f} ms"

    print("\n=== 读路径对比（N = %d） ===" % n)
    print(f"  基线 · 解析全部 markdown        {ms(base)}")
    print(f"  索引 · all() 全量取回           {ms(idx['all'])}   （{idx['all_count']} 条）")
    print(f"  → 加速                          {speedup:6.1f}×")
    print("\n=== 索引各操作耗时 ===")
    print(f"  rebuild（建全量索引）           {ms(idx['rebuild'])}")
    print(f"  by_blobs（取 50 条）            {ms(idx['by_blobs'])}")
    print(f"  get_by_suffix（按 id 后缀取）    {ms(idx['get_by_suffix'])}")
    print(f"  built_at = {idx['built_at']}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv))
