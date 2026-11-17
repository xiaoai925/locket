"""Tests for the HTTP/JSON API (webapi.py) over MemoryService."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from locket.service import MemoryService
from locket.store import Locket
from locket.webapi import make_server


@pytest.fixture
def server(tmp_path):
    repo = tmp_path / "mem"
    Locket(repo).init("T <t@example.com>")
    srv = make_server(MemoryService(repo=repo))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()


def call(server, method, path, body=None, headers=None):
    port = server.server_address[1]
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, json.loads(raw) if raw else None


def test_healthz(server):
    status, body = call(server, "GET", "/healthz")
    assert status == 200 and body["ok"] is True


def test_remember_list_get_recall_forget(server):
    status, body = call(server, "POST", "/remember", {"text": "用户喜欢喝茶", "title": "饮品"})
    assert status == 200
    mid, short = body["memory"]["id"], body["memory"]["short"]

    status, body = call(server, "GET", "/memories")
    assert status == 200 and any(m["id"] == mid for m in body["memories"])

    status, body = call(server, "GET", f"/memory/{short}")
    assert status == 200 and body["memory"]["id"] == mid

    status, body = call(server, "POST", "/recall", {"query": "喝茶"})
    assert status == 200 and any(h["id"] == mid for h in body["hits"])

    status, body = call(server, "POST", f"/memory/{short}/forget", {})
    assert status == 200


def test_bad_requests(server):
    status, body = call(server, "POST", "/remember", {"title": "缺正文"})
    assert status == 400 and "error" in body

    status, body = call(server, "POST", "/remember", {"text": "内容", "type": "bogus"})
    assert status == 400 and "error" in body

    status, _ = call(server, "GET", "/no-such-route")
    assert status == 404


def test_session_isolation_over_http(server):
    headers = {"X-Locket-Session": "s1"}
    status, body = call(server, "POST", "/remember", {"text": "会话里的临时记忆"}, headers)
    assert status == 200
    mid = body["memory"]["id"]

    # no session header -> main view, not visible yet
    status, body = call(server, "GET", "/memories")
    assert all(m["id"] != mid for m in body["memories"])

    # with the session header -> visible via recall
    status, body = call(server, "POST", "/recall", {"query": "会话"}, headers)
    assert any(h["id"] == mid for h in body["hits"])

    # end the session -> merged into main
    status, body = call(server, "POST", "/session/end", {}, headers)
    assert status == 200 and body["merged"] is True
    status, body = call(server, "GET", "/memories")
    assert any(m["id"] == mid for m in body["memories"])
