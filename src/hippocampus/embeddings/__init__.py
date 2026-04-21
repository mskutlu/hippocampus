"""Embedding provider + vector store + semantic search.

Hippocampus uses **local** embeddings by default (fastembed + ONNX), so
nothing ever leaves the machine. The provider layer is pluggable so other
backends (OpenAI, sentence-transformers, llama.cpp, …) can be added by
writing an adapter that implements the `EmbeddingProvider` protocol.

Graceful degradation is a core requirement: if `fastembed` is not installed
or the model download fails, the system falls back to FTS-only recall and
logs a warning rather than crashing.
"""

from __future__ import annotations

import logging
from typing import Protocol, Sequence

log = logging.getLogger("hippocampus.embeddings")


class EmbeddingProvider(Protocol):
    """A minimal provider contract."""

    model: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return a list of vectors, one per input."""


_provider_singleton: EmbeddingProvider | None = None
_provider_available: bool | None = None


def load_provider() -> EmbeddingProvider | None:
    """Load the configured provider. Returns None if unavailable."""
    global _provider_singleton, _provider_available
    if _provider_singleton is not None:
        return _provider_singleton
    if _provider_available is False:
        return None

    from hippocampus import config  # local import to avoid cycles

    name = (config.get_setting("embedding_provider") or "fastembed").lower()
    model = config.get_setting("embedding_model") or "BAAI/bge-small-en-v1.5"
    truncate_dim = config.get_setting("embedding_truncate_dim")
    trust_remote_code = bool(config.get_setting("embedding_trust_remote_code"))

    try:
        if name == "fastembed":
            from hippocampus.embeddings.fastembed_provider import FastEmbedProvider

            _provider_singleton = FastEmbedProvider(model=model)

        elif name in ("sentence-transformers", "sentence_transformers", "st"):
            from hippocampus.embeddings.st_provider import StProvider

            _provider_singleton = StProvider(
                model=model,
                truncate_dim=int(truncate_dim) if truncate_dim else None,
                trust_remote_code=trust_remote_code,
            )
        else:
            log.warning("unknown embedding provider: %s", name)
            _provider_available = False
            return None

        _provider_available = True
        log.info("embedding provider loaded: %s / %s (dim=%d)",
                 name, model, _provider_singleton.dim)
        return _provider_singleton

    except ImportError as e:
        hint = (
            "sentence-transformers" if name != "fastembed" else "hippocampus[semantic]"
        )
        log.warning(
            "embedding provider %s unavailable (%s). "
            "Install with `uv pip install -e '.[%s]'` to enable semantic recall.",
            name, e, "heavy" if hint == "sentence-transformers" else "semantic",
        )
        _provider_available = False
        return None
    except Exception as e:  # noqa: BLE001
        log.exception("failed to initialise embedding provider: %s", e)
        _provider_available = False
        return None


def reset_provider() -> None:
    """Test hook: forget the cached provider instance."""
    global _provider_singleton, _provider_available
    _provider_singleton = None
    _provider_available = None


def set_provider(provider: EmbeddingProvider | None) -> None:
    """Test hook: install a provider instance directly."""
    global _provider_singleton, _provider_available
    _provider_singleton = provider
    _provider_available = provider is not None


def provider_available() -> bool:
    return load_provider() is not None
