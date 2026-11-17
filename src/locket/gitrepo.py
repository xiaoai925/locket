"""Thin wrapper around the git command line.

Session writes use plumbing with a throwaway index, so several agents can write
their own session branches at the same time without touching the working tree.
Only merges into main use the working tree, which keeps main readable as files.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

ZERO_SHA = "0" * 40
_INHERITED = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class Identity:
    name: str
    email: str

    def env(self) -> dict[str, str]:
        return {
            "GIT_AUTHOR_NAME": self.name,
            "GIT_AUTHOR_EMAIL": self.email,
            "GIT_COMMITTER_NAME": self.name,
            "GIT_COMMITTER_EMAIL": self.email,
        }


@dataclass(frozen=True)
class LogEntry:
    sha: str
    author: str
    date: str
    subject: str
    op: str


class Git:
    def __init__(self, root: Path):
        self.root = Path(root)

    def proc(self, *args: str, input: str | bytes | None = None,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        full = os.environ.copy()
        for key in _INHERITED:
            full.pop(key, None)
        full["GIT_TERMINAL_PROMPT"] = "0"
        if env:
            full.update(env)
        if isinstance(input, str):
            input = input.encode("utf-8")
        cmd = ["git", "-c", "core.quotepath=false", "-c", "i18n.logOutputEncoding=utf-8",
               "-C", str(self.root), *args]
        try:
            # Never let a Git child inherit the MCP protocol's stdin pipe.
            return subprocess.run(cmd, input=input,
                                  stdin=subprocess.DEVNULL if input is None else None,
                                  capture_output=True, env=full,
                                  timeout=60 if args and args[0] in {"clone", "push", "fetch", "pull"} else 20)
        except subprocess.TimeoutExpired:
            raise GitError("Git operation timed out; completion is unconfirmed") from None

    def run(self, *args: str, input: str | bytes | None = None,
            env: dict[str, str] | None = None) -> str:
        p = self.proc(*args, input=input, env=env)
        if p.returncode != 0:
            raise GitError(f"git {args[0]} 失败：{p.stderr.decode('utf-8', 'replace').strip()}")
        return p.stdout.decode("utf-8")

    @property
    def git_dir(self) -> Path:
        return self.root / ".git"

    def rev_parse(self, ref: str) -> str | None:
        p = self.proc("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.proc("merge-base", "--is-ancestor", a, b).returncode == 0

    def tree_files(self, commit: str) -> dict[str, str]:
        """Map path -> blob sha for every file in the commit's tree."""
        p = self.proc("ls-tree", "-r", "-z", commit)
        if p.returncode != 0:
            raise GitError(p.stderr.decode("utf-8", "replace").strip())
        files = {}
        for entry in p.stdout.split(b"\0"):
            if not entry:
                continue
            meta, path = entry.split(b"\t", 1)
            _mode, kind, sha = meta.split()
            if kind == b"blob":
                files[path.decode("utf-8")] = sha.decode()
        return files

    def read_blobs(self, shas: list[str]) -> dict[str, bytes]:
        if not shas:
            return {}
        p = self.proc("cat-file", "--batch", input="".join(s + "\n" for s in shas))
        out, pos, blobs = p.stdout, 0, {}
        for _ in shas:
            nl = out.index(b"\n", pos)
            header = out[pos:nl].split()
            if header[1] == b"missing":
                pos = nl + 1
                continue
            size = int(header[2])
            blobs[header[0].decode()] = out[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1
        return blobs

    def show(self, commit: str, path: str) -> bytes | None:
        p = self.proc("cat-file", "blob", f"{commit}:{path}")
        return p.stdout if p.returncode == 0 else None

    def commit_changes(self, ref: str, changes: dict[str, bytes | str | None], message: str,
                       author: Identity, base: str | None = None,
                       merge_parents: tuple[str, ...] = ()) -> str:
        """Commit `changes` on top of `ref`: path -> new content (bytes),
        an existing blob sha (str), or None to delete.

        If `ref` does not exist yet it is created from `base`. The ref is moved
        with a compare-and-swap, so a concurrent writer makes this fail rather
        than silently dropping the other commit.
        """
        old = self.rev_parse(ref)
        parent = old or base
        parents = ([parent] if parent else []) + list(merge_parents)
        with tempfile.TemporaryDirectory() as tmp:
            index_env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
            if parent:
                self.run("read-tree", parent, env=index_env)
            for path, data in changes.items():
                if data is None:
                    self.run("update-index", "--force-remove", "--", path, env=index_env)
                else:
                    sha = data if isinstance(data, str) else \
                        self.run("hash-object", "-w", "--stdin", input=data).strip()
                    self.run("update-index", "--add", "--cacheinfo", f"100644,{sha},{path}",
                             env=index_env)
            tree = self.run("write-tree", env=index_env).strip()
        args = ["commit-tree", tree] + [a for p in parents for a in ("-p", p)] + \
            self._sign_args() + ["-F", "-"]
        commit = self.run(*args, input=message, env=author.env()).strip()
        self.run("update-ref", "-m", message.splitlines()[0], ref, commit, old or ZERO_SHA)
        return commit

    def _sign_args(self) -> list[str]:
        """['-S'] when the owner turned on commit.gpgsign (spec §5.2), else []."""
        p = self.proc("config", "--get", "commit.gpgsign")
        if p.returncode == 0 and p.stdout.decode().strip().lower() in ("true", "1", "yes"):
            key = self.proc("config", "--get", "user.signingkey")
            k = key.stdout.decode().strip() if key.returncode == 0 else ""
            return [f"-S{k}" if k else "-S"]
        return []

    def merge_base(self, a: str, b: str) -> str | None:
        p = self.proc("merge-base", a, b)
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def head_branch(self) -> str | None:
        p = self.proc("symbolic-ref", "-q", "HEAD")
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def sync_worktree(self, old: str | None, new: str) -> bool:
        """Bring the checked-out files from `old` to `new` after the branch moved underneath.

        Returns False (and leaves files alone) if local edits would be overwritten.
        """
        args = ["read-tree", "-m", "-u", old, new] if old else ["read-tree", "-u", "--reset", new]
        return self.proc(*args).returncode == 0

    def delete_ref(self, ref: str, expected: str) -> None:
        self.run("update-ref", "-d", ref, expected)

    def list_refs(self, prefix: str) -> list[tuple[str, str]]:
        out = self.run("for-each-ref", "--format=%(refname) %(objectname)", prefix)
        return [tuple(line.split(" ", 1)) for line in out.splitlines() if line]

    # ---- remotes & sync ----------------------------------------------------

    def get_remote_url(self, name: str = "origin") -> str | None:
        p = self.proc("remote", "get-url", name)
        return p.stdout.decode().strip() if p.returncode == 0 else None

    def set_remote(self, url: str, name: str = "origin") -> None:
        if self.get_remote_url(name) is None:
            self.run("remote", "add", name, url)
        else:
            self.run("remote", "set-url", name, url)

    def fetch(self, name: str = "origin") -> None:
        """Update remote-tracking refs. Never prompts (GIT_TERMINAL_PROMPT=0)."""
        p = self.proc("fetch", "--quiet", "--tags", name)
        if p.returncode != 0:
            raise GitError(p.stderr.decode("utf-8", "replace").strip() or "fetch 失败")

    def push(self, refspec: str, name: str = "origin") -> tuple[bool, str]:
        """Push one refspec. Returns (ok, combined output) instead of raising,
        so callers can turn a non-fast-forward rejection into a 'pull first' hint."""
        p = self.proc("push", name, refspec)
        msg = (p.stdout + b"\n" + p.stderr).decode("utf-8", "replace").strip()
        return p.returncode == 0, msg

    # ---- history rewrite (for redaction gc, spec §7) -----------------------

    def rev_list(self, tip: str) -> list[str]:
        """Commits reachable from tip, oldest first (parents before children)."""
        out = self.run("rev-list", "--reverse", "--topo-order", tip)
        return [line for line in out.splitlines() if line]

    def commit_object(self, sha: str) -> dict:
        """Parse a commit object: tree, parents, author, committer, message."""
        raw = self.run("cat-file", "commit", sha)
        head, _, message = raw.partition("\n\n")
        info: dict = {"parents": [], "message": message}
        for line in head.splitlines():
            key, _, val = line.partition(" ")
            if key == "parent":
                info["parents"].append(val)
            elif key in ("tree", "author", "committer"):
                info[key] = val
        return info

    def filtered_tree(self, commit: str, dead: set[str]) -> tuple[str, int]:
        """Build a tree identical to `commit`'s, minus any blob whose sha is in `dead`."""
        p = self.proc("ls-tree", "-r", "-z", commit)
        if p.returncode != 0:
            raise GitError(p.stderr.decode("utf-8", "replace").strip())
        removed = 0
        with tempfile.TemporaryDirectory() as tmp:
            index_env = {"GIT_INDEX_FILE": os.path.join(tmp, "index")}
            for entry in p.stdout.split(b"\0"):
                if not entry:
                    continue
                meta, path = entry.split(b"\t", 1)
                mode, kind, sha = meta.split()
                if kind != b"blob":
                    continue
                if sha.decode() in dead:
                    removed += 1
                    continue
                self.run("update-index", "--add", "--cacheinfo",
                         f"{mode.decode()},{sha.decode()},{path.decode('utf-8')}", env=index_env)
            tree = self.run("write-tree", env=index_env).strip()
        return tree, removed

    def commit_tree_raw(self, tree: str, parents: list[str], message: str,
                        author: str, committer: str) -> str:
        """Recreate a commit faithfully (author/committer identity and dates preserved).

        `author`/`committer` are raw object lines: 'Name <email> 1690000000 +0800'.
        """
        def split(line: str) -> dict:
            m = re.fullmatch(r"(.*) <(.*)> (\d+ [+-]\d{4})", line.strip())
            return {"name": m.group(1), "email": m.group(2), "date": m.group(3)} if m \
                else {"name": line, "email": "", "date": ""}

        a, c = split(author), split(committer)
        env = {
            "GIT_AUTHOR_NAME": a["name"], "GIT_AUTHOR_EMAIL": a["email"], "GIT_AUTHOR_DATE": a["date"],
            "GIT_COMMITTER_NAME": c["name"], "GIT_COMMITTER_EMAIL": c["email"], "GIT_COMMITTER_DATE": c["date"],
        }
        args = ["commit-tree", tree] + [x for p in parents for x in ("-p", p)] + ["-F", "-"]
        return self.run(*args, input=message, env=env).strip()

    def set_ref(self, ref: str, new: str, old: str | None) -> None:
        self.run("update-ref", ref, new, old or ZERO_SHA)

    def prune_now(self) -> None:
        self.proc("reflog", "expire", "--expire=now", "--all")
        self.proc("gc", "--prune=now", "--quiet")

    def log(self, rev: str, path: str | None = None, follow: bool = False) -> list[LogEntry]:
        fmt = "%H%x1f%an%x1f%aI%x1f%s%x1f%(trailers:key=Pmf-Op,valueonly,separator=%x2C)%x1e"
        args = ["log", f"--format={fmt}"] + (["--follow"] if follow else []) + [rev]
        args += ["--", path] if path else []
        entries = []
        for chunk in self.run(*args).split("\x1e"):
            chunk = chunk.strip("\n")
            if chunk:
                sha, author, date, subject, op = chunk.split("\x1f")
                entries.append(LogEntry(sha, author, date, subject, op.strip()))
        return entries
