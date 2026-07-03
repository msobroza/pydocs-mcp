"""FastEmbedEmbedder — Embedder backed by fastembed.TextEmbedding."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fastembed import TextEmbedding  # type: ignore[import-not-found]

from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)
from pydocs_mcp.models import Embedding

# fastembed's custom-model registry is process-global CLASS state and
# re-registering a label raises ValueError (verified on 0.8.0) — so local-dir
# registrations are memoized here. Keyed by label (directory basename);
# the value is the full recipe so a same-label/different-recipe collision
# fails loudly instead of silently serving the first directory's model.
_REGISTERED_LOCAL_MODELS: dict[str, tuple[str, int, str, bool, str]] = {}


def _register_local_model(
    model_dir: Path,
    *,
    dim: int,
    pooling: str,
    normalize: bool,
    model_file: str,
) -> str:
    """Register a side-loaded model dir with fastembed; return its label."""
    # Lazy import: the submodule is only needed in local mode, and tests mock
    # the fastembed package per the file's existing convention.
    from fastembed.common.model_description import (  # type: ignore[import-not-found]
        ModelSource,
        PoolingType,
    )

    label = model_dir.name
    recipe = (str(model_dir), dim, pooling, normalize, model_file)
    previous = _REGISTERED_LOCAL_MODELS.get(label)
    if previous == recipe:
        return label
    if previous is not None:
        raise ValueError(
            f"Local model label {label!r} is already registered for "
            f"{previous[0]!r} with a different recipe; cannot re-register it "
            f"for {model_dir}. Rename one model directory so labels are unique."
        )
    pooling_types = {
        "mean": PoolingType.MEAN,
        "cls": PoolingType.CLS,
        "disabled": PoolingType.DISABLED,
    }
    # ModelSource requires at least one source (0.8.0 raises on empty), but a
    # local load never consults it: TextEmbedding's download_model()
    # short-circuits on specific_model_path. The label is a harmless dummy.
    TextEmbedding.add_custom_model(
        model=label,
        pooling=pooling_types[pooling],
        normalization=normalize,
        sources=ModelSource(hf=label),
        dim=dim,
        model_file=model_file,
    )
    _REGISTERED_LOCAL_MODELS[label] = recipe
    return label


@dataclass
class FastEmbedEmbedder:
    """Embedder backed by FastEmbed (ONNX-accelerated, no API key).

    Zero-copy from FastEmbed's TextEmbedding.embed() yields straight
    through to our Embedding type — both are np.ndarray (1D, float32).

    When ``model_name`` is a local DIRECTORY (airgap side-load), the folder
    is registered as a fastembed custom model and loaded via
    ``specific_model_path`` — zero network. ``pooling`` / ``normalize`` /
    ``model_file_name`` state the recipe fastembed cannot read from an
    arbitrary ONNX folder; they are ignored on the online repo-id path.
    """

    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    # Execution device — drives the onnxruntime provider list so the same
    # config can run CPU or GPU without code changes.
    device: str = "cpu"
    pooling: str = "mean"
    normalize: bool = True
    model_file_name: str | None = None
    _model: TextEmbedding = field(init=False, repr=False)

    def __post_init__(self) -> None:
        ctor_kwargs: dict[str, Any] = {}
        if self.device == "cuda":
            # CPU listed second as graceful fallback when the GPU runtime
            # is absent (onnxruntime warns and uses CPU rather than crashing).
            ctor_kwargs["providers"] = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
        model_dir = local_model_dir(self.model_name)
        if model_dir is not None:
            enable_hf_offline()
            label = _register_local_model(
                model_dir,
                dim=self.dim,
                pooling=self.pooling,
                normalize=self.normalize,
                model_file=self.model_file_name or "onnx/model.onnx",
            )
            self._model = TextEmbedding(
                model_name=label,
                specific_model_path=str(model_dir),
                **ctor_kwargs,
            )
            return
        self._model = TextEmbedding(model_name=self.model_name, **ctor_kwargs)

    async def embed_query(self, text: str) -> Embedding:
        results = await asyncio.to_thread(
            lambda: list(self._model.embed([text])),
        )
        # FastEmbed yields np.ndarray (float32, 1D) per document.
        return np.asarray(results[0], dtype=np.float32)

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        results = await asyncio.to_thread(
            lambda: list(self._model.embed(list(texts))),
        )
        return tuple(np.asarray(v, dtype=np.float32) for v in results)


__all__ = ("FastEmbedEmbedder",)
