"""sentence-transformers provider with MPS / CUDA / CPU auto-selection.

Supports the full hugging-face ecosystem including:
- dunzhang/stella_en_1.5B_v5  (1024 dim matryoshka, ~3 GB, MTEB ~67)
- BAAI/bge-large-en-v1.5       (1024 dim, ~1.3 GB, MTEB ~54)
- Alibaba-NLP/gte-Qwen2-7B-instruct (3584 dim, ~15 GB, MTEB ~70)
- intfloat/e5-mistral-7b-instruct  (4096 dim, ~14 GB, MTEB ~67)

Model + tokenizer cache under `~/.cache/huggingface/hub` by default.

The model is loaded lazily and cached in module scope so long-lived
processes (MCP server, web UI) only pay the load cost once.
"""

from __future__ import annotations

import logging
from typing import Sequence

log = logging.getLogger("hippocampus.embeddings.st")


def _pick_device() -> str:
    """Return the best available device string for sentence-transformers."""
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


class StProvider:
    def __init__(
        self,
        model: str = "dunzhang/stella_en_1.5B_v5",
        *,
        truncate_dim: int | None = None,
        trust_remote_code: bool = True,
        device: str | None = None,
    ) -> None:
        from sentence_transformers import SentenceTransformer  # lazy

        resolved_device = device or _pick_device()
        log.info("loading %s on device=%s (trust_remote_code=%s)",
                 model, resolved_device, trust_remote_code)

        load_kwargs: dict = {"device": resolved_device}
        if trust_remote_code:
            load_kwargs["trust_remote_code"] = True
        # sentence-transformers ≥ 3.0 honours truncate_dim at load time for
        # Matryoshka models; older versions ignore it (we truncate manually).
        if truncate_dim is not None:
            load_kwargs["truncate_dim"] = truncate_dim

        self._impl = SentenceTransformer(model, **load_kwargs)
        self._model_name = model
        self._truncate_dim = truncate_dim
        self._device = resolved_device

        # Learn dim by embedding one probe text.
        sample = self._impl.encode(
            ["probe"],
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,
        )
        if sample is None or len(sample) == 0:
            raise RuntimeError(f"sentence-transformers returned no output for probe on {model}")
        self._dim = int(sample.shape[1])
        if truncate_dim and self._dim > truncate_dim:
            self._dim = truncate_dim
        log.info("provider ready: %s dim=%d device=%s", model, self._dim, resolved_device)

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def device(self) -> str:
        return self._device

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._impl.encode(
            list(texts),
            convert_to_numpy=True,
            show_progress_bar=False,
            normalize_embeddings=True,  # unit-norm; cosine == dot product
        )
        if self._truncate_dim is not None and vectors.shape[1] > self._truncate_dim:
            vectors = vectors[:, : self._truncate_dim]
        return [v.tolist() for v in vectors]
