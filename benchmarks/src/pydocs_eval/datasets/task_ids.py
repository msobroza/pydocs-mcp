"""Task-id spellings for a corpus that mints more than one framing (design §5).

Two spellings coexist, deliberately:

- **two-part** ``<dataset>/<record_id>`` (or a bare record id) — what every
  single-framing corpus mints and keeps minting. ``CombinedDataset`` prefixes
  its members this way and ``task_id_prefix`` groups on it.
- **three-part** ``<dataset>/<task_name>/<record_id>`` — the reserved spelling
  the run-contract design §5 says "lands with the first second framing, never
  before". It activates here with ``repo_qa``, the first task name minted over
  records another framing already covers.

**The three-part form is NOT positionally parseable repo-wide.** Existing ids
already carry 0..4 slash segments with different meanings — ``swe-qa`` mints
``swe_qa/<repo>/<index>`` and ``repoqa`` mints
``<org>/<name>@<sha7>/<path>::<symbol>`` — so ``split("/", 2)`` would read a
repo name as a task name. :func:`parse_framed_task_id` is therefore
VOCABULARY-ANCHORED: it accepts a three-part id only when the middle segment
is one of the product's enumerated task names, and returns ``None`` otherwise
so a two-part id keeps its existing meaning byte-for-byte.

Explicit fields beat parsing: :attr:`EvalTask.record_id` carries the record a
row was minted from, and :func:`record_id_of` is the ONE reader. The parse is
the fallback for rows that arrive without the field.

BASE install by contract: stdlib + ``base_dataset`` only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .base_dataset import EvalTask

# One separator, three segments — the record id is the REMAINDER, because a
# record id may itself contain separators (``repoqa`` needle paths do).
TASK_ID_SEPARATOR = "/"
_FRAMED_SEGMENTS = 3


@dataclass(frozen=True, slots=True)
class FramedTaskId:
    """The three parts of a framed task id: corpus, framing, record."""

    dataset: str
    task_name: str
    record_id: str


def mint_framed_task_id(*, dataset: str, task_name: str, record_id: str) -> str:
    """Spell one framed task id (``"repoqa-qa/repo_qa/psf/black@f03ee11/..."``).

    ``dataset`` is the producing dataset's REGISTERED name, so
    ``task_id_prefix`` on the result is that name and per-task-type rubric
    vocabularies keep working. ``record_id`` may contain separators; the other
    two segments may not, or the id would not round-trip.

    Raises:
        ValueError: an empty segment, or a separator inside ``dataset`` /
            ``task_name`` — the message carries the offending value.

    Example:
        >>> mint_framed_task_id(dataset="swe-qa-qa", task_name="repo_qa", record_id="swe_qa/django/7")
        'swe-qa-qa/repo_qa/swe_qa/django/7'
    """
    for label, value in (("dataset", dataset), ("task_name", task_name)):
        if not value or TASK_ID_SEPARATOR in value:
            raise ValueError(
                f"invalid {label} {value!r} for a framed task id: expected a non-empty "
                f"segment without {TASK_ID_SEPARATOR!r} (only record_id may contain one)"
            )
    if not record_id:
        raise ValueError(
            f"invalid record_id {record_id!r} for a framed task id in "
            f"{dataset}/{task_name}: expected a non-empty record identifier"
        )
    return TASK_ID_SEPARATOR.join((dataset, task_name, record_id))


def parse_framed_task_id(task_id: str, *, task_names: Sequence[str]) -> FramedTaskId | None:
    """Split a framed task id, or ``None`` when ``task_id`` is not framed.

    Anchored on ``task_names`` rather than on segment count: an id whose middle
    segment is not an enumerated framing (``swe_qa/matplotlib/12``) is a
    two-part id that happens to carry two separators, and it must keep its
    existing meaning.

    Example:
        >>> parse_framed_task_id("swe_qa/matplotlib/12", task_names=("repo_qa",)) is None
        True
        >>> parse_framed_task_id("repoqa-qa/repo_qa/n1", task_names=("repo_qa",)).record_id
        'n1'
    """
    parts = task_id.split(TASK_ID_SEPARATOR, _FRAMED_SEGMENTS - 1)
    if len(parts) != _FRAMED_SEGMENTS:
        return None
    dataset, task_name, record_id = parts
    if task_name not in task_names or not dataset or not record_id:
        return None
    return FramedTaskId(dataset=dataset, task_name=task_name, record_id=record_id)


def record_id_of(task: EvalTask, *, task_names: Sequence[str] = ()) -> str:
    """The RECORD a task row was minted from — the split + clustering unit.

    THE one derivation, in the module docstring's order: the explicit
    ``record_id`` field, else the record segment of a framed id, else the task
    id itself. Every single-framing corpus keeps its existing split side
    byte-for-byte; a framing layer sets ``record_id`` explicitly so sibling
    rows over one record travel together (platform spec §5.4).

    ``task_names`` is the framing vocabulary the id parse anchors on. It
    defaults to EMPTY — this module is stdlib+``base_dataset`` by contract and
    must not import the product's enumeration — so a caller that wants the
    parse fallback (the ask track, whose harness key is the record) passes it.
    Without it a framed row still resolves correctly whenever its ``record_id``
    field is set, which is what every shipped framing dataset does.

    Example:
        >>> record_id_of(EvalTask("t", "q", GoldAnswer(), lambda: None))  # doctest: +SKIP
        't'
    """
    if task.record_id:
        return task.record_id
    framed = parse_framed_task_id(task.task_id, task_names=task_names)
    return framed.record_id if framed else task.task_id


__all__ = [
    "TASK_ID_SEPARATOR",
    "FramedTaskId",
    "mint_framed_task_id",
    "parse_framed_task_id",
    "record_id_of",
]
