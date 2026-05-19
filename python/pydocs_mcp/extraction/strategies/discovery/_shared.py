"""Helpers shared by both file discoverers.

Both ``ProjectFileDiscoverer`` and ``DependencyFileDiscoverer`` filter
out files larger than ``scope.max_file_size_bytes`` and skip files
inside the hardcoded blocklist of directory names (.git, .venv, etc.).
These helpers live here so the two implementations stay byte-identical
on their pruning policy.
"""
from __future__ import annotations

import os

from pydocs_mcp.extraction.config import path_under_excluded


def _within_size_budget(path: str, max_bytes: int) -> bool:
    """Return ``True`` iff the file exists and its size ≤ ``max_bytes``.

    Missing / unreadable files are dropped silently — ``os.getsize``
    raising means the downstream reader would also fail, so there's no
    point surfacing an error here.
    """
    try:
        return os.path.getsize(path) <= max_bytes
    except OSError:
        return False


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
