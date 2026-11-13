"""The alignment annotation page, driven the way the owner drives it (AppTest; spec 9e, Q60).

The page is opened the way ``streamlit run annotate_app.py -- <labels> [--read-only]``
opens it — Streamlit's bootstrap sets ``sys.argv`` to the script and the arguments
after ``--`` — over a label file written by fake-client-backed labellers
(``_annotation_fakes``), so it reads exactly what a ``label-alignment`` run writes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from pydocs_eval.judge.alignment_label_file import read_alignment_labels
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelVerdict
from pydocs_eval.judge.annotate_app import decision_widget_key, note_widget_key, save_button_key

from ._alignment_fakes import REPOQA_ANSWER, REPOQA_REFERENCE
from ._annotation_fakes import DISAGREED_ITEM, evidence_line, write_labelled_file

# The page is LOCATED, never imported here: AppTest executes the file itself, as __main__.
_PAGE_SPEC = importlib.util.find_spec("pydocs_eval.judge.annotate_app")
_PAGE_PATH = _PAGE_SPEC.origin if _PAGE_SPEC is not None and _PAGE_SPEC.origin else ""
# A run takes well under a second; the bound only absorbs this shared box's VM pauses.
_RUN_TIMEOUT_S = 120
_REPOQA_QUESTIONS = ("needle_identified", "addresses_grader", "contradicts_reference")


def _opened(monkeypatch: pytest.MonkeyPatch, labels: Path, *flags: str) -> AppTest:
    """The page as ``streamlit run annotate_app.py -- <labels> <flags>`` starts it."""
    monkeypatch.setattr(sys, "argv", [_PAGE_PATH, str(labels), *flags])
    at = AppTest.from_file(_PAGE_PATH, default_timeout=_RUN_TIMEOUT_S).run()
    assert not at.exception, at.exception
    return at


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
    assert "Find the function that returns the estimator's parameters as a dict." in shown
    assert "sklearn/base.py · BaseEstimator.get_params" in shown
    assert REPOQA_REFERENCE in shown
    assert REPOQA_ANSWER in shown
    for question_id in _REPOQA_QUESTIONS:
        for slot in LabellerSlot:
            evidence = evidence_line(slot, DISAGREED_ITEM, question_id)
            assert [text for text in shown if text.endswith(f" — {evidence}")], evidence
    astra_says = evidence_line(LabellerSlot.ASTRA, DISAGREED_ITEM, "needle_identified")
    opus_says = evidence_line(LabellerSlot.OPUS, DISAGREED_ITEM, "needle_identified")
    assert f"Astra: true (high) — {astra_says}" in shown
    assert f"Opus: false (high) — {opus_says}" in shown
    assert {"needle cited: yes", "gold-site coverage: 100%", "needle_identified: yes"} <= set(shown)
    assert "disagreement on needle_identified" in shown


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
