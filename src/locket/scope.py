"""Read scope and sensitivity enforcement (spec §8 scope, §9 sensitivity).

Pure, dependency-free policy logic so it can be tested in isolation and reused by
the service, the CLI and the tenancy layer. The service calls `visible()` to filter
every read and `may_write()` to guard every write, so a principal can never see or
create a memory outside its granted scope — the hard gate before multi-user sharing.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

# Ordered least → most sensitive (spec §9.1). A principal reads everything at or
# below its `max_sensitivity`; `secret` additionally requires encryption (§9.3,
# not yet implemented) so it is never emitted in a view.
SENSITIVITY_ORDER = ("public", "personal", "sensitive", "secret")
DEFAULT_SENSITIVITY = "personal"


def rank(level: str | None) -> int:
    """Numeric rank of a sensitivity level; unknown/empty → the default."""
    try:
        return SENSITIVITY_ORDER.index(str(level or DEFAULT_SENSITIVITY))
    except ValueError:
        return SENSITIVITY_ORDER.index(DEFAULT_SENSITIVITY)


def about_matches(about: str, patterns: tuple[str, ...]) -> bool:
    """Does `about` (e.g. 'self', 'project:si-ag') match any granted pattern?

    Patterns are globs over the about string: '*' = everything, 'project:*' = all
    projects, 'self' = exactly self. An empty pattern set grants nothing.
    """
    return any(fnmatch.fnmatchcase(about, pat) for pat in patterns)


@dataclass(frozen=True)
class Scope:
    """What a principal is allowed to read and write.

    - `max_sensitivity`: highest level readable (default `personal`; `secret` is
      never readable until encryption lands).
    - `about`: glob patterns of `about` values in scope (default everything).
    - `can_write`: may create/update/forget memories at all.
    """
    max_sensitivity: str = DEFAULT_SENSITIVITY
    about: tuple[str, ...] = ("*",)
    can_write: bool = True

    def can_read(self, *, about: str, sensitivity: str | None) -> bool:
        if rank(sensitivity) > rank(self.max_sensitivity):
            return False
        if rank(sensitivity) >= rank("secret"):
            return False  # secret is never emitted (needs §9.3 encryption)
        return about_matches(about, self.about)

    def allows_write_sensitivity(self, sensitivity: str | None) -> bool:
        return rank(sensitivity) <= rank(self.max_sensitivity)

    def can_write_about(self, about: str) -> bool:
        return self.can_write and about_matches(about, self.about)


# Owner of the repo: sees and writes everything (the default for CLI / single-user).
OWNER_SCOPE = Scope(max_sensitivity="sensitive", about=("*",), can_write=True)
