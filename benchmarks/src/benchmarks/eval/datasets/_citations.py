"""Answer-text → file citations, the §D14 pseudo-qrel extractor.

File-LEVEL labels only: line ranges are captured for provenance but relevance
is path membership (SWE-QA's pins are unverified against the HF release, so
line-level labels would be false precision; spec §D14 documents this).
"""

from __future__ import annotations

import re

# path or bare filename, optionally backticked, with optional 'line N[-M]' tail.
_CITE_RE = re.compile(
    r"`?(?P<path>[A-Za-z0-9_\-./]+\.py)`?"
    r"(?:\s*[:,(]?\s*lines?\s+(?P<start>\d+)(?:\s*[-–]\s*(?P<end>\d+))?)?"
)


def extract_path_citations(answer: str) -> tuple[tuple[str, int, int], ...]:
    """Every distinct (path, start, end) cited in an answer; (0, 0) when unranged."""
    seen: dict[tuple[str, int, int], None] = {}
    for m in _CITE_RE.finditer(answer):
        start = int(m.group("start") or 0)
        end = int(m.group("end") or start)
        seen.setdefault((m.group("path"), start, end))
    return tuple(seen)


def resolve_bare_filenames(
    citations: tuple[tuple[str, int, int], ...],
    repo_tree: tuple[str, ...],
) -> tuple[tuple[tuple[str, int, int], ...], tuple[str, ...]]:
    """Map bare filenames to repo-relative paths by UNIQUE basename; ambiguous → dropped.

    Returns (resolved citations, dropped filenames) so callers can log drops
    (no-silent-caps rule).
    """
    by_basename: dict[str, list[str]] = {}
    for path in repo_tree:
        by_basename.setdefault(path.rsplit("/", 1)[-1], []).append(path)
    resolved: list[tuple[str, int, int]] = []
    dropped: list[str] = []
    tree_set = set(repo_tree)
    for path, start, end in citations:
        if path in tree_set:
            resolved.append((path, start, end))
            continue
        if "/" in path:
            # slash-qualified but not in tree: try suffix match (answers often
            # cite paths relative to a subdir, e.g. src/... vs repo root)
            matches = [t for t in repo_tree if t.endswith("/" + path) or t == path]
        else:
            matches = by_basename.get(path, [])
        if len(matches) == 1:
            resolved.append((matches[0], start, end))
        else:
            dropped.append(path)
    return tuple(resolved), tuple(dropped)
