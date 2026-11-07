"""Parse a JSONL run file line by line; each reader keeps its own corruption policy (#420).

The JSONL tracker (resuming a named run) and ``reporting/ci_compare.py``
(reading a run's last aggregate) read the same files. They disagree, on
purpose, about what a bad line means — the tracker skips one with a warning,
``ci_compare`` refuses a file whose bad line was not followed by a resume —
so this module only reports the facts of each line and leaves the policy to
the caller.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunFileLine:
    """One line of a run file.

    ``record`` is the line's JSON object, ``None`` for a blank line, a JSON
    value that is not an object, or a line that is not JSON at all; only the
    last carries ``decode_error``.
    """

    number: int  # 1-based, as an editor shows it
    record: Mapping[str, object] | None
    decode_error: str | None = None


def read_run_file_lines(path: Path) -> tuple[RunFileLine, ...]:
    """Every line of ``path``, in file order."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(_parse_line(number, line) for number, line in enumerate(lines, start=1))


def _parse_line(number: int, line: str) -> RunFileLine:
    if not line.strip():
        return RunFileLine(number=number, record=None)
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        return RunFileLine(number=number, record=None, decode_error=str(exc))
    return RunFileLine(number=number, record=value if isinstance(value, Mapping) else None)


__all__ = ("RunFileLine", "read_run_file_lines")
