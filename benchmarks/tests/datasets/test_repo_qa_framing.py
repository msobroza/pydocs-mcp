"""The ``repo_qa`` framing datasets — fixture-backed, no network, no git.

Both wrappers delegate acquisition to the dataset they wrap, so every test
here injects a fixture-backed source and asserts only what the framing layer
itself owns: the three-part id, the shared ``record_id``, the QA query and the
deterministic-checkable gold.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import Dataset, EvalTask, GoldAnswer
from pydocs_eval.datasets.repo_qa import (
    GOLD_SYMBOL_KEY,
    REPO_QA_TASK_NAME,
    RepoQaQuestionDataset,
    SweQaQuestionDataset,
)
from pydocs_eval.datasets.repoqa import RepoQADataset
from pydocs_eval.datasets.swe_qa import SweQaDataset
from pydocs_eval.datasets.task_ids import parse_framed_task_id, record_id_of
from pydocs_eval.registries import dataset_registry
from tests.optimize._trajectories import make_trajectory

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_REPOQA_FIXTURE = _FIXTURES / "repoqa_mini.json"
_SWEQA_FIXTURE = _FIXTURES / "swe_qa_mini.jsonl"
_CORPUS_DIR = _FIXTURES / "swe_qa_corpus"
_CORPUS_TREE = (
    "src/qibo/models/variational.py",
    "src/pkg/mod.py",
    "lib/matplotlib/backends/backend_pgf.py",
)


@dataclass
class _FakeRepoCache:
    """No git, no network — the ``test_swe_qa_loaders`` fake, reused."""

    tree: tuple[str, ...] = _CORPUS_TREE
    corpus_dir: Path = field(default=_CORPUS_DIR)

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return self.tree


@dataclass
class _OneTaskSource:
    """A minimal source dataset yielding exactly the row a test cares about."""

    task: EvalTask
    name: str = "fake-source"
    revision: str = "0"

    async def tasks(self):
        yield self.task


def _repoqa_framing() -> RepoQaQuestionDataset:
    return RepoQaQuestionDataset(source=RepoQADataset(fixture_path=_REPOQA_FIXTURE))


def _swe_qa_framing() -> SweQaQuestionDataset:
    return SweQaQuestionDataset(
        source=SweQaDataset(fixture_path=_SWEQA_FIXTURE, repo_cache=_FakeRepoCache())
    )


class TestRegistration:
    def test_both_framings_are_registered_and_satisfy_the_protocol(self) -> None:
        for name in ("repoqa-qa", "swe-qa-qa"):
            dataset = dataset_registry.build(name)
            assert isinstance(dataset, Dataset)
            assert dataset.name == name

    def test_the_registry_accepts_the_fixture_path_build_signature(self) -> None:
        # ``runner``/``dry_run`` build every dataset with fixture_path=…
        assert dataset_registry.build("repoqa-qa", fixture_path=_REPOQA_FIXTURE).name == "repoqa-qa"


class TestRepoQaOverRepoqa:
    async def test_every_row_is_a_three_part_id_over_the_source_record(self) -> None:
        source_ids = [t.task_id async for t in RepoQADataset(fixture_path=_REPOQA_FIXTURE).tasks()]
        tasks = [t async for t in _repoqa_framing().tasks()]
        assert len(tasks) == len(source_ids) == 5
        for task, source_id in zip(tasks, source_ids, strict=True):
            parsed = parse_framed_task_id(task.task_id, task_names=(REPO_QA_TASK_NAME,))
            assert parsed is not None
            assert parsed.dataset == "repoqa-qa"
            assert parsed.task_name == REPO_QA_TASK_NAME
            assert parsed.record_id == source_id
            # The RECORD is what a sibling framing shares — not the row id.
            assert record_id_of(task) == source_id != task.task_id

    async def test_the_description_becomes_a_question(self) -> None:
        source = await anext(RepoQADataset(fixture_path=_REPOQA_FIXTURE).tasks())
        framed = await anext(_repoqa_framing().tasks())
        assert framed.query.endswith(source.query)
        assert framed.query != source.query
        assert "?" in framed.query.removesuffix(source.query)

    async def test_gold_becomes_the_symbol_and_its_path_and_drops_ast_body(self) -> None:
        task = await anext(_repoqa_framing().tasks())
        assert task.gold.file_set == (task.metadata["needle_path"],)
        assert task.gold.extra[GOLD_SYMBOL_KEY] == task.metadata["needle_name"]
        # The retrieval track dispatches on ast_body; a QA gold must not claim it.
        assert task.gold.ast_body is None

    @pytest.mark.parametrize(
        ("answer_parts", "expected_gate", "expected_recall"),
        [
            (("needle_path", "needle_name"), True, 1.0),  # both halves named
            (("needle_path",), False, 0.5),  # path only — the flagship's near miss
            (("needle_name",), False, 0.5),  # symbol only
            ((), False, 0.0),  # neither
        ],
    )
    async def test_the_gold_is_checkable_by_the_shipped_deterministic_metrics(
        self,
        answer_parts: tuple[str, ...],
        expected_gate: bool,
        expected_recall: float,
    ) -> None:
        # Through the PUBLIC surface the flagship binds (evaluate_gate /
        # evaluate_check), never a private candidate helper: a gate that
        # ignored the gold entirely still satisfies a candidate-list assertion.
        from pydocs_eval.optimize.rubric.checks import Check, evaluate_check
        from pydocs_eval.optimize.rubric.gates import evaluate_gate
        from pydocs_eval.optimize.rubric.model import GateCheck

        task = await anext(_repoqa_framing().tasks())
        answer = " and ".join(task.metadata[part] for part in answer_parts) or "no idea"
        trajectory = make_trajectory(answer=answer)
        gate = GateCheck(name="names_symbol_and_file", kind="gold_substring_all", params={})
        assert evaluate_gate(gate, task, trajectory) is expected_gate
        recall = Check(name="recall", kind="gold_recall")
        assert evaluate_check(recall, task, trajectory).score == pytest.approx(expected_recall)

    async def test_a_row_without_needle_metadata_is_refused_with_its_value(self) -> None:
        naked = EvalTask(
            task_id="r1",
            query="describe",
            gold=GoldAnswer(ast_body="def f(): ..."),
            corpus_source=lambda: None,  # type: ignore[arg-type,return-value]
        )
        dataset = RepoQaQuestionDataset(source=_OneTaskSource(task=naked))
        with pytest.raises(ValueError) as excinfo:
            _ = [t async for t in dataset.tasks()]
        message = str(excinfo.value)
        assert "'r1'" in message and "needle_name" in message and "needle_path" in message


class TestRepoQaOverSweQa:
    async def test_rows_keep_their_question_and_file_set_gold(self) -> None:
        source = [
            t
            async for t in SweQaDataset(
                fixture_path=_SWEQA_FIXTURE, repo_cache=_FakeRepoCache()
            ).tasks()
        ]
        tasks = [t async for t in _swe_qa_framing().tasks()]
        assert tasks and len(tasks) == len(source)
        for task, origin in zip(tasks, source, strict=True):
            assert task.query == origin.query  # already a genuine question
            assert task.gold == origin.gold  # already citation-resolved paths
            assert record_id_of(task) == origin.task_id
            assert task.task_id == f"swe-qa-qa/{REPO_QA_TASK_NAME}/{origin.task_id}"

    async def test_an_empty_gold_file_set_is_refused_with_its_task_id(self) -> None:
        naked = EvalTask(
            task_id="swe_qa/django/9",
            query="how?",
            gold=GoldAnswer(),
            corpus_source=lambda: None,  # type: ignore[arg-type,return-value]
        )
        dataset = SweQaQuestionDataset(source=_OneTaskSource(task=naked))
        with pytest.raises(ValueError) as excinfo:
            _ = [t async for t in dataset.tasks()]
        assert "'swe_qa/django/9'" in str(excinfo.value)


class TestBothFramingsShareOneTaskName:
    async def test_the_two_corpora_mint_under_the_same_product_task_name(self) -> None:
        from pydocs_mcp.harness.core.skill_artifact_loader import TASK_NAMES

        assert REPO_QA_TASK_NAME in TASK_NAMES
        repoqa_row = await anext(_repoqa_framing().tasks())
        swe_row = await anext(_swe_qa_framing().tasks())
        names = {
            parse_framed_task_id(row.task_id, task_names=TASK_NAMES).task_name  # type: ignore[union-attr]
            for row in (repoqa_row, swe_row)
        }
        assert names == {REPO_QA_TASK_NAME}
