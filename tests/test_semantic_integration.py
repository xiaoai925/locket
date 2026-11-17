"""Semantic conflict detection wired into the engine (spec §6.2).

The pure detector is covered by test_semantic.py; here we test the store integration:
off by default, and when policy turns it on, near-duplicate memories at *different*
paths get a conflict recorded on merge (and previewed read-only).
"""
import pytest

from locket.store import Locket, Session, MAIN


@pytest.fixture
def store(tmp_path):
    s = Locket(tmp_path / "mem")
    s.init("Tester <t@example.com>")
    return s


def _enable(store):
    cfg = store.root / ".pmf" / "config.toml"
    text = cfg.read_text("utf-8").rstrip() + '\nsemantic_conflicts = "heuristic"\n'
    cfg.write_text(text, "utf-8")
    store.save()


DUP_A = "用户喜欢在清晨六点起床跑步锻炼身体保持健康的生活方式"
DUP_B = "用户喜欢在清晨六点起床跑步锻炼身体保持健康生活方式呢"


def test_off_by_default_records_nothing(store):
    s = Session("cli", "1")
    store.remember(DUP_A, s, about="self", title="a")
    store.end_session(s)
    s2 = Session("cli", "2")
    store.remember(DUP_B, s2, about="self", title="b")
    store.end_session(s2)
    assert store.conflicts() == []            # detector off → no semantic conflict
    assert store.detect_conflicts() == []     # scan also off by default


def test_merge_records_duplicate_when_enabled(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember(DUP_A, s, about="self", title="a")
    store.end_session(s)
    s2 = Session("claude-code", "2")
    store.remember(DUP_B, s2, about="self", title="b")
    result = store.end_session(s2)
    conflicts = store.conflicts()
    assert conflicts, "near-duplicate should be recorded on merge when enabled"
    c = conflicts[0]
    assert c.meta["kind"] in ("duplicate", "contradiction")
    assert len(c.meta["members"]) == 2        # links the two memory ids
    # both original memories remain in main (nothing deleted to resolve it)
    assert len([m for m in store.memories() if m.about == "self"]) == 2


def test_different_about_not_flagged(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember(DUP_A, s, about="self", title="a")
    store.end_session(s)
    s2 = Session("cli", "2")
    store.remember(DUP_B, s2, about="project:x", title="b")  # different subject
    store.end_session(s2)
    assert store.conflicts() == []


def test_detect_conflicts_preview_is_readonly(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember(DUP_A, s, about="self", title="a")
    store.end_session(s)
    # add the near-duplicate directly to main without triggering merge detection:
    # use a fresh scan on an already-populated repo
    s2 = Session("cli", "2")
    store.remember("完全不相干的另一件事情内容", s2, about="self", title="c")
    store.end_session(s2)
    before = store.git.rev_parse(MAIN)
    findings = store.detect_conflicts()       # read-only
    assert store.git.rev_parse(MAIN) == before  # scan commits nothing
    assert isinstance(findings, list)


def test_duplicate_not_recorded_twice(store):
    _enable(store)
    s = Session("cli", "1")
    store.remember(DUP_A, s, about="self", title="a")
    store.end_session(s)
    s2 = Session("cli", "2")
    store.remember(DUP_B, s2, about="self", title="b")
    store.end_session(s2)
    n1 = len(store.conflicts())
    # a third unrelated merge should not re-record the same pair
    s3 = Session("cli", "3")
    store.remember("无关内容第三条随便写点什么", s3, about="self", title="c")
    store.end_session(s3)
    assert len(store.conflicts()) == n1
