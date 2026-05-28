"""Load and build :class:`IngestionPipeline` from YAML (spec §7.3).

Single YAML file → single pipeline instance. The shipped
``pipelines/ingestion.yaml`` is the default; users override via
``extraction.ingestion.pipeline_path`` in their config YAML. Path
candidates resolve through the SAME sub-PR #2 allowlist that retrieval's
``pipeline_path`` uses (AC #33, spec §5.9): shipped ``pipelines/`` directory
or the user's config directory — symlinks resolve BEFORE the check, so a
symlink planted inside ``pipelines/`` pointing at ``/etc/shadow`` is
rejected.

Side-effect import of ``extraction.pipeline.stages`` populates :data:`stage_registry`
via the six ``@stage_registry.register(...)`` decorators; without that
import :func:`load_ingestion_pipeline` would raise ``KeyError`` on the
first stage lookup.
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from pydocs_mcp.extraction.pipeline import IngestionPipeline

# Side-effect import — registers the 6 ingestion stages via decorators. Python's
# sys.modules cache means re-entering this module's own package during the
# partial load of ``extraction/__init__.py`` is safe: the submodule finishes
# loading before control returns, and the registry is populated.
from pydocs_mcp.extraction.pipeline import stages as _stages  # noqa: F401
from pydocs_mcp.extraction.serialization import stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.protocols import Embedder, LlmClient, UnitOfWork


@cache
def _default_ingestion_pipeline_path() -> Path:
    """Resolve the bundled ``pipelines/ingestion.yaml`` via ``importlib.resources``.

    Using ``importlib.resources`` (rather than ``__file__`` arithmetic) keeps
    the lookup correct under zipimport / installed wheels where
    ``Path(__file__).parent`` may not map cleanly to on-disk layout. Cached
    for the same reason ``retrieval.config._shipped_default_config_path``
    caches — the shipped pipelines directory never changes at runtime.
    """
    return Path(str(importlib.resources.files("pydocs_mcp.pipelines").joinpath("ingestion.yaml")))


def load_ingestion_pipeline(
    path: Path,
    cfg: AppConfig,
    *,
    embedder: Embedder | None = None,
    uow_factory: Callable[[], UnitOfWork] | None = None,
    pipeline_hash: str = "",
    llm_client: LlmClient | None = None,
) -> IngestionPipeline:
    """Load and build an :class:`IngestionPipeline` from a YAML file.

    The path is resolved + allowlisted through retrieval's
    ``_resolve_pipeline_path`` so ingestion and retrieval share a single
    security-critical path-check implementation (AC #33).

    ``embedder`` is threaded into the :class:`BuildContext` so
    :class:`EmbedChunksStage.from_dict` can construct itself with a real
    :class:`Embedder` — production wiring (see ``__main__.py``) supplies
    one via :func:`~pydocs_mcp.extraction.strategies.embedders.build_embedder`;
    tests pass a :class:`tests._fakes.MockEmbedder`. The
    pipeline YAML wires ``embed_chunks`` by default, so any caller building
    that pipeline must supply an embedder.

    ``uow_factory`` + ``pipeline_hash`` are likewise threaded into
    :class:`BuildContext` so :class:`LoadExistingChunkHashesStage.from_dict`
    can find the composite UoW factory (it reads SQLite for the package's
    existing chunk hashes) and :class:`AssignChunkContentHashStage.from_dict`
    can read the embedder + ingestion-YAML identity slot. Production wiring
    in ``__main__.py`` provides both at startup; tests that don't exercise
    the cache-skip path may omit them (the affected stages raise loud
    ValueErrors if the YAML wires them without the deps).

    ``llm_client`` is reserved for future ingestion-time LLM stages
    (e.g., LLM-driven chunk summarization). No shipped ingestion stage
    consumes it today, but accepting the kwarg symmetrically with
    ``embedder`` keeps the composition roots in ``__main__.py`` /
    ``server.py`` uniform: every dependency built once at startup is
    threaded through the same path. When a future ingestion stage
    needs it, the wiring is already in place.
    """
    resolved = _resolve_ingestion_pipeline_path(path, cfg)
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "stages" not in data:
        raise ValueError(f"invalid ingestion pipeline YAML: {resolved!s}")
    # Reuse retrieval's BuildContext — extraction stages read
    # ``context.app_config`` + ``context.embedder`` + ``context.uow_factory``
    # + ``context.pipeline_hash`` inside ``from_dict``; the other BuildContext
    # fields stay unused here but must be constructed to satisfy the
    # dataclass's required ``connection_provider`` field. A ``None``
    # stand-in is acceptable because no extraction stage dereferences it.
    from pydocs_mcp.retrieval.serialization import BuildContext

    context = BuildContext(  # type: ignore[arg-type]
        connection_provider=None,
        app_config=cfg,
        embedder=embedder,
        uow_factory=uow_factory,
        pipeline_hash=pipeline_hash,
        llm_client=llm_client,
    )
    pipeline_stages = tuple(stage_registry.build(s, context) for s in data["stages"])
    return IngestionPipeline(stages=pipeline_stages)


def build_ingestion_pipeline(
    cfg: AppConfig,
    *,
    embedder: Embedder | None = None,
    uow_factory: Callable[[], UnitOfWork] | None = None,
    pipeline_hash: str = "",
    llm_client: LlmClient | None = None,
) -> IngestionPipeline:
    """Build the :class:`IngestionPipeline` for this :class:`AppConfig`.

    Uses ``cfg.extraction.ingestion.pipeline_path`` if set (allowlist
    enforced); otherwise falls back to the bundled
    ``pipelines/ingestion.yaml``. The bundled default stays inside
    ``_shipped_pipelines_dir`` and always passes the allowlist.

    ``embedder`` / ``uow_factory`` / ``pipeline_hash`` / ``llm_client``
    are forwarded to :func:`load_ingestion_pipeline`; the bundled
    pipeline includes :class:`EmbedChunksStage` +
    :class:`LoadExistingChunkHashesStage` +
    :class:`AssignChunkContentHashStage`, so production callers must
    supply all three (the composition root in ``__main__.py`` does
    this at startup). ``llm_client`` is plumbed symmetrically for a
    future LLM-driven ingestion stage.
    """
    override = cfg.extraction.ingestion.pipeline_path
    path = override if override is not None else _default_ingestion_pipeline_path()
    return load_ingestion_pipeline(
        Path(path),
        cfg,
        embedder=embedder,
        uow_factory=uow_factory,
        pipeline_hash=pipeline_hash,
        llm_client=llm_client,
    )


def _resolve_ingestion_pipeline_path(
    path: Path,
    cfg: AppConfig,
) -> Path:
    """Resolve ``path`` through retrieval's shared pipeline_path allowlist.

    Keeping this in a private helper (instead of calling
    ``_resolve_pipeline_path`` directly at the top level) isolates the
    tightly-coupled import from the public API — if the retrieval helper
    moves or renames, only this one function needs updating.
    """
    # Deferred import — retrieval.config pulls in pydantic-settings + formatters
    # + retrievers via its own side-effect imports; deferring keeps ``extraction``
    # importable from contexts that don't need retrieval's registry fully warmed.
    from pydocs_mcp.retrieval.config import _resolve_pipeline_path

    user_config_path = cfg._user_config_path() if hasattr(cfg, "_user_config_path") else None
    return _resolve_pipeline_path(path, user_config_path)


__all__ = ("build_ingestion_pipeline", "load_ingestion_pipeline")
