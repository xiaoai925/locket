"""Framework-agnostic memory service: the shared core behind the HTTP API and MCP.

Maps the spec §8 agent tools to engine calls and returns JSON-able dicts. Knows nothing
about HTTP or MCP, so both adapters (webapi.py, mcp.py) sit on top of exactly this.

Multi-tenancy is a pluggable **resolver**: given an authenticated principal, return the
`Locket` repo to operate on. The default resolver always returns one repo (single-user,
dev, CLI). The cloud resolver (`tenancy.py`) maps each Gitea user to their own repo.
"""
from __future__ import annotations

import builtins
from contextvars import ContextVar
from functools import wraps
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .gitrepo import GitError
from .memory import Memory
from .scope import OWNER_SCOPE, Scope, SENSITIVITY_ORDER
from .store import Locket, LocketError, Session, short_id
from .locking import repository_lock

API_AGENT = "api"
_operation_store = ContextVar("locket_operation_store", default=None)


def serialized(fn):
    """Resolve once and coordinate the entire operation across service processes."""
    @wraps(fn)
    def invoke(self, p, *args, **kwargs):
        store = self.repo(p)
        with repository_lock(store.root):
            token = _operation_store.set((self, p, store))
            try:
                return fn(self, p, *args, **kwargs)
            finally:
                _operation_store.reset(token)
                release = getattr(self._resolve, "release", None)
                if release:
                    release(store)
    return invoke


@dataclass
class Principal:
    """Who is making the request, after authentication.

    `user` is the tenant (e.g. a Gitea login, or "local"); `agent` is the writer label
    recorded in commits; `session` is the conversation id — when set, writes go to that
    session branch and are NOT auto-merged (the agent ends the session explicitly); when
    None, each write is immediately merged into main (like the CLI without --session).
    `scope` is what this principal may read/write (spec §8/§9); it defaults to full
    owner access, so single-user/CLI behaviour is unchanged.
    """
    user: str = "local"
    agent: str = API_AGENT
    session: str | None = None
    # The bearer token the request arrived with, if any. Carried so a cloud resolver
    # can access this user's own Gitea repo on their behalf. Never logged/serialized.
    token: str | None = field(default=None, repr=False)
    scope: Scope = field(default_factory=lambda: OWNER_SCOPE)


def mem_dict(m: Memory, *, body: bool = True) -> dict:
    """A memory as a JSON-able dict (same shape for HTTP and MCP)."""
    d = {
        "id": m.id, "short": short_id(m.id), "type": m.type, "about": m.about,
        "title": m.title, "path": m.path, "active": m.active, "importance": m.importance,
    }
    for key in ("created", "valid_from", "valid_to", "confidence", "sensitivity",
                "tags", "supersedes", "derived_from", "sources"):
        if key in m.meta:
            d[key] = m.meta[key]
    if body:
        d["text"] = m.body
    return d


# A resolver turns a principal into the repo to operate on.
Resolver = Callable[[Principal], Locket]


def single_repo_resolver(repo: Path | str | None = None) -> Resolver:
    """Default tenancy: everyone shares one repo (dev / single user / CLI)."""
    store = Locket(repo)

    def resolve(_: Principal) -> Locket:
        return store

    return resolve


