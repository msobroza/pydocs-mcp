"""CaptureDecisionsStage — mine architectural decisions during ingestion (spec §D8).

Project-target only: runs the configured deterministic mining sources
concurrently over the already-extracted :class:`DocumentNode` trees (plus a
single bounded ``git log`` read passed in via ``CaptureContext.git_log_text``),
merges the per-source :class:`RawDecision`\\s, and surfaces the result two ways:

* stashes the merged tuple on ``state.decisions`` so
  :class:`ProjectIndexer` can thread it into
  :meth:`IndexingService.reindex_package` (reconcile + persist), and
* appends one *decision-as-chunk* per merged decision to
  ``state.chunks.chunks`` so architectural rationale flows through the SAME
  hashing → embedding → retrieval machinery as code/doc chunks. Each chunk
  carries ``origin=decision_record`` and a ``decision_key`` (normalized-title
  key) that the persistence layer maps to the assigned ``decision_id``.

Ordered BEFORE ``assign_chunk_content_hash`` in ``ingestion.yaml`` so the new
chunks pick up the pipeline-aware hash on the normal path. Per-source failure
isolation (spec §D8): one source raising is logged and skipped, never failing
the whole index.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydocs_mcp.extraction.decisions._git import read_git_log
from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionSource,
    RawDecision,
    decision_source_registry,
)
from pydocs_mcp.extraction.decisions.engine import decision_key, merge_raw_decisions
from pydocs_mcp.extraction.decisions.structuring import structure_decisions
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.models import Chunk, ChunkOrigin
from pydocs_mcp.retrieval.config import DecisionCaptureConfig
from pydocs_mcp.retrieval.protocols import LlmClient

log = logging.getLogger("pydocs-mcp")


@stage_registry.register("capture_decisions")
@dataclass(frozen=True, slots=True)
class CaptureDecisionsStage:
    """Mine + merge decisions, stash on state, emit one chunk per decision.

    ``config`` is the ``decision_capture`` YAML sub-model (which sources run,
    the merge Jaccard threshold, per-source bounds). ``pipeline_hash`` is
    unused here directly (the downstream ``assign_chunk_content_hash`` stage
    consumes it) but is carried so the stage's identity mirrors the other
    hash-aware stages' ``from_dict`` context access.
    """

    config: DecisionCaptureConfig = None  # type: ignore[assignment]
    pipeline_hash: str = ""
    # Wired ONLY when ``llm_structuring.enabled`` (built in ``from_dict`` from
    # ``app_config.llm``). ``None`` = the default-off path: no client, no LLM
    # touched during indexing. Kept as a field (not built in ``run``) so the
    # eager OpenAI import cost is paid at composition time, not per-index-call.
    llm_client: LlmClient | None = None
    name: str = "capture_decisions"

    def __post_init__(self) -> None:
        # Default-construct here (not in the field default) so the frozen
        # dataclass gets a fresh config when a bare CaptureDecisionsStage() is
        # built in a test — mirrors ReferenceCaptureStage's module-singleton
        # baseline without sharing a mutable default across instances.
        if self.config is None:
            object.__setattr__(self, "config", DecisionCaptureConfig())

    async def run(self, state: IngestionState) -> IngestionState:
        # Project-target only — decisions are a project-scoped concept; mining
        # site-packages would surface a dependency's internal rationale as if
        # it were the user's. Same target guard shape as dependency_doc_pages.
        if state.files.target_kind is not TargetKind.PROJECT or not self.config.enabled:
            return state

        root = state.files.root
        ctx = await self._build_context(state, root)
        raws = await self._mine_all(ctx)
        merged = merge_raw_decisions(raws, jaccard_threshold=self.config.merge_jaccard)

        decision_chunks = tuple(
            _decision_to_chunk(decision, package=state.files.package_name) for decision in merged
        )
        new_chunks = replace(state.chunks, chunks=(*state.chunks.chunks, *decision_chunks))
        structured = await self._structure(merged)
        return replace(state, chunks=new_chunks, decisions=merged, decision_structured=structured)

    async def _structure(
        self, merged: tuple[RawDecision, ...]
    ) -> dict[str, tuple[dict[str, object], str]]:
        """Optional §D12 structuring: LLM-structure + ground the merged decisions.

        A no-op unless the gate is enabled AND a client was wired in
        ``from_dict`` — the deterministic path never touches an LLM.
        ``structure_decisions`` already short-circuits on a disabled config, so
        the client-presence guard is what keeps the off path allocation-free.
        """
        if self.llm_client is None:
            return {}
        return await structure_decisions(merged, self.llm_client, self.config.llm_structuring)

    async def _build_context(self, state: IngestionState, root: Path) -> CaptureContext:
        """Read the bounded git log ONCE, then bundle the source input."""
        git_cfg = self.config.commit_messages
        git_log_text = await asyncio.to_thread(
            read_git_log,
            root,
            max_commits=git_cfg.max_commits,
            timeout_seconds=git_cfg.timeout_seconds,
        )
        return CaptureContext(
            project_root=root,
            trees=state.chunks.trees,
            config=self.config,
            git_log_text=git_log_text,
        )

    async def _mine_all(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        """Run every enabled source concurrently; isolate per-source failures.

        A source that raises is logged and skipped (spec §D8) — one broken
        source never fails the whole index. Results concatenate in config order.
        """
        sources = _enabled_sources(self.config.sources)
        results = await asyncio.gather(
            *(src.mine(ctx) for src in sources),
            return_exceptions=True,
        )
        out: list[RawDecision] = []
        for src, result in zip(sources, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("decision source %r failed: %s", src.name, result)
                continue
            out.extend(result)
        return tuple(out)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> CaptureDecisionsStage:
        app_config = getattr(context, "app_config", None)
        config = getattr(app_config, "decision_capture", None) or DecisionCaptureConfig()
        return cls(
            config=config,
            pipeline_hash=getattr(context, "pipeline_hash", ""),
            llm_client=_maybe_build_llm_client(config, app_config),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"type": "capture_decisions"}


def _maybe_build_llm_client(config: DecisionCaptureConfig, app_config: Any) -> LlmClient | None:
    """Build the structuring client ONLY when the default-off gate is enabled.

    Off path (the default): return ``None`` without importing or constructing
    any client, so ``from_dict`` pays no eager OpenAI-import cost. On path: build
    via the shared ``build_llm_client`` from ``app_config.llm`` — imported lazily
    so the module attribute (and test patches of it) resolve at call time.
    """
    if not config.llm_structuring.enabled or app_config is None:
        return None
    from pydocs_mcp.retrieval.llm_clients import build_llm_client

    return build_llm_client(app_config.llm)


def _enabled_sources(names: list[str]) -> tuple[DecisionSource, ...]:
    """Instantiate the registered sources named in the config list, in order.

    Looks each name up on ``decision_source_registry`` (populated by the
    side-effect import of ``extraction.decisions.sources``). Unknown names are
    skipped defensively — the config Literal already closes misspellings at YAML
    load, so this only guards a source removed after a config was written.
    """
    out: list[DecisionSource] = []
    for name in names:
        cls = decision_source_registry._types.get(name)
        if cls is None:
            log.warning("decision source %r not registered — skipping", name)
            continue
        out.append(cls())
    return tuple(out)


def _decision_to_chunk(decision: RawDecision, *, package: str) -> Chunk:
    """One merged decision → a searchable decision-as-chunk (spec §D9).

    ``text`` = title + a blank line + the joined evidence texts, so BM25 / dense
    retrieval sees both the decision statement and its verbatim grounding.
    ``decision_key`` lets the persistence layer stamp ``decision_id`` after the
    record's id is assigned.
    """
    evidence_text = "\n\n".join(ev.text for ev in decision.evidence)
    text = f"{decision.title}\n\n{evidence_text}" if evidence_text else decision.title
    return Chunk(
        text=text,
        metadata={
            "package": package,
            "module": "",
            "title": decision.title,
            "origin": ChunkOrigin.DECISION_RECORD.value,
            "decision_key": decision_key(decision.title),
        },
    )


__all__ = ("CaptureDecisionsStage",)
