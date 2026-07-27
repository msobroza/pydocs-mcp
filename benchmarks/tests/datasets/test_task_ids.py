"""Framed task-id vocabulary — the three-part spelling and its guard rails.

Design §5 reserves ``<dataset>/<task_name>/<record_id>`` for the first second
framing and keeps the two-part spelling for every single-framing corpus. These
pins cover BOTH directions and, most importantly, the ids that merely LOOK
three-part and must keep their old meaning.
"""

from __future__ import annotations

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.task_ids import (
    mint_framed_task_id,
    parse_framed_task_id,
    record_id_of,
)
from pydocs_eval.optimize._prefix_report import task_id_prefix

_TASK_NAMES = ("sweqapro", "ccv", "repo_qa")


def _task(task_id: str, *, record_id: str = "") -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="q",
        gold=GoldAnswer(),
        corpus_source=lambda: None,  # type: ignore[arg-type,return-value]
        record_id=record_id,
    )


class TestMinting:
    def test_a_framed_id_round_trips_through_the_parser(self) -> None:
        task_id = mint_framed_task_id(
            dataset="repoqa-qa", task_name="repo_qa", record_id="psf/black@f03ee11/x.py::y"
        )
        assert task_id == "repoqa-qa/repo_qa/psf/black@f03ee11/x.py::y"
        parsed = parse_framed_task_id(task_id, task_names=_TASK_NAMES)
        assert parsed is not None
        assert (parsed.dataset, parsed.task_name) == ("repoqa-qa", "repo_qa")
        assert parsed.record_id == "psf/black@f03ee11/x.py::y"

    def test_the_dataset_segment_is_what_task_id_prefix_reports(self) -> None:
        # The per-task-type rubric vocabulary reads the PREFIX, so a framed id
        # must still group under the dataset that produced it.
        task_id = mint_framed_task_id(dataset="swe-qa-qa", task_name="repo_qa", record_id="r1")
        assert task_id_prefix(task_id) == "swe-qa-qa"

    def test_every_prefix_consumer_reads_the_dataset_not_the_framing(self) -> None:
        # ``rubric/checks.score_checks`` and the stratified sampler both key on
        # the prefix, NOT on task_name: an ``applies_to`` / ``weight_by_type``
        # vocabulary names DATASETS, and a framed id must keep that true.
        from pydocs_eval.optimize._prefix_report import count_by_task_prefix
        from pydocs_eval.optimize.multitask.sampling import row_type

        task_id = mint_framed_task_id(dataset="repoqa-qa", task_name="repo_qa", record_id="n1")
        assert row_type({"task_id": task_id}) == "repoqa-qa"
        assert count_by_task_prefix([task_id, "ccv/cve-1"]) == {"repoqa-qa": 1, "ccv": 1}

    @pytest.mark.parametrize("dataset", ["", "repoqa/qa"])
    def test_a_separator_or_empty_dataset_is_refused_with_the_value(self, dataset: str) -> None:
        with pytest.raises(ValueError) as excinfo:
            mint_framed_task_id(dataset=dataset, task_name="repo_qa", record_id="r1")
        assert repr(dataset) in str(excinfo.value)

    def test_a_separator_in_the_task_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="task_name"):
            mint_framed_task_id(dataset="repoqa-qa", task_name="repo/qa", record_id="r1")

    def test_an_empty_record_id_is_refused_naming_the_framing(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            mint_framed_task_id(dataset="repoqa-qa", task_name="repo_qa", record_id="")
        assert "repoqa-qa/repo_qa" in str(excinfo.value)


class TestParsingIsVocabularyAnchoredNotPositional:
    @pytest.mark.parametrize(
        "task_id",
        [
            "swe_qa/matplotlib/12",  # swe-qa's own three-segment id
            "swe_qa_pro/qiboteam/qibo/12",  # four segments, org/name record
            "psf/black@f03ee11/src/black/trans.py::_merge",  # repoqa, no dataset prefix
            "ds1000/pandas/Origin/42",
            "sweqapro/swe_qa_pro/0001",  # a two-part combined id
            "ccv/cve-2025-10283",
            "cve-2025-10283",  # bare
        ],
    )
    def test_an_unframed_id_parses_as_none(self, task_id: str) -> None:
        # Positional parsing would read "matplotlib" / "qiboteam" as a framing.
        assert parse_framed_task_id(task_id, task_names=_TASK_NAMES) is None

    def test_a_middle_segment_outside_the_vocabulary_is_not_a_framing(self) -> None:
        assert parse_framed_task_id("d/not_a_task/r", task_names=_TASK_NAMES) is None
        assert parse_framed_task_id("d/repo_qa/r", task_names=_TASK_NAMES) is not None

    def test_an_empty_vocabulary_disables_framed_parsing_entirely(self) -> None:
        assert parse_framed_task_id("repoqa-qa/repo_qa/r1", task_names=()) is None


class TestRecordIdResolution:
    def test_an_unframed_task_is_its_own_record(self) -> None:
        assert record_id_of(_task("cve-2025-10283")) == "cve-2025-10283"

    def test_an_explicit_record_id_wins_over_the_task_id(self) -> None:
        task = _task("repoqa-qa/repo_qa/n1", record_id="n1")
        assert record_id_of(task) == "n1"

    def test_siblings_over_one_record_share_the_record_id(self) -> None:
        a = _task("repoqa-qa/repo_qa/n1", record_id="n1")
        b = _task("repoqa/n1", record_id="n1")
        assert record_id_of(a) == record_id_of(b)
        assert a.task_id != b.task_id

    def test_a_framed_row_without_the_field_falls_back_to_the_id_parse(self) -> None:
        # The reader implements the docstring's full chain, so it can never
        # disagree with ``sample_row_for_task`` about what the record IS.
        framed = _task("repoqa-qa/repo_qa/n1")
        assert record_id_of(framed, task_names=_TASK_NAMES) == "n1"

    def test_the_parse_fallback_needs_the_vocabulary_and_never_guesses(self) -> None:
        framed = _task("repoqa-qa/repo_qa/n1")
        assert record_id_of(framed) == "repoqa-qa/repo_qa/n1"
        assert record_id_of(_task("swe_qa/matplotlib/12"), task_names=_TASK_NAMES) == (
            "swe_qa/matplotlib/12"
        )
