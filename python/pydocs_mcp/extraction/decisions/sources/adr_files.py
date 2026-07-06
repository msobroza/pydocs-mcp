"""``adr_files`` source — mine Architecture Decision Records (spec §D8).

Globs the conventional ADR directories under the project root for ``*.md``
files and parses each one's ``# `` heading (title, minus a leading numeral),
``Status:`` header (mapped to a lifecycle state), and ``Date:`` header (parsed
to an epoch). The whole file is the verbatim evidence span. ADRs are
author-curated, so they mine at confidence 1.0 — the highest of any source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydocs_mcp.extraction.decisions._types import (
    _ADR_STATUS_MAP,
    CaptureContext,
    DecisionEvidence,
    RawDecision,
    decision_source_registry,
)

# Author-curated → the highest-trust source. See ``inline_markers`` (0.95).
_CONFIDENCE = 1.0
_DEFAULT_STATUS = "proposed"
_NAME = "adr_files"

# Conventional ADR homes, relative to the project root (spec §D8).
_ADR_DIRS = ("docs/adr", "doc/adr", "docs/decisions", "adr")

_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
# Strip a leading ADR ordinal like "1." / "12." from the heading text.
_LEADING_ORDINAL_RE = re.compile(r"^\d+\.\s*")
_STATUS_RE = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_DATE_RE = re.compile(r"^Date:\s*(\d{4}-\d{2}-\d{2})", re.MULTILINE | re.IGNORECASE)
# File-path token: contains a slash and ends in .py (validated for existence).
_PATH_TOKEN_RE = re.compile(r"[\w./-]+\.py\b")
# Dotted-name token: at least one dot between identifier chunks.
_DOTTED_TOKEN_RE = re.compile(r"\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+\b")


@decision_source_registry.register(_NAME)
@dataclass(frozen=True, slots=True)
class AdrFilesSource:
    """Mines ADR markdown files from the conventional decision directories."""

    name: str = _NAME

    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]:
        tree_qnames = _collect_tree_qnames(ctx)
        raws: list[RawDecision] = []
        for path in _adr_paths(ctx.project_root):
            raw = _parse_adr(path, ctx.project_root, tree_qnames)
            if raw is not None:
                raws.append(raw)
        return tuple(raws)


def _adr_paths(project_root: Path) -> list[Path]:
    """Every ``*.md`` under a conventional ADR directory, sorted for determinism."""
    paths: list[Path] = []
    for rel in _ADR_DIRS:
        base = project_root / rel
        if base.is_dir():
            paths.extend(sorted(base.glob("*.md")))
    return paths


def _parse_adr(path: Path, project_root: Path, tree_qnames: frozenset[str]) -> RawDecision | None:
    """Parse one ADR file into a :class:`RawDecision`, or ``None`` if it has no heading."""
    body = path.read_text(encoding="utf-8", errors="replace")
    title = _extract_title(body)
    if title is None:
        return None
    locator = _rel_locator(path, project_root)
    files, qnames = _scan_affected(body, project_root, tree_qnames)
    return RawDecision(
        title=title,
        status=_extract_status(body),
        source=_NAME,
        confidence=_CONFIDENCE,
        evidence=(DecisionEvidence(source=_NAME, locator=locator, text=body),),
        affected_files=files,
        affected_qnames=qnames,
        evidence_date=_extract_date(body),
    )


def _extract_title(body: str) -> str | None:
    """First ``# `` heading, stripped of a leading ADR ordinal; ``None`` if none."""
    match = _HEADING_RE.search(body)
    if match is None:
        return None
    return _LEADING_ORDINAL_RE.sub("", match.group(1)).strip()


def _extract_status(body: str) -> str:
    """``Status:`` header mapped to a lifecycle state; unknown → "proposed"."""
    match = _STATUS_RE.search(body)
    if match is None:
        return _DEFAULT_STATUS
    return _ADR_STATUS_MAP.get(match.group(1).strip().lower(), _DEFAULT_STATUS)


def _extract_date(body: str) -> float | None:
    """``Date: YYYY-MM-DD`` header parsed to a UTC epoch; ``None`` if absent/bad."""
    match = _DATE_RE.search(body)
    if match is None:
        return None
    try:
        parsed = datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed.timestamp()


def _scan_affected(
    body: str, project_root: Path, tree_qnames: frozenset[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Path tokens that exist under the root + dotted tokens matching a tree qname."""
    files = tuple(_existing_files(body, project_root))
    qnames = tuple(_matching_qnames(body, tree_qnames))
    return files, qnames


def _existing_files(body: str, project_root: Path) -> list[str]:
    """Repo-relative ``*.py`` tokens whose file actually exists under the root."""
    seen: dict[str, None] = {}
    for token in _PATH_TOKEN_RE.findall(body):
        rel = token.lstrip("./")
        if rel not in seen and (project_root / rel).is_file():
            seen[rel] = None
    return list(seen)


def _matching_qnames(body: str, tree_qnames: frozenset[str]) -> list[str]:
    """Dotted tokens that are (a prefix of) a known tree qualified name."""
    seen: dict[str, None] = {}
    for token in _DOTTED_TOKEN_RE.findall(body):
        if token in seen:
            continue
        if any(qn == token or qn.startswith(f"{token}.") for qn in tree_qnames):
            seen[token] = None
    return list(seen)


def _collect_tree_qnames(ctx: CaptureContext) -> frozenset[str]:
    """Every ``qualified_name`` in the extracted trees (recursive over children)."""
    names: set[str] = set()
    stack = list(ctx.trees)
    while stack:
        node = stack.pop()
        names.add(node.qualified_name)
        stack.extend(node.children)
    return frozenset(names)


def _rel_locator(path: Path, project_root: Path) -> str:
    """Repo-relative path string; falls back to the absolute path if outside root."""
    try:
        return str(path.relative_to(project_root))
    except ValueError:
        return str(path)
