"""What the reference writer is shown for one task: ground truth only (judge 9c).

A reference answer is written once from ground truth — never from an agent
answer — so the judges can score agreement against it. A repoqa-qa task shows
the question, the gold path and symbol, and the needle's body with
``context_lines`` lines on each side; a chat record shows the question and
every gold site's path, symbol and span text. The files are read from the
task's own corpus, materialized for the read and removed after it.

Example:
    >>> with corpus_file_reader(task) as read_file:  # doctest: +SKIP
    ...     source = repoqa_reference_source(task, read_file, context_lines=5)
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import gold_sites_of
from pydocs_eval.datasets.repo_qa import GOLD_SYMBOL_KEY
from pydocs_eval.datasets.repoqa import NEEDLE_FIRST_LINE_KEY, NEEDLE_LAST_LINE_KEY
from pydocs_eval.judge.needle_citation import NeedleSite

#: Reads one file of a task's corpus by its repo-relative path.
FileReader = Callable[[str], str]


class ReferenceShape(StrEnum):
    """Which dataset's gold a source carries, and so which code check its reference passes."""

    REPOQA = "repoqa"
    CHAT = "chat"


@dataclass(frozen=True, slots=True)
class ShownCode:
    """One gold site as the writer sees it: where it is, and its lines with those around them.

    ``start`` and ``end`` are the gold span; ``first_line`` numbers ``lines[0]``.
    """

    site: NeedleSite
    start: int
    end: int
    first_line: int
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceSource:
    """Everything the writer is shown for one task, in gold-site order."""

    task_id: str
    question: str
    shape: ReferenceShape
    code: tuple[ShownCode, ...]


@dataclass(frozen=True, slots=True)
class CorpusFiles:
    """One task's materialized corpus, read by repo-relative path."""

    root: Path

    def __call__(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8")


@contextmanager
def corpus_file_reader(task: EvalTask) -> Iterator[CorpusFiles]:
    """``task``'s corpus on disk for the block, removed after it.

    Example:
        >>> with corpus_file_reader(task) as read_file:  # doctest: +SKIP
        ...     read_file("pkg/mod.py")
    """
    root = task.corpus_source()
    try:
        yield CorpusFiles(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def repoqa_reference_source(
    task: EvalTask, read_file: FileReader, *, context_lines: int
) -> ReferenceSource:
    """A repoqa-qa task as the writer sees it: the needle, ``context_lines`` around it.

    Raises:
        ValueError: the task lacks its gold path, symbol or needle span.
    """
    site = NeedleSite(_only_gold_path(task), str(task.gold.extra.get(GOLD_SYMBOL_KEY, "")))
    if not site.symbol:
        raise ValueError(f"repoqa task {task.task_id!r} has no gold symbol, expected one")
    start, end = _needle_span(task)
    code = _shown(site, start, end, read_file(site.path), context_lines)
    return ReferenceSource(task.task_id, task.query, ReferenceShape.REPOQA, (code,))


def chat_reference_source(task: EvalTask, read_file: FileReader) -> ReferenceSource:
    """A chat record as the writer sees it: every gold site's span text, nothing around it.

    Raises:
        ValueError: the task carries no gold site.
    """
    sites = gold_sites_of(task)
    if not sites:
        raise ValueError(f"chat task {task.task_id!r} has no gold site, expected at least one")
    code = tuple(
        _shown(NeedleSite(site.path, site.symbol), site.start, site.end, read_file(site.path), 0)
        for site in sites
    )
    return ReferenceSource(task.task_id, task.query, ReferenceShape.CHAT, code)


def _only_gold_path(task: EvalTask) -> str:
    paths = task.gold.file_set
    if len(paths) != 1:
        raise ValueError(
            f"repoqa task {task.task_id!r} has gold paths {paths!r}, expected exactly one"
        )
    return paths[0]


def _needle_span(task: EvalTask) -> tuple[int, int]:
    """The needle's 1-indexed, inclusive span, from the repoqa loader's metadata."""
    try:
        start = int(task.metadata[NEEDLE_FIRST_LINE_KEY])
        end = int(task.metadata[NEEDLE_LAST_LINE_KEY])
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"repoqa task {task.task_id!r} lacks a numeric {exc} in its metadata, expected "
            f"{NEEDLE_FIRST_LINE_KEY!r} and {NEEDLE_LAST_LINE_KEY!r} from the repoqa loader"
        ) from None
    return start, end


def _shown(site: NeedleSite, start: int, end: int, text: str, context_lines: int) -> ShownCode:
    """``site``'s span in ``text``, widened by ``context_lines`` and cut at the file's edges."""
    lines = text.splitlines()
    first = max(1, start - context_lines)
    last = min(len(lines), end + context_lines)
    return ShownCode(site, start, end, first, tuple(lines[first - 1 : last]))


__all__ = (
    "CorpusFiles",
    "FileReader",
    "ReferenceShape",
    "ReferenceSource",
    "ShownCode",
    "chat_reference_source",
    "corpus_file_reader",
    "repoqa_reference_source",
)
