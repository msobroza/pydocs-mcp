"""The ``repo_qa`` framing — the FIRST second framing (run-contract design §5).

Two registered datasets, one task name. Both re-mint an existing corpus's
records as repository-QA rows whose answer is ONE location (a symbol and the
file that holds it), so both arms scored under them share — and update — the
single harness-invariant ``TASK_HEAD: repo_qa`` section:

- ``repoqa-qa`` re-frames ``repoqa``'s function-retrieval needles: the needle
  DESCRIPTION becomes the question (behind a QA stem), and the gold becomes
  the needle's symbol name plus its repo-relative path.
- ``swe-qa-qa`` re-frames ``swe-qa``'s genuine QA pairs: the question is
  already a question, and the gold is already the citation-resolved file set.

**Wrappers, not loaders** (the ``repoqa-structural`` / ``CombinedDataset``
precedent): each delegates acquisition, caching, splits and pins to the source
dataset it wraps and changes only ``task_id``, ``record_id``, ``query`` and
``gold``. There is no second downloader, no second cache and no second set of
commit pins to drift.

**Rows are SIBLINGS, not replacements.** Each minted row carries
``record_id`` = the source row's task id, so the record-keyed split and the
paired statistics' record-level clustering (platform spec §5.4) keep both
framings of one record on the same side. The task id is the three-part
``<dataset>/<task_name>/<record_id>`` spelling design §5 reserved for exactly
this event; the ``<dataset>`` segment is the FRAMING dataset's registered
name, so ``task_id_prefix`` keeps naming the dataset that produced the row.

The QA stem lives HERE and not in ``task_rendering._SCAFFOLD``: the scaffold is
shared by every execution path and editing it bumps ``TASK_SCAFFOLD_VERSION``,
which moves every objective hash and every arm hash. A dataset's own question
text is the dataset's business.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path

from ..registries import dataset_registry
from .base_dataset import Dataset, EvalTask, GoldAnswer
from .repoqa import RepoQADataset
from .swe_qa import SweQaDataset
from .task_ids import mint_framed_task_id

# The product-enumerated framing both datasets below mint under
# (``skill_artifact_loader.TASK_NAMES``). One name, two corpora — that IS the
# demonstration: two arms, two rubrics, ONE shared TASK_HEAD section.
REPO_QA_TASK_NAME = "repo_qa"

# RepoQA needles carry a structured behavior DESCRIPTION, not a question. The
# stem is what turns the retrieval prompt into the QA framing; it names both
# halves of the gold so the answer shape and the deterministic checks agree.
_DESCRIPTION_QUESTION_STEM = (
    "Which function in this repository implements the following, and which "
    "file holds it? Name the function and its repo-relative path.\n\n"
)

# This LAYER's own version — bumped when the framing (stem, gold shape, id
# spelling) changes, independently of the corpus underneath it.
_FRAMING_VERSION = "v1"


def _framed_revision(source_revision: str) -> str:
    """``"<framing>+<corpus pin>"`` — both halves of what a framed row IS.

    ``dry_run._arm_summary`` and ``sweep`` write ``{name}@{revision}`` into the
    owner-facing arm plan and every run record, so a frozen ``"v1"`` would make
    two runs over materially different corpus releases indistinguishable in the
    report. The corpus half is READ from the wrapped dataset's own pin, never
    re-spelled here.
    """
    return f"{_FRAMING_VERSION}+{source_revision}"


# Where the needle's symbol name lands in gold.extra. The gold candidate
# readers (``gold_substring`` / ``gold_substring_all`` / ``gold_recall``) take
# ``file_set`` plus every STRING value of ``extra``, so a symbol under this key
# is checkable with no new predicate — and ``params: {keys: [...]}`` can select
# it by name.
GOLD_SYMBOL_KEY = "symbol"


@dataset_registry.register("repoqa-qa")
@dataclass
class RepoQaQuestionDataset:
    """RepoQA needles re-framed as single-location QA (Apache-2.0, upstream)."""

    name: str = "repoqa-qa"
    #: The framing version PLUS the wrapped corpus's release pin, read from
    #: ``RepoQADataset`` so re-pinning the corpus moves this stamp too (a
    #: derivation, not a second source of truth).
    revision: str = _framed_revision(RepoQADataset.revision)
    fixture_path: Path | None = None
    #: Test-injection seam (the ``CombinedDataset.members`` precedent): a
    #: pre-built source dataset, so a fixture-backed test never downloads.
    source: Dataset | None = None

    def tasks(self) -> AsyncIterator[EvalTask]:
        """Protocol method — a plain ``def`` returning the async generator."""
        return self._iter_tasks()

    async def _iter_tasks(self) -> AsyncIterator[EvalTask]:
        source = self.source or RepoQADataset(fixture_path=self.fixture_path)
        async for task in source.tasks():
            yield _reframe_needle_as_question(task, dataset=self.name)


@dataset_registry.register("swe-qa-qa")
@dataclass
class SweQaQuestionDataset:
    """SWE-QA question/answer pairs re-framed under ``repo_qa`` (Apache-2.0)."""

    name: str = "swe-qa-qa"
    revision: str = _framed_revision(SweQaDataset.revision)
    fixture_path: Path | None = None
    source: Dataset | None = None

    def tasks(self) -> AsyncIterator[EvalTask]:
        """Protocol method — a plain ``def`` returning the async generator."""
        return self._iter_tasks()

    async def _iter_tasks(self) -> AsyncIterator[EvalTask]:
        source = self.source or SweQaDataset(fixture_path=self.fixture_path)
        async for task in source.tasks():
            yield _reframe_question_row(task, dataset=self.name)


def _reframe_needle_as_question(task: EvalTask, *, dataset: str) -> EvalTask:
    """One RepoQA needle row as a ``repo_qa`` row.

    Gold becomes symbol + path and DROPS ``ast_body`` deliberately: the QA
    framing's gold is the location, and ``metrics/_relevance.py`` dispatches
    the retrieval track on ``ast_body is not None`` — leaving it would let this
    dataset be scored as if its gold were still the function's source.

    Raises:
        ValueError: the source row carries no needle name/path metadata, so no
            checkable QA gold can be built from it.
    """
    symbol = task.metadata.get("needle_name", "")
    path = task.metadata.get("needle_path", "")
    if not symbol or not path:
        raise ValueError(
            f"repoqa-qa cannot frame task {task.task_id!r}: got needle_name={symbol!r}, "
            f"needle_path={path!r} in metadata, expected both non-empty (the QA gold is "
            "the function name plus its repo-relative file path)"
        )
    return replace(
        task,
        task_id=_framed_id(dataset, task.task_id),
        record_id=task.task_id,
        query=f"{_DESCRIPTION_QUESTION_STEM}{task.query}",
        gold=GoldAnswer(file_set=(path,), extra={GOLD_SYMBOL_KEY: symbol}),
    )


def _reframe_question_row(task: EvalTask, *, dataset: str) -> EvalTask:
    """One SWE-QA row as a ``repo_qa`` row — id and record only.

    The question is already a question and the gold is already the
    citation-resolved file set, so nothing else is touched: this arm's gold is
    file-level by construction (the source's commit pins are unverified, spec
    §D14), which is exactly why it binds a different rubric than the
    symbol-level ``repoqa-qa`` arm.

    Raises:
        ValueError: the source row carries an empty gold file set, which would
            make every gold check pass vacuously.
    """
    if not task.gold.file_set:
        raise ValueError(
            f"swe-qa-qa cannot frame task {task.task_id!r}: gold.file_set is empty, "
            "expected at least one cited repo path (a vacuous gold would score 1.0 "
            "on every deterministic check)"
        )
    return replace(
        task,
        task_id=_framed_id(dataset, task.task_id),
        record_id=task.task_id,
    )


def _framed_id(dataset: str, record_id: str) -> str:
    """The three-part id for one framed row — one call site per dataset."""
    return mint_framed_task_id(dataset=dataset, task_name=REPO_QA_TASK_NAME, record_id=record_id)


__all__ = [
    "GOLD_SYMBOL_KEY",
    "REPO_QA_TASK_NAME",
    "RepoQaQuestionDataset",
    "SweQaQuestionDataset",
]
