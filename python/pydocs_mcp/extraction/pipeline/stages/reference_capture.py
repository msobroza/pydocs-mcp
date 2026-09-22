"""ReferenceCaptureStage — captures cross-node references via LanguageAnalyzers.

Dispatches each file in ``state.files.file_contents`` to the
extension-keyed :data:`~pydocs_mcp.extraction.strategies.analyzers.analyzer_registry`
(ADR 0004 seam — ``.py`` runs the CPython-ast emitters, ``.md`` the
regex MENTIONS capture, and the seven tree-sitter code extensions run
their per-language analyzers (ADR 0022); unknown extensions are skipped,
mirroring ``ChunkingStage``'s chunker_registry policy). Stores the unresolved
tuple on ``state.refs.references``, the per-module alias table on
``state.refs.reference_aliases``, and the per-class ``self.X``
attribute-type table on ``state.refs.class_attribute_types``. The
resolver pass runs later inside
``IndexingService.reindex_package`` (where it has access to the
cross-package qname universe via ``uow.trees``).

Per-file isolation: a ``SyntaxError`` or other ``Exception`` on one
file logs and continues — same contract as
:class:`~pydocs_mcp.extraction.pipeline.stages.chunking.ChunkingStage`
(AC #27). Analyzers raise freely; containment lives here. The dedicated
stage (rather than rewiring ``ChunkingStage`` to thread
``ref_collector`` everywhere) keeps capture single-purpose and the cost
is one extra parse per file — bounded: CPython ``ast`` for ``.py``,
tree-sitter for the seven code extensions (grammars and the top-level
query come from the chunker's caches; the reference queries get their own
cache, ADR 0022).

The capture configuration (``enabled`` + ``kinds`` filter) is BOUND from
``app_config.reference_graph.capture`` when the stage is built from a pipeline
YAML (issue #347): ``ContentHashStage`` folds that same object into every
package hash, so a hash can never claim capture settings the capture did not
use — even under a composition root that never pushes the module global (the
eval suite's, the test fixtures). A stage built without an app config falls
back to the module-level singleton ``configure_from_app_config`` updates at
server / CLI startup, which stays the stage-isolation tests' seam.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import (
    IngestionState,
    ReferenceBundle,
)
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig

log = logging.getLogger("pydocs-mcp")


# Module-level capture config — installed by ``configure_from_app_config`` at
# server / CLI startup. Default keeps the pre-#5c behavior (all three AST
# kinds enabled) so unit tests and any caller that constructs the stage
# without going through the YAML path get the safe baseline.
_CAPTURE_CONFIG: ReferenceCaptureConfig = ReferenceCaptureConfig()


def _get_capture_config() -> ReferenceCaptureConfig:
    """Return the active reference-capture config (module-level singleton)."""
    return _CAPTURE_CONFIG


def _set_capture_config(cfg: ReferenceCaptureConfig) -> None:
    """Install a new reference-capture config — called by
    ``configure_from_app_config(cfg)`` at server / CLI startup."""
    global _CAPTURE_CONFIG
    _CAPTURE_CONFIG = cfg


def capture_config_from_build_context(context: Any) -> ReferenceCaptureConfig | None:
    """``context.app_config.reference_graph.capture``, or None when absent.

    The one read both :class:`ReferenceCaptureStage` and ``ContentHashStage``
    decode through, so the stage that captures and the stage that hashes see the
    same object (issue #347). Tolerant of partial contexts (``object()``, a bare
    namespace) because stage-isolation tests build stages from those.
    """
    app_config = getattr(context, "app_config", None)
    return getattr(getattr(app_config, "reference_graph", None), "capture", None)


@stage_registry.register("reference_capture")
@dataclass(frozen=True, slots=True)
class ReferenceCaptureStage:
    # Bound by ``from_dict`` from the pipeline's app config; None means "read
    # the module global", the path a bare stage in an isolation test takes.
    # Wiring, not a stage tunable: it never round-trips through ``to_dict``, so
    # no pipeline YAML byte (and no ``ingestion_pipeline_hash``) moves with it.
    config: ReferenceCaptureConfig | None = None
    name: str = "reference_capture"

    async def run(self, state: IngestionState) -> IngestionState:
        cfg = self.config or _get_capture_config()
        if not cfg.enabled:
            # Short-circuit — capture disabled by YAML. Reset the
            # ReferenceBundle so a re-run from a state with prior captures
            # doesn't keep stale values.
            return replace(state, refs=ReferenceBundle())
        allowed = frozenset(cfg.kinds)
        refs, aliases, attr_types = await asyncio.to_thread(
            self._capture_all,
            state,
            allowed,
        )
        new_refs_bundle = ReferenceBundle(
            references=tuple(refs),
            reference_aliases=aliases,
            class_attribute_types=attr_types,
        )
        return replace(state, refs=new_refs_bundle)

    def _capture_all(
        self,
        state: IngestionState,
        allowed: frozenset[str],
    ) -> tuple[list[Any], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
        # Deferred imports — analyzer registration pulls in ast + reference
        # value objects, irrelevant at stage-registry construction time.
        from pydocs_mcp.extraction.strategies.analyzers import analyzer_registry
        from pydocs_mcp.extraction.strategies.references import ReferenceCollector

        collector = ReferenceCollector()
        for path, source in state.files.file_contents:
            if not source:
                continue
            analyzer = analyzer_registry.get(Path(path).suffix.lower())
            if analyzer is None:
                continue  # unknown extension — skip silently (policy, not error)
            try:
                analyzer.capture(
                    source,
                    path=path,
                    root=state.files.root,
                    from_package=state.files.package_name,
                    allowed=allowed,
                    collector=collector,
                )
            except Exception as exc:
                # Per-file containment — same contract as ChunkingStage (AC #27).
                log.warning("reference_capture failed on %s: %s", path, exc)
        # Filter IMPORTS rows out of collector.refs if "imports" isn't allowed.
        # The alias table (collector.aliases) is untouched — the resolver
        # consumes it independently of whether IMPORTS edges land in the DB.
        if "imports" not in allowed:
            refs = [r for r in collector.refs if r.kind is not ReferenceKind.IMPORTS]
        else:
            refs = collector.refs
        return refs, collector.aliases, collector.class_attribute_types

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> ReferenceCaptureStage:
        return cls(config=capture_config_from_build_context(context))

    def to_dict(self) -> dict:
        return {"type": "reference_capture"}


__all__ = (
    "ReferenceCaptureStage",
    "_get_capture_config",
    "_set_capture_config",
    "capture_config_from_build_context",
)
