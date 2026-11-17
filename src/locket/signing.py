"""Commit-signature detection and verification (PMF spec §5.2).

This layer does NOT manage the owner's keys — whether commits get signed is the
owner's own git configuration (`commit.gpgsign`, `gpg.format`, `user.signingkey`).
It only: reports whether signing is configured, produces the sign argument to splice
into a git commit, and verifies a given commit's signature so the UI/CLI can show
"this memory was signed by the owner" versus "written unsigned by some agent".

Verification relies on the local git plus gpg/ssh toolchain; when a key or verifier
is unavailable git reports the signature as uncheckable, which we surface honestly
rather than claiming trust.
"""
from __future__ import annotations

# git's %G? signature-status codes (see `man git-log`, PRETTY FORMATS).
_GOOD = ("G", "U")          # G = valid, U = valid but the key is of unknown trust
_PRESENT = ("G", "B", "U", "X", "Y", "R")  # some signature exists (good/bad/expired/revoked)
_US = "\x1f"


def signing_status(git) -> dict:
    """What the repo is configured to do when committing.

    {"enabled": bool, "format": "openpgp"|"ssh", "key": str|None}
    """
    raw = _config(git, "commit.gpgsign")
    enabled = str(raw).strip().lower() in ("true", "1", "yes", "on")
    fmt = (_config(git, "gpg.format") or "openpgp").strip().lower()
    if fmt not in ("openpgp", "ssh", "x509"):
        fmt = "openpgp"
    key = _config(git, "user.signingkey")
    return {"enabled": enabled, "format": fmt, "key": key or None}


def sign_args(git) -> list[str]:
    """The sign flag to splice into `git commit` / `git commit-tree`.

    [] when signing is off; ["-S"] when on (or ["-S<key>"] when a key is set, so
    the signature is made with exactly that key).
    """
    status = signing_status(git)
    if not status["enabled"]:
        return []
    key = status["key"]
    return [f"-S{key}"] if key else ["-S"]


def verify_commit(git, rev: str) -> dict:
    """Verify one commit's signature.

    {"signed": bool, "status": "<%G?>", "signer": str, "key": str, "verified": bool}
    - status: git's %G? code (N = none, G = good, U = good/unknown-trust,
      B = bad, X/Y/R = expired/expired-key/revoked, E = cannot check).
    - verified: the signature checks out and is trusted enough (G or U).
    Safe on a missing/invalid rev: returns the unsigned default, never raises.
    """
    default = {"signed": False, "status": "N", "signer": "", "key": "", "verified": False}
    if not rev:
        return default
    p = git.proc("log", "-1", f"--format=%G?{_US}%GS{_US}%GK", rev)
    if p.returncode != 0:
        return default
    out = p.stdout.decode("utf-8", "replace").strip("\n")
    if not out:
        return default
    parts = out.split(_US)
    status = (parts[0] or "N").strip() or "N"
    signer = parts[1].strip() if len(parts) > 1 else ""
    key = parts[2].strip() if len(parts) > 2 else ""
    return {
        "signed": status in _PRESENT,
        "status": status,
        "signer": signer,
        "key": key,
        "verified": status in _GOOD,
    }


def describe(git, rev: str) -> str:
    """A one-line, human-readable summary of a commit's signature (Chinese)."""
    v = verify_commit(git, rev)
    if not v["signed"]:
        return "未签名"
    who = f" {v['signer']}" if v["signer"] else ""
    if v["verified"]:
        trust = "可信" if v["status"] == "G" else "密钥信任未知"
        return f"已由{who} 签名（{trust}）"
    reason = {
        "B": "签名无效",
        "X": "签名已过期",
        "Y": "签名密钥已过期",
        "R": "签名密钥已吊销",
        "E": "无法校验签名",
    }.get(v["status"], "签名存在但未通过校验")
    return f"{reason}{('（' + v['signer'] + '）') if v['signer'] else ''}"


def _config(git, name: str) -> str | None:
    """Read one git config value, or None when unset."""
    p = git.proc("config", "--get", name)
    if p.returncode != 0:
        return None
    val = p.stdout.decode("utf-8", "replace").strip()
    return val or None
