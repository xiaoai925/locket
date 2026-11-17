"""Merge approval: main_requires_approval leaves agent sessions pending (spec §6.1)."""
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

import pytest

from locket.store import Locket, LocketError, Session, MAIN


@pytest.fixture
def store(tmp_path):
    s = Locket(tmp_path / "mem")
    s.init("Tester <t@example.com>")
    return s


def _set_approval(store, on=True):
    cfg = store.root / ".pmf" / "config.toml"
    text = cfg.read_text("utf-8")
    text = text.replace("main_requires_approval = false", f"main_requires_approval = {str(on).lower()}")
    if "main_requires_approval" not in text:
        text += f"\n[policy]\nmain_requires_approval = {str(on).lower()}\n"
    cfg.write_text(text, "utf-8")
    store.save()


def test_agent_session_is_pending_until_approved(store):
    _set_approval(store, True)
    assert store.requires_approval() is True
    s = Session("claude-code", "s1")
    m, _ = store.remember("agent 学到的事", s, title="t")
    result = store.end_session(s)
    assert result.pending and not result.merged
    # not in main yet
    assert all(x.id != m.id for x in store.memories())
    # branch still there, awaiting approval
    assert any(sess.label == "claude-code/s1" for sess, _ in store.pending())
    # owner approves → merged
    approved = store.approve(s)
    assert approved.merged
    assert any(x.id == m.id for x in store.memories())
    assert store.git.rev_parse(s.ref) is None


def test_owner_direct_write_bypasses_approval(store):
    _set_approval(store, True)
    s = Session("cli", "owner1")
    store.remember("所有者直接写", s, title="o")
    result = store.end_session(s, owner=True)   # owner acting directly
    assert result.merged and not result.pending


def test_no_approval_merges_immediately(store):
    _set_approval(store, False)
    s = Session("claude-code", "s2")
    m, _ = store.remember("直接合并", s)
    result = store.end_session(s)
    assert result.merged and not result.pending
    assert any(x.id == m.id for x in store.memories())


def test_approve_unknown_session_raises(store):
    _set_approval(store, True)
    with pytest.raises(LocketError):
        store.approve(Session("nobody", "nope"))
