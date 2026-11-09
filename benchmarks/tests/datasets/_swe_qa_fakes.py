"""Named fakes for the swe-qa-questions framing: an offline source of known gold sizes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer

#: Gold sizes of the fake corpus: 12 one-file rows, 10 two-or-three, 8 four-plus.
GOLD_SIZES = (1,) * 12 + (2, 3) * 5 + (4, 6) * 4
REPOS = ("django", "flask", "sympy", "pytest", "requests")


def sized_gold_task(index: int, size: int) -> EvalTask:
    """One swe-qa row whose gold cites ``size`` files, in one of :data:`REPOS`."""
    repo = REPOS[index % len(REPOS)]
    return EvalTask(
        task_id=f"swe_qa/{repo}/{index}",
        query=f"question {index}?",
        gold=GoldAnswer(file_set=tuple(f"pkg/m{n}.py" for n in range(size))),
        corpus_source=lambda: Path("unused"),
        metadata={"repo": f"{repo}/{repo}", "commit": "abc1234"},
    )


@dataclass
class FakeSweQaSource:
    """A swe-qa stand-in yielding rows of known gold size, in a fixed order."""

    name: str = "swe-qa"
    revision: str = "fake"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for index, size in enumerate(GOLD_SIZES):
            yield sized_gold_task(index, size)
