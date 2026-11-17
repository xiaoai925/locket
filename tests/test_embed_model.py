"""Optional local model embedding backend (embed_model.py).

The library (`sentence-transformers`) is an optional extra. Tests that actually load a
model are skipped when it is absent; the import, `available()`, the unavailable-path
error, and lazy `name` access are always exercised (they must not need the library).
"""
from __future__ import annotations

import pytest

from locket import embed_model
from locket.embed import cosine


def test_import_does_not_require_library():
    # Importing the module must never pull in sentence-transformers.
    assert embed_model.DEFAULT_MODEL
    assert embed_model.INSTALL_HINT


def test_available_returns_bool_without_raising():
    result = embed_model.available()
    assert isinstance(result, bool)


def test_load_raises_with_install_hint_when_unavailable(monkeypatch):
    # Simulate the library being absent regardless of what is installed.
    monkeypatch.setattr(embed_model, "available", lambda: False)
    with pytest.raises(RuntimeError) as exc:
        embed_model.load_model_embedder()
    assert "locket-memory[model]" in str(exc.value)


def test_lazy_name_known_without_loading():
    # name must be available without loading the model (no library needed).
    emb = embed_model.LazyModelEmbedder("some-model")
    assert emb.name == "st:some-model"
    assert emb.loaded is False


def test_lazy_dim_access_raises_hint_when_unavailable(monkeypatch):
    monkeypatch.setattr(embed_model, "available", lambda: False)
    emb = embed_model.LazyModelEmbedder()
    with pytest.raises(RuntimeError):
        _ = emb.dim
    assert emb.loaded is False


# ---- real-model tests: only when sentence-transformers is actually installed ----

requires_model = pytest.mark.skipif(
    not embed_model.available(),
    reason="需要 sentence-transformers：pip install 'locket-memory[model]'",
)


@requires_model
def test_load_model_embedder_produces_normalized_vectors():
    import math

    emb = embed_model.load_model_embedder()
    vec = emb.embed("用户喜欢在清晨跑步")
    assert len(vec) == emb.dim
    assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-6


@requires_model
def test_semantic_similarity_beats_unrelated():
    emb = embed_model.load_model_embedder()
    q = emb.embed("用户的交通工具")
    near = emb.embed("用户开特斯拉上下班")
    far = emb.embed("今天的天气很适合晒被子")
    assert cosine(q, near) > cosine(q, far)


@requires_model
def test_lazy_embedder_loads_on_first_use():
    emb = embed_model.LazyModelEmbedder()
    assert emb.loaded is False
    vec = emb.embed("一条测试记忆")
    assert emb.loaded is True
    assert len(vec) == emb.dim
