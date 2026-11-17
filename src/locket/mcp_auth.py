"""Host-owned MCP grants. Tokens select identities; tool arguments never do."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .scope import OWNER_SCOPE, Scope, SENSITIVITY_ORDER
from .service import Principal
from .store import Locket, LocketError


def scope_from_dict(value: dict | None) -> Scope:
    if value is None:
        return OWNER_SCOPE
    if not isinstance(value, dict) or set(value) - {"about", "max_sensitivity", "can_write"}:
        raise LocketError("无效的记忆授权范围")
    about = value.get("about", ["*"])
    level = value.get("max_sensitivity", "personal")
    write = value.get("can_write", False)
    if (not isinstance(about, list) or not about or len(about) > 64
            or any(not isinstance(x, str) or not 1 <= len(x) <= 200 for x in about)
            or level not in SENSITIVITY_ORDER[:-1] or type(write) is not bool):
        raise LocketError("无效的记忆授权范围")
    return Scope(about=tuple(about), max_sensitivity=level, can_write=write)


class GrantAuthority:
    """Reload a small admin-controlled file for every request (immediate revocation).

    Format: {"version":1,"grants":[{"sha256":...,"user":...,"repo":absolute,
    "agent":...,"scope":{...},"expires_at":unix_seconds}]}. No plaintext tokens.
    Repositories must already exist; requests cannot create or choose filesystem paths.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _grant(self, token: str) -> dict:
        if not isinstance(token, str) or not 32 <= len(token) <= 4096:
            raise LocketError("令牌无效或已撤销")
        try:
            raw = self.path.read_bytes()
            if len(raw) > 1024 * 1024:
                raise ValueError()
            data = json.loads(raw)
            if data.get("version") != 1 or not isinstance(data.get("grants"), list):
                raise ValueError()
            digest = hashlib.sha256(token.encode()).hexdigest()
            matches = [g for g in data["grants"] if g.get("sha256") == digest]
            if len(matches) != 1:
                raise ValueError()
            grant = matches[0]
            if grant.get("disabled", False) or grant.get("expires_at", float("inf")) <= time.time():
                raise ValueError()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", grant["user"]):
                raise ValueError()
            if not Path(grant["repo"]).is_absolute():
                raise ValueError()
            # A scope must be explicit in network grants: no accidental owner default.
            if not isinstance(grant["scope"], dict):
                raise ValueError()
            scope_from_dict(grant["scope"])
            return grant
        except (OSError, KeyError, ValueError, TypeError, AttributeError, LocketError):
            raise LocketError("令牌无效、已撤销或授权配置不可用") from None

    def authenticate(self, token: str) -> Principal:
        g = self._grant(token)
        # Namespace sessions by grant, including grants for different channels of a tenant.
        session = g.get("session")
        if session:
            namespace = json.dumps(g["scope"], sort_keys=True)
            session = "mcp-" + hashlib.sha256((token + namespace + str(session)).encode()).hexdigest()[:32]
        return Principal(user=g["user"], agent=str(g.get("agent", "mcp"))[:80],
                         session=session, token=token, scope=scope_from_dict(g["scope"]))

    def resolve(self, p: Principal) -> Locket:
        grant = self._grant(p.token)
        if p.user != grant["user"] or p.scope != scope_from_dict(grant["scope"]):
            raise LocketError("授权已变化，请重试")
        store = Locket(grant["repo"])
        if not store.exists():
            raise LocketError("记忆仓未初始化，请联系管理者")
        return store

    @staticmethod
    def release(store: Locket) -> None:
        store.close()

    def __call__(self, p: Principal) -> Locket:
        return self.resolve(p)
