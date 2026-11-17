"""Tests for locket.signing: signature detection and verification (spec §5.2).

Uses only unsigned commits so it stays fast and never depends on a real gpg/ssh key.
"""
import subprocess

import pytest

from locket.gitrepo import Git
from locket.signing import describe, sign_args, signing_status, verify_commit


def _git(tmp, *args):
    subprocess.run(["git", "-C", str(tmp), *args], check=True,
                   capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A plain git repo with one unsigned commit on main."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True,
                   capture_output=True)
    _git(tmp_path, "config", "user.name", "Tester")
    _git(tmp_path, "config", "user.email", "tester@example.com")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("hello", "utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "first")
    return Git(tmp_path)


def test_signing_status_defaults_off(repo):
    s = signing_status(repo)
    assert s["enabled"] is False
    assert s["format"] == "openpgp"
    assert s["key"] is None


def test_sign_args_empty_when_disabled(repo):
    assert sign_args(repo) == []


def test_signing_status_enabled_after_config(repo):
    _git(repo.root, "config", "commit.gpgsign", "true")
    s = signing_status(repo)
    assert s["enabled"] is True
    assert sign_args(repo) == ["-S"]


def test_sign_args_includes_key_when_set(repo):
    _git(repo.root, "config", "commit.gpgsign", "true")
    _git(repo.root, "config", "user.signingkey", "ABCD1234")
    assert signing_status(repo)["key"] == "ABCD1234"
    assert sign_args(repo) == ["-SABCD1234"]


def test_gpg_format_ssh(repo):
    _git(repo.root, "config", "gpg.format", "ssh")
    assert signing_status(repo)["format"] == "ssh"


def test_verify_unsigned_commit(repo):
    v = verify_commit(repo, "HEAD")
    assert v["signed"] is False
    assert v["status"] == "N"
    assert v["verified"] is False
    assert v["signer"] == "" and v["key"] == ""


def test_verify_missing_rev_is_safe(repo):
    v = verify_commit(repo, "deadbeef")
    assert v == {"signed": False, "status": "N", "signer": "", "key": "", "verified": False}


def test_verify_empty_rev_is_safe(repo):
    assert verify_commit(repo, "")["signed"] is False


def test_describe_unsigned(repo):
    assert "未签名" in describe(repo, "HEAD")


def test_describe_missing_rev(repo):
    assert "未签名" in describe(repo, "deadbeef")
