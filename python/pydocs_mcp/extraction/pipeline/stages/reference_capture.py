"""ReferenceCaptureStage — captures cross-node references via LanguageAnalyzers.

Dispatches each file in ``state.files.file_contents`` to the
extension-keyed :data:`~pydocs_mcp.extraction.strategies.analyzers.analyzer_registry`
(ADR 0004 seam — ``.py`` runs the CPython-ast emitters, ``.md`` the
regex MENTIONS capture; unknown extensions are skipped, mirroring
``ChunkingStage``'s chunker_registry policy). Stores the unresolved
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
is one extra ``ast.parse`` per file — bounded and only over ``.py``
files.

The capture configuration (``enabled`` + ``kinds`` filter) lives as a
module-level singleton updated by ``configure_from_app_config`` at
server / CLI startup. A module-level constant is the right shape here
because the stage's ``run`` is otherwise stateless and the config is
process-global, not per-pipeline-invocation.
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


@stage_registry.register("reference_capture")
@dataclass(frozen=True, slots=True)
class ReferenceCaptureStage:
    name: str = "reference_capture"

    async def run(self, state: IngestionState) -> IngestionState:
        cfg = _get_capture_config()
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
        return cls()

    def to_dict(self) -> dict:
        return {"type": "reference_capture"}


__all__ = (
    "ReferenceCaptureStage",
    "_get_capture_config",
    "_set_capture_config",
)
