"""Tests for the P2b security boundary: read scope + sensitivity enforcement
(spec §8/§9) and per-repo write serialization."""
import concurrent.futures

import pytest

from locket.scope import (DEFAULT_SENSITIVITY, OWNER_SCOPE, SENSITIVITY_ORDER, Scope,
                          about_matches, rank)
from locket.service import MemoryService, Principal
from locket.store import Locket, LocketError


# --------------------------------------------------------------------------
# scope.py — pure policy logic
# --------------------------------------------------------------------------

def test_rank_order_is_increasing():
    ranks = [rank(level) for level in SENSITIVITY_ORDER]
    assert ranks == sorted(ranks)
    assert ranks == list(range(len(SENSITIVITY_ORDER)))
    assert rank("public") < rank("personal") < rank("sensitive") < rank("secret")


def test_rank_unknown_and_none_fall_back_to_default():
    assert rank("bogus") == rank(DEFAULT_SENSITIVITY)
    assert rank(None) == rank(DEFAULT_SENSITIVITY)
    assert rank("") == rank(DEFAULT_SENSITIVITY)
    assert DEFAULT_SENSITIVITY == "personal"


def test_about_matches_glob_rules():
    assert about_matches("self", ("*",))
    assert about_matches("project:si-ag", ("*",))
    assert about_matches("self", ("self",))
    assert not about_matches("project:x", ("self",))
    assert about_matches("project:si-ag", ("project:*",))
    assert not about_matches("self", ("project:*",))
    assert not about_matches("self", ())  # empty set grants nothing


def test_scope_can_read_sensitivity_ceiling():
    personal = Scope(max_sensitivity="personal", about=("*",))
    assert personal.can_read(about="self", sensitivity="public")
    assert personal.can_read(about="self", sensitivity="personal")
    assert personal.can_read(about="self", sensitivity=None)  # default personal
    assert not personal.can_read(about="self", sensitivity="sensitive")


def test_scope_never_reads_secret_even_with_max_secret():
    maxed = Scope(max_sensitivity="secret", about=("*",))
    assert not maxed.can_read(about="self", sensitivity="secret")
    # but it can still read everything below secret
    assert maxed.can_read(about="self", sensitivity="sensitive")


def test_scope_can_read_about_restriction():
    only_self = Scope(max_sensitivity="personal", about=("self",))
    assert only_self.can_read(about="self", sensitivity="personal")
    assert not only_self.can_read(about="project:x", sensitivity="personal")


def test_allows_write_sensitivity():
    personal = Scope(max_sensitivity="personal")
    assert personal.allows_write_sensitivity("public")
    assert personal.allows_write_sensitivity("personal")
    assert personal.allows_write_sensitivity(None)
    assert not personal.allows_write_sensitivity("sensitive")
    assert Scope(max_sensitivity="sensitive").allows_write_sensitivity("sensitive")


def test_owner_scope_is_permissive():
    assert OWNER_SCOPE.can_write
    assert OWNER_SCOPE.can_read(about="project:anything", sensitivity="sensitive")
    assert not OWNER_SCOPE.can_read(about="self", sensitivity="secret")


# --------------------------------------------------------------------------
# service enforcement
# --------------------------------------------------------------------------

@pytest.fixture
def svc(tmp_path):
    Locket(tmp_path / "mem").init("T <t@x>")
    return MemoryService(repo=tmp_path / "mem")


@pytest.fixture
def seeded(svc):
    """Owner writes two memories: a self/personal one and a project/sensitive one."""
    owner = Principal()  # OWNER_SCOPE
    self_mem = svc.remember(owner, "用户偏好用中文交流", about="self",
                            title="交流语言")["memory"]
    secret_mem = svc.remember(owner, "项目使用内部密钥轮换流程", about="project:x",
                              sensitivity="sensitive", title="密钥流程")["memory"]
    return svc, self_mem, secret_mem


def test_list_filters_out_of_scope(seeded):
    svc, self_mem, secret_mem = seeded
    p_read = Principal(scope=Scope(max_sensitivity="personal", about=("self",), can_write=False))
    ids = [m["id"] for m in svc.list(p_read)["memories"]]
    assert self_mem["id"] in ids
    assert secret_mem["id"] not in ids
    assert svc.list(p_read)["count"] == 1
    # owner still sees both
    assert svc.list(Principal())["count"] == 2


def test_recall_filters_out_of_scope(seeded):
    svc, self_mem, secret_mem = seeded
    p_read = Principal(scope=Scope(max_sensitivity="personal", about=("self",), can_write=False))
    # the in-scope memory is findable
    hits = svc.recall(p_read, "交流")["hits"]
    assert any(h["id"] == self_mem["id"] for h in hits)
    # the out-of-scope (sensitive, project) memory is never surfaced
    hits2 = svc.recall(p_read, "密钥")["hits"]
    assert all(h["id"] != secret_mem["id"] for h in hits2)
    # owner can find the sensitive one
    owner_hits = svc.recall(Principal(), "密钥")["hits"]
    assert any(h["id"] == secret_mem["id"] for h in owner_hits)


