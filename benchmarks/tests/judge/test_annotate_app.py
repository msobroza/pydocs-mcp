"""The alignment annotation page, driven the way the owner drives it (AppTest; spec 9e, Q60).

The page is opened the way ``streamlit run annotate_app.py -- <labels> [--read-only]``
opens it — Streamlit's bootstrap sets ``sys.argv`` to the script and the arguments
after ``--`` — over a label file written by fake-client-backed labellers
(``_annotation_fakes``), so it reads exactly what a ``label-alignment`` run writes.
"""

from __future__ import annotations

import errno
import importlib.util
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from pydocs_eval.judge.alignment_label_file import read_alignment_labels, write_alignment_labels
from pydocs_eval.judge.alignment_rows import OwnerEscalation
from pydocs_eval.judge.alignment_vocabulary import EscalationReason, LabellerSlot, LabelVerdict
from pydocs_eval.judge.annotate_app import (
    EVERY_ITEM_KEY,
    ITEM_PICKER_KEY,
    decision_widget_key,
    note_widget_key,
    save_button_key,
)

from ._alignment_fakes import (
    ASTRA_MODEL,
    OPUS_MODEL,
    REPOQA_ANSWER,
    REPOQA_REFERENCE,
    labelled,
    labels_of,
    repoqa_context,
    repoqa_row,
)
from ._annotation_fakes import (
    CLEAN_ITEM,
    DISAGREED_ITEM,
    LOW_CONFIDENCE_ITEM,
    evidence_line,
    write_labelled_file,
)

# The page is LOCATED, never imported here: AppTest executes the file itself, as __main__.
_PAGE_SPEC = importlib.util.find_spec("pydocs_eval.judge.annotate_app")
_PAGE_PATH = _PAGE_SPEC.origin if _PAGE_SPEC is not None and _PAGE_SPEC.origin else ""
# A run takes well under a second; the bound only absorbs this shared box's VM pauses.
_RUN_TIMEOUT_S = 120
_REPOQA_QUESTIONS = ("needle_identified", "addresses_grader", "contradicts_reference")


