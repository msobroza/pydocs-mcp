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

from pydocs_mcp.extraction.config import path_under_excluded

# Same logger name as the chunkers (see chunkers/ast_python.py) so all
# indexing-side diagnostics share one `pydocs-mcp -v` channel.
log = logging.getLogger("pydocs-mcp")


def _within_size_budget(path: str, max_bytes: int) -> bool:
    """Return ``True`` iff the file exists and its size ≤ ``max_bytes``.

    Missing / unreadable files are dropped silently — ``Path.stat``
    raising means the downstream reader would also fail, so there's no
    point surfacing an error here. Oversized files are dropped LOUDLY:
    a silent size skip once hid an unindexed 561KB module and capped
    retrieval recall for every method (PAGEINDEX_DIVS.md F3), so every
    skipped file is named with the cap that excluded it.
    """
    try:
        size = Path(path).stat().st_size
    except OSError:
        return False
    if size > max_bytes:
        log.warning(
            "skipping %s (%d bytes > max_file_size_bytes=%d); raise "
            "extraction.discovery.*.max_file_size_bytes in your config "
            "YAML to index it",
            path,
            size,
            max_bytes,
        )
        return False
    return True


def _in_excluded_dir(relpath: str) -> bool:
    """True iff any path component of ``relpath`` is blocklisted.

    Delegates to :func:`pydocs_mcp.extraction.config.path_under_excluded`
    so this module and the members extractor enforce the same policy
    with the same splitting rules (M2). Guards against dependency wheels
    that ship vestigial ``.git`` or ``__pycache__`` directories (rare
    but real — spec §11.1).
    """
    return path_under_excluded(relpath)


__all__ = ("_in_excluded_dir", "_within_size_budget")
