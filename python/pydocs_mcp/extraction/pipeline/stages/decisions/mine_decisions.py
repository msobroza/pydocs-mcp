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
the sub-pipeline an identity for decision-free runs. The mining gate
(``decision_mining_applies``) lives on :class:`CaptureDecisionsPipeline`, not
here.

A DEPENDENCY target (mined only under ``decision_capture.include_deps``, issue
#346) runs only the sources in :data:`_DEPENDENCY_SOURCES` and never reads
git: see that constant for why.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydocs_mcp.extraction.decisions._git import GitLogReader, read_git_log
from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionSource,
    RawDecision,
    decision_source_registry,
)
from pydocs_mcp.extraction.decisions.engine import merge_raw_decisions
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.retrieval.config import DecisionCaptureConfig

log = logging.getLogger("pydocs-mcp")

# The sources a DEPENDENCY target runs, intersected with the configured list.
# ``inline_markers`` reads only the dependency's own chunk trees, so what it
# mines is attributed to the right package. The others read the filesystem or
# git from ``state.files.root``, which for a dependency is the site-packages
# directory EVERY dependency shares (or ``Path()`` when the dist is missing):
# ``adr_files``, ``changelog`` and ``docs_prose`` would attribute one package's
# files to every other, and ``commit_messages`` would mine the enclosing
# project's history (see ``_git_log_text``). A module constant rather than a
# ``DecisionCaptureConfig`` field, because a new field would move the pinned
# stock digest in ``stages/content_hash.py`` and re-extract every project.
_DEPENDENCY_SOURCES = frozenset({"inline_markers"})


@dataclass(frozen=True, slots=True)
class MineDecisionsStage:
    """Build context → fan out over the enabled sources → merge → decisions.

    ``config`` is the ``decision_capture`` YAML sub-model (which sources run,
    per-source bounds, the ``merge_jaccard`` threshold). ``git_log_reader`` is
    the bounded ``git log`` reader, a seam so a test can inject a recording
    fake instead of spawning git.
    """

    config: DecisionCaptureConfig = field(default_factory=DecisionCaptureConfig)
    git_log_reader: GitLogReader = read_git_log
    name: str = "mine_decisions"

    async def run(self, state: IngestionState) -> IngestionState:
        ctx = await self._build_context(state, state.files.root)
        raws = await self._mine_all(ctx, self._source_names(state))
        if not raws:
            # Nothing mined → identity out, so the whole sub-pipeline passes
            # the state through untouched on decision-free runs.
            return state
        merged = merge_raw_decisions(raws, jaccard_threshold=self.config.merge_jaccard)
        return replace(state, decisions=merged)

    async def _build_context(self, state: IngestionState, root: Path) -> CaptureContext:
        """Read the bounded git log at most ONCE, then bundle the source input."""
        return CaptureContext(
            project_root=root,
            trees=state.chunks.trees,
            config=self.config,
            git_log_text=await self._git_log_text(state.files.target_kind, root),
            # The exact set the same run's discovery walk pruned against
            # (spec D8/9.1) — never re-derived, never a second TOML read.
            excluded=state.files.effective_excludes,
        )

    async def _git_log_text(self, target_kind: TargetKind, root: Path) -> str:
        """The bounded ``git log`` at ``root`` for a project, "" for a dependency.

        WHY never for a dependency (issue #346): its root is the shared
        site-packages directory, and ``git -C <site-packages> log`` walks up to
        whatever repository encloses a project-local venv, so every dependency
        would mine the PROJECT's commits as its own decisions — one git spawn
        per dependency, each with the full timeout.
        """
        if target_kind is not TargetKind.PROJECT:
            return ""
        git_cfg = self.config.commit_messages
        return await asyncio.to_thread(
            self.git_log_reader,
            root,
            max_commits=git_cfg.max_commits,
            timeout_seconds=git_cfg.timeout_seconds,
        )

    def _source_names(self, state: IngestionState) -> Sequence[str]:
        """The configured sources this target runs, in config order.

        A project runs them all; a dependency only those in
        :data:`_DEPENDENCY_SOURCES`, and the rest are logged at debug level.
        """
        configured = self.config.sources
        if state.files.target_kind is TargetKind.PROJECT:
            return configured
        skipped = [name for name in configured if name not in _DEPENDENCY_SOURCES]
        if skipped:
            _log_skipped_dependency_sources(state.files.package_name, skipped)
        return [name for name in configured if name in _DEPENDENCY_SOURCES]

    async def _mine_all(
        self, ctx: CaptureContext, source_names: Sequence[str]
    ) -> tuple[RawDecision, ...]:
        """Run every enabled source concurrently; isolate per-source failures.

        A source that raises is logged and skipped (spec §D8) — one broken
        source never fails the whole index. Results concatenate in config order.
        """
        sources = _enabled_sources(source_names)
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


def _log_skipped_dependency_sources(package: str, skipped: list[str]) -> None:
    """One structured debug record naming the sources a dependency did not run."""
    log.debug(
        json.dumps(
            {"event": "dependency_decision_sources_skipped", "package": package, "skipped": skipped}
        )
    )


def _enabled_sources(names: Sequence[str]) -> tuple[DecisionSource, ...]:
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
