"""The owner's ruling reaches one row of the label file, and only a ruling that fits the row does."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.judge.alignment_label_file import read_alignment_labels, write_alignment_labels
from pydocs_eval.judge.alignment_owner_ruling import OwnerRuling, record_owner_ruling
from pydocs_eval.judge.alignment_vocabulary import LabelVerdict

from ._alignment_fakes import chat_row, repoqa_row


@pytest.fixture
def labels(tmp_path: Path) -> Path:
    path = tmp_path / "labels.jsonl"
    write_alignment_labels(path, (repoqa_row("item_a"), chat_row("item_b")))
    return path


def test_the_ruling_lands_on_its_item_and_the_other_rows_keep_theirs(labels: Path) -> None:
    ruling = OwnerRuling("item_b", {"completeness": LabelVerdict.L1}, "Leaves out the README.")

    ruled = record_owner_ruling(labels, ruling)

    first, second = read_alignment_labels(labels)
    assert first == repoqa_row("item_a")
    assert second == ruled
    assert (ruled.owner_decision, ruled.owner_note) == (
        {"completeness": LabelVerdict.L1},
        "Leaves out the README.",
    )


@pytest.mark.parametrize(
    ("ruling", "named"),
    [
        (OwnerRuling("item_gone", {}, "note"), "item_gone"),
        (OwnerRuling("item_a", {"site_0": LabelVerdict.TRUE}), "site_0"),
        (OwnerRuling("item_a", {"needle_identified": LabelVerdict.L2}), "'L2'"),
        (OwnerRuling("item_b", {"completeness": LabelVerdict.TRUE}), "'true'"),
        # The audit books any owner verdict unlike the agreed one as the labellers being
        # wrong, so "cannot decide" would grow it: the owner leaves the question unruled.
        (OwnerRuling("item_a", {"needle_identified": LabelVerdict.UNDECIDED}), "'undecided'"),
    ],
    ids=[
        "item not in the file",
        "question not asked",
        "level on a yes/no",
        "yes/no on a level",
        "undecided",
    ],
)
def test_a_ruling_that_does_not_fit_the_file_is_refused_and_nothing_is_written(
    labels: Path, ruling: OwnerRuling, named: str
) -> None:
    before = labels.read_bytes()

    with pytest.raises(ValueError, match=named):
        record_owner_ruling(labels, ruling)

    assert labels.read_bytes() == before
