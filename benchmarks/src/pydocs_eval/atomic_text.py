"""Writing a text file so that a reader sees the old file or the whole new one, never half.

The text goes to a temporary file in the target's own directory (a rename is
atomic only within one filesystem), which then replaces the target.

Example:
    >>> write_text_atomically(Path("rows.jsonl"), "{}\\n")  # doctest: +SKIP
"""

from __future__ import annotations

import tempfile
from pathlib import Path

_TEMPORARY_SUFFIX = ".tmp"


def write_text_atomically(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 in one replace, creating its directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=_TEMPORARY_SUFFIX, delete=False
    ) as partial:
        partial.write(text)
    Path(partial.name).replace(path)


__all__ = ("write_text_atomically",)
