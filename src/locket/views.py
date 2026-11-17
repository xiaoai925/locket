"""PMF views (spec §9.2): deterministic, read-only, shareable subsets of memory.

A view is a branch `refs/heads/view/<name>` holding only the memories selected by a
rule in `.pmf/views.toml` on main. It lets the owner share *part* of their memory
(e.g. only work-related, public memories) with another person or AI, without giving
access to the whole repo.

Guarantees:
- **Deterministic**: the same main tree and the same rule always produce the same
  view *tree* (the commit sha may differ only by timestamp). A view is a pure
  function of (main contents, rule).
- **Read-only**: views are regenerated from main, never written to directly.
- **Filtered**: a memory is included only if its path matches the rule's `include`
  globs (default: every memory), matches none of its `exclude` globs, and its
  sensitivity is at or below the view's `max_sensitivity` (default `public`).
- **secret never leaves**: `secret` memories are excluded from every view
  unconditionally (they require §9.3 encryption, not plaintext sharing).

Only memory files are copied in; `.pmf/`, `conflicts/` and tombstones never appear
in a view.
"""
from __future__ import annotations

import fnmatch
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from dataclasses import dataclass

from . import __version__
from .gitrepo import Identity
from .memory import parse
from .scope import rank
from .store import MAIN, is_memory_path

VIEWS_PATH = ".pmf/views.toml"
VIEW_PREFIX = "refs/heads/view/"
VIEW_IDENTITY = Identity("locket-view", "view@locket.local")
DEFAULT_MAX_SENSITIVITY = "public"


@dataclass
class ViewResult:
    name: str
    ref: str
    commit: str | None
    count: int


def _ref(name: str) -> str:
    return f"{VIEW_PREFIX}{name}"


def view_specs(store) -> dict[str, dict]:
    """Parse `.pmf/views.toml` on main into {name: {include, exclude, max_sensitivity, ref}}.

    Missing file or empty config returns {}. Unknown keys are ignored; missing keys
    fall back to sensible defaults (include everything, exclude nothing, public).
    """
    raw = store.git.show(MAIN, VIEWS_PATH)
    if not raw:
        return {}
    data = tomllib.loads(raw.decode("utf-8")) or {}
    specs: dict[str, dict] = {}
    for name, cfg in data.items():
        if not isinstance(cfg, dict):
            continue
        include = cfg.get("include")
        exclude = cfg.get("exclude")
        specs[name] = {
            "include": list(include) if include else ["**"],
            "exclude": list(exclude) if exclude else [],
            "max_sensitivity": str(cfg.get("max_sensitivity", DEFAULT_MAX_SENSITIVITY)),
            "ref": _ref(name),
        }
    return specs


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in patterns)


def _selected(store, spec: dict) -> dict[str, str]:
    """Return {path: blob sha} of the memories on main that this view includes."""
    main = store.git.rev_parse(MAIN)
    if not main:
        return {}
    files = {p: sha for p, sha in store.git.tree_files(main).items() if is_memory_path(p)}
    include, exclude = spec["include"], spec["exclude"]
    ceiling = rank(spec["max_sensitivity"])
    secret = rank("secret")
    blobs = store.git.read_blobs(list(files.values()))
    chosen: dict[str, str] = {}
    for path in sorted(files):  # sorted → stable iteration (tree is order-independent anyway)
        if not _matches(path, include) or _matches(path, exclude):
            continue
        try:
            memory = parse(blobs[files[path]], path)
        except (ValueError, KeyError, UnicodeDecodeError):
            continue
        level = rank(memory.meta.get("sensitivity"))
        if level >= secret or level > ceiling:
            continue  # secret never leaves; nothing above the view's ceiling
        chosen[path] = files[path]
    return chosen


def generate_view(store, name: str) -> ViewResult:
    """(Re)generate the `view/<name>` branch from main. Deterministic in its tree."""
    store.require()
    specs = view_specs(store)
    if name not in specs:
        from .store import LocketError
        raise LocketError(f"views.toml 中没有名为 {name} 的视图")
    spec = specs[name]
    ref = spec["ref"]
    chosen = _selected(store, spec)

    old = store.git.rev_parse(ref)
    # Build a tree equal to EXACTLY the chosen set: delete any stale path left from a
    # previous generation, then write the chosen blobs. commit_changes builds on the
    # old ref's tree, so the explicit deletes are what make regeneration deterministic.
    changes: dict[str, str | None] = {}
    if old:
        for path in store.git.tree_files(old):
            if path not in chosen:
                changes[path] = None
    for path, sha in chosen.items():
        changes[path] = sha

    message = (f"view: {name}\n\n"
               f"从 main 生成只读视图，收录 {len(chosen)} 条记忆。\n\n"
               f"Pmf-Op: view\n"
               f"Pmf-View: {name}\n"
               f"Pmf-Agent: locket {__version__}\n")
    commit = store.git.commit_changes(ref, changes, message, VIEW_IDENTITY)
    return ViewResult(name=name, ref=ref, commit=commit, count=len(chosen))


def generate_all(store) -> list[ViewResult]:
    """Regenerate every view defined in views.toml. No views → []."""
    return [generate_view(store, name) for name in view_specs(store)]


def list_views(store) -> list[tuple[str, str]]:
    """Existing view branches as (name, commit sha)."""
    out = []
    for ref, sha in store.git.list_refs(VIEW_PREFIX.rstrip("/")):
        if ref.startswith(VIEW_PREFIX):
            out.append((ref[len(VIEW_PREFIX):], sha))
    return sorted(out)