def _started(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> AppTest:
    """The page as ``streamlit run annotate_app.py -- <arguments>`` starts it."""
    monkeypatch.setattr(sys, "argv", [_PAGE_PATH, *arguments])
    at = AppTest.from_file(_PAGE_PATH, default_timeout=_RUN_TIMEOUT_S).run()
    assert not at.exception, at.exception
    return at


def _opened(monkeypatch: pytest.MonkeyPatch, labels: Path, *flags: str) -> AppTest:
    """The page opened on ``labels``."""
    return _started(monkeypatch, str(labels), *flags)


def _shown_texts(at: AppTest) -> list[str]:
    """Every text the page shows, element by element."""
    elements = [
        *at.title,
        *at.header,
        *at.subheader,
        *at.markdown,
        *at.caption,
        *at.text,
        *at.code,
        *at.info,
        *at.success,
        *at.warning,
        *at.error,
    ]
    return [str(element.value) for element in elements]


@pytest.fixture
def labels(tmp_path: Path) -> Path:
    path = tmp_path / "alignment" / "repoqa-qa__jev-1.13.jsonl"
    write_labelled_file(path)
    return path


def test_the_page_shows_the_owner_the_item_both_labellers_and_the_code_check(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels)

    shown = _shown_texts(at)
    verbatim = [code.value for code in at.code]  # never rendered as Markdown
    plain = [text.value for text in at.text]
    assert "Find the function that returns the estimator's parameters as a dict." in verbatim
    assert REPOQA_REFERENCE in verbatim
    assert REPOQA_ANSWER in verbatim
    assert "sklearn/base.py · BaseEstimator.get_params" in plain
    assert str(labels) in plain
    assert (
        "repoqa-qa · repoqa-qa/repo_qa/sklearn::get_params · pilot · stratum borderline"
        " · arm baseline@60d5a00a · trajectory traj-1"
    ) in plain
    for question_id in _REPOQA_QUESTIONS:
        for slot in LabellerSlot:
            evidence = evidence_line(slot, DISAGREED_ITEM, question_id)
            assert [text for text in shown if text.endswith(f" — {evidence}")], evidence
    astra_says = evidence_line(LabellerSlot.ASTRA, DISAGREED_ITEM, "needle_identified")
    opus_says = evidence_line(LabellerSlot.OPUS, DISAGREED_ITEM, "needle_identified")
    assert f"Astra: true (high) — {astra_says}" in plain
    assert f"Opus: false (high) — {opus_says}" in plain
    assert f"Astra: {ASTRA_MODEL} · Opus: {OPUS_MODEL}" in plain
    assert {"needle cited: yes", "gold-site coverage: 100%", "needle_identified: yes"} <= set(shown)
    assert "disagreement on needle_identified" in shown


def test_the_code_at_the_gold_is_shown_as_the_labellers_saw_it(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels)

    (expander,) = at.expander
    assert (
        expander.label
        == "sklearn/base.py · BaseEstimator.get_params · gold lines 10–12 · shown 8–12"
    )
    (code,) = expander.code
    assert code.value.splitlines()[2] == "    def get_params(self, deep=True):"


def test_an_item_without_a_reference_answer_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    labels = tmp_path / "repoqa-qa__jev-1.13.jsonl"
    no_reference = repoqa_row(
        "item_no_reference",
        context=repoqa_context(reference=None),
        questions=("needle_identified", "addresses_grader"),
        escalation_reasons=(OwnerEscalation(EscalationReason.AUDIT),),
    )
    write_alignment_labels(labels, (no_reference,))

    at = _opened(monkeypatch, labels)

    assert "(no reference answer)" in [code.value for code in at.code]
    assert "audit (whole item)" in _shown_texts(at)


def _listed_items(at: AppTest) -> list[str]:
    """The item ids the picker offers, in order (each option starts with its item id)."""
    return [option.split(" · ")[0] for option in at.selectbox(key=ITEM_PICKER_KEY).options]


def test_the_picker_lists_the_owners_queue_and_opens_any_item_of_it(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels)

    assert _listed_items(at) == [DISAGREED_ITEM, LOW_CONFIDENCE_ITEM]
    assert "3 items · 2 in the owner queue · 2 awaiting a ruling" in _shown_texts(at)
    at.selectbox(key=ITEM_PICKER_KEY).set_value(LOW_CONFIDENCE_ITEM).run()
    assert not at.exception, at.exception
    shown = _shown_texts(at)
    assert "How are documents scored?" in shown
    assert "low_confidence on completeness" in shown
    assert (
        "src/needle/scoring/strategies.py · MaxSimStrategy · src/needle/scoring/strategies.py:42-43"
        in shown
    )


def test_every_item_can_be_listed_the_agreed_ones_too(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels)

    at.checkbox(key=EVERY_ITEM_KEY).check().run()

    assert not at.exception, at.exception
    assert _listed_items(at) == [DISAGREED_ITEM, LOW_CONFIDENCE_ITEM, CLEAN_ITEM]
    at.selectbox(key=ITEM_PICKER_KEY).set_value(CLEAN_ITEM).run()
    assert "not in the owner's queue: the labellers agreed and nothing escalated it" in (
        _shown_texts(at)
    )


def test_an_item_not_settled_yet_says_why_never_that_the_labellers_agreed(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    both_answered = labels_of(
        needle_identified="true", addresses_grader="false", contradicts_reference="false"
    )
    not_adjudicated = labelled(repoqa_row("item_not_adjudicated"), both_answered, both_answered)
    being_labelled = repoqa_row("item_being_labelled")
    write_alignment_labels(
        labels, (*read_alignment_labels(labels), not_adjudicated, being_labelled)
    )
    at = _opened(monkeypatch, labels)
    at.checkbox(key=EVERY_ITEM_KEY).check().run()

    options = at.selectbox(key=ITEM_PICKER_KEY).options
    assert [option.split(" · ")[-1] for option in options] == [
        "awaiting a ruling",
        "awaiting a ruling",
        "agreed",
        "not adjudicated",
        "being labelled",
    ]
    for item_id, why in (
        ("item_not_adjudicated", "not adjudicated since both labellers answered"),
        ("item_being_labelled", "the labellers have not both answered"),
    ):
        at.selectbox(key=ITEM_PICKER_KEY).set_value(item_id).run()
        assert f"not in the owner's queue: {why}" in _shown_texts(at)


def test_the_owner_rules_with_the_questions_own_verdicts_or_not_at_all(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels)
    needle = at.radio(key=decision_widget_key(DISAGREED_ITEM, "needle_identified"))
    assert needle.options == ["no ruling", "true", "false"]

    at.selectbox(key=ITEM_PICKER_KEY).set_value(LOW_CONFIDENCE_ITEM).run()

    completeness = at.radio(key=decision_widget_key(LOW_CONFIDENCE_ITEM, "completeness"))
    assert completeness.options == ["no ruling", "L0", "L1", "L2", "L3"]


_NOTE = "It names get_params but never says it lives on BaseEstimator."


def _rule(at: AppTest, item_id: str, decision: dict[str, str], note: str) -> AppTest:
    """Pick a verdict per question in ``decision``, write ``note``, and press Save."""
    for question_id, verdict in decision.items():
        at.radio(key=decision_widget_key(item_id, question_id)).set_value(verdict)
    at.text_area(key=note_widget_key(item_id)).input(note)
    at.button(key=save_button_key(item_id)).click().run()
    assert not at.exception, at.exception
    return at


def _line_of(lines: list[bytes], item_id: str) -> int:
    (index,) = [i for i, line in enumerate(lines) if f'"item_id":"{item_id}"'.encode() in line]
    return index


def _outside_the_owner_fields(line: bytes) -> tuple[bytes, bytes]:
    """The bytes before ``owner_decision`` and after ``owner_note``.

    A line's keys are sorted, so the two owner fields sit together between
    ``opus`` and ``planted_injection``.
    """
    head, decision_key, rest = line.partition(b',"owner_decision":')
    _, note_end, tail = rest.partition(b',"planted_injection":')
    assert decision_key and note_end, line
    return head, tail


def test_a_decision_and_a_note_round_trip_and_nothing_else_in_the_file_moves(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    before = labels.read_bytes().splitlines(keepends=True)
    ruled = _line_of(before, DISAGREED_ITEM)

    _rule(_opened(monkeypatch, labels), DISAGREED_ITEM, {"needle_identified": "false"}, _NOTE)

    after = labels.read_bytes().splitlines(keepends=True)
    assert after[:ruled] + after[ruled + 1 :] == before[:ruled] + before[ruled + 1 :]
    assert _outside_the_owner_fields(after[ruled]) == _outside_the_owner_fields(before[ruled])
    (row,) = [row for row in read_alignment_labels(labels) if row.item_id == DISAGREED_ITEM]
    assert row.owner_decision == {"needle_identified": LabelVerdict.FALSE}
    assert row.owner_note == _NOTE


def test_clearing_a_ruling_restores_the_file_byte_for_byte(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    before = labels.read_bytes()
    at = _rule(_opened(monkeypatch, labels), DISAGREED_ITEM, {"needle_identified": "false"}, _NOTE)
    assert labels.read_bytes() != before

    _rule(at, DISAGREED_ITEM, {"needle_identified": "no ruling"}, "")

    assert labels.read_bytes() == before


def test_a_saved_ruling_is_confirmed_and_leaves_the_awaiting_count(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _rule(_opened(monkeypatch, labels), DISAGREED_ITEM, {"needle_identified": "false"}, _NOTE)

    assert [success.value for success in at.success] == [
        f"Saved the ruling on {DISAGREED_ITEM} to {labels.name}."
    ]
    assert "3 items · 2 in the owner queue · 1 awaiting a ruling" in _shown_texts(at)
    assert at.selectbox(key=ITEM_PICKER_KEY).value == DISAGREED_ITEM


def test_a_save_keeps_what_a_labelling_run_wrote_while_the_page_was_open(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels)
    drawn_later = repoqa_row("item_drawn_later")
    write_alignment_labels(labels, (*read_alignment_labels(labels), drawn_later))

    _rule(at, DISAGREED_ITEM, {"needle_identified": "false"}, _NOTE)

    rows = read_alignment_labels(labels)
    assert [row.item_id for row in rows[:3]] == [DISAGREED_ITEM, LOW_CONFIDENCE_ITEM, CLEAN_ITEM]
    assert rows[3] == drawn_later
    assert rows[0].owner_note == _NOTE


def _fail_every_replace(source: object, target: object) -> None:
    """``os.replace`` on a disk that fails: the swap an atomic rewrite ends with never happens."""
    raise OSError(errno.EIO, "Input/output error")


def test_a_failing_write_leaves_the_label_file_intact_and_says_it_did_not_save(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    before = labels.read_bytes()
    at = _opened(monkeypatch, labels)
    monkeypatch.setattr(os, "replace", _fail_every_replace)

    _rule(at, DISAGREED_ITEM, {"needle_identified": "false"}, _NOTE)

    assert labels.read_bytes() == before
    assert [path.name for path in labels.parent.iterdir()] == [labels.name]
    (error,) = [error.value for error in at.error]
    assert error.startswith(f"Not saved: the ruling on {DISAGREED_ITEM} did not reach ")
    assert "Input/output error" in error
    assert not at.success


def test_a_read_only_page_says_so_and_offers_no_way_to_rule(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    at = _opened(monkeypatch, labels, "--read-only")

    assert [info.value for info in at.info] == [
        f"Read-only: this page writes nothing to {labels.name}."
    ]
    assert at.button(key=save_button_key(DISAGREED_ITEM)).disabled
    assert at.text_area(key=note_widget_key(DISAGREED_ITEM)).disabled
    for question_id in _REPOQA_QUESTIONS:
        assert at.radio(key=decision_widget_key(DISAGREED_ITEM, question_id)).disabled


def _file_state(path: Path) -> tuple[bytes, int, list[str]]:
    """Everything a write would move: the bytes, the mtime, and the directory's listing."""
    return path.read_bytes(), path.stat().st_mtime_ns, sorted(p.name for p in path.parent.iterdir())


def test_a_read_only_run_writes_nothing(monkeypatch: pytest.MonkeyPatch, labels: Path) -> None:
    before = _file_state(labels)

    at = _opened(monkeypatch, labels, "--read-only")
    at.selectbox(key=ITEM_PICKER_KEY).set_value(LOW_CONFIDENCE_ITEM).run()
    at.checkbox(key=EVERY_ITEM_KEY).check().run()
    at.selectbox(key=ITEM_PICKER_KEY).set_value(CLEAN_ITEM).run()

    assert not at.exception, at.exception
    assert _file_state(labels) == before


def test_a_page_started_without_a_label_file_says_how_to_start_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    at = _started(monkeypatch)

    (error,) = [error.value for error in at.error]
    assert "the following arguments are required: labels" in error
    assert "usage: annotate_app.py [--read-only] labels" in error


def test_a_label_file_that_does_not_exist_is_named_never_shown_as_an_empty_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    absent = tmp_path / "repoqa-qa__jev-1.13.jsonl"

    at = _opened(monkeypatch, absent)

    (error,) = [error.value for error in at.error]
    assert f"{absent} does not exist" in error
    assert not at.selectbox
    assert not absent.exists()


def test_a_label_file_the_reader_refuses_is_named_and_left_as_it_is(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    with labels.open("a", encoding="utf-8") as handle:
        handle.write('{"revision": "0.9"}\n')
    before = labels.read_bytes()

    at = _opened(monkeypatch, labels)

    (error,) = [error.value for error in at.error]
    assert f"{labels.name} line 4: revision '0.9'" in error
    assert not at.selectbox
    assert labels.read_bytes() == before


def test_an_owner_verdict_its_question_cannot_take_is_named_never_drawn(
    monkeypatch: pytest.MonkeyPatch, labels: Path
) -> None:
    first, *rest = read_alignment_labels(labels)
    hand_edited = replace(first, owner_decision={"needle_identified": LabelVerdict.L2})
    write_alignment_labels(labels, (hand_edited, *rest))
    before = labels.read_bytes()

    at = _opened(monkeypatch, labels)

    (error,) = [error.value for error in at.error]
    assert f"{labels.name}: needle_identified = 'L2' on item '{DISAGREED_ITEM}'" in error
    assert not at.selectbox
    assert labels.read_bytes() == before
