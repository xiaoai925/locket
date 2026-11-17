"""Hybrid recall (P6) wired into the store: BM25 + optional vector semantics.

Default is pure BM25 (semantic off → offline-safe, zero behaviour change). When
policy turns semantic recall on, the local vector index adds meaning-based matches.
"""
import pytest

from locket.store import Locket, Session
from locket.embed import HashingEmbedder


@pytest.fixture
def store(tmp_path):
    s = Locket(tmp_path / "mem")
    s.init("Tester <t@example.com>")
    return s


def _enable(store):
    cfg = store.root / ".pmf" / "config.toml"
    cfg.write_text(cfg.read_text("utf-8").rstrip() + '\nsemantic_recall = "on"\n', "utf-8")
    store.save()


def test_recall_defaults_to_bm25_no_vectors(store):
    s = Session("cli", "1")
    store.remember("用户使用 Python 3.12 开发", s, title="env")
    store.end_session(s)
    assert store.embedder() is None          # off by default
    assert store.vectors is None
    hits = store.recall("Python")
    assert hits and "Python" in hits[0].memory.body


def test_recall_still_works_with_semantic_on(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember("用户喜欢在清晨六点起床跑步锻炼", s, title="晨跑")
    store.remember("用户的工作是软件工程师", s, title="职业")
    store.end_session(s)
    assert store.embedder() is not None and store.vectors is not None
    # keyword still matches
    hits = store.recall("跑步")
    assert hits and hits[0].memory.title == "晨跑"


def test_semantic_recalls_on_overlap_without_exact_query_terms(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember("用户每天清晨六点起床沿着江边跑步锻炼身体保持健康", s, title="晨跑")
    store.remember("用户最喜欢的颜色是蓝色和绿色", s, title="颜色")
    store.end_session(s)
    # a query that shares meaning/tokens with 晨跑 but isn't an exact keyword line
    hits = store.recall("清晨 起床 锻炼 身体")
    assert hits and hits[0].memory.title == "晨跑"


def test_vector_index_rebuilds_and_drops_forgotten(store):
    _enable(store)
    s = Session("cli", "1")
    m, _ = store.remember("一条会被遗忘的记忆内容", s, title="临时")
    store.remember("一条保留下来的记忆内容", s, title="长期")
    store.end_session(s)
    store.recall("记忆")                       # triggers index build
    ids_before = store.vectors.all_ids()
    assert m.id in ids_before
    s2 = Session("cli", "2")
    store.forget(m.id, s2)
    store.end_session(s2)
    store.recall("记忆")                       # rebuild after main moved
    assert m.id not in store.vectors.all_ids()  # forgotten → dropped from vectors


def test_reindex_builds_vectors_when_enabled(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember("可被重建索引的记忆", s, title="x")
    store.end_session(s)
    n = store.reindex()
    assert n == 1 and store.vectors.all_ids()
