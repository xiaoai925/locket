"""HTTP/JSON API over the memory service (ARCHITECTURE.md P1b).

The backend skeleton every front end (the Go+Vue web app, scripts, other agents) talks
to. Pure standard library: `http.server` + `json` + `urllib`, no new dependencies. It is
a thin adapter — all memory semantics live in `service.MemoryService`; this module only
does routing, auth header parsing, and error-code mapping.

Auth: `Authorization: Bearer <token>` is handed to an injectable `authenticate(token)`
callable that returns a `Principal` (default: a local single user). Headers
`X-Locket-Session` / `X-Locket-Agent` override the principal's session / agent, so one
process can serve many conversations.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from .service import MemoryService, Principal
from .store import LocketError


class _BadRequest(Exception):
    """A malformed request (bad JSON, missing required field) -> HTTP 400."""


def _default_auth(token: str | None) -> Principal:
    """Single-user default: no real identity, everyone is the local owner."""
    return Principal()


def _truthy(value) -> bool:
    return str(value).lower() in ("1", "true", "yes", "on")


class Handler(BaseHTTPRequestHandler):
    # Set per-server via make_server():
    service: MemoryService = None  # type: ignore[assignment]
    authenticate: Callable[[str | None], Principal] = staticmethod(_default_auth)

    server_version = "LocketAPI/0.2"

    def log_message(self, *args):  # silence the default stderr access log
        pass

    # ---- response helpers --------------------------------------------------

    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _principal(self) -> Principal:
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else None
        principal = self.authenticate(token)
        session = self.headers.get("X-Locket-Session")
        agent = self.headers.get("X-Locket-Agent")
        if session:
            principal.session = session
        if agent:
            principal.agent = agent
        return principal

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise _BadRequest("请求体不是合法 JSON")
        if not isinstance(data, dict):
            raise _BadRequest("请求体必须是 JSON 对象")
        return data

    # ---- dispatch ----------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        parts = [seg for seg in url.path.split("/") if seg]
        try:
            if method == "GET" and parts == ["healthz"]:
                return self._send(200, {"ok": True, "spec": "0.2"})
            try:
                principal = self._principal()
            except Exception as exc:  # noqa: BLE001 - any auth failure is a 401
                return self._send(401, {"error": f"认证失败：{exc}"})
            result = self._route(method, parts, parse_qs(url.query), principal)
            if result is None:
                return self._send(404, {"error": f"未知路由：{method} {url.path}"})
            self._send(200, result)
        except _BadRequest as exc:
            self._send(400, {"error": str(exc)})
        except LocketError as exc:
            self._send(400, {"error": str(exc)})
        except Exception:  # noqa: BLE001 - never leak a stack trace to the client
            self._send(500, {"error": "服务器内部错误"})

    def _route(self, method: str, parts: list[str], query: dict, p: Principal):
        s = self.service

        def q1(name: str, default=None):
            return query.get(name, [default])[0]

        if method == "GET":
            if parts == ["memories"]:
                return s.list(p, about=q1("about"), type=q1("type"),
                              include_inactive=_truthy(q1("include_inactive")))
            if parts == ["conflicts"]:
                return s.conflicts(p, status=q1("status", "open"))
            if parts == ["settings"]:
                return s.get_settings(p)
            if parts == ["pending"]:
                return s.pending(p)
            if parts == ["detect-conflicts"]:
                return s.detect_conflicts(p)
            if parts == ["views"]:
                return s.list_views(p)
            if len(parts) == 2 and parts[0] == "memory":
                return s.get(p, parts[1])
            if len(parts) == 3 and parts[0] == "memory" and parts[2] == "why":
                return s.why(p, parts[1])
            if len(parts) == 3 and parts[0] == "memory" and parts[2] == "history":
                return s.history(p, parts[1])
            return None

        # POST — always drain the body first
        body = self._body()
        if parts == ["recall"]:
            if not body.get("query"):
                raise _BadRequest("缺少 query")
            return s.recall(p, body["query"], k=int(body.get("k", 5)),
                            about=body.get("about"),
                            include_inactive=bool(body.get("include_inactive", False)))
        if parts == ["remember"]:
            if not body.get("text"):
                raise _BadRequest("缺少 text")
            return s.remember(p, body["text"], type=body.get("type", "fact"),
                              about=body.get("about", "self"), title=body.get("title"),
                              tags=body.get("tags"), importance=body.get("importance"),
                              confidence=body.get("confidence"), sensitivity=body.get("sensitivity"),
                              quote=body.get("quote"), valid_from=body.get("valid_from"))
        if parts == ["session", "end"]:
            return s.end_session(p)
        if parts == ["settings"]:
            return s.update_settings(p, body)
        if parts == ["approve"]:
            if not body.get("session"):
                raise _BadRequest("缺少 session")
            return s.approve(p, body["session"])
        if parts == ["views", "generate"]:
            return s.generate_views(p, body.get("name"))
        if len(parts) == 3 and parts[0] == "memory" and parts[2] == "update":
            return s.update(p, parts[1], text=body.get("text"),
                            fields=body.get("fields"), reason=body.get("reason"))
        if len(parts) == 3 and parts[0] == "memory" and parts[2] == "supersede":
            if not body.get("text"):
                raise _BadRequest("缺少 text")
            return s.supersede(p, parts[1], body["text"], effective=body.get("effective"),
                               title=body.get("title"), quote=body.get("quote"))
        if len(parts) == 3 and parts[0] == "memory" and parts[2] == "forget":
            return s.forget(p, parts[1], reason=body.get("reason"))
        if len(parts) == 3 and parts[0] == "conflict" and parts[2] == "resolve":
            return s.resolve(p, parts[1], note=body.get("note"))
        return None


def make_server(service: MemoryService, host: str = "127.0.0.1", port: int = 8723,
                authenticate: Callable[[str | None], Principal] | None = None) -> ThreadingHTTPServer:
    """Bind (but do not start) a threaded API server. `port=0` picks a free port."""
    handler = type("LocketHandler", (Handler,), {
        "service": service,
        "authenticate": staticmethod(authenticate or _default_auth),
    })
    return ThreadingHTTPServer((host, port), handler)


def serve(service: MemoryService | None = None, host: str = "127.0.0.1", port: int = 8723,
          authenticate: Callable[[str | None], Principal] | None = None, repo=None) -> None:
    """Run the API server until interrupted."""
    service = service or MemoryService(repo=repo)
    server = make_server(service, host, port, authenticate)
    bound_host, bound_port = server.server_address[:2]
    print(f"Locket API 监听 http://{bound_host}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
