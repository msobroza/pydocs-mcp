"""Helpers shared by both file discoverers.

Both ``ProjectFileDiscoverer`` and ``DependencyFileDiscoverer`` filter
out files larger than ``scope.max_file_size_bytes`` and skip files
inside the effective exclusion set (hardcoded floor + configured
additions). These helpers live here so the two implementations stay
byte-identical on their pruning policy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, path_under_excluded

# Same logger name as the chunkers (see chunkers/ast_python.py) so all
# indexing-side diagnostics share one `pydocs-mcp -v` channel.
log = logging.getLogger("pydocs-mcp")


def _within_size_budget(path: str, max_bytes: int) -> bool:
    """Return ``True`` iff the file exists and its size ≤ ``max_bytes``.

    Missing / unreadable files are dropped silently — ``Path.stat``
    raising means the downstream reader would also fail, so there's no
    point surfacing an error here. Oversized files are dropped LOUDLY
    (:func:`size_within_budget`).
    """
    try:
        size = Path(path).stat().st_size
    except OSError:
        return False
    return size_within_budget(path, size, max_bytes)


def size_within_budget(path: str, size: int, max_bytes: int) -> bool:
    """``size <= max_bytes``; an oversized file is named in the log with the cap.

    WHY loud: a silent size skip once hid an unindexed 561KB module and capped
    retrieval recall for every method (PAGEINDEX_DIVS.md F3). Shared by the
    walk (size from ``stat``) and the branch manifest filter (size from the
    tree listing, #310), so both skip with the same message.
    """
    if size <= max_bytes:
        return True
    log.warning(
        "skipping %s (%d bytes > max_file_size_bytes=%d); raise "
        "extraction.discovery.*.max_file_size_bytes in your config "
        "YAML to index it",
        path,
        size,
        max_bytes,
    )
    return False


def _in_excluded_dir(
    relpath: str,
    excluded: frozenset[str] = _EXCLUDED_DIRS,
) -> bool:
    """True iff any path component of ``relpath`` is in ``excluded``.

    Delegates to :func:`pydocs_mcp.extraction.config.path_under_excluded`
    so this module and the members extractor enforce the same policy
    with the same splitting rules (M2). ``excluded`` defaults to the
    hardcoded floor (checked against the FULL file relpath — today's
    framing, unchanged); the dependency walk's user-entry check passes
    the effective union set (floor ∪ YAML ``dependency.exclude_dirs``
    bare names) with a PARENT-directory relpath, so user entries stay
    directories-only (§4). Guards against dependency wheels that ship
    vestigial ``.git`` or ``__pycache__`` directories (rare but real —
    spec §11.1).
    """
    return path_under_excluded(relpath, excluded)


__all__ = ("_in_excluded_dir", "_within_size_budget", "size_within_budget")
