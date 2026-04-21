"""fastembed adapter.

The model is downloaded on first use and cached under
`~/.hippocampus/models/`. Subsequent inference calls are CPU-local and
fast.

We keep the model object in module scope so a long-lived process (the MCP
server, the web UI) only pays the load cost once.
"""

from __future__ import annotations

from typing import Sequence

from hippocampus import config


class FastEmbedProvider:
    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding  # imported lazily

        cache_dir = config.HIPPOCAMPUS_HOME / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)

        self.model_name = model
        self._impl = TextEmbedding(
            model_name=model,
            cache_dir=str(cache_dir),
        )
        # Peek one vector to learn dim.
        sample = list(self._impl.embed(["probe"]))
        if not sample:
            raise RuntimeError("fastembed returned no output for probe text")
        self._dim = len(sample[0])

    @property
    def model(self) -> str:
        return self.model_name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [list(map(float, v)) for v in self._impl.embed(list(texts))]
        return vectors
