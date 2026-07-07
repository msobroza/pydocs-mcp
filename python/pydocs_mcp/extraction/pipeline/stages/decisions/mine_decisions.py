"""MineDecisionsStage — project-only decision mining fan-out (spec §D8).

First stage of the ``capture_decisions`` sub-pipeline. Applies the
project-target + ``config.enabled`` guard, then (when applicable) reads a
single bounded ``git log``, builds the :class:`CaptureContext`, and runs the
configured deterministic mining sources concurrently. The raw per-source
:class:`RawDecision`\\s land on ``state.decisions_raw`` for the downstream
:class:`MergeDecisionsStage` to collapse.

The fan-out stays INSIDE this stage on purpose: ingestion has no parallel
primitive, and treating each source as its own stage would fracture the
per-source failure isolation (spec §D8) that the ``asyncio.gather`` here
owns. On the guard's non-applicable path (dependency target OR disabled
config) the input state is returned untouched so a bare
``capture_decisions`` sub-pipeline is an identity for dependency packages.
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
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.retrieval.config import DecisionCaptureConfig

log = logging.getLogger("pydocs-mcp")


@stage_registry.register("mine_decisions")
@dataclass(frozen=True, slots=True)
class MineDecisionsStage:
    """Guard → build context → fan out over the enabled sources → stash raws.

    ``config`` is the ``decision_capture`` YAML sub-model (which sources run,
    per-source bounds). ``pipeline_hash`` is carried for identity parity with
    the other hash-aware stages' ``from_dict`` context access even though this
    stage never consumes it directly.
    """

    config: DecisionCaptureConfig = None  # type: ignore[assignment]
    pipeline_hash: str = ""
    name: str = "mine_decisions"

    def __post_init__(self) -> None:
        # Default-construct here (not in the field default) so a bare
        # MineDecisionsStage() built in a test gets a fresh config instead of
        # sharing a mutable None — mirrors the previous monolith's baseline.
        if self.config is None:
            object.__setattr__(self, "config", DecisionCaptureConfig())

    async def run(self, state: IngestionState) -> IngestionState:
        # Project-target only — decisions are a project-scoped concept; mining
        # site-packages would surface a dependency's internal rationale as if
        # it were the user's. Same target guard shape as dependency_doc_pages.
        if state.files.target_kind is not TargetKind.PROJECT or not self.config.enabled:
            return state

        ctx = await self._build_context(state, state.files.root)
        raws = await self._mine_all(ctx)
        return replace(state, decisions_raw=raws)

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
    def from_dict(cls, data: Mapping[str, Any], context: Any) -> MineDecisionsStage:
        app_config = getattr(context, "app_config", None)
        config = getattr(app_config, "decision_capture", None) or DecisionCaptureConfig()
        return cls(config=config, pipeline_hash=getattr(context, "pipeline_hash", ""))

    def to_dict(self) -> dict[str, Any]:
        return {"type": "mine_decisions"}


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


__all__ = ("MineDecisionsStage",)
