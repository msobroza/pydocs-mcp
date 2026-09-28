"""Dataset axis contract (spec §4.3).

Owns ``CorpusSource``, ``GoldAnswer``, ``EvalTask`` and the ``Dataset``
``@runtime_checkable`` Protocol. Concrete datasets in
``benchmarks/eval/datasets/`` implement the Protocol and are reachable
through ``dataset_registry`` in ``registries.py`` — the runner never
imports the concretes directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# Each task carries a zero-arg factory that materializes the corpus on
# demand — the runner can then ``shutil.rmtree`` the dir between tasks
# without the dataset object needing to track per-task state.
CorpusSource = Callable[[], Path]

# Task metadata keys pinning the embedder a task's gold was authored against. A
# dataset that sets them is refused by a run serving another embedder (the
# before/after plan's preflight); one that sets neither is never checked.
GOLD_EMBEDDER_MODEL_KEY = "gold_embedder_model"
GOLD_EMBEDDER_DIM_KEY = "gold_embedder_dim"

# The ``GoldAnswer.extra`` key a reference answer rides under. Read it only
# through ``reference_answer_of``.
REFERENCE_ANSWER_KEY = "reference_answer"


@dataclass(frozen=True, slots=True)
class ReferenceAnswer:
    """A reference answer, written once from ground truth for the answer judge.

    A value object, never a ``str``: the rubric gates and checks take every
    string value of ``gold.extra`` as text an answer must contain, so a string
    reference would turn into a must-appear candidate. ``model_id`` names the
    writer and ``prompt_hash`` the prompt it was given (empty when the text is
    the dataset's own). The text stays out of ``repr``, so logging or printing
    a gold never shows it.

    Example:
        >>> ref = ReferenceAnswer(text="...", model_id="swe-qa", prompt_hash="")
        >>> reference_answer_of(GoldAnswer(extra={REFERENCE_ANSWER_KEY: ref})) is ref
        True
    """

    text: str = field(repr=False)
    model_id: str
    prompt_hash: str

    def __post_init__(self) -> None:
        for name in ("text", "model_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"ReferenceAnswer.{name} = {value!r}, expected a non-empty string")
        if not isinstance(self.prompt_hash, str):
            raise ValueError(
                f"ReferenceAnswer.prompt_hash = {self.prompt_hash!r}, expected a string"
            )


@dataclass(frozen=True, slots=True)
class GoldAnswer:
    """The retrieval target. ``ast_body`` covers function-retrieval
    datasets (RepoQA); ``file_set`` and ``extra`` keep the shape open for
    SWE-bench-style file-list golds without forcing a Protocol revision."""

    ast_body: str | None = None
    file_set: tuple[str, ...] = ()
    extra: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Refused at construction, not at first read: a bare string under the
        # key would be read by the rubric gates as text the answer must contain.
        reference_answer_of(self)


def reference_answer_of(gold: GoldAnswer) -> ReferenceAnswer | None:
    """The gold's reference answer, or ``None`` when it carries none.

    Raises:
        TypeError: something other than a ``ReferenceAnswer`` sits under
            ``REFERENCE_ANSWER_KEY``.

    Example:
        >>> reference_answer_of(GoldAnswer(file_set=("a.py",))) is None
        True
    """
    if REFERENCE_ANSWER_KEY not in gold.extra:
        return None
    reference = gold.extra[REFERENCE_ANSWER_KEY]
    if not isinstance(reference, ReferenceAnswer):
        raise TypeError(
            f"gold.extra[{REFERENCE_ANSWER_KEY!r}] = {reference!r}, expected a ReferenceAnswer"
        )
    return reference


@dataclass(frozen=True, slots=True)
class EvalTask:
    """One scoring unit: a query, a gold answer, and a callable that
    builds the corpus on demand.

    ``record_id`` names the underlying RECORD this row was minted from — what
    multi-framing siblings SHARE (run-contract design §5; platform spec §5.4's
    record-level clustering and the record-keyed split both bind on it). Empty
    means "this row is its own record": read it through
    ``task_ids.record_id_of`` rather than directly, so that default resolves in
    exactly one place and every pre-framing corpus keeps its split side."""

    task_id: str
    query: str
    gold: GoldAnswer
    corpus_source: CorpusSource
    metadata: Mapping[str, str] = field(default_factory=dict)
    record_id: str = ""


@runtime_checkable
class Dataset(Protocol):
    name: str
    revision: str

    # WHY: ``def`` (not ``async def``) so concrete impls can be plain async
    # generators — callers iterate as ``async for task in dataset.tasks()``
    # instead of the clunky ``async for task in await dataset.tasks()``.
    # An ``async def`` function returning a generator would force the
    # await-then-iterate pattern; ``def`` returning ``AsyncIterator``
    # accepts both shapes.
    def tasks(self) -> AsyncIterator[EvalTask]: ...


__all__ = [
    "REFERENCE_ANSWER_KEY",
    "CorpusSource",
    "Dataset",
    "EvalTask",
    "GoldAnswer",
    "ReferenceAnswer",
    "reference_answer_of",
]
