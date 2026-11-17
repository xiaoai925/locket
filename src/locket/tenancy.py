"""Multi-tenant Gitea backend: who is the caller, and which repo is theirs.

Two injectable pieces that turn the single-user service into a cloud service
(ARCHITECTURE.md §2/§3), without the service layer knowing anything about Gitea:

- ``GiteaAuthenticator``  bearer token -> ``Principal`` (verified against Gitea).
  Plug into ``webapi``'s ``authenticate``.
- ``GiteaResolver``       ``Principal`` -> that user's ``Locket`` repo, a server-side
  working copy of ``<user>/<repo>`` on Gitea. Plug into ``MemoryService(resolve=...)``.

Each Gitea user owns one private memory repo; isolation is Gitea's repo-level ACL.
Tokens are used only transiently as git credentials and are never written into
``.git/config`` (the stored origin URL is tokenless) nor logged.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .service import Principal
from .store import Locket, LocketError
from .locking import repository_lock
from .git_credentials import CredentialGit

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(user: str) -> str:
    name = _SAFE.sub("-", user or "").strip("-.")
    return name or "user"


class GiteaAuthenticator:
    """Turn a bearer token into a verified ``Principal`` by asking Gitea who it is."""

    def __init__(self, base_url: str, verify: Callable[[str], str | None] | None = None):
        self.base_url = base_url.rstrip("/")
        self._verify = verify or self._default_verify

    def _default_verify(self, token: str) -> str | None:
        """GET {base}/api/v1/user; try PAT scheme then OAuth2 Bearer; return login."""
        url = f"{self.base_url}/api/v1/user"
        for scheme in ("token", "Bearer"):
            req = urllib.request.Request(url, headers={"Authorization": f"{scheme} {token}"})
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                login = data.get("login") or data.get("username")
                if login:
                    return str(login)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    continue  # try the other auth scheme
                return None
            except (urllib.error.URLError, ValueError, TimeoutError, OSError):
                return None
        return None

    def verify(self, token: str) -> str | None:
        return self._verify(token)

    def __call__(self, token: str | None) -> Principal:
        if not token:
            raise LocketError("需要 Authorization: Bearer <Gitea 令牌>")
        login = self.verify(token)
        if not login:
            raise LocketError("Gitea 令牌无效或已过期")
        # A user authenticating with their own token to their own repo is the owner:
        # Principal's default scope (OWNER_SCOPE) grants full read/write. Restricted
        # scopes are for future delegated/shared access (views, §9.2 / P4), where a
        # resolver would hand back a narrower Scope.
        return Principal(user=login, token=token)


class GiteaResolver:
    """Resolve a principal to their server-side working copy of ``<user>/<repo>``.

    First request for a user clones their repo (seeding an empty one); later requests
    freshen it with a pull. Writes are published by the caller via ``Locket.push()``;
    this resolver keeps ``origin`` tokenless and only injects the token for the actual
    network call.
    """

    auto_push = True  # This cache is a cloud gateway, not a device-local-only repo.

    def __init__(self, base_url: str, data_dir: str | Path, repo_name: str = "memory"):
        self.base_url = base_url.rstrip("/")
        self.data_dir = Path(data_dir)
        self.repo_name = repo_name

    # --- overridable address scheme (tests point these at local bare repos) ---

    def _remote_url(self, user: str) -> str:
        return f"{self.base_url}/{user}/{self.repo_name}.git"

    # --- git helpers ---

    @staticmethod
    def _redact(text: str, token: str) -> str:
        return text.replace(token, "***") if token else text

    def _clone(self, user: str, token: str, dest: Path) -> None:
        p = CredentialGit(dest.parent, user, token).proc(
            "clone", "--quiet", self._remote_url(user), str(dest))
        if p.returncode != 0:
            raise LocketError("无法克隆该用户的记忆仓")

    def _with_token(self, store: Locket, user: str, token: str, fn: Callable):
        """Inject credentials without changing on-disk Git configuration."""
        previous = store.git
        store.git = CredentialGit(store.root, user, token)
        try:
            return fn()
        finally:
            store.git = previous

    def _seed(self, store: Locket, user: str, token: str) -> None:
        """A freshly-created empty Gitea repo: initialise PMF layout and push it."""
        store.init()
        self._with_token(store, user, token, store.push)

    def _freshen(self, store: Locket, user: str, token: str) -> None:
        """Best-effort pull: if the remote is unreachable, serve the local copy
        (a later write will push and reconcile)."""
        try:
            self._with_token(store, user, token, store.pull)
        except Exception:  # noqa: BLE001
            pass

    # --- the resolver ---

    def __call__(self, p: Principal) -> Locket:
        if not p.token:
            raise LocketError("缺少访问令牌，无法定位用户记忆仓")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", p.user) or ".." in p.user:
            raise LocketError("无效的租户身份")
        dest = self.data_dir / p.user.lower()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with repository_lock(dest):
            store = Locket(dest)
            store.git = CredentialGit(dest, p.user, p.token)
            if (dest / ".git").is_dir():
                if store.exists():
                    self._freshen(store, p.user, p.token)
                else:
                    self._seed(store, p.user, p.token)
                return store
            self._clone(p.user, p.token, dest)
            store.git.set_remote(self._remote_url(p.user))
            if not store.exists():
                self._seed(store, p.user, p.token)
            return store

    @staticmethod
    def release(store: Locket) -> None:
        store.close()
