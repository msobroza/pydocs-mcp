"""MineDecisionsStage — decision mining fan-out + folded merge (spec §D8).

First stage of the ``capture_decisions`` sub-pipeline. Reads a single bounded
``git log``, builds the :class:`CaptureContext`, runs the configured
deterministic mining sources concurrently, then Jaccard-merges the raw
per-source :class:`RawDecision`\\s straight onto ``state.decisions`` — mining
and merging are one transform (the raws are never useful separately, so they
don't ride the state between stages).

The fan-out stays INSIDE this stage on purpose: ingestion has no parallel
primitive, and treating each source as its own stage would fracture the
per-source failure isolation (spec §D8) that the ``asyncio.gather`` here
owns. Mining that finds nothing returns the input state untouched, keeping
the sub-pipeline an identity for decision-free runs. The project-only +
``enabled`` guard lives on :class:`CaptureDecisionsPipeline`, not here.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydocs_mcp.extraction.decisions._git import read_git_log
from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionSource,
    RawDecision,
    decision_source_registry,
)
from pydocs_mcp.extraction.decisions.engine import merge_raw_decisions
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.retrieval.config import DecisionCaptureConfig

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class MineDecisionsStage:
    """Build context → fan out over the enabled sources → merge → decisions.

    ``config`` is the ``decision_capture`` YAML sub-model (which sources run,
    per-source bounds, the ``merge_jaccard`` threshold).
    """

    config: DecisionCaptureConfig = field(default_factory=DecisionCaptureConfig)
    name: str = "mine_decisions"

    async def run(self, state: IngestionState) -> IngestionState:
        ctx = await self._build_context(state, state.files.root)
        raws = await self._mine_all(ctx)
        if not raws:
            # Nothing mined → identity out, so the whole sub-pipeline passes
            # the state through untouched on decision-free runs.
            return state
        merged = merge_raw_decisions(raws, jaccard_threshold=self.config.merge_jaccard)
        return replace(state, decisions=merged)

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


def _enabled_sources(names: list[str]) -> tuple[DecisionSource, ...]:
    """Instantiate the registered sources named in the config list, in order.

    Looks each name up on ``decision_source_registry`` (populated by the
    side-effect import of ``extraction.decisions.sources``). Unknown names are
    skipped defensively — the config Literal already closes misspellings at YAML
    load, so this only guards a source removed after a config was written.
    """
    out: list[DecisionSource] = []
    for name in names:
        cls = decision_source_registry.get(name)
        if cls is None:
            log.warning("decision source %r not registered — skipping", name)
            continue
        out.append(cls())
    return tuple(out)


__all__ = ("MineDecisionsStage",)
