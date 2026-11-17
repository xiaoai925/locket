"""Device-local settings: how *this* device uses memory (P7).

A personal, per-device configuration the user drives from the phone's settings screen —
the memory-source mode, which cloud to sync with, which embedding to use, and privacy/
sync preferences. It is **device-local and never committed or synced**: it lives in
`state_dir` (under `.git/pmf/`), because "which cloud / which embedding / my token" is a
property of the device, not of the shared memory repo. Repo-wide policy (whether secret
is encrypted, whether main needs approval) still lives in the synced `.pmf/config.toml`;
this file only holds what differs per device.

The engine reads these to resolve the embedder and the sync remote and to decide, in
`cloud`/`hybrid` mode, whether to auto pull/push. Clients (the phone UI) read and write
them through the service/API so the user's choices take effect without touching code.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Memory-source modes the user can pick on the phone:
#   local  — only this device's local repo; never auto-syncs; recall is local.
#   cloud  — kept in step with the cloud: auto pull before reads, push after writes.
#   hybrid — local-first; syncs with the cloud on demand / when auto_sync is on.
MODES = ("local", "cloud", "hybrid")
# Embedding backends (see embed.py). "off" = pure BM25; "local" = offline HashingEmbedder;
# "model"/"cloud" are higher quality but injected by the host (need a model or network).
EMBED_BACKENDS = ("off", "local", "model", "cloud")
SETTINGS_FILE = "settings.json"


@dataclass
class CloudSettings:
    url: str = ""          # Gitea base URL, e.g. http://host:3000
    repo: str = "memory"   # the user's memory repo name on that host
    # Token is device-local and sensitive: kept only in this local file, never synced.
    token: str = field(default="", repr=False)

    def configured(self) -> bool:
        return bool(self.url)


@dataclass
class EmbeddingSettings:
    backend: str = "off"   # off | local | model | cloud
    model: str = ""        # optional model id/path for the model/cloud backends

    def normalized(self) -> str:
        return self.backend if self.backend in EMBED_BACKENDS else "off"


@dataclass
class PrivacySettings:
    default_sensitivity: str = "personal"  # default level for new memories
    auto_sync: bool = False                 # in cloud/hybrid: pull before read, push after write
    encrypt_secret: bool = True             # honour secret encryption (needs recipients + crypto)


@dataclass
class DeviceSettings:
    mode: str = "local"
    cloud: CloudSettings = field(default_factory=CloudSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)

    # ---- derived helpers the engine uses ----

    def normalized_mode(self) -> str:
        return self.mode if self.mode in MODES else "local"

    def semantic_on(self) -> bool:
        return self.embedding.normalized() != "off"

    def should_auto_sync(self) -> bool:
        return self.normalized_mode() in ("cloud", "hybrid") and self.privacy.auto_sync

    def to_public_dict(self) -> dict:
        """JSON-able view for the UI, with the token redacted to a boolean flag."""
        d = {
            "mode": self.normalized_mode(),
            "cloud": {"url": self.cloud.url, "repo": self.cloud.repo,
                      "has_token": bool(self.cloud.token)},
            "embedding": {"backend": self.embedding.normalized(), "model": self.embedding.model},
            "privacy": asdict(self.privacy),
            "modes": list(MODES),
            "embed_backends": list(EMBED_BACKENDS),
        }
        return d


def _merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def from_dict(data: dict) -> DeviceSettings:
    data = data or {}
    cloud = data.get("cloud") or {}
    emb = data.get("embedding") or {}
    priv = data.get("privacy") or {}
    return DeviceSettings(
        mode=str(data.get("mode", "local")),
        cloud=CloudSettings(url=str(cloud.get("url", "")), repo=str(cloud.get("repo", "memory")),
                            token=str(cloud.get("token", ""))),
        embedding=EmbeddingSettings(backend=str(emb.get("backend", "off")),
                                    model=str(emb.get("model", ""))),
        privacy=PrivacySettings(
            default_sensitivity=str(priv.get("default_sensitivity", "personal")),
            auto_sync=bool(priv.get("auto_sync", False)),
            encrypt_secret=bool(priv.get("encrypt_secret", True))),
    )


def load(path: Path | str) -> DeviceSettings:
    p = Path(path)
    if not p.exists():
        return DeviceSettings()
    try:
        return from_dict(json.loads(p.read_text("utf-8")))
    except (ValueError, OSError):
        return DeviceSettings()  # corrupt/unreadable → safe defaults


def save(path: Path | str, settings: DeviceSettings) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), "utf-8")


def update(path: Path | str, patch: dict) -> DeviceSettings:
    """Apply a partial update (deep-merged) and persist. Returns the new settings.

    Validates enums; a bad value falls back to the current/default rather than erroring,
    so a UI can send a subset of fields safely."""
    current = asdict(load(path))
    merged = _merge(current, patch or {})
    settings = from_dict(merged)
    if settings.mode not in MODES:
        settings.mode = "local"
    if settings.embedding.backend not in EMBED_BACKENDS:
        settings.embedding.backend = "off"
    save(path, settings)
    return settings
