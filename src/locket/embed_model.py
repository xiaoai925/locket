"""Optional local model embedding backend for true semantic recall (P6, high-quality tier).

The default `HashingEmbedder` in `embed.py` only captures surface token overlap, so it will
not relate "交通工具" to "特斯拉". This module provides a real sentence-embedding backend that
does, running **locally** (no cloud, no API key) via `sentence-transformers`.

It is an OPTIONAL dependency, kept strictly lazy:
  - importing this module NEVER requires `sentence-transformers` (nothing heavy at top level);
  - `available()` reports whether the library is present, without raising;
  - the model is only loaded (and downloaded, once) inside `load_model_embedder`, or on the
    first use of a `LazyModelEmbedder`.
When the library is missing, construction raises a clear install hint rather than crashing.

Default model: ``paraphrase-multilingual-MiniLM-L12-v2`` (384-dim). Rationale: Locket memories
mix Chinese and English ("用户开特斯拉上班", "prefers dark mode"), so a *multilingual* model is
the safe default; MiniLM-L12 is small (~470 MB) and fast enough to run on a phone in Termux,
yet gives genuine cross-lingual semantic matching. A Chinese-only model such as
``BAAI/bge-small-zh-v1.5`` scores higher on pure-Chinese tasks but is weaker on the English
fragments that pepper developer memories, so it is not the default (pass it explicitly to
``load_model_embedder`` if a deployment is Chinese-only).

INTEGRATOR WIRING (not done here — a parallel task owns cli/store/service/pyproject):
  - pyproject: ``[project.optional-dependencies]`` add ``model = ["sentence-transformers>=2.2"]``
    (installs torch + transformers transitively).
  - ``store.embedder()``: when ``settings.embedding.backend == "model"``, return
    ``embed_model.LazyModelEmbedder(settings.embedding.model or embed_model.DEFAULT_MODEL)``.
    Lazy construction keeps startup cheap; the model loads on first recall/index build.
    The vector index stores whatever ``dim`` the active embedder reports and is rebuildable,
    so switching to/from this backend just means a reindex.
"""
from __future__ import annotations

import importlib.util

from . import embed

DEFAULT_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
INSTALL_HINT = "需要: pip install 'locket-memory[model]'"


def available() -> bool:
    """True when `sentence-transformers` can be imported. Never raises."""
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


def load_model_embedder(model: str = DEFAULT_MODEL, *, cache: bool = True) -> embed.Embedder:
    """Load a sentence-transformers model and wrap it as an `Embedder` (eager load).

    `model` is a sentence-transformers model id or local path. `cache=True` uses the
    library's default on-disk cache (so the download happens only once); `cache=False`
    is a hint to avoid persisting — honoured only if the backend supports it, otherwise
    a no-op. Raises RuntimeError with an install hint when the library is absent.
    """
    if not available():
        raise RuntimeError(INSTALL_HINT)
    from sentence_transformers import SentenceTransformer

    kwargs = {}
    if not cache:
        # best-effort: keep the model out of the shared default cache
        import tempfile
        kwargs["cache_folder"] = tempfile.mkdtemp(prefix="locket-st-")
    st_model = SentenceTransformer(model, **kwargs)
    dim = int(st_model.get_sentence_embedding_dimension())

    def encode(text: str) -> list[float]:
        # encode a single string -> 1-D vector; CallableEmbedder L2-normalizes it.
        vec = st_model.encode(text or "", show_progress_bar=False)
        return [float(x) for x in vec.tolist()]

    return embed.CallableEmbedder(encode, dim=dim, name=f"st:{model}")


class LazyModelEmbedder:
    """An `Embedder` that defers loading the model until first use.

    Construction is cheap and library-free, so `settings.embedding.backend == "model"`
    can hand one back without paying the model-load cost up front. The model loads on the
    first access to `dim` or `embed()` — in practice when the vector index is first built.
    `name` is known without loading, so it can be compared cheaply.
    """

    def __init__(self, model: str = DEFAULT_MODEL, *, cache: bool = True):
        self._model = model
        self._cache = cache
        self._impl: embed.Embedder | None = None

    def _ensure(self) -> embed.Embedder:
        if self._impl is None:
            self._impl = load_model_embedder(self._model, cache=self._cache)
        return self._impl

    @property
    def name(self) -> str:
        return f"st:{self._model}"

    @property
    def loaded(self) -> bool:
        return self._impl is not None

    @property
    def dim(self) -> int:
        return self._ensure().dim

    def embed(self, text: str) -> list[float]:
        return self._ensure().embed(text)
