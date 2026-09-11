"""PyLateEmbedder — wraps PyLate's ``models.ColBERT`` (Decision A).

Lazy import: ``pylate`` is the optional ``[late-interaction]`` extra. The
import happens inside :meth:`from_config` so a default install (no extra)
never pays the cost.

Only a genuinely absent ``pylate`` gets the install hint. An installed pylate
that fails to import or to build the model (pylate 1.0.0 on
sentence-transformers 5.5 did both) raises an error that says the extra IS
installed and carries the underlying message — telling the user to install
an extra they already have hides the real cause.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.extraction.strategies.embedders.local_source import (
    enable_hf_offline,
    local_model_dir,
)
from pydocs_mcp.retrieval.config import LateInteractionConfig

_INSTALL_HINT = (
    "Late-interaction retrieval requires the 'late-interaction' extra. "
    "Install with: pip install 'pydocs-mcp[late-interaction]' (pulls "
    "pylate + sentence-transformers + torch + transformers; expect ~1-5 GB "
    "depending on CUDA wheel selection)."
)


class LateInteractionModelLoadError(PydocsMCPError, RuntimeError):
    """The [late-interaction] extra is installed, but pylate could not build
    the configured ColBERT model (incompatible versions or an unusable model)."""


def _is_absent_pylate(error: ImportError) -> bool:
    """True only when pylate itself is not installed — the one case where
    "install the extra" is the right advice. A ModuleNotFoundError naming any
    OTHER module (a moved sentence-transformers submodule, say) means pylate
    is installed and its dependency set is broken."""
    name = error.name or ""
    return isinstance(error, ModuleNotFoundError) and (
        name == "pylate" or name.startswith("pylate.")
    )


def _extra_installed_but_failed(step: str, model_path: str, error: BaseException) -> str:
    return (
        f"The 'late-interaction' extra is installed, but {step} failed for "
        f"late_interaction.model_name={model_path!r}: {type(error).__name__}: {error}. "
        "This is not a missing extra: the installed pylate / sentence-transformers / "
        "transformers versions do not fit together (compare `pip show pylate "
        "sentence-transformers transformers` with the [late-interaction] extra's "
        "pins), or the model cannot be loaded. The chained exception is the "
        "underlying error."
    )


def _import_pylate_models(model_path: str) -> Any:
    try:
        from pylate import models  # type: ignore[import-not-found]
    except ImportError as e:
        if _is_absent_pylate(e):
            raise ImportError(_INSTALL_HINT) from e
        raise ImportError(_extra_installed_but_failed("importing pylate", model_path, e)) from e
    return models


def _build_colbert(models: Any, model_path: str, cfg: LateInteractionConfig) -> Any:
    # ``pool_factor`` is not passed: ``models.ColBERT.__init__`` does not
    # accept it (verified on the pylate==1.6.0 signature). It is a
    # token-pooling knob of pylate's index and of ``encode()``; applying it
    # changes the stored multi-vectors, so it stays unwired until the
    # fast-plaid index wiring uses ``cfg.pool_factor``.
    try:
        return models.ColBERT(
            model_name_or_path=model_path,
            embedding_size=cfg.embedding_dim,
            document_length=cfg.document_length,
            query_length=cfg.query_length,
            device=cfg.device,
        )
    except Exception as e:
        raise LateInteractionModelLoadError(
            _extra_installed_but_failed("constructing pylate's ColBERT model", model_path, e)
        ) from e


@dataclass
class PyLateEmbedder:
    model_name: str
    dim: int
    document_length: int
    query_length: int
    pool_factor: int
    device: str = "cpu"
    _model: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_config(cls, cfg: LateInteractionConfig) -> PyLateEmbedder:
        # Airgap (spec D5): see local_source — force HF offline before pylate
        # (sentence-transformers underneath) can attempt a Hub fallback.
        # ``model_path`` carries the expanded form: ColBERT does not
        # expanduser, so a `~/models/x` spelling would otherwise be rejected
        # as a malformed HF repo id.
        model_path = cfg.model_name
        local_dir = local_model_dir(cfg.model_name)
        if local_dir is not None:
            enable_hf_offline()
            model_path = str(local_dir)
        models = _import_pylate_models(model_path)
        self = cls(
            # The configured spelling is the model's identity; ``model_path``
            # is only what the loader needs (see the SentenceTransformers
            # embedder for why the two must not be conflated).
            model_name=cfg.model_name,
            dim=cfg.embedding_dim,
            document_length=cfg.document_length,
            query_length=cfg.query_length,
            pool_factor=cfg.pool_factor,
            device=cfg.device,
        )
        self._model = _build_colbert(models, model_path, cfg)
        return self

    async def embed_query(self, text: str) -> list[np.ndarray]:
        mat = await asyncio.to_thread(
            lambda: self._model.encode(
                [text],
                is_query=True,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )[0],
        )
        return [np.asarray(row, dtype=np.float32) for row in mat]

    async def embed_chunks(
        self,
        texts: Sequence[str],
    ) -> tuple[list[np.ndarray], ...]:
        if not texts:
            return ()
        mats = await asyncio.to_thread(
            lambda: self._model.encode(
                list(texts),
                is_query=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ),
        )
        return tuple([np.asarray(row, dtype=np.float32) for row in mat] for mat in mats)