def test_get_out_of_scope_raises_without_leaking(seeded):
    svc, _self_mem, secret_mem = seeded
    p_read = Principal(scope=Scope(max_sensitivity="personal", about=("self",), can_write=False))
    with pytest.raises(LocketError):
        svc.get(p_read, secret_mem["short"])
    with pytest.raises(LocketError):
        svc.why(p_read, secret_mem["short"])
    with pytest.raises(LocketError):
        svc.history(p_read, secret_mem["short"])
    # owner can get it fine
    assert svc.get(Principal(), secret_mem["short"])["memory"]["id"] == secret_mem["id"]


def test_remember_denied_without_write(seeded):
    svc, _, _ = seeded
    p_read = Principal(scope=Scope(max_sensitivity="personal", about=("self",), can_write=False))
    with pytest.raises(LocketError):
        svc.remember(p_read, "试图写入", about="self")


def test_update_forget_denied_without_write(seeded):
    svc, self_mem, _ = seeded
    p_read = Principal(scope=Scope(max_sensitivity="personal", about=("self",), can_write=False))
    with pytest.raises(LocketError):
        svc.update(p_read, self_mem["short"], text="改")
    with pytest.raises(LocketError):
        svc.forget(p_read, self_mem["short"])


def test_remember_sensitivity_above_ceiling_denied(svc):
    p_w = Principal(scope=Scope(max_sensitivity="personal", about=("*",), can_write=True))
    with pytest.raises(LocketError):
        svc.remember(p_w, "太敏感", sensitivity="sensitive")
    # at/below ceiling is allowed
    assert svc.remember(p_w, "正常", sensitivity="personal")["memory"]["id"]


def test_default_principal_full_access(svc):
    owner = Principal()
    svc.remember(owner, "一条自我记忆", about="self", title="a")
    svc.remember(owner, "一条项目敏感记忆", about="project:y", sensitivity="sensitive", title="b")
    listed = svc.list(owner)
    assert listed["count"] == 2
    short = listed["memories"][0]["short"]
    assert svc.get(owner, short)["memory"]["id"]


# --------------------------------------------------------------------------
# write serialization
# --------------------------------------------------------------------------

def test_concurrent_writes_are_serialized(svc):
    n = 8
    owner = Principal()

    def write(i):
        return svc.remember(owner, f"记忆{i}", title=f"t{i}")["memory"]["id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        ids = list(pool.map(write, range(n)))

    assert len(ids) == n
    assert len(set(ids)) == n  # all distinct, none lost
    listed = svc.list(owner)
    assert listed["count"] == n


def test_writes_cannot_escape_about_and_public_updates_work(svc):
    p = Principal(scope=Scope(max_sensitivity="public", about=("person:qq-12345",)))
    with pytest.raises(LocketError):
        svc.remember(p, "越界", about="self", sensitivity="public")
    mid = svc.remember(p, "白饭", about="person:qq-12345", sensitivity="public")["memory"]["id"]
    assert svc.update(p, mid, text="爱吃白饭")["memory"]["text"] == "爱吃白饭"
    with pytest.raises(LocketError):
        svc.update(p, mid, fields={"about": "person:qq-98765"})
    assert svc.supersede(p, mid, "现在喜欢面条")["merged"]


def test_conflict_and_similar_results_are_scoped(svc):
    from locket.store import Session
    owner = Principal()
    hidden = svc.remember(owner, "共同词白饭", about="project:hidden")["memory"]
    visible = svc.remember(owner, "共同词白饭", about="self")["memory"]
    store = svc.repo(owner)
    a, b = Session("test", "a"), Session("test", "b")
    store.update(hidden["id"], a, text="隐私版本甲")
    store.update(hidden["id"], b, text="隐私版本乙")
    store.end_session(a)
    store.end_session(b)
    conflict = svc.conflicts(owner)["conflicts"][0]
    p = Principal(scope=Scope(about=("self",)))
    assert svc.conflicts(p)["conflicts"] == []
    assert svc.recall(p, "白饭")["open_conflicts"] == []
    result = svc.remember(p, "共同词白饭", about="self")
    assert all(m["id"] != hidden["id"] for m in result["similar"])
    assert svc.history(p, visible["id"])["versions"] == []
    with pytest.raises(LocketError):
        svc.resolve(p, conflict["id"])


def test_delegate_cannot_bypass_owner_approval(svc):
    root = svc.repo(Principal()).root
    config = root / ".pmf/config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace(
        "main_requires_approval = false", "main_requires_approval = true"), encoding="utf-8")
    svc.repo(Principal()).save()
    p = Principal(scope=Scope(about=("self",)))
    out = svc.remember(p, "需要审核的偏好")
    assert out["merged"] is False
    assert svc.list(Principal())["count"] == 0
    assert len(svc.pending(Principal())["pending"]) == 1
