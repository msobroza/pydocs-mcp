"""Writing a text file so that a reader sees the old file or the whole new one, never half.

The text goes to a temporary file in the target's own directory (a rename is
atomic only within one filesystem), which then replaces the target. A write or
a swap that fails removes the temporary file, so the directory holds the old
target and nothing else (#382: the vendored label files live in the source tree).

Example:
    >>> write_text_atomically(Path("rows.jsonl"), "{}\\n")  # doctest: +SKIP
"""

from __future__ import annotations

import tempfile
from pathlib import Path

_TEMPORARY_SUFFIX = ".tmp"


def write_text_atomically(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 in one replace, creating its directory.

    Raises:
        OSError: the write or the swap failed; ``path`` keeps its old content.
        UnicodeEncodeError: ``text`` has no UTF-8 encoding; ``path`` keeps its old content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = tempfile.NamedTemporaryFile(  # noqa: SIM115 — closed below, before the swap
        "w", encoding="utf-8", dir=path.parent, suffix=_TEMPORARY_SUFFIX, delete=False
    )
    try:
        with partial:
            partial.write(text)
        Path(partial.name).replace(path)
    except BaseException:
        Path(partial.name).unlink(missing_ok=True)
        raise


__all__ = ("write_text_atomically",)
