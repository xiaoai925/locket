"""Git credentials supplied only to child environments, never remote URLs."""
from __future__ import annotations

import shlex
import sys
import tempfile
from pathlib import Path

from .gitrepo import Git

_ASKPASS: str | None = None


def askpass_program() -> str:
    """Path of a directly executable askpass wrapper.

    Git spawns GIT_ASKPASS as a single program (no shell, no argv splitting), so
    "python git_askpass.py" fails with "cannot spawn". A tiny /bin/sh wrapper works
    on Linux/macOS and with Git for Windows (which honours shebangs). It holds no
    secret: credentials still arrive only through the child environment.
    """
    global _ASKPASS
    if _ASKPASS is None or not Path(_ASKPASS).exists():
        helper = Path(__file__).with_name("git_askpass.py").as_posix()
        script = Path(tempfile.mkdtemp(prefix="locket-askpass-")) / "askpass"
        script.write_text("#!/bin/sh\nexec " + shlex.quote(Path(sys.executable).as_posix())
                          + " " + shlex.quote(helper) + ' "$@"\n', encoding="utf-8", newline="\n")
        script.chmod(0o700)
        _ASKPASS = script.as_posix()
    return _ASKPASS


class CredentialGit(Git):
    def __init__(self, root, user: str, token: str):
        super().__init__(root)
        self.user, self.token = user, token

    def proc(self, *args, input=None, env=None):
        full = dict(env or {})
        full.update({
            "GIT_ASKPASS": askpass_program(),
            "GCM_INTERACTIVE": "never", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
            "LOCKET_GIT_USER": self.user, "LOCKET_GIT_TOKEN": self.token,
        })
        result = super().proc(*args, input=input, env=full)
        if result.returncode:
            # Never forward provider stderr, which may contain credentials/URLs.
            result.stderr = b"Authenticated Git operation failed"
        return result