class MemoryService:
    """The spec §8 tool surface, over a resolver. Raises LocketError on bad input;
    adapters translate that into an HTTP 4xx or an MCP tool error."""

    def __init__(self, resolve: Resolver | None = None, repo: Path | str | None = None):
        self._resolve = resolve or single_repo_resolver(repo)

    def repo(self, p: Principal) -> Locket:
        active = _operation_store.get()
        if active is not None and active[0] is self and active[1] is p:
            return active[2]
        return self._resolve(p)

    def _session(self, p: Principal) -> Session | None:
        return Session(p.agent, p.session) if p.session else None

    def _write_lock(self, store: Locket):
        return repository_lock(store.root)

    # ---- scope enforcement (spec §8/§9) ------------------------------------

    def _readable(self, p: Principal, m: Memory) -> bool:
        return p.scope.can_read(about=m.about, sensitivity=m.meta.get("sensitivity"))

    def _visible(self, p: Principal, items: list[Memory]) -> list[Memory]:
        return [m for m in items if self._readable(p, m)]

    def _get_scoped(self, p: Principal, id: str) -> tuple[Locket, Memory]:
        """Fetch a memory, refusing (as 'not found') anything out of read scope,
        so existence of out-of-scope memories is not leaked."""
        store = self.repo(p)
        m = store.get(id, self._session(p))
        if not self._readable(p, m):
            raise LocketError("找不到记忆或无权访问")
        return store, m

    def _guard_write(self, p: Principal, sensitivity: str | None = None,
                     about: str | None = None) -> None:
        if not p.scope.can_write:
            raise LocketError("无写入权限")
        if sensitivity is not None and sensitivity not in SENSITIVITY_ORDER:
            raise LocketError("无效的敏感级别")
        if sensitivity is not None and not p.scope.allows_write_sensitivity(sensitivity):
            raise LocketError(f"无权写入 {sensitivity} 级别的记忆")
        if about is not None and not p.scope.can_write_about(about):
            raise LocketError("无权写入该记忆范围")

    def _owner(self, p: Principal) -> None:
        if p.scope != OWNER_SCOPE:
            raise LocketError("需要记忆仓所有者权限")

    def _conflict_visible(self, p: Principal, store: Locket, conflict: Memory) -> bool:
        if p.scope == OWNER_SCOPE:
            return True
        members = conflict.meta.get("members") or []
        if not members:
            return False
        try:
            return all(self._readable(p, store.get(mid, self._session(p))) for mid in members)
        except LocketError:
            return False

    def _safe_history(self, p: Principal, store: Locket, memory: Memory) -> list:
        entries = store.history(memory)
        if p.scope == OWNER_SCOPE:
            return entries
        # Git commit subjects can describe another record touched by the same merge.
        # Restricted callers receive no raw repository history or commit subjects.
        return []

    # ---- auto-sync (P7: cloud/hybrid mode, best-effort, offline-safe) -------

    def _auto_pull(self, store: Locket) -> None:
        """In cloud/hybrid mode with auto_sync on, freshen from the remote before a read.
        Best-effort: offline or no-remote just serves the local copy (never raises)."""
        if not store.settings().should_auto_sync() or not store.git.get_remote_url("origin"):
            return
        try:
            store.pull()
        except (LocketError, GitError):
            pass

    def _auto_push(self, store: Locket) -> None:
        """After a write in cloud/hybrid mode with auto_sync on, publish to the remote.
        Best-effort: offline just leaves it committed locally for the next sync."""
        if not (getattr(self._resolve, "auto_push", False) or store.settings().should_auto_sync()) or not store.git.get_remote_url("origin"):
            return
        try:
            store.push()
        except (LocketError, GitError):
            pass

    # ---- read --------------------------------------------------------------

    @serialized
    def recall(self, p: Principal, query: str, k: int = 5, about: str | None = None,
               include_inactive: bool = False) -> dict:
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 4000:
            raise LocketError("查询长度必须为 1 到 4000 字符")
        if type(k) is not int or not 1 <= k <= 50:
            raise LocketError("k 必须为 1 到 50 的整数")
        store = self.repo(p)
        self._auto_pull(store)
        boost = ("self",) if about is None else ()
        # Bound candidate work, then filter; a narrow scope may return fewer than k.
        hits = store.recall(query, k=k * 4 if p.scope.about != ("*",) or
                            p.scope.max_sensitivity != "sensitive" else k,
                            session=self._session(p), about=about,
                            include_inactive=include_inactive, boost_about=boost)
        hits = [h for h in hits if self._readable(p, h.memory)][:k]
        open_conflicts = [c for c in store.conflicts(self._session(p))
                          if self._conflict_visible(p, store, c)]
        return {
            "hits": [{**mem_dict(h.memory), "score": round(h.score, 4), "matched": h.matched}
                     for h in hits],
            "open_conflicts": [self._conflict(c) for c in open_conflicts[:5]],
        }

    @serialized
    def get(self, p: Principal, id: str) -> dict:
        _, m = self._get_scoped(p, id)
        return {"memory": mem_dict(m)}

    @serialized
    def list(self, p: Principal, about: str | None = None, type: str | None = None,
             include_inactive: bool = False, limit: int | None = None, offset: int = 0) -> dict:
        if (builtins.type(offset) is not int or offset < 0 or
                (limit is not None and (builtins.type(limit) is not int or not 1 <= limit <= 200))):
            raise LocketError("分页范围无效（limit 为 1 到 200）")
        store = self.repo(p)
        self._auto_pull(store)
        items = self._visible(p, store.memories(self._session(p), include_inactive=include_inactive))
        if about:
            items = [m for m in items if m.about == about]
        if type:
            items = [m for m in items if m.type == type]
        items.sort(key=lambda m: (m.about, m.path or ""))
        page = items[offset:offset + limit] if limit is not None else items[offset:]
        return {"memories": [mem_dict(m, body=False) for m in page], "count": len(items),
                "next_offset": offset + len(page) if offset + len(page) < len(items) else None}

    @serialized
    def why(self, p: Principal, id: str) -> dict:
        store, m = self._get_scoped(p, id)
        from . import signing
        history = self._safe_history(p, store, m)
        latest = history[0].sha if history else None
        return {
            "memory": mem_dict(m),
            "sources": m.meta.get("sources") or [],
            "supersedes": m.meta.get("supersedes") or [],
            "derived_from": m.meta.get("derived_from") or [],
            "signature": signing.verify_commit(store.git, latest) if latest else None,
            "history": [self._entry(e) for e in history],
        }

    @serialized
    def history(self, p: Principal, id: str) -> dict:
        store, m = self._get_scoped(p, id)
        return {"id": m.id, "versions": [self._entry(e) for e in self._safe_history(p, store, m)]}

    @serialized
    def conflicts(self, p: Principal, status: str = "open") -> dict:
        store = self.repo(p)
        items = [c for c in store.conflicts(self._session(p), status=status)
                 if self._conflict_visible(p, store, c)]
        return {"conflicts": [self._conflict(c) for c in items]}

    @serialized
    def detect_conflicts(self, p: Principal) -> dict:
        """Read-only preview of semantic duplicates/contradictions (spec §6.2)."""
        self._owner(p)
        return {"findings": self.repo(p).detect_conflicts()}

    # ---- views (spec §9.2 shared read-only subsets) ------------------------

    @serialized
    def list_views(self, p: Principal) -> dict:
        self._owner(p)
        from . import views
        return {"views": [{"name": n, "sha": sha[:7]} for n, sha in views.list_views(self.repo(p))]}

    @serialized
    def generate_views(self, p: Principal, name: str | None = None) -> dict:
        self._owner(p)
        from . import views
        self._guard_write(p)  # generating a view ref is an owner action
        store = self.repo(p)
        with self._write_lock(store):
            results = [views.generate_view(store, name)] if name else views.generate_all(store)
        return {"results": [{"name": r.name, "ref": r.ref, "count": r.count} for r in results]}

    # ---- write (session branch; auto-merged when no session is held) -------

    @serialized
    def remember(self, p: Principal, text: str, type: str = "fact", about: str = "self",
                 title: str | None = None, tags: list[str] | None = None,
                 importance: float | None = None, confidence: float | None = None,
                 sensitivity: str | None = None, quote: str | None = None,
                 valid_from: str | None = None) -> dict:
        self._guard_write(p, sensitivity or "personal", about)
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 16000:
            raise LocketError("记忆长度必须为 1 到 16000 字符")
        store = self.repo(p)
        with self._write_lock(store):
            session = self._ensure_session(p)
            memory, similar = store.remember(
                text, session, type=type, about=about, title=title, tags=tags,
                importance=importance, confidence=confidence, sensitivity=sensitivity,
                quote=quote, valid_from=valid_from)
            merged = self._maybe_merge(store, p, session)
            return {"memory": mem_dict(memory),
                    "similar": [{**mem_dict(h.memory, body=False), "score": round(h.score, 4)}
                                for h in similar if self._readable(p, h.memory)],
                    "merged": merged}

    @serialized
    def update(self, p: Principal, id: str, text: str | None = None,
               fields: dict | None = None, reason: str | None = None) -> dict:
        if text is not None and (not isinstance(text, str) or not 1 <= len(text.strip()) <= 16000):
            raise LocketError("记忆长度必须为 1 到 16000 字符")
        self._guard_write(p, (fields or {}).get("sensitivity"))
        store, current = self._get_scoped(p, id)
        self._guard_write(p, (fields or {}).get("sensitivity", current.meta.get("sensitivity")) or "personal",
                          (fields or {}).get("about", current.about))
        with self._write_lock(store):
            session = self._ensure_session(p)
            m = store.update(id, session, text=text, fields=fields, reason=reason)
            return {"memory": mem_dict(m), "merged": self._maybe_merge(store, p, session)}

    @serialized
    def supersede(self, p: Principal, id: str, text: str, effective: str | None = None,
                  title: str | None = None, quote: str | None = None) -> dict:
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 16000:
            raise LocketError("记忆长度必须为 1 到 16000 字符")
        self._guard_write(p)
        store, current = self._get_scoped(p, id)
        self._guard_write(p, current.meta.get("sensitivity") or "personal", current.about)
        with self._write_lock(store):
            session = self._ensure_session(p)
            old, new = store.supersede(id, text, session, effective=effective, title=title, quote=quote)
            return {"old": mem_dict(old), "new": mem_dict(new),
                    "merged": self._maybe_merge(store, p, session)}

    @serialized
    def forget(self, p: Principal, id: str, reason: str | None = None) -> dict:
        self._guard_write(p)
        store, current = self._get_scoped(p, id)
        self._guard_write(p, current.meta.get("sensitivity") or "personal", current.about)
        with self._write_lock(store):
            session = self._ensure_session(p)
            m = store.forget(id, session, reason)
            return {"forgot": mem_dict(m, body=False), "merged": self._maybe_merge(store, p, session)}

    @serialized
    def resolve(self, p: Principal, id: str, note: str | None = None) -> dict:
        # Resolving a conflict is an owner action, committed straight to main.
        self._owner(p)
        self._guard_write(p)
        store = self.repo(p)
        matches = [c for c in store.conflicts(status="") if c.id.lower().endswith(id.lower())]
        if len(matches) != 1 or not self._conflict_visible(p, store, matches[0]):
            raise LocketError("找不到冲突或无权访问")
        with self._write_lock(store):
            c = store.resolve(id, note)
        return {"conflict": self._conflict(c)}

    @serialized
    def end_session(self, p: Principal) -> dict:
        self._guard_write(p)
        if not p.session:
            raise LocketError("没有进行中的会话可结束")
        store = self.repo(p)
        with self._write_lock(store):
            result = store.end_session(Session(p.agent, p.session))
        if result.merged:
            self._auto_push(store)
        return {"merged": result.merged, "pending": result.pending, "commit": result.commit,
                "changed": result.changed, "conflicts": result.conflicts}

    @serialized
    def approve(self, p: Principal, session_label: str) -> dict:
        self._owner(p)
        """Owner approves a pending agent session (spec §6.1). Requires write scope."""
        self._guard_write(p)
        agent, _, sid = session_label.partition("/")
        if not sid:
            agent, sid = p.agent, session_label
        store = self.repo(p)
        with self._write_lock(store):
            result = store.approve(Session(agent, sid))
        if result.merged:
            self._auto_push(store)
        return {"merged": result.merged, "commit": result.commit,
                "changed": result.changed, "conflicts": result.conflicts}

    @serialized
    def pending(self, p: Principal) -> dict:
        self._owner(p)
        """Session branches awaiting the owner's approval."""
        return {"pending": [{"session": s.label, "sha": sha[:7]}
                            for s, sha in self.repo(p).pending()]}

    # ---- device settings (P7: the phone's "how I use memory" screen) -------

    @serialized
    def get_settings(self, p: Principal) -> dict:
        self._owner(p)
        """This device's memory settings for the phone UI (token redacted)."""
        return {"settings": self.repo(p).settings().to_public_dict()}

    @serialized
    def update_settings(self, p: Principal, patch: dict) -> dict:
        self._owner(p)
        """Apply the user's settings changes from the phone and return the new state."""
        if not isinstance(patch, dict):
            raise LocketError("设置必须是一个对象")
        return {"settings": self.repo(p).save_settings(patch).to_public_dict()}

    # ---- helpers -----------------------------------------------------------

    def _ensure_session(self, p: Principal) -> Session:
        """A write always happens on a session branch; make an ephemeral one if the
        caller holds none (it is merged immediately by _maybe_merge)."""
        if p.session:
            return Session(p.agent, p.session)
        import uuid
        return Session(p.agent, "api-" + uuid.uuid4().hex[:12])

    def _maybe_merge(self, store: Locket, p: Principal, session: Session) -> bool:
        """When the caller holds no session, the owner is editing their own repo
        directly, so publish to main at once (bypassing agent-PR approval)."""
        if p.session:
            return False
        merged = store.end_session(session, owner=p.scope == OWNER_SCOPE).merged
        if merged:
            self._auto_push(store)
        return merged

    def _entry(self, e) -> dict:
        return {"sha": e.sha[:7], "date": e.date[:16], "author": e.author,
                "subject": e.subject, "op": e.op}

    def _conflict(self, c: Memory) -> dict:
        return {"id": c.id, "short": short_id(c.id), "status": c.meta.get("status"),
                "kind": c.meta.get("kind"), "path": c.meta.get("path"),
                "question": c.meta.get("question"), "members": c.meta.get("members") or []}
