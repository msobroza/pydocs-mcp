"""``docs_prose`` source — mine decisions from top-level prose docs (spec §D8).

Reads a bounded set of conventional prose files (``README.md``,
``ARCHITECTURE.md``, ``DESIGN.md``, ``CONTRIBUTING.md``, and ``docs/*.md``),
paragraph-splits each, and keyword-gates every paragraph with the SAME scorer
the git/changelog sources use. Bounded by ``config.docs_prose.max_files`` (extra
candidates dropped) and ``max_kb_per_file`` (oversize files skipped); every drop
is logged. Prose is the weakest deterministic signal — confidence 0.60.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionEvidence,
    RawDecision,
    decision_source_registry,
)
from pydocs_mcp.extraction.decisions.sources.adr_files import (
    _collect_tree_qnames,
    _scan_affected,
)
from pydocs_mcp.extraction.decisions.sources.commit_messages import qualifies

_LOGGER = logging.getLogger(__name__)

# The weakest deterministic source: hand-written prose that MENTIONS a decision
# without the ADR/marker structure. Below commit/changelog (0.70).
_CONFIDENCE = 0.60
_TITLE_MAX = 80
_NAME = "docs_prose"

# Conventional top-level prose files (relative to the project root).
_ROOT_DOCS = ("README.md", "ARCHITECTURE.md", "DESIGN.md", "CONTRIBUTING.md")
_DOCS_GLOB_DIR = "docs"
_BYTES_PER_KB = 1024


@decision_source_registry.register(_NAME)
@dataclass(frozen=True, slots=True)
class DocsProseSource:
    """Mines keyword-bearing paragraphs from a bounded set of prose files."""

    name: str = _NAME

    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        cfg = ctx.config.docs_prose
        tree_qnames = _collect_tree_qnames(ctx)
        candidates = _candidate_files(ctx.project_root)
        selected, over_cap = candidates[: cfg.max_files], candidates[cfg.max_files :]
        raws: list[RawDecision] = []
        skipped_size = 0
        for path in selected:
            body = _read_within_cap(path, cfg.max_kb_per_file)
            if body is None:
                skipped_size += 1
                continue
            raws.extend(_mine_file(body, path, ctx.project_root, tree_qnames))
        _log_drops(len(over_cap), skipped_size)
        return tuple(raws)


def _candidate_files(project_root: Path) -> list[Path]:
    """Root prose files that exist, then ``docs/*.md`` sorted — deterministic order."""
    files = [project_root / name for name in _ROOT_DOCS if (project_root / name).is_file()]
    docs_dir = project_root / _DOCS_GLOB_DIR
    if docs_dir.is_dir():
        files.extend(sorted(docs_dir.glob("*.md")))
    return files


def _read_within_cap(path: Path, max_kb_per_file: int) -> str | None:
    """Read a file's text, or ``None`` when it exceeds the per-file size cap."""
    if path.stat().st_size > max_kb_per_file * _BYTES_PER_KB:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _mine_file(
    body: str,
    path: Path,
    project_root: Path,
    tree_qnames: frozenset[str],
) -> list[RawDecision]:
    """Keyword-gate each paragraph of one prose file into pre-merge decisions."""
    locator_base = _rel_locator(path, project_root)
    raws: list[RawDecision] = []
    for index, paragraph in enumerate(_split_paragraphs(body)):
        if not qualifies(paragraph, paragraph):
            continue
        files, qnames = _scan_affected(paragraph, project_root, tree_qnames)
        raws.append(
            RawDecision(
                title=_first_line(paragraph)[:_TITLE_MAX],
                status="proposed",
                source=_NAME,
                confidence=_CONFIDENCE,
                evidence=(
                    DecisionEvidence(
                        source=_NAME,
                        locator=f"{locator_base}#p{index}",
                        text=paragraph,
                    ),
                ),
                affected_files=files,
                affected_qnames=qnames,
            )
        )
    return raws


def _split_paragraphs(body: str) -> list[str]:
    """Blank-line-delimited paragraphs, stripped, empties dropped."""
    return [block.strip() for block in body.split("\n\n") if block.strip()]


def _first_line(paragraph: str) -> str:
    """The paragraph's first non-empty line, used as the decision title."""
    for line in paragraph.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return paragraph.strip()


def _log_drops(over_cap: int, skipped_size: int) -> None:
    """Emit one structured drop-count log when anything was bounded out."""
    if over_cap or skipped_size:
        _LOGGER.info(
            "docs_prose bounded out candidates",
            extra={"over_cap_files": over_cap, "oversize_files": skipped_size},
        )


def _rel_locator(path: Path, project_root: Path) -> str:
    """Repo-relative path string; falls back to the absolute path if outside root."""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)
