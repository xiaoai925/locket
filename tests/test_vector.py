"""Tests for the derived local vector index (P6). Pure vectors, no git."""
from locket.embed import HashingEmbedder
from locket.vector import VectorIndex, VHit

DIM = 256


def _emb():
    return HashingEmbedder(dim=DIM)


def _rows(emb, items):
    # items: list of (id, blob, text) -> rows of (id, blob, vector)
    return [(i, b, emb.embed(t)) for i, b, t in items]


def test_rebuild_populates_meta_and_ids(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    rows = _rows(emb, [("a", "blobA", "用户喜欢喝茶"),
                       ("b", "blobB", "项目跑在 Windows 上"),
                       ("c", "blobC", "今天天气很好")])
    vi.rebuild(rows, at="deadbeef", embedder_name=emb.name)
    assert vi.all_ids() == {"a", "b", "c"}
    assert vi.built_at == "deadbeef"
    assert vi.embedder_name == emb.name
    assert vi.stored_dim == DIM
    assert vi.dim_matches(DIM)
    assert not vi.dim_matches(128)


def test_persists_across_reopen(tmp_path):
    emb = _emb()
    p = tmp_path / "pmf" / "vec.sqlite"
    vi = VectorIndex(p, DIM)
    vi.rebuild(_rows(emb, [("a", "blobA", "喝茶的记忆")]), at="sha1", embedder_name=emb.name)
    vi.close()
    vi2 = VectorIndex(p, DIM)
    assert vi2.all_ids() == {"a"}
    assert vi2.built_at == "sha1"
    assert vi2.embedder_name == emb.name


def test_parent_dir_is_created(tmp_path):
    # the .git/pmf/ dir need not exist yet
    deep = tmp_path / "repo" / ".git" / "pmf" / "vec.sqlite"
    vi = VectorIndex(deep, DIM)
    assert deep.exists()
    assert vi.all_ids() == set()


def test_search_finds_overlapping_text(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.rebuild(_rows(emb, [
        ("tea", "b1", "用户喜欢在早晨喝一杯热茶"),
        ("win", "b2", "这个项目部署在 Windows 服务器上"),
        ("weather", "b3", "周末会去公园散步晒太阳"),
    ]), at="x", embedder_name=emb.name)
    # query overlaps the "tea" row most
    q = emb.embed("用户早晨喝热茶的习惯")
    hits = vi.search(q, k=3)
    assert hits and hits[0].id == "tea"
    assert 0.0 <= hits[0].score <= 1.0
    assert hits[0].blob == "b1"
    # scores are sorted descending
    assert all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1))


def test_search_respects_allowed_ids(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.rebuild(_rows(emb, [
        ("tea", "b1", "用户喜欢在早晨喝一杯热茶"),
        ("tea2", "b2", "用户早晨总要喝热茶才清醒"),
        ("win", "b3", "项目部署在 Windows 服务器"),
    ]), at="x", embedder_name=emb.name)
    q = emb.embed("早晨喝热茶")
    hits = vi.search(q, k=5, allowed_ids={"win"})
    assert {h.id for h in hits} <= {"win"}


def test_has_by_blob(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.upsert("a", "blobA", emb.embed("一些内容"))
    assert vi.has("blobA")
    assert not vi.has("unknown-blob")


def test_upsert_overwrites_same_id(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.upsert("a", "blobOld", emb.embed("旧内容文字"))
    vi.upsert("a", "blobNew", emb.embed("全新的内容文字"))
    assert vi.all_ids() == {"a"}          # still one row
    assert vi.has("blobNew") and not vi.has("blobOld")


def test_remove_and_clear(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.rebuild(_rows(emb, [("a", "b1", "一"), ("b", "b2", "二"), ("c", "b3", "三")]),
               at="x", embedder_name=emb.name)
    vi.remove(["b"])
    assert vi.all_ids() == {"a", "c"}
    vi.remove([])  # no-op, must not raise
    assert vi.all_ids() == {"a", "c"}
    vi.clear()
    assert vi.all_ids() == set()
    assert vi.built_at is None
    assert vi.embedder_name is None


def test_search_dim_mismatch_is_safe(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.rebuild(_rows(emb, [("a", "b1", "内容")]), at="x", embedder_name=emb.name)
    assert vi.search([0.1, 0.2, 0.3], k=3) == []   # wrong-dim query
    assert vi.search([], k=3) == []                 # empty query


def test_vhit_shape(tmp_path):
    emb = _emb()
    vi = VectorIndex(tmp_path / "pmf" / "vec.sqlite", DIM)
    vi.upsert("a", "b1", emb.embed("内容文字"))
    hits = vi.search(emb.embed("内容文字"), k=1)
    assert isinstance(hits[0], VHit)
    assert hits[0].id == "a" and hits[0].blob == "b1"
