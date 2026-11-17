"""PMF memory files: YAML front matter followed by a Markdown body (spec §4)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

import yaml

SPEC_VERSION = "0.2"
CORE_TYPES = ("fact", "preference", "event", "procedure", "summary", "relation")
PROTECTED_FIELDS = ("pmf", "id", "created")
_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


def _without_timestamps(resolvers: dict) -> dict:
    return {first: [(tag, rx) for tag, rx in items if tag != _TIMESTAMP_TAG]
            for first, items in resolvers.items()}


class _Loader(yaml.SafeLoader):
    """Reads dates as the exact strings that were written, so they round-trip unchanged."""


class _Dumper(yaml.SafeDumper):
    pass


_Loader.yaml_implicit_resolvers = _without_timestamps(yaml.SafeLoader.yaml_implicit_resolvers)
_Dumper.yaml_implicit_resolvers = _without_timestamps(yaml.SafeDumper.yaml_implicit_resolvers)

_FRONT_MATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$\n?(.*)\Z", re.S | re.M)
_ABOUT = re.compile(r"self|world|(project|person):\S+")


def load_yaml(text: str):
    return yaml.load(text, Loader=_Loader)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return date.today().isoformat()


@dataclass
class Memory:
    meta: dict
    body: str
    path: str | None = None

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def type(self) -> str:
        return str(self.meta.get("type", ""))

    @property
    def about(self) -> str:
        return str(self.meta.get("about", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or "")

    @property
    def label(self) -> str:
        return self.title or shorten(self.body, 40)

    @property
    def importance(self) -> float:
        try:
            return float(self.meta.get("importance", 0.5))
        except (TypeError, ValueError):
            return 0.5

    @property
    def active(self) -> bool:
        """False once `valid_to` has passed (the memory describes the past)."""
        valid_to = self.meta.get("valid_to")
        return not valid_to or str(valid_to) > today()

    def search_text(self) -> str:
        tags = " ".join(str(t) for t in self.meta.get("tags") or [])
        # An encrypted (secret) body is ciphertext; indexing it would leak nothing but
        # would pollute recall with noise tokens, so search only title + tags for those.
        body = "" if "locket-enc-v1" in self.body[:64] else self.body
        return f"{self.title} {self.title} {tags} {body}"

    def dumps(self) -> bytes:
        front = yaml.dump(self.meta, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=1000)
        return f"---\n{front}---\n{self.body.strip()}\n".encode("utf-8")


def parse(data: bytes | str, path: str | None = None) -> Memory:
    text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if not match:
        raise ValueError(f"{path or '记忆文件'}：缺少 front matter")
    meta = load_yaml(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError(f"{path or '记忆文件'}：front matter 不是键值表")
    return Memory(meta=meta, body=match.group(2).strip(), path=path)


def shorten(text: str, limit: int) -> str:
    line = " ".join(text.split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def slugify(text: str, max_len: int = 40) -> str:
    """ASCII-only, so paths behave the same on every OS."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-")


def scope_slug(name: str) -> str:
    return slugify(name) or "x" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]


def validate_about(about: str) -> str:
    if not _ABOUT.fullmatch(about):
        raise ValueError(f"about 必须是 self、world、project:<名字> 或 person:<名字>，收到 {about!r}")
    return about


def default_path(meta: dict, when: datetime) -> str:
    """Where a new memory goes. Paths only organise files; `id` is the identity."""
    about, kind = meta["about"], meta["type"]
    if kind == "event":
        folder = f"episodes/{when:%Y/%m}"
    elif kind == "procedure":
        folder = "procedures"
    elif about == "self":
        folder = "self"
    elif about.startswith("project:"):
        folder = "projects/" + scope_slug(about.split(":", 1)[1])
    elif about.startswith("person:"):
        folder = "people/" + scope_slug(about.split(":", 1)[1])
    else:
        folder = "world"
    name = slugify(str(meta.get("title") or "")) or "m"
    return f"{folder}/{name}-{meta['id'][-8:].lower()}.md"
