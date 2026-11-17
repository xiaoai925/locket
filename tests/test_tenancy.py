"""Gitea tenancy: network-free (local bare repos stand in for Gitea)."""
import subprocess
from pathlib import Path

import pytest

from locket.service import Principal
from locket.store import Session
from locket.tenancy import GiteaAuthenticator, GiteaResolver, _safe_name


def _bare(path: Path) -> None:
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(path)], check=True)


class LocalResolver(GiteaResolver):
    """Point the address scheme at local bare repos instead of a real Gitea."""

    def __init__(self, remotes_dir: Path, data_dir: Path):
        super().__init__("http://gitea.invalid", data_dir)
        self.remotes_dir = Path(remotes_dir)

    def _remote_url(self, user: str) -> str:
        return str(self.remotes_dir / f"{user}.git")

    def _authed_url(self, user: str, token: str) -> str:  # local path needs no creds
        return self._remote_url(user)


def test_authenticator_maps_token_to_user():
    auth = GiteaAuthenticator("http://gitea.invalid",
                              verify=lambda t: "alice" if t == "good" else None)
    p = auth("good")
    assert p.user == "alice" and p.token == "good" and p.agent == "api"
    with pytest.raises(Exception):
        auth("bad")
    with pytest.raises(Exception):
        auth(None)


def test_resolver_seeds_isolates_and_freshens(tmp_path):
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    _bare(remotes / "alice.git")
    _bare(remotes / "bob.git")
    res = LocalResolver(remotes, tmp_path / "data")

    a = res(Principal(user="alice", token="x"))
    b = res(Principal(user="bob", token="x"))
    assert a.root != b.root
    assert a.exists() and b.exists()  # empty remotes were seeded (init + push)
    assert (a.root / ".pmf" / "config.toml").exists()

    # a write on alice, published, is visible to a fresh clone of alice's remote
    s = Session("cli", "s1")
    a.remember("alice 的记忆内容", s)
    a.end_session(s)
    a.push()
    fresh = tmp_path / "data2"
    again = LocalResolver(remotes, fresh)(Principal(user="alice", token="x"))
    assert any("alice 的记忆内容" in m.body for m in again.memories())

    # re-resolving alice reuses the same working copy and only freshens
    a2 = res(Principal(user="alice", token="x"))
    assert a2.root == a.root and a2.exists()


def test_resolver_requires_token(tmp_path):
    res = LocalResolver(tmp_path / "remotes", tmp_path / "data")
    (tmp_path / "remotes").mkdir()
    with pytest.raises(Exception):
        res(Principal(user="carol", token=None))


def test_gitea_service_write_is_published_to_own_cloud_repo(tmp_path):
    from locket.service import MemoryService
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    _bare(remotes / "alice.git")
    resolver = LocalResolver(remotes, tmp_path / "cache")
    svc = MemoryService(resolve=resolver)
    result = svc.remember(Principal(user="alice", token="synthetic"), "Cloud gateway synthetic record")
    assert result["merged"]
    fresh = LocalResolver(remotes, tmp_path / "fresh")(Principal(user="alice", token="synthetic"))
    try:
        assert any(m.id == result["memory"]["id"] for m in fresh.memories())
    finally:
        fresh.close()


def test_safe_name_sanitises():
    assert _safe_name("alice") == "alice"
    assert _safe_name("../evil") == "evil"
    assert _safe_name("a/b\\c") == "a-b-c"
    assert _safe_name("") == "user"


def test_credential_callback_is_standalone_and_never_updates_remote(tmp_path):
    import os
    import shlex
    from locket.git_credentials import CredentialGit
    token = "synthetic-token-never-a-real-secret"
    git = CredentialGit(tmp_path, "alice", token)
    # Capture only the generated environment; run the actual standalone callback.
    from unittest.mock import patch
    from locket.gitrepo import Git
    captured = {}
    def capture(self, *args, **kwargs):
        captured.update(kwargs["env"])
        return subprocess.CompletedProcess([], 0, b"", b"")
    with patch.object(Git, "proc", capture):
        git.proc("ls-remote", "https://example.invalid/alice/memory.git")
    # Git spawns GIT_ASKPASS as one program, so it must be a single executable path.
    askpass = captured["GIT_ASKPASS"]
    assert os.path.isfile(askpass) and os.access(askpass, os.X_OK)
    assert token not in open(askpass, encoding="utf-8").read()
    command = ["sh", askpass]  # run it the way git would (via its shebang)
    output = subprocess.run([*command, "Password for https://example.invalid"],
        env={**os.environ, **captured}, capture_output=True, check=True)
    assert output.stdout.decode().strip() == token
    output = subprocess.run([*command, "Username for https://example.invalid"],
        env={**os.environ, **captured}, capture_output=True, check=True)
    assert output.stdout.decode().strip() == "alice"
    assert captured["GIT_CONFIG_VALUE_0"] == ""
    assert not (tmp_path / ".git/config").exists()
