"""Where a value appears on disk: the bench VM leak scans, reporting places, never the value.

A plain module with no package-relative import: the tests import it, and so
does the fixture child, run as a script, to scan while it still runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path


def files_under(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()] if root.is_dir() else []


def _holds(path: Path, needle: bytes) -> bool:
    try:
        return needle in path.read_bytes()
    except FileNotFoundError:  # renamed or deleted while the scan ran
        return False


def places_holding(roots: Mapping[str, Path], needle: bytes) -> list[str]:
    """``<label>:<relative path>`` of every file under the labelled roots that holds ``needle``."""
    return [
        f"{label}:{path.relative_to(root).as_posix()}"
        for label, root in roots.items()
        for path in files_under(root)
        if _holds(path, needle)
    ]
