"""SentenceTransformersEmbedder — dense Embedder over a SentenceTransformer model.

Serves ``Qwen/Qwen3-Embedding-0.6B`` (and other SentenceTransformer models)
via torch. It is GPU-reliable across a benchmark's sequential index-builds:
torch frees device memory when the model is dropped (see :meth:`close`), so a
sweep that builds one index per needle does not accumulate CUDA arenas.

Model-agnostic: queries go through ST's ``encode_query`` and documents
through ``encode_document``, so whatever asymmetric prompting the model
itself defines is applied — an asymmetric model (e.g. Qwen3, which stores a
``"query"`` prompt) gets its query prompt automatically, while a symmetric
model is not forced through a prompt it doesn't have. ``normalize`` and the
optional ``query_prompt_name`` override are config-driven, not baked to any
one model.

``model`` is injectable so tests run without a model download / torch load.
When ``model is None`` the real model is loaded lazily inside
``__post_init__`` so a default install never imports torch.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)
from pydocs_mcp.models import Embedding

_INSTALL_HINT = (
    "The 'sentence_transformers' embedding provider requires the "
    "'sentence-transformers' extra. Install with: "
    "pip install 'pydocs-mcp[sentence-transformers]' (pulls "
    "sentence-transformers + torch + transformers; expect ~1-5 GB "
    "depending on CUDA wheel selection). torchvision is NOT included; "
    "if a model load demands it, see the error raised at construction "
    "time for remedies."
)

_TORCHVISION_HINT = (
    "Constructing the SentenceTransformer model raised an error that "
    "mentions 'torchvision'. torchvision is NOT part of the "
    "'pydocs-mcp[sentence-transformers]' extra: recent transformers "
    "releases (5.0 <= v < 5.10) hard-require it for image-processing "
    "classes that some model repos reference. Remedies, in order:\n"
    "  1. Upgrade transformers to >= 5.10 (adds a Pillow fallback that "
    "removes the torchvision requirement for image processors):\n"
    "       pip install -U 'transformers>=5.10,<6'\n"
    "  2. Or install torchvision — NOTE it exact-pins its torch sibling "
    "(e.g. torchvision 0.26.0 <-> torch 2.11.0), so match your installed "
    "torch:\n"
    "       pip install torchvision\n"
    "  3. If embedding.model_name points at a multimodal repo by mistake, "
    "switch to a text-only embedding model (e.g. "
    "Qwen/Qwen3-Embedding-0.6B).\n"
    "Do NOT follow upstream's 'pip install -U sentence-transformers[image]' "
    "hint on transformers 4.x or 5.0.x — there that extra installs only "
    "Pillow and does not resolve the error (transformers' vision extra "
    "gained torchvision in 5.1)."
)


def _mentions_torchvision(exc: BaseException) -> bool:
    """True when exc or anything in its __cause__/__context__ chain names
    torchvision — ST's suggest_extra_on_exception re-raises with the
    original as __cause__, so the marker may sit one level down."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if "torchvision" in str(cur).lower():
            return True
        cur = cur.__cause__ or cur.__context__
    return False


