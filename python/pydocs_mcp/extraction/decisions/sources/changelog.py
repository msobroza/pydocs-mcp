"""``changelog`` source — mine architectural decisions from changelogs (spec §D8).

Reads ``CHANGELOG.md`` / ``CHANGES.md`` at the project root and under ``docs/``,
splits each on ``#``-to-``###`` headings into entries, and keyword-gates every
entry with the SAME scorer ``commit_messages`` uses (single source of truth).
Qualifying entries mine at confidence 0.70 / status ``proposed`` with the entry
body as verbatim evidence and locator ``<path>#<heading>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.decisions._types import (
    CaptureContext,
    DecisionEvidence,
    RawDecision,
    decision_source_registry,
)
from pydocs_mcp.extraction.decisions.sources.commit_messages import qualifies

# Changelog prose is the same moderate signal as commit history (spec §D8).
_CONFIDENCE = 0.70
_TITLE_MAX = 80
_NAME = "changelog"

# Conventional changelog filenames, at the root and under ``docs/``.
_CHANGELOG_NAMES = ("CHANGELOG.md", "CHANGES.md")
_CHANGELOG_DIRS = (".", "docs")

# A level-1..3 ATX heading line: captures the heading text after the hashes.
_HEADING_RE = re.compile(r"^#{1,3}\s+(.+?)\s*$")


@dataclass(frozen=True, slots=True)
class _Entry:
    heading: str
    body: str


@decision_source_registry.register(_NAME)
@dataclass(frozen=True, slots=True)
class ChangelogSource:
    """Mines keyword-bearing changelog entries as pre-merge decisions."""

    name: str = _NAME

    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        raws: list[RawDecision] = []
        for path in _changelog_paths(ctx.project_root):
            locator_base = _rel_locator(path, ctx.project_root)
            body = path.read_text(encoding="utf-8", errors="replace")
            for entry in _split_entries(body):
                raw = _entry_to_raw(entry, locator_base)
                if raw is not None:
                    raws.append(raw)
        return tuple(raws)


def _changelog_paths(project_root: Path) -> list[Path]:
    """Existing changelog files at the root and under ``docs/``, sorted."""
    paths: list[Path] = []
    for rel_dir in _CHANGELOG_DIRS:
        for name in _CHANGELOG_NAMES:
            candidate = project_root / rel_dir / name
            if candidate.is_file():
                paths.append(candidate)
    return sorted(set(paths))


def _split_entries(body: str) -> list[_Entry]:
    """Split changelog text into (heading, body) entries on level-1..3 headings."""
    entries: list[_Entry] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        match = _HEADING_RE.match(line)
        if match is not None:
            _flush(entries, heading, buffer)
            heading, buffer = match.group(1), []
        else:
            buffer.append(line)
    _flush(entries, heading, buffer)
    return entries


def _flush(entries: list[_Entry], heading: str | None, buffer: list[str]) -> None:
    """Append the accumulated entry when it has a heading (drops preamble)."""
    if heading is not None:
        entries.append(_Entry(heading=heading, body="\n".join(buffer).strip()))


def _entry_to_raw(entry: _Entry, locator_base: str) -> RawDecision | None:
    """Keyword-gate one entry and build its :class:`RawDecision` when it passes."""
    scored_text = f"{entry.heading}\n{entry.body}"
    if not qualifies(scored_text, entry.body):
        return None
    evidence = DecisionEvidence(
        source=_NAME,
        locator=f"{locator_base}#{entry.heading}",
        text=scored_text.strip(),
    )
    return RawDecision(
        title=entry.heading[:_TITLE_MAX],
        status="proposed",
        source=_NAME,
        confidence=_CONFIDENCE,
        evidence=(evidence,),
        affected_files=(),
        affected_qnames=(),
    )


def _rel_locator(path: Path, project_root: Path) -> str:
    """Repo-relative path string; falls back to the absolute path if outside root."""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)
