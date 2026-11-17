"""Tests for the framework-agnostic MemoryService (the shared core behind HTTP/MCP)."""
import pytest

from locket.service import MemoryService, Principal
from locket.store import Locket, LocketError


@pytest.fixture
def svc(tmp_path):
    repo = tmp_path / "mem"
    Locket(repo).init("T <t@example.com>")
    return MemoryService(repo=repo)


def test_remember_without_session_merges_and_is_queryable(svc):
    p = Principal()
    out = svc.remember(p, "用户偏好中文交流", type="preference", title="交流语言")
    assert out["merged"] is True
    mem = out["memory"]
    assert mem["id"] and mem["short"] and mem["type"] == "preference"
    assert mem["about"] == "self" and mem["text"] == "用户偏好中文交流"

    hits = svc.recall(p, "中文交流")["hits"]
    assert any(h["id"] == mem["id"] for h in hits)
    assert all("score" in h and "matched" in h for h in hits)

    listing = svc.list(p)
    assert listing["count"] >= 1
    assert any(m["id"] == mem["id"] for m in listing["memories"])

    assert svc.get(p, mem["short"])["memory"]["id"] == mem["id"]


def test_session_isolates_until_end(svc):
    held = Principal(session="s1")
    main = Principal()
    out = svc.remember(held, "SI_Ag 项目使用 Python 3.12", about="project:SI_Ag")
    assert out["merged"] is False
    mid = out["memory"]["id"]

    assert any(h["id"] == mid for h in svc.recall(held, "Python")["hits"])
    assert all(h["id"] != mid for h in svc.recall(main, "Python")["hits"])

    end = svc.end_session(held)
    assert end["merged"] is True
    assert any(h["id"] == mid for h in svc.recall(main, "Python")["hits"])


def test_update_changes_text_keeps_id(svc):
    p = Principal()
    m = svc.remember(p, "生日是 3 月 5 日", title="生日")["memory"]
    upd = svc.update(p, m["short"], text="生日是 5 月 3 日", reason="记反了")
    assert upd["memory"]["id"] == m["id"]
    assert upd["memory"]["text"] == "生日是 5 月 3 日"
    assert svc.get(p, m["short"])["memory"]["text"] == "生日是 5 月 3 日"


def test_supersede_keeps_both_with_validity(svc):
    p = Principal()
    old = svc.remember(p, "用户住在北京", title="居住城市")["memory"]
    sup = svc.supersede(p, old["short"], "用户住在上海", effective="2026-10-01")
    assert sup["old"]["valid_to"] == "2026-10-01"
    assert sup["new"]["supersedes"] == [old["id"]]
    assert sup["merged"] is True


def test_forget_removes_from_main(svc):
    p = Principal()
    m = svc.remember(p, "临时信息内容", title="临时")["memory"]
    out = svc.forget(p, m["short"])
    assert out["merged"] is True
    with pytest.raises(LocketError):
        svc.get(p, m["short"])


def test_why_and_history(svc):
    p = Principal()
    m = svc.remember(p, "用户用 py -3 运行 Python", type="procedure", title="运行")["memory"]
    why = svc.why(p, m["short"])
    assert why["memory"]["id"] == m["id"]
    assert why["sources"] and why["sources"][0]["kind"] == "conversation"
    assert any(e["op"] == "learn" for e in why["history"])
    hist = svc.history(p, m["short"])
    assert hist["id"] == m["id"] and len(hist["versions"]) >= 1


def test_memory_dict_has_core_fields(svc):
    m = svc.remember(Principal(), "带标签的事实内容", title="标签事实", tags=["a", "b"])["memory"]
    for key in ("id", "short", "type", "about", "title", "text", "path", "active", "importance"):
        assert key in m
    assert m["tags"] == ["a", "b"]


def test_invalid_type_raises(svc):
    with pytest.raises(LocketError):
        svc.remember(Principal(), "内容", type="bogus")


def test_end_session_without_session_raises(svc):
    with pytest.raises(LocketError):
        svc.end_session(Principal())
