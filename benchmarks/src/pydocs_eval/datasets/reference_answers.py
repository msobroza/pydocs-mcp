"""Reference answers on disk: one JSON row per task, keyed by task id (turn-efficiency judge 9c).

A reference is written once from ground truth by the judge's reference writer
and never rewritten: a row stored under a task id may be written again only
byte for byte. Each line is canonical JSON (sorted keys) carrying the text, the
writer's model id, the prompt hash, why the fallback wrote it (empty when the
primary did) and the format's revision, so the file's digest moves only when a
row does. The repoqa-qa references ship as package data beside the dataset
that attaches them (:func:`vendored_repoqa_reference_path`).

Example:
    >>> reference_answers_by_task(vendored_repoqa_reference_path())  # doctest: +SKIP
    {'repoqa-qa/repo_qa/…::factorial': ReferenceAnswer(model_id='anthropic/…', prompt_hash='…')}
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.datasets.base_dataset import ReferenceAnswer

# The rows file's format; a reader refuses any other, so a format change is a
# deliberate bump read by every consumer.
_REFERENCE_REVISION = "1.0"
_FIELDS = ("task_id", "revision", "text", "model_id", "prompt_hash", "fallback_reason")
_VENDORED_DIR = Path(__file__).parent / "data" / "repoqa_qa"
#: The repoqa-qa references' file name; batch 2 appends to the same file.
REPOQA_REFERENCE_FILE = "repoqa_reference_answers_v1.jsonl"


class ReferenceRowsError(ValueError):
    """A rows file, or a row about to be written, outside the declared format."""


@dataclass(frozen=True, slots=True)
class ReferenceRow:
    """One task's reference; ``fallback_reason`` is why the primary writer could not write it."""

    task_id: str
    reference: ReferenceAnswer
    fallback_reason: str = ""


def vendored_repoqa_reference_path() -> Path:
    """Where the repoqa-qa references live in this source tree, shipped as package data.

    Example:
        >>> vendored_repoqa_reference_path().name
        'repoqa_reference_answers_v1.jsonl'
    """
    return _VENDORED_DIR / REPOQA_REFERENCE_FILE


def read_reference_rows(path: Path) -> tuple[ReferenceRow, ...]:
    """Every row of ``path`` in file order; a missing file holds none.

    Raises:
        ReferenceRowsError: a line that is not a row of this revision, named by number.
    """
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(_row_of(path, number, line) for number, line in enumerate(lines, 1) if line)


def reference_answers_by_task(path: Path) -> dict[str, ReferenceAnswer]:
    """Each stored reference by its task id — what a dataset attaches to its gold."""
    return {row.task_id: row.reference for row in read_reference_rows(path)}


def write_reference_rows(path: Path, rows: Sequence[ReferenceRow]) -> None:
    """Add ``rows`` to ``path``, sorted by task id, in one atomic replace.

    Raises:
        ReferenceRowsError: a row differs from the one already stored under its task id.
    """
    stored = {row.task_id: row for row in read_reference_rows(path)}
    for row in rows:
        if stored.setdefault(row.task_id, row) != row:
            raise ReferenceRowsError(
                f"{row.task_id!r} already has a stored reference, expected it written once"
            )
    lines = [_line_of(stored[task_id]) for task_id in sorted(stored)]
    _replace(path, "".join(f"{line}\n" for line in lines))


def _line_of(row: ReferenceRow) -> str:
    fields = {
        "task_id": row.task_id,
        "revision": _REFERENCE_REVISION,
        "text": row.reference.text,
        "model_id": row.reference.model_id,
        "prompt_hash": row.reference.prompt_hash,
        "fallback_reason": row.fallback_reason,
    }
    return json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _row_of(path: Path, number: int, line: str) -> ReferenceRow:
    where = f"{path.name} line {number}"
    try:
        raw = json.loads(line)
        values = {name: raw[name] for name in _FIELDS}
    except (ValueError, TypeError, KeyError) as exc:
        raise ReferenceRowsError(
            f"{where}: {exc!r}, expected a JSON object with {list(_FIELDS)}"
        ) from None
    if values["revision"] != _REFERENCE_REVISION:
        raise ReferenceRowsError(
            f"{where}: revision {values['revision']!r}, expected {_REFERENCE_REVISION!r}"
        )
    return _checked_row(where, values)


def _checked_row(where: str, values: Mapping[str, object]) -> ReferenceRow:
    text, model_id, prompt_hash, task_id, fallback_reason = (
        _string(where, values, name)
        for name in ("text", "model_id", "prompt_hash", "task_id", "fallback_reason")
    )
    try:
        reference = ReferenceAnswer(text=text, model_id=model_id, prompt_hash=prompt_hash)
    except ValueError as exc:
        raise ReferenceRowsError(f"{where}: {exc}") from None
    return ReferenceRow(task_id, reference, fallback_reason)


def _string(where: str, values: Mapping[str, object], name: str) -> str:
    value = values[name]
    if not isinstance(value, str):
        raise ReferenceRowsError(f"{where}: {name} = {value!r}, expected a string")
    return value


def _replace(path: Path, text: str) -> None:
    """Write ``text`` to ``path``: a reader sees the old file or the whole new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as partial:
        partial.write(text)
    Path(partial.name).replace(path)


__all__ = (
    "REPOQA_REFERENCE_FILE",
    "ReferenceRow",
    "ReferenceRowsError",
    "read_reference_rows",
    "reference_answers_by_task",
    "vendored_repoqa_reference_path",
    "write_reference_rows",
)
