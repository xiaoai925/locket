"""Locket memory repository: PMF operations on top of a plain git repository.

Layout of refs (spec §6.1):
  refs/heads/main                     long-term memory, checked out so people can read it
  refs/heads/session/<agent>/<id>     one conversation's writes, merged into main at the end
Derived, never-committed data lives in .git/pmf/ (spec §3).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .gitrepo import Git, GitError, Identity
from .index import Index
from .ids import new_ulid
from . import crypto
from . import semantic
from . import embed as embedding
from . import settings as device_settings
from .vector import VectorIndex
from .memory import (CORE_TYPES, PROTECTED_FIELDS, SPEC_VERSION, Memory, default_path,
                     load_yaml, now_iso, parse, shorten, today, validate_about)
from .search import bm25

MAIN = "refs/heads/main"
REMOTE = "origin"
REMOTE_MAIN = "refs/remotes/origin/main"
SESSION_PREFIX = "refs/heads/session/"
CONFIG_PATH = ".pmf/config.toml"
TOMBSTONES_PATH = ".pmf/tombstones.jsonl"
RECIPIENTS_PATH = crypto.RECIPIENTS_PATH
SENSITIVITY = ("public", "personal", "sensitive", "secret")
MERGER = Identity("locket", "merge@locket.local")


class LocketError(RuntimeError):
    pass


def default_home() -> Path:
    return Path(os.environ.get("LOCKET_HOME") or Path.home() / ".locket")


def _clean(part: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", part).strip("-.")
    return cleaned or "unknown"


@dataclass(frozen=True)
class Session:
    agent: str
    id: str

    def __post_init__(self):
        object.__setattr__(self, "agent", _clean(self.agent))
        object.__setattr__(self, "id", _clean(self.id))

    @property
    def ref(self) -> str:
        return f"{SESSION_PREFIX}{self.agent}/{self.id}"

    @property
    def label(self) -> str:
        return f"{self.agent}/{self.id}"

    @property
    def identity(self) -> Identity:
        return Identity(self.agent, f"{self.agent}@agents.locket.local")


@dataclass
class Hit:
    memory: Memory
    score: float
    matched: int


@dataclass
class MergeResult:
    session: Session
    merged: bool
    commit: str | None = None
    changed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    worktree_synced: bool = True
    pending: bool = False  # left unmerged awaiting owner approval (spec §6.1)


@dataclass
class SyncResult:
    # pull: "up-to-date" | "fast-forward" | "merge" | "created"
    # push: "up-to-date" | "pushed" | "rejected"
    status: str
    commit: str | None = None
    changed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    worktree_synced: bool = True
    detail: str = ""


def short_id(memory_id: str) -> str:
    return memory_id[-8:].lower()


def is_memory_path(path: str) -> bool:
    return (path.endswith(".md") and not path.startswith((".", "conflicts/"))
            and "/" in path)


class Locket:
    def __init__(self, root: Path | str | None = None,
                 embedder: "embedding.Embedder | None" = None):
        self.root = Path(root) if root else default_home()
        self.git = Git(self.root)
        self._index: Index | None = None
        self._vectors: VectorIndex | None = None
        # Semantic recall is opt-in; when no embedder is injected we read policy and
        # fall back to the always-available offline one, or disable (pure BM25).
        self._embedder = embedder
        self._embedder_resolved = embedder is not None
        self._settings: "device_settings.DeviceSettings | None" = None

    # ---- setup -------------------------------------------------------------

    def close(self) -> None:
        """Release derived database handles; the Git files remain authoritative."""
        for name in ("_index", "_vectors"):
            index = getattr(self, name, None)
            if index is not None:
                index.close()
                setattr(self, name, None)

    @property
    def state_dir(self) -> Path:
        return self.root / ".git" / "pmf"

    def exists(self) -> bool:
        return (self.root / ".git").is_dir() and self.git.rev_parse(MAIN) is not None

    def require(self) -> None:
        if not self.exists():
            raise LocketError(f"{self.root} 不是 Locket 记忆仓库，先运行 locket init")

    def init(self, owner: str | None = None) -> bool:
        """Create the repository. Returns False if it already existed."""
        if self.exists():
            return False
        self.root.mkdir(parents=True, exist_ok=True)
        if not (self.root / ".git").is_dir():
            self.git.run("init", "-q", "-b", "main")
        owner = owner or self._git_owner()
        config = (
            f'spec = "{SPEC_VERSION}"\n'
            f'implementation = "locket {__version__}"\n'
            f"owner = {json.dumps(owner, ensure_ascii=False)}\n"
            'default_language = "zh-CN"\n\n'
            "[policy]\n"
            "main_requires_approval = false   # 会话结束时自动合并进 main\n"
            'auto_resolve = ["supersede"]\n'
        )
        files = {CONFIG_PATH: config.encode("utf-8"), TOMBSTONES_PATH: b""}
        self.git.commit_changes(MAIN, files, _message("init", "创建记忆仓库", None),
                                self.owner_identity(owner))
        if self.git.head_branch() == MAIN:
            self.git.sync_worktree(None, self.git.rev_parse(MAIN))
        return True

    def _git_owner(self) -> str:
        name = self.git.proc("config", "user.name").stdout.decode().strip() or "owner"
        email = self.git.proc("config", "user.email").stdout.decode().strip() or "owner@locket.local"
        return f"{name} <{email}>"

    def config(self, at: str = MAIN) -> dict:
        data = self.git.show(at, CONFIG_PATH)
        return tomllib.loads(data.decode("utf-8")) if data else {}

    def owner_identity(self, owner: str | None = None) -> Identity:
        owner = owner or str(self.config().get("owner", "owner <owner@locket.local>"))
        match = re.fullmatch(r"\s*(.*?)\s*<(.+)>\s*", owner)
        return Identity(match.group(1), match.group(2)) if match else Identity(owner, "owner@locket.local")

    # ---- reading -----------------------------------------------------------

    def view(self, session: Session | None = None) -> dict[str, str]:
        """Files as the session sees them: main plus the session's own changes."""
        self.require()
        main = self.git.rev_parse(MAIN)
        tip = self.git.rev_parse(session.ref) if session else None
        if not tip:
            return self.git.tree_files(main)
        files, _ = self._three_way(self.git.merge_base(main, tip), main, tip)
        return files

    def tombstoned(self, files: dict[str, str]) -> set[str]:
        sha = files.get(TOMBSTONES_PATH)
        if not sha:
            return set()
        data = self.git.read_blobs([sha]).get(sha, b"")
        ids = set()
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                ids.add(json.loads(line)["id"])
        return ids

    @property
    def index(self) -> Index:
        if self._index is None:
            self._index = Index(self.state_dir / "index.sqlite")
        return self._index

    def settings(self) -> "device_settings.DeviceSettings":
        """This device's user-chosen settings (mode / cloud / embedding / privacy).

        Device-local, read from state_dir; defaults when unset. Cached per instance."""
        if self._settings is None:
            self._settings = device_settings.load(self.state_dir / device_settings.SETTINGS_FILE)
        return self._settings

    def save_settings(self, patch: dict) -> "device_settings.DeviceSettings":
        """Apply a partial settings update (from the phone UI) and re-resolve derived state."""
        self._settings = device_settings.update(
            self.state_dir / device_settings.SETTINGS_FILE, patch)
        self._embedder_resolved = False   # embedding backend may have changed
        self._embedder = None
        self._vectors = None
        s = self._settings
        if s.cloud.configured():          # keep the git remote in step with the chosen cloud
            try:
                self.git.set_remote(f"{s.cloud.url.rstrip('/')}/{s.cloud.repo}.git", REMOTE)
            except GitError:
                pass
        return self._settings

    def embedder(self) -> "embedding.Embedder | None":
        """The active embedder for semantic recall, or None for pure BM25.

        Resolution order: an injected embedder wins; else device settings
        (embedding.backend: local→offline HashingEmbedder; model/cloud expect a host-
        injected embedder, so without one they stay None); else repo policy
        semantic_recall as a fallback. Keeps the offline default and low-cost promise."""
        if not self._embedder_resolved:
            backend = self.settings().embedding.normalized()
            if backend == "local":
                self._embedder = embedding.default_embedder()
            elif backend == "model":
                try:
                    from . import embed_model
                    self._embedder = embed_model.LazyModelEmbedder(
                        self.settings().embedding.model or embed_model.DEFAULT_MODEL)
                except Exception:  # noqa: BLE001 — model lib missing → fall back to BM25
                    self._embedder = None
            elif backend == "cloud":
                self._embedder = None  # a cloud backend must be injected by the host
            else:
                policy = self.config().get("policy", {}) or {}
                mode = str(policy.get("semantic_recall", "off")).lower()
                if mode in ("on", "local", "hashing", "true", "1", "yes"):
                    self._embedder = embedding.default_embedder()
            self._embedder_resolved = True
        return self._embedder

    @property
    def vectors(self) -> VectorIndex | None:
        emb = self.embedder()
        if emb is None:
            return None
        if self._vectors is None:
            self._vectors = VectorIndex(self.state_dir / "vectors.sqlite", emb.dim)
        return self._vectors

    def _main_entries(self, main: str) -> list[tuple[str, Memory]]:
        """Parse main's memory files once, for (re)building the derived index."""
        files = self.git.tree_files(main)
        paths = [p for p in files if is_memory_path(p)]
        blobs = self.git.read_blobs([files[p] for p in paths])
        gone = self.tombstoned(files)
        entries = []
        for path in paths:
            try:
                memory = parse(blobs[files[path]], path)
            except (ValueError, KeyError, UnicodeDecodeError):
                continue
            if memory.id in gone:
                continue
            entries.append((files[path], memory))
        return entries

    def _ensure_index(self) -> None:
        """Keep the derived read index in step with the current main (rebuild if stale)."""
        main = self.git.rev_parse(MAIN)
        if not main or self.index.built_at == main:
            return
        entries = self._main_entries(main)
        self.index.rebuild(entries, main)
        self._rebuild_vectors(entries, main)

    def _rebuild_vectors(self, entries: list[tuple[str, Memory]], main: str) -> None:
        """Recompute the derived vector index from the same parsed entries.
        No-op when semantic recall is off. Embedding is cheap for the default
        offline embedder; a costly backend can add blob-keyed caching later."""
        vec = self.vectors
        if vec is None:
            return
        emb = self.embedder()
        rows = []
        for blob, m in entries:
            text = m.search_text()
            rows.append((m.id, blob, emb.embed(text) if text.strip() else [0.0] * emb.dim))
        vec.rebuild(rows, main, emb.name)

    def reindex(self) -> int:
        """Rebuild the derived index(es) from scratch (after corruption or migration)."""
        self.require()
        main = self.git.rev_parse(MAIN)
        entries = self._main_entries(main) if main else []
        self.index.rebuild(entries, main or "")
        if self.vectors is not None:
            self._rebuild_vectors(entries, main or "")
        return len(entries)

    def memories(self, session: Session | None = None, files: dict[str, str] | None = None,
                 include_inactive: bool = True) -> list[Memory]:
        """Current memories, served from the derived index (spec §3 derived data).

        The common main view is answered straight from the index without parsing
        any file; a session view reuses indexed blobs and parses only the session's
        own (not-yet-indexed) changes."""
        self.require()
        self._ensure_index()
        if session is None and files is None:
            return self.index.all(include_inactive=include_inactive)
        files = self.view(session) if files is None else files
        paths = [p for p in files if is_memory_path(p)]
        known = self.index.by_blobs([files[p] for p in paths])
        blobs = self.git.read_blobs([files[p] for p in paths if files[p] not in known])
        gone = self.tombstoned(files)
        result = []
        for path in paths:
            sha = files[path]
            memory = known.get(sha)
            if memory is None:
                try:
                    memory = parse(blobs[sha], path)
                except (ValueError, KeyError, UnicodeDecodeError):
                    continue  # not a PMF memory file; left untouched
            elif memory.path != path:
                memory = Memory(dict(memory.meta), memory.body, path)
            if memory.id in gone or (not include_inactive and not memory.active):
                continue
            result.append(memory)
        return result

    def get(self, ref: str, session: Session | None = None,
            files: dict[str, str] | None = None) -> Memory:
        """Find a memory by full id or a unique id suffix (the 8-char short id)."""
        ref = ref.strip().upper()
        if len(ref) < 6:
            raise LocketError("id 至少要 6 个字符")
        found = [m for m in self.memories(session, files) if m.id.upper().endswith(ref)]
        if not found:
            raise LocketError(f"找不到记忆 {ref.lower()}")
        if len(found) > 1:
            raise LocketError(f"{ref.lower()} 匹配到多条记忆，请给出更长的 id")
        return found[0]

    def recall(self, query: str, k: int = 5, session: Session | None = None,
               about: str | None = None, include_inactive: bool = False,
               boost_about: tuple[str, ...] = ()) -> list[Hit]:
        """Hybrid recall: BM25 keyword score blended with vector semantic similarity
        (when semantic recall is on), so exact words and "by meaning" both count.
        Falls back to pure BM25 when no embedder is active (offline-safe default off)."""
        pool = [m for m in self.memories(session, include_inactive=include_inactive)
                if not about or m.about == about]
        if not pool:
            return []
        # BM25 keyword scores, normalized to 0..1 so it can blend with cosine.
        bm = {}
        matched_of = {}
        raw = bm25(query, [m.search_text() for m in pool])
        top_bm = max((s for _, s, _ in raw), default=0.0) or 1.0
        for i, score, matched in raw:
            bm[i] = score / top_bm
            matched_of[i] = matched
        # Vector semantic scores (optional), aligned to the same pool by memory id.
        # allowed_ids restricts to the pool, which already reflects session/scope/active
        # filtering, so session-only memories simply fall back to their BM25 score.
        sem = {}
        vec, emb = self.vectors, self.embedder()
        if vec is not None and emb is not None:
            idx_of = {m.id: i for i, m in enumerate(pool)}
            for vh in vec.search(emb.embed(query), k=max(k * 4, 20),
                                 allowed_ids=set(idx_of)):
                if vh.id in idx_of:
                    sem[idx_of[vh.id]] = vh.score
        alpha = 0.5 if sem else 0.0  # weight on semantics only when we have vectors
        hits = []
        for i in set(bm) | set(sem):
            memory = pool[i]
            blended = (1 - alpha) * bm.get(i, 0.0) + alpha * sem.get(i, 0.0)
            blended *= 0.5 + memory.importance
            if memory.about in boost_about:
                blended *= 1.3
            hits.append(Hit(memory, blended, matched_of.get(i, 1 if i in sem else 0)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]

    def conflicts(self, session: Session | None = None, status: str = "open") -> list[Memory]:
        files = self.view(session)
        paths = [p for p in files if p.startswith("conflicts/") and p.endswith(".md")]
        blobs = self.git.read_blobs([files[p] for p in paths])
        result = []
        for path in paths:
            try:
                item = parse(blobs[files[path]], path)
            except ValueError:
                continue
            if not status or item.meta.get("status") == status:
                result.append(item)
        return sorted(result, key=lambda c: c.id)

    def detect_conflicts(self, detector=None) -> list[dict]:
        """Read-only scan of main for semantic duplicates/contradictions (spec §6.2).

        Previews what the detector would flag, without recording anything — useful on
        an already-merged repo. Pairs already recorded as open conflicts are skipped.
        Defaults to the policy-configured detector; pass one to override."""
        self.require()
        detector = detector or self._configured_detector()
        if isinstance(detector, semantic.NullDetector):
            return []
        mems = self.memories(include_inactive=False)
        by_about: dict[str, list[Memory]] = {}
        for m in mems:
            by_about.setdefault(m.about, []).append(m)
        existing = {frozenset(c.meta.get("members") or [])
                    for c in self.conflicts(status="") if len(c.meta.get("members") or []) == 2}
        seen = set(existing)
        out = []
        for mem in mems:
            candidates = [(o, 0.0) for o in by_about.get(mem.about, []) if o.id != mem.id]
            for f in detector.judge(mem, candidates):
                pair = frozenset([mem.id, f.other_id])
                if pair in seen:
                    continue
                seen.add(pair)
                out.append({"a": mem.id, "b": f.other_id, "kind": f.kind,
                            "question": f.question, "confidence": round(float(f.confidence), 3)})
        return out

    def history(self, memory: Memory) -> list:
        return self.git.log(MAIN, memory.path, follow=True)

    def reveal(self, ref: str, private_key: str, session: Session | None = None) -> str:
        """Decrypt a secret memory locally with a recipient's private key.

        Decryption is a client-side operation on purpose: the private key is never
        sent to the server, so a cloud/Gitea host that stores the ciphertext can
        never read it. A non-secret memory is returned as-is."""
        memory = self.get(ref, session)
        if not crypto.is_encrypted(memory.body):
            return memory.body
        if not crypto.available():
            raise LocketError("解密需要：pip install 'locket-memory[secret]'")
        try:
            return crypto.decrypt(memory.body.encode("utf-8"), private_key).decode("utf-8")
        except Exception as e:  # noqa: BLE001
            raise LocketError(f"解密失败：{e}") from None

    def sessions(self) -> list[tuple[Session, str]]:
        found = []
        for ref, sha in self.git.list_refs(SESSION_PREFIX.rstrip("/")):
            rest = ref[len(SESSION_PREFIX):]
            if "/" in rest:
                agent, sid = rest.split("/", 1)
                found.append((Session(agent, sid), sha))
        return found

    # ---- writing (session branch) -----------------------------------------

    def _commit(self, session: Session, changes: dict, op: str, summary: str,
                details: str | None = None) -> str:
        self.require()
        message = _message(op, summary, session, details)
        return self.git.commit_changes(session.ref, changes, message, session.identity,
                                       base=self.git.rev_parse(MAIN))

    def remember(self, text: str, session: Session, *, type: str = "fact", about: str = "self",
                 title: str | None = None, tags: list[str] | None = None,
                 importance: float | None = None, confidence: float | None = None,
                 sensitivity: str | None = None, quote: str | None = None,
                 valid_from: str | None = None) -> tuple[Memory, list[Hit]]:
        """Add a memory. Also returns existing memories that look like the same thing."""
        text = text.strip()
        if not text:
            raise LocketError("记忆内容不能为空")
        if type not in CORE_TYPES and not type.startswith("x-"):
            raise LocketError(f"type 必须是 {'、'.join(CORE_TYPES)} 之一，或以 x- 开头的扩展类型")
        if sensitivity and sensitivity not in SENSITIVITY:
            raise LocketError(f"sensitivity 必须是 {'、'.join(SENSITIVITY)} 之一")
        if sensitivity == "secret":
            if not crypto.available():
                raise LocketError("secret 级别需要加密支持：pip install 'locket-memory[secret]'")
            recipients = crypto.Recipients.load(self.git.show(MAIN, RECIPIENTS_PATH))
            if not recipients.keys:
                raise LocketError("secret 级别需要先在 .pmf/recipients.toml 配置加密接收者公钥")
        try:
            validate_about(about)
        except ValueError as e:
            raise LocketError(str(e)) from None

        files = self.view(session)
        similar = [h for h in self.recall(f"{title or ''} {text}", k=3, session=session)
                   if h.matched >= 3]
        now = datetime.now().astimezone()
        source = {"kind": "conversation", "session": session.label, "at": now_iso()}
        if quote and sensitivity != "secret":  # never store plaintext for a secret
            source["quote"] = quote
        meta = {"pmf": SPEC_VERSION, "id": new_ulid(), "type": type, "about": about}
        if title:
            meta["title"] = title
        meta["created"] = now_iso()
        if valid_from:
            meta["valid_from"] = valid_from
        meta["sources"] = [source]
        for key, value in (("confidence", confidence), ("importance", importance),
                           ("sensitivity", sensitivity)):
            if value is not None:
                meta[key] = value
        if tags:
            meta["tags"] = tags
        path = default_path(meta, now)
        if path in files:
            raise LocketError(f"路径冲突：{path}")
        body = text
        if sensitivity == "secret":  # encrypt at rest; the server/Gitea never see plaintext
            body = crypto.encrypt(text.encode("utf-8"), recipients.keys).decode("utf-8")
        memory = Memory(meta, body, path)
        self._commit(session, {path: memory.dumps()}, "learn", memory.label)
        return memory, similar

    def update(self, ref: str, session: Session, *, text: str | None = None,
               fields: dict | None = None, reason: str | None = None) -> Memory:
        """Correction: we had it wrong. Same id, edited in place (spec §4.3)."""
        memory = self.get(ref, session)
        for key, value in (fields or {}).items():
            if key in PROTECTED_FIELDS:
                raise LocketError(f"{key} 字段不能修改")
            if value is None:
                memory.meta.pop(key, None)
            else:
                memory.meta[key] = value
        if text is not None:
            memory.body = text.strip()
        self._commit(session, {memory.path: memory.dumps()}, "correct", memory.label, reason)
        return memory

    def supersede(self, ref: str, text: str, session: Session, *, effective: str | None = None,
                  title: str | None = None, quote: str | None = None) -> tuple[Memory, Memory]:
        """World change: the old memory was true, things changed (spec §4.3)."""
        effective = effective or today()
        old = self.get(ref, session)
        old.meta["valid_to"] = effective
        now = datetime.now().astimezone()
        source = {"kind": "conversation", "session": session.label, "at": now_iso()}
        if quote:
            source["quote"] = quote
        meta = {"pmf": SPEC_VERSION, "id": new_ulid(), "type": old.type, "about": old.about}
        if title or old.title:
            meta["title"] = title or old.title
        meta.update({"created": now_iso(), "valid_from": effective,
                     "supersedes": [old.id], "sources": [source]})
        for key in ("importance", "sensitivity", "tags"):
            if key in old.meta:
                meta[key] = old.meta[key]
        new = Memory(meta, text.strip(), default_path(meta, now))
        changes = {old.path: old.dumps(), new.path: new.dumps()}
        self._commit(session, changes, "learn", f"{new.label}（取代 {short_id(old.id)}）")
        return old, new

    def forget(self, ref: str, session: Session, reason: str | None = None) -> Memory:
        """Soft forget: removed from the current tree, still in history."""
        memory = self.get(ref, session)
        self._commit(session, {memory.path: None}, "forget", memory.label, reason)
        return memory

    # ---- merging -----------------------------------------------------------

    def _three_way(self, base: str | None, ours: str, theirs: str):
        base_f = self.git.tree_files(base) if base else {}
        ours_f = self.git.tree_files(ours)
        theirs_f = self.git.tree_files(theirs)
        result, conflicts = dict(ours_f), []
        for path in set(base_f) | set(theirs_f):
            b, o, t = base_f.get(path), ours_f.get(path), theirs_f.get(path)
            if b == t or o == t:
                continue
            if o == b:
                if t is None:
                    result.pop(path, None)
                else:
                    result[path] = t
            else:
                conflicts.append((path, b, o, t))  # both sides changed: main wins, recorded
        return result, conflicts

    def requires_approval(self) -> bool:
        """When true, end_session leaves the branch as a pending PR (spec §6.1)."""
        policy = self.config().get("policy", {}) or {}
        return bool(policy.get("main_requires_approval", False))

    def end_session(self, session: Session, owner: bool = False) -> MergeResult:
        """End a session. Merge into main, unless policy requires owner approval and
        this is an agent's session (not the owner acting directly), in which case the
        branch is left intact as a pending request (spec §6.1)."""
        self.require()
        tip = self.git.rev_parse(session.ref)
        if not tip:
            return MergeResult(session, merged=False)
        if not owner and self.requires_approval():
            old_main = self.git.rev_parse(MAIN)
            if self.git.merge_base(old_main, tip) == tip:
                self.git.delete_ref(session.ref, tip)  # nothing new to approve
                return MergeResult(session, merged=False)
            return MergeResult(session, merged=False, pending=True)
        return self._do_merge(session)

    def approve(self, session: Session) -> MergeResult:
        """Owner approves a pending session branch and merges it into main (spec §6.1)."""
        self.require()
        if not self.git.rev_parse(session.ref):
            raise LocketError(f"找不到待审批的会话 {session.label}")
        return self._do_merge(session)

    def pending(self) -> list[tuple[Session, str]]:
        """Session branches awaiting approval (all sessions, when approval is on)."""
        return self.sessions() if self.requires_approval() else []

    def _do_merge(self, session: Session) -> MergeResult:
        tip = self.git.rev_parse(session.ref)
        if not tip:
            return MergeResult(session, merged=False)
        old_main = self.git.rev_parse(MAIN)
        on_main = self.git.head_branch() == MAIN
        if on_main:
            self.save()  # keep the owner's hand edits before files change underneath
            old_main = self.git.rev_parse(MAIN)
        base = self.git.merge_base(old_main, tip)
        if base == tip:
            self.git.delete_ref(session.ref, tip)
            return MergeResult(session, merged=False)

        changes, names = self._merge_changes(old_main, tip, f"会话 {session.label}",
                                             detector=self._configured_detector())
        labels = self._labels(tip, changes)
        details = "\n".join(f"- {l}" for l in labels[:20]) or None
        message = _message("merge", f"合并会话 {session.label}", session, details)
        commit = self.git.commit_changes(MAIN, changes, message, MERGER, merge_parents=(tip,))
        self.git.delete_ref(session.ref, tip)
        synced = self.git.sync_worktree(old_main, commit) if on_main else True
        return MergeResult(session, True, commit, sorted(changes), names, synced)

    def _labels(self, tip: str, changes: dict) -> list[str]:
        shas = [s for p, s in changes.items() if isinstance(s, str) and is_memory_path(p)]
        blobs = self.git.read_blobs(shas)
        labels = []
        for path, sha in sorted(changes.items()):
            if not is_memory_path(path):
                continue
            if sha is None:
                labels.append(f"删除 {path}")
            elif isinstance(sha, str) and sha in blobs:
                try:
                    labels.append(parse(blobs[sha]).label)
                except ValueError:
                    labels.append(path)
        return labels

    def _conflict_record(self, path: str, ours: str | None, theirs: str | None,
                         source: str) -> Memory:
        """A path collision: `path` was changed to different content on both sides.

        `source` labels where the non-main side came from (a session or a remote)."""
        blobs = self.git.read_blobs([s for s in (ours, theirs) if s])
        member, theirs_text = None, f"（{source} 中删除了这条记忆）"
        for sha in (theirs, ours):
            if sha in blobs:
                try:
                    member = member or parse(blobs[sha]).id
                except ValueError:
                    pass
        if theirs in blobs:
            theirs_text = blobs[theirs].decode("utf-8")
        cid = new_ulid()
        meta = {
            "pmf": SPEC_VERSION, "id": cid, "type": "x-conflict", "kind": "path",
            "status": "open", "members": [member] if member else [],
            "path": path, "detected_by": f"locket {__version__}", "detected_at": now_iso(),
            "question": f"这条记忆在 main 和 {source} 中被改成了不同内容，应该保留哪个版本？",
        }
        body = (f"main 中的版本保持不变。{source} 中的版本如下：\n\n"
                f"````markdown\n{theirs_text.strip()}\n````")
        return Memory(meta, body, f"conflicts/{cid.lower()}.md")

    def _configured_detector(self):
        """The semantic-conflict detector set by policy (default off = NullDetector).

        `policy.semantic_conflicts = "heuristic"` turns on the token-overlap heuristic;
        an LLM detector is pluggable in code but never enabled from config alone."""
        policy = self.config().get("policy", {}) or {}
        mode = str(policy.get("semantic_conflicts", "off")).lower()
        if mode in ("heuristic", "on", "true", "1", "yes"):
            return semantic.HeuristicDetector()
        return semantic.NullDetector()

    def _semantic_conflict_record(self, mem: Memory, other: Memory,
                                  finding: "semantic.Finding") -> Memory:
        """A §6.2 conflict record for two memories at *different* paths that the
        detector flagged as a duplicate or a contradiction (not a path collision)."""
        cid = new_ulid()
        meta = {
            "pmf": SPEC_VERSION, "id": cid, "type": "x-conflict", "kind": finding.kind,
            "status": "open", "members": sorted([mem.id, other.id]),
            "detected_by": f"locket {__version__}", "detected_at": now_iso(),
            "confidence": round(float(finding.confidence), 3),
            "question": finding.question,
        }
        body = (f"检测到两条记忆{'疑似重复' if finding.kind == 'duplicate' else '可能矛盾'}，"
                f"都保留在 main 中等待确认：\n\n"
                f"- [{short_id(mem.id)}] {mem.label}（{mem.path}）\n"
                f"- [{short_id(other.id)}] {other.label}（{other.path}）")
        return Memory(meta, body, f"conflicts/{cid.lower()}.md")

    def _parse_memories(self, files: dict[str, str]) -> list[Memory]:
        """Parse the memory files in a path->sha map into Memory objects."""
        paths = [p for p in files if is_memory_path(p)]
        blobs = self.git.read_blobs([files[p] for p in paths])
        out = []
        for path in paths:
            try:
                out.append(parse(blobs[files[path]], path))
            except (ValueError, KeyError, UnicodeDecodeError):
                continue
        return out

    def _semantic_records(self, final_files: dict[str, str], new_paths: set[str],
                          detector, existing_pairs: set[frozenset]) -> list[Memory]:
        """For each newly added/changed memory, ask the detector whether it duplicates
        or contradicts another memory (same `about`), and build conflict records for
        new findings. `existing_pairs` are member-id pairs already recorded, to dedup."""
        if isinstance(detector, semantic.NullDetector):
            return []
        mems = self._parse_memories(final_files)
        by_about: dict[str, list[Memory]] = {}
        for m in mems:
            by_about.setdefault(m.about, []).append(m)
        new_ids = {final_files[p] for p in new_paths if p in final_files}
        seen = set(existing_pairs)
        records = []
        for mem in mems:
            if final_files.get(mem.path) not in new_ids:
                continue  # only check memories this merge introduced or changed
            candidates = [(o, 0.0) for o in by_about.get(mem.about, []) if o.id != mem.id]
            for finding in detector.judge(mem, candidates):
                other = next((o for o in mems if o.id == finding.other_id), None)
                if not other:
                    continue
                pair = frozenset([mem.id, other.id])
                if pair in seen:
                    continue
                seen.add(pair)
                records.append(self._semantic_conflict_record(mem, other, finding))
        return records

    def _open_conflict_pairs(self, files: dict[str, str]) -> set[frozenset]:
        pairs = set()
        for p in files:
            if not (p.startswith("conflicts/") and p.endswith(".md")):
                continue
            try:
                c = parse(self.git.read_blobs([files[p]])[files[p]], p)
            except (ValueError, KeyError):
                continue
            members = c.meta.get("members") or []
            if len(members) == 2:
                pairs.add(frozenset(members))
        return pairs

    def _merge_changes(self, old_main: str, theirs: str, source: str,
                       detector=None) -> tuple[dict, list[str]]:
        """Three-way merge `theirs` into main and return (changes, conflict paths).

        Shared by session merges and remote pulls: main always wins path
        collisions (recorded as conflicts), and a redacted memory is never
        resurrected by an older branch or clone (spec §7). When a semantic
        `detector` is given, newly merged memories are also checked for
        duplicate/contradiction against existing ones (§6.2)."""
        base = self.git.merge_base(old_main, theirs)
        merged, conflicts = self._three_way(base, old_main, theirs)
        main_files = self.git.tree_files(old_main)
        gone = self.tombstoned(merged)
        changes: dict[str, str | bytes | None] = {}
        for path in set(main_files) | set(merged):
            if main_files.get(path) != merged.get(path):
                changes[path] = merged.get(path)
        if gone:
            blobs = self.git.read_blobs([s for p, s in changes.items() if s and is_memory_path(p)])
            for path, sha in list(changes.items()):
                if sha and is_memory_path(path):
                    try:
                        if parse(blobs[sha]).id in gone:
                            del changes[path]
                    except (ValueError, KeyError):
                        pass
        names = []
        for path, _b, ours, theirs_sha in conflicts:
            item = self._conflict_record(path, ours, theirs_sha, source)
            changes[item.path] = item.dumps()
            names.append(item.path)
        if detector is not None and not isinstance(detector, semantic.NullDetector):
            # Build the post-merge file map, then flag semantic conflicts among new memories.
            final_files = dict(main_files)
            new_paths = set()
            for path, data in changes.items():
                if data is None:
                    final_files.pop(path, None)
                else:
                    sha = data if isinstance(data, str) else \
                        self.git.run("hash-object", "-w", "--stdin", input=data).strip()
                    final_files[path] = sha
                    if is_memory_path(path):
                        new_paths.add(path)
            existing = self._open_conflict_pairs(final_files)
            for rec in self._semantic_records(final_files, new_paths, detector, existing):
                changes[rec.path] = rec.dumps()
                names.append(rec.path)
        return changes, names

    def resolve(self, ref: str, note: str | None = None) -> Memory:
        """Mark a conflict resolved (owner action, committed to main)."""
        ref = ref.strip().upper()
        found = [c for c in self.conflicts(status="") if c.id.upper().endswith(ref)]
        if len(found) != 1:
            raise LocketError(f"找不到唯一的冲突 {ref.lower()}")
        item = found[0]
        item.meta["status"] = "resolved"
        item.meta["resolved_at"] = now_iso()
        if note:
            item.meta["resolution"] = note
        self._owner_commit({item.path: item.dumps()}, "resolve", item.meta.get("question", item.id))
        return item

    # ---- owner actions on main ---------------------------------------------

    def _owner_commit(self, changes: dict, op: str, summary: str, details: str | None = None) -> str:
        on_main = self.git.head_branch() == MAIN
        if on_main:
            self.save()
        old = self.git.rev_parse(MAIN)
        commit = self.git.commit_changes(MAIN, changes, _message(op, summary, None, details),
                                         self.owner_identity())
        if on_main:
            self.git.sync_worktree(old, commit)
        return commit

    def redact(self, ref: str, reason: str = "owner-request") -> Memory:
        """Hard delete: remove the file and record a tombstone (spec §7)."""
        memory = self.get(ref)
        blobs = []
        for entry in self.git.log(MAIN, memory.path, follow=True):
            p = self.git.proc("rev-parse", f"{entry.sha}:{memory.path}")
            if p.returncode == 0:
                blobs.append(p.stdout.decode().strip())
        record = {"id": memory.id, "blobs": sorted(set(blobs)), "at": now_iso(), "reason": reason,
                  "by": str(self.config().get("owner", ""))}
        current = self.git.show(MAIN, TOMBSTONES_PATH) or b""
        line = json.dumps(record, ensure_ascii=False) + "\n"
        changes = {memory.path: None, TOMBSTONES_PATH: current + line.encode("utf-8")}
        self._owner_commit(changes, "redact", f"删除记忆 {short_id(memory.id)}")
        return memory

    def save(self) -> str | None:
        """Commit the owner's own edits in the working tree as a correction."""
        if self.git.head_branch() != MAIN:
            return None
        status = self.git.run("status", "--porcelain", "-uall")
        if not status.strip():
            return None
        self.git.run("add", "-A")
        changed = [line[3:] for line in status.splitlines()]
        message = _message("correct", f"手动修改 {len(changed)} 个文件", None,
                           "\n".join(f"- {p}" for p in changed[:20]))
        self.git.run("commit", "-q", "--no-verify", "-F", "-", input=message,
                     env=self.owner_identity().env())
        return self.git.rev_parse(MAIN)

    def end_stale(self, agent: str, keep: str | None = None,
                  older_than: timedelta = timedelta(hours=6)) -> list[MergeResult]:
        """Merge session branches a crashed client never ended."""
        results = []
        limit = datetime.now(timezone.utc) - older_than
        for session, sha in self.sessions():
            if session.agent != _clean(agent) or session.id == (keep and _clean(keep)):
                continue
            when = self.git.run("log", "-1", "--format=%cI", sha).strip()
            if datetime.fromisoformat(when) < limit:
                results.append(self.end_session(session))
        return results

    # ---- sync across devices (spec §10.3) ----------------------------------

    def remote(self, url: str | None = None) -> str | None:
        """Get the sync remote, or set it when `url` is given."""
        if url:
            self.require()
            self.git.set_remote(url, REMOTE)
        return self.git.get_remote_url(REMOTE)

    def _require_remote(self) -> None:
        self.require()
        if not self.git.get_remote_url(REMOTE):
            raise LocketError("还没有配置远程，先运行：locket remote <仓库地址>")

    def pull(self) -> SyncResult:
        """Fetch main from the remote and integrate it into local main.

        Fast-forward when possible; otherwise a three-way merge that records
        path collisions as conflicts (same machinery as a session merge) and
        never resurrects a redacted memory. Only long-term memory (main) and
        tags sync; session branches stay local (spec §6.1)."""
        self._require_remote()
        self.git.fetch(REMOTE)
        theirs = self.git.rev_parse(REMOTE_MAIN)
        if not theirs:
            return SyncResult("up-to-date", detail="远程还没有 main 分支")
        local = self.git.rev_parse(MAIN)
        on_main = self.git.head_branch() == MAIN
        if on_main:
            self.save()
            local = self.git.rev_parse(MAIN)
        if theirs == local:
            return SyncResult("up-to-date")
        if self.git.is_ancestor(theirs, local):
            return SyncResult("up-to-date", detail="本地领先远程，pull 无变化")
        if self.git.is_ancestor(local, theirs):  # fast-forward
            self.git.set_ref(MAIN, theirs, local)
            synced = self.git.sync_worktree(local, theirs) if on_main else True
            local_files, remote_files = self.git.tree_files(local), self.git.tree_files(theirs)
            changed = sorted(p for p in local_files.keys() | remote_files.keys()
                             if local_files.get(p) != remote_files.get(p))
            return SyncResult("fast-forward", theirs, changed, [], synced)
        # diverged: merge remote main into local main
        changes, names = self._merge_changes(local, theirs, "远程",
                                             detector=self._configured_detector())
        labels = self._labels(theirs, changes)
        details = "\n".join(f"- {l}" for l in labels[:20]) or None
        message = _message("merge", "同步远程记忆", None, details)
        commit = self.git.commit_changes(MAIN, changes, message, MERGER, merge_parents=(theirs,))
        synced = self.git.sync_worktree(local, commit) if on_main else True
        return SyncResult("merge", commit, sorted(changes), names, synced)

    def push(self) -> SyncResult:
        """Publish local main (and tags) to the remote."""
        self._require_remote()
        local = self.git.rev_parse(MAIN)
        ok, msg = self.git.push(f"{MAIN}:{MAIN}")
        if not ok:
            if any(w in msg for w in ("rejected", "non-fast-forward", "fetch first", "behind")):
                raise LocketError("远程有本地还没有的提交，先运行 locket pull，再 push")
            raise LocketError(f"推送失败：{msg}")
        self.git.push("refs/tags/*:refs/tags/*")  # best effort; milestones are optional
        remote_now = self.git.rev_parse(REMOTE_MAIN)
        return SyncResult("up-to-date" if remote_now == local and "up-to-date" in msg else "pushed",
                          local, detail=msg)

    def sync(self) -> tuple[SyncResult, SyncResult]:
        """Pull then push: the everyday 'reconcile this device with the rest'."""
        pulled = self.pull()
        pushed = self.push()
        return pulled, pushed

    # ---- hard erase: rewrite history to drop redacted blobs (spec §7) ------

    def gc(self) -> dict:
        """Physically remove every tombstoned blob from main's history.

        The tombstone (§7) already makes a memory vanish from current trees on
        every clone that pulls it; this is the maintenance step that also erases
        the bytes locally. It rewrites commit ids, so after a gc the next push is
        a force-push the owner must perform knowingly — hence gc is never run
        automatically on sync. Objects still reachable from other refs (e.g. a
        remote-tracking branch not yet rewritten) survive until those refs move."""
        self.require()
        current = self.git.show(MAIN, TOMBSTONES_PATH) or b""
        dead: set[str] = set()
        for line in current.decode("utf-8").splitlines():
            if line.strip():
                dead.update(json.loads(line).get("blobs", []))
        if not dead:
            return {"removed": 0, "rewritten": 0}
        tip = self.git.rev_parse(MAIN)
        on_main = self.git.head_branch() == MAIN
        if on_main:
            self.save()
            tip = self.git.rev_parse(MAIN)
        mapping: dict[str, str] = {}
        removed_total = 0
        for old in self.git.rev_list(tip):
            info = self.git.commit_object(old)
            new_parents = [mapping.get(p, p) for p in info["parents"]]
            tree, removed = self.git.filtered_tree(old, dead)
            removed_total += removed
            if removed == 0 and new_parents == info["parents"] and tree == info["tree"]:
                mapping[old] = old
                continue
            mapping[old] = self.git.commit_tree_raw(
                tree, new_parents, info["message"], info["author"], info["committer"])
        new_tip = mapping[tip]
        rewritten = sum(1 for o, n in mapping.items() if o != n)
        if new_tip != tip:
            self.git.set_ref(MAIN, new_tip, tip)
            if on_main:
                self.git.sync_worktree(tip, new_tip)
        self.git.prune_now()
        return {"removed": removed_total, "rewritten": rewritten, "blobs": len(dead),
                "old": tip, "new": new_tip}


def _message(op: str, summary: str, session: Session | None, details: str | None = None) -> str:
    lines = [f"{op}: {shorten(summary, 60)}", ""]
    if details:
        lines += [details.strip(), ""]
    lines.append(f"Pmf-Op: {op}")
    if session:
        lines += [f"Pmf-Session: {session.label}", f"Pmf-Agent: {session.agent}"]
    return "\n".join(lines) + "\n"