@dataclass
class SentenceTransformersEmbedder:
    model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    dim: int = 1024
    # Execution device — threaded into SentenceTransformer(device=...) so the
    # same config can run CPU or GPU without code changes.
    device: str = "cpu"
    batch_size: int = 32
    # Token cap. Transformer attention is O(seq^2) in memory, so an
    # un-truncated long chunk can OOM the GPU on a long-context model. Capping
    # the model's max sequence length keeps embedding within VRAM while still
    # covering retrieval-doc context comfortably; tune per model/hardware.
    max_seq_length: int = 2048
    normalize: bool = True
    # Optional named query prompt for ASYMMETRIC models. ``None`` (default)
    # keeps the embedder model-agnostic: ``encode_query`` applies whatever
    # query prompt the model itself defines (e.g. Qwen3's ``"query"``), and a
    # model without one is not forced through a non-existent prompt (which
    # would raise). Set it only to override the model's own default.
    query_prompt_name: str | None = None
    # ST inference runtime: "torch" (default) | "onnx" | "openvino". The
    # non-torch backends enable fast CPU inference — typically ~2-4x with a
    # qint8-quantized ``model_file_name`` — and need the matching ST extra
    # (``sentence-transformers[openvino]`` / ``[onnx]``) installed.
    backend: str = "torch"
    # Specific exported weight file inside the HF repo (e.g.
    # ``openvino/openvino_model_qint8_quantized.xml``); ``None`` uses the
    # backend's default export. Threaded as ST's model_kwargs["file_name"].
    model_file_name: str | None = None
    # Injectable so tests run without loading the real model. ``Any`` — the
    # real type is sentence_transformers.SentenceTransformer, imported lazily.
    model: Any = None

    def __post_init__(self) -> None:
        # Airgap (spec D5): a side-loaded model dir must never fall back to
        # the Hub for a missing file — force HF offline before any load.
        local_dir = local_model_dir(self.model_name)
        if local_dir is not None:
            enable_hf_offline()
            # Hand the loader the expanded path — SentenceTransformer does
            # not expanduser, so a `~/models/x` spelling would otherwise be
            # rejected as a malformed HF repo id.
            self.model_name = str(local_dir)
        if self.model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as e:
                raise ImportError(_INSTALL_HINT) from e
            # Pass backend/model_kwargs ONLY when non-default so the torch
            # path constructs byte-identically to before this feature.
            ctor_kwargs: dict[str, Any] = {"device": self.device}
            if self.backend != "torch":
                ctor_kwargs["backend"] = self.backend
            if self.model_file_name is not None:
                ctor_kwargs["model_kwargs"] = {"file_name": self.model_file_name}
            try:
                self.model = SentenceTransformer(self.model_name, **ctor_kwargs)
            except (ImportError, AttributeError) as e:
                # ST >= 5.5 routes model loading through AutoProcessor wrapped
                # in suggest_extra_on_exception(); a missing torchvision
                # surfaces as an ImportError (or AttributeError from
                # lazy-module resolution) whose chain mentions 'torchvision'.
                # Upstream's own remedy hint is broken on transformers
                # 4.x/5.0.x (there its [image] extra == Pillow only), so we
                # own an actionable message here. See spec
                # docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md
                if _mentions_torchvision(e):
                    raise ImportError(_TORCHVISION_HINT) from e
                if not isinstance(e, ImportError) or self.backend == "torch":
                    # ModuleNotFoundError (e.g. missing optimum) is an
                    # ImportError subclass, so the clause below covers it.
                    raise
                # A non-torch backend fails construction when optimum /
                # openvino aren't installed — surface the extras hint instead
                # of the deep import error.
                raise ImportError(
                    f"embedding.backend: {self.backend} requires the matching "
                    "sentence-transformers extra. Install with: pip install "
                    f"'sentence-transformers[{self.backend}]'"
                ) from e
        # Cap sequence length so a long code chunk can't OOM attention. Applied
        # to injected models too so test + real paths stay symmetric.
        self.model.max_seq_length = self.max_seq_length

    async def embed_query(self, text: str) -> Embedding:
        # Queries go through ST's encode_query so an asymmetric model applies
        # its own query prompt. We pass prompt_name ONLY when explicitly
        # configured, keeping the embedder model-agnostic — a model without a
        # named query prompt is not forced through one (which would raise).
        # sentence-transformers 5.x has NO async API, so the sync encode runs
        # in a worker thread to keep the event loop free.
        kwargs: dict[str, Any] = {
            "normalize_embeddings": self.normalize,
            "convert_to_numpy": True,
        }
        if self.query_prompt_name is not None:
            kwargs["prompt_name"] = self.query_prompt_name
        vec = await asyncio.to_thread(lambda: self.model.encode_query([text], **kwargs)[0])
        return np.asarray(vec, dtype=np.float32)

    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        mat = await asyncio.to_thread(
            lambda: self.model.encode_document(
                list(texts),
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
                batch_size=self.batch_size,
            ),
        )
        return tuple(np.asarray(row, dtype=np.float32) for row in mat)

    def close(self) -> None:
        """Drop the model ref + free CUDA memory so a sweep doesn't accumulate.

        torch returns device memory when the model object is garbage-collected
        and the cached allocator is emptied. Dropping the only reference,
        emptying the CUDA cache, and forcing a collection is what actually
        returns the VRAM between needles. Idempotent + safe on an already-
        closed embedder, and safe when torch is absent (the import is guarded).
        """
        if self.model is None:
            return
        self.model = None
        # torch may be absent (default install) — best-effort cache release.
        with contextlib.suppress(Exception):
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        gc.collect()

    def __del__(self) -> None:
        # Best-effort release if a caller forgot ``close()``. Suppressed
        # broadly because ``__del__`` can run during interpreter shutdown
        # (where gc / attributes may already be torn down) or on a
        # partially-constructed instance whose ``__post_init__`` raised before
        # ``model`` was set.
        with contextlib.suppress(Exception):
            self.close()


__all__ = ("SentenceTransformersEmbedder",)
