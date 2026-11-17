"""Protocol tests use synthetic repositories, no real cloud or user memories."""
import asyncio
import hashlib
import json
import os
import sys
from datetime import timedelta
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytest.importorskip("mcp")
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from locket.mcp import build_server, serve
from locket.mcp_auth import GrantAuthority
from locket.service import MemoryService, Principal
from locket.store import Locket


@pytest.fixture
def anyio_backend():
    return "asyncio"


def init(path):
    Locket(path).init("Synthetic <test@example.invalid>")
    return str(path.resolve())


def data(result):
    assert not result.isError, result
    return result.structuredContent or json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_stdio_uses_cli_repo_and_reports_errors(tmp_path):
    repo = init(tmp_path / "correct-repo")
    env = dict(os.environ, LOCKET_HOME=str(tmp_path / "wrong-repo"))
    # Host selects read scope. The model cannot pass an identity or filesystem path.
    env["LOCKET_SCOPE_JSON"] = json.dumps({"about": ["person:qq-12345"],
                                         "max_sensitivity": "public", "can_write": True})
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "locket", "--repo", repo, "mcp"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as client:
            await client.initialize()
            out = await client.call_tool("memory_remember", {"text": "喜欢白饭", "about": "person:qq-12345",
                                                            "sensitivity": "public"})
            mid = data(out)["memory"]["id"]
            assert data(await client.call_tool("memory_get", {"id": mid}))["memory"]["text"] == "喜欢白饭"
            assert (await client.call_tool("memory_remember", {"text": "越权", "about": "self"})).isError
            assert (await client.call_tool("memory_recall", {"query": "", "k": 0})).isError
            assert data(await client.call_tool("memory_list", {"limit": 1}))["count"] == 1
    assert not (tmp_path / "wrong-repo").exists()


@pytest.mark.anyio
async def test_readonly_stdio_does_not_advertise_writers(tmp_path):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "locket", "--repo", init(tmp_path / "repo"), "mcp"],
        env=dict(os.environ, LOCKET_SCOPE_JSON=json.dumps({"can_write": False})))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=40)) as client:
            await client.initialize()
            names = {t.name for t in (await client.list_tools()).tools}
            assert "memory_recall" in names
            assert "memory_remember" not in names


@asynccontextmanager
async def http_server(server):
    import uvicorn
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    runner = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), log_level="critical"))
    task = asyncio.create_task(runner.serve(sockets=[sock]))
    try:
        with anyio.fail_after(10):
            while not runner.started:
                if task.done():
                    await task
                await asyncio.sleep(.02)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        runner.should_exit = True
        await asyncio.wait_for(task, 10)
        sock.close()


@asynccontextmanager
async def session(url, token):
    import httpx
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}) as http:
        async with streamable_http_client(url, http_client=http) as (r, w, _):
            async with ClientSession(r, w) as client:
                await client.initialize()
                yield client


@pytest.mark.anyio
async def test_http_two_tenants_scope_revocation_and_persistence(tmp_path):
    import httpx
    tokens = ["a" * 40, "b" * 40, "c" * 40]
    repos = [init(tmp_path / "alice"), init(tmp_path / "bob")]
    path = tmp_path / "grants.json"
    grants = [dict(sha256=hashlib.sha256(t.encode()).hexdigest(), user=u, repo=r,
                   scope={"about": ["self"], "max_sensitivity": "personal", "can_write": wr})
              for t, u, r, wr in zip(tokens, ["alice", "bob", "alice"], [*repos, repos[0]], [True, True, False])]
    path.write_text(json.dumps({"version": 1, "grants": grants}))
    auth = GrantAuthority(path)
    server = build_server(MemoryService(resolve=auth), authenticate=auth.authenticate)
    async with http_server(server) as url:
        async with httpx.AsyncClient() as http:
            assert (await http.post(url, json={})).status_code == 401
        async with session(url, tokens[0]) as alice, session(url, tokens[1]) as bob:
            am = data(await alice.call_tool("memory_remember", {"text": "Alice 喜欢白饭"}))["memory"]
            bm = data(await bob.call_tool("memory_remember", {"text": "Bob 喜欢面条"}))["memory"]
            assert (await bob.call_tool("memory_get", {"id": am["id"]})).isError
            assert (await alice.call_tool("memory_get", {"id": bm["id"]})).isError
            assert data(await alice.call_tool("memory_list", {}))["count"] == 1
            assert data(await bob.call_tool("memory_list", {}))["count"] == 1
        async with session(url, tokens[2]) as ro:
            assert data(await ro.call_tool("memory_get", {"id": am["id"]}))["memory"]["id"] == am["id"]
            assert (await ro.call_tool("memory_forget", {"id": am["id"]})).isError
            grants[2]["disabled"] = True
            path.write_text(json.dumps({"version": 1, "grants": grants}))
            async with httpx.AsyncClient() as http:
                assert (await http.post(url, headers={"Authorization": "Bearer " + tokens[2]}, json={})).status_code == 401
        async with session(url, tokens[0]) as alice:
            assert data(await alice.call_tool("memory_get", {"id": am["id"]}))["memory"]["text"] == "Alice 喜欢白饭"


def test_http_refuses_anonymous_network():
    from locket.store import LocketError
    with pytest.raises(LocketError):
        serve(transport="streamable-http")
