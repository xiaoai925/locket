"""Isolated unit tests for the derived SQLite fast-lookup index (locket.index.Index).

Driven purely by Memory objects — no git. A sibling task writes src/locket/index.py;
these tests describe the contract store.py relies on. Do not assume index.py exists yet.
"""
import pytest

from locket.index import Index
from locket.ids import new_ulid
from locket.memory import Memory


def mk(body, *, about="self", type="fact", title=None, valid_to=None,
       importance=None, id=None, extra=None):
    meta = {"pmf": "0.2", "id": id or new_ulid(), "type": type, "about": about}
    if title:
        meta["title"] = title
    if valid_to:
        meta["valid_to"] = valid_to
    if importance is not None:
        meta["importance"] = importance
    if extra:
        meta.update(extra)
    return Memory(meta, body, f"self/{title or 'm'}-{meta['id'][-8:].lower()}.md")


def fake_sha(i: int) -> str:
    return f"{i:040x}"


def entries(*mems):
    """Pair memories with distinct fake blob shas, as rebuild/apply expect."""
    return [(fake_sha(i), m) for i, m in enumerate(mems)]


@pytest.fixture
def index(tmp_path):
    return Index(tmp_path / "pmf" / "index.sqlite")  # parent dir must be created by Index


# 1. rebuild populates everything and records the sha it reflects
def test_rebuild_populates_and_sets_built_at(index):
    a, b, c = mk("第一条"), mk("第二条"), mk("第三条")
    index.rebuild(entries(a, b, c), "sha-main-1")
    assert index.built_at == "sha-main-1"
    assert {m.id for m in index.all()} == {a.id, b.id, c.id}


# 2. the index persists across reopening the same file
def test_persists_across_reopen(tmp_path):
    path = tmp_path / "pmf" / "index.sqlite"
    a, b = mk("甲"), mk("乙")
    Index(path).rebuild(entries(a, b), "sha-xyz")
    reopened = Index(path)
    assert reopened.built_at == "sha-xyz"
    assert {m.id for m in reopened.all()} == {a.id, b.id}


# 3. by_blobs returns only the shas present, round-tripping content incl. unknown fields
def test_by_blobs_returns_present_only_and_roundtrips(index):
    a = mk("带扩展字段的记忆", extra={"x-acme-weight": 3, "tags": ["t1", "t2"]})
    b = mk("另一条")
    index.rebuild([(fake_sha(1), a), (fake_sha(2), b)], "sha-1")

    got = index.by_blobs([fake_sha(1), fake_sha(2), fake_sha(99)])
    assert set(got) == {fake_sha(1), fake_sha(2)}  # missing sha absent
    ra = got[fake_sha(1)]
    assert ra.id == a.id
    assert ra.body == "带扩展字段的记忆"
    assert ra.path == a.path
    assert ra.meta["x-acme-weight"] == 3  # unknown field survives
    assert ra.meta["tags"] == ["t1", "t2"]


def test_by_blobs_empty_input(index):
    index.rebuild(entries(mk("x")), "s")
    assert index.by_blobs([]) == {}


# 4. get_by_suffix: unique hit, miss, and ambiguous (same 8-char suffix)
def test_get_by_suffix_unique_and_miss(index):
    a, b = mk("找我"), mk("别找我")
    index.rebuild(entries(a, b), "s")
    hits = index.get_by_suffix(a.id[-8:])
    assert [m.id for m in hits] == [a.id]
    assert index.get_by_suffix("ZZZZZZZZ") == []  # no such suffix


def test_get_by_suffix_ambiguous(index):
    suffix = "ABCD1234"
    id1 = new_ulid()[:-8] + suffix
    id2 = new_ulid()[:-8] + suffix
    a = mk("第一", id=id1)
    b = mk("第二", id=id2)
    index.rebuild(entries(a, b), "s")
    hits = index.get_by_suffix(suffix)
    assert {m.id for m in hits} == {id1, id2}


# 5. filtering by about / type
def test_all_filters_by_about_and_type(index):
    self_fact = mk("自己的事实", about="self", type="fact")
    proj_pref = mk("项目偏好", about="project:si-ag", type="preference")
    world = mk("世界知识", about="world", type="fact")
    index.rebuild(entries(self_fact, proj_pref, world), "s")

    assert {m.id for m in index.all(about="self")} == {self_fact.id}
    assert {m.id for m in index.all(about="project:si-ag")} == {proj_pref.id}
    assert {m.id for m in index.all(type="fact")} == {self_fact.id, world.id}
    assert {m.id for m in index.all(type="preference")} == {proj_pref.id}


# 6. include_inactive=False drops memories whose valid_to is in the past
def test_include_inactive_filters_expired(index):
    live = mk("仍然有效", title="live")
    future = mk("未来才失效", title="future", valid_to="2999-01-01")
    expired = mk("早已失效", title="expired", valid_to="2000-01-01")
    index.rebuild(entries(live, future, expired), "s")

    assert {m.id for m in index.all(include_inactive=True)} == {live.id, future.id, expired.id}
    assert {m.id for m in index.all(include_inactive=False)} == {live.id, future.id}

    assert index.get_by_suffix(expired.id[-8:], include_inactive=True)
    assert index.get_by_suffix(expired.id[-8:], include_inactive=False) == []
    assert index.get_by_suffix(future.id[-8:], include_inactive=False)


# 7. apply does incremental upsert + remove and advances built_at
def test_apply_upsert_and_remove(index):
    a, b = mk("原始甲", title="a"), mk("原始乙", title="b")
    index.rebuild([(fake_sha(1), a), (fake_sha(2), b)], "sha-1")

    a_edited = Memory({**a.meta}, "甲改过了", a.path)
    c = mk("新来的丙", title="c")
    index.apply(upsert=[(fake_sha(3), a_edited), (fake_sha(4), c)],
                remove_ids=[b.id], at="sha-2")

    assert index.built_at == "sha-2"
    by_id = {m.id: m for m in index.all()}
    assert set(by_id) == {a.id, c.id}  # b removed
    assert by_id[a.id].body == "甲改过了"  # a updated in place (same id)


# 8. clear empties the index and resets built_at
def test_clear_resets(index):
    index.rebuild(entries(mk("x"), mk("y")), "s")
    index.clear()
    assert index.all() == []
    assert index.built_at is None


# 9. Chinese content round-trips as UTF-8
def test_chinese_roundtrip(index):
    m = mk("用户的主力开发环境是 Windows 11，Python 用 py -3 调用。", title="环境")
    index.rebuild([(fake_sha(1), m)], "s")
    got = index.by_blobs([fake_sha(1)])[fake_sha(1)]
    assert got.body == "用户的主力开发环境是 Windows 11，Python 用 py -3 调用。"
    assert got.title == "环境"


# built_at is None before anything is built
def test_built_at_none_before_build(tmp_path):
    assert Index(tmp_path / "pmf" / "fresh.sqlite").built_at is None
