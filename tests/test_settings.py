"""Device settings (P7): per-device memory mode / cloud / embedding / privacy.

Device-local, not committed; drives the embedder and sync remote; exposed to the
phone UI through the service.
"""
import json

import pytest

from locket import settings as S
from locket.store import Locket
from locket.service import MemoryService, Principal


def test_defaults_are_local_and_off(tmp_path):
    s = S.load(tmp_path / "settings.json")
    assert s.normalized_mode() == "local"
    assert s.embedding.normalized() == "off"
    assert s.semantic_on() is False
    assert s.should_auto_sync() is False


def test_update_deep_merges_and_persists(tmp_path):
    p = tmp_path / "settings.json"
    S.update(p, {"mode": "hybrid", "embedding": {"backend": "local"}})
    S.update(p, {"cloud": {"url": "http://host:3000"}})  # partial, must not wipe embedding
    s = S.load(p)
    assert s.normalized_mode() == "hybrid"
    assert s.embedding.normalized() == "local" and s.semantic_on()
    assert s.cloud.url == "http://host:3000" and s.cloud.configured()


def test_bad_enum_falls_back(tmp_path):
    p = tmp_path / "settings.json"
    s = S.update(p, {"mode": "bogus", "embedding": {"backend": "nope"}})
    assert s.mode == "local" and s.embedding.backend == "off"


def test_auto_sync_requires_cloud_or_hybrid(tmp_path):
    p = tmp_path / "settings.json"
    s = S.update(p, {"mode": "local", "privacy": {"auto_sync": True}})
    assert s.should_auto_sync() is False          # local never auto-syncs
    s = S.update(p, {"mode": "cloud"})
    assert s.should_auto_sync() is True


def test_public_dict_redacts_token(tmp_path):
    p = tmp_path / "settings.json"
    S.update(p, {"cloud": {"url": "http://h", "token": "SECRET123"}})
    pub = S.load(p).to_public_dict()
    assert pub["cloud"]["has_token"] is True
    assert "token" not in pub["cloud"]            # never exposed
    assert "SECRET123" not in json.dumps(pub)


def test_corrupt_file_is_safe(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ not json", "utf-8")
    assert S.load(p).normalized_mode() == "local"  # falls back, no raise


def test_store_embedder_follows_settings(tmp_path):
    store = Locket(tmp_path / "mem")
    store.init("T <t@x>")
    assert store.embedder() is None               # default off
    store.save_settings({"embedding": {"backend": "local"}})
    assert store.embedder() is not None           # now the offline embedder
    assert store.vectors is not None


def test_store_settings_sets_remote(tmp_path):
    store = Locket(tmp_path / "mem")
    store.init("T <t@x>")
    store.save_settings({"cloud": {"url": "http://host:3000/", "repo": "memory"}})
    assert store.git.get_remote_url("origin") == "http://host:3000/memory.git"


def test_service_get_update_settings(tmp_path):
    store = Locket(tmp_path / "mem")
    store.init("T <t@x>")
    svc = MemoryService(repo=tmp_path / "mem")
    p = Principal()
    got = svc.get_settings(p)["settings"]
    assert got["mode"] == "local" and "has_token" in got["cloud"]
    updated = svc.update_settings(p, {"mode": "hybrid", "cloud": {"token": "t"}})["settings"]
    assert updated["mode"] == "hybrid" and updated["cloud"]["has_token"] is True
    assert "token" not in updated["cloud"]
