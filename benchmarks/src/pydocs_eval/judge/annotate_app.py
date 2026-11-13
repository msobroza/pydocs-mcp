"""The alignment annotation page: the owner rules on the items the two labellers did not settle.

Launch it on one label file (Streamlit comes with the eval suite's ``[ask]`` extra)::

    streamlit run benchmarks/src/pydocs_eval/judge/annotate_app.py -- LABELS

It shows the owner's queue — every item with an escalation reason — one item at
a time: the question, the gold and the code at it, the reference answer, the
agent's answer, both labellers' verdicts with their evidence lines, the code
check, and why the item is in the queue.

Streamlit is imported inside the functions that draw, never at module level, so
importing ``pydocs_eval.judge`` — this module included — never loads it. The
page runs only as a script, the way ``streamlit run`` and AppTest execute it
(as ``__main__``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.judge.alignment_annotation_text import (
    code_check_lines,
    escalation_reason_line,
    gold_site_line,
    item_identity_line,
    labeller_answer_line,
    shown_code_caption,
)
from pydocs_eval.judge.alignment_label_file import read_alignment_labels
from pydocs_eval.judge.alignment_owner_ruling import (
    OwnerRuling,
    owner_ruling_verdicts,
    record_owner_ruling,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelVerdict

# The choice that gives a question no verdict: the owner has not ruled on it.
_NO_RULING = "no ruling"


def decision_widget_key(item_id: str, question_id: str) -> str:
    """The session-state key of the owner's verdict on ``question_id`` of ``item_id``."""
    return f"annotation_decision:{item_id}:{question_id}"


def note_widget_key(item_id: str) -> str:
    """The session-state key of the owner's note on ``item_id``."""
    return f"annotation_note:{item_id}"


def save_button_key(item_id: str) -> str:
    """The session-state key of the button that saves the ruling on ``item_id``."""
    return f"annotation_save:{item_id}"


@dataclass(frozen=True, slots=True)
class AnnotationPageOptions:
    """What the page was started on: the one label file it reads."""

    labels: Path


def annotation_page_options(argv: Sequence[str]) -> AnnotationPageOptions:
    """The options in the page's script arguments (what follows ``--`` on ``streamlit run``)."""
    parser = argparse.ArgumentParser(prog="annotate_app.py", add_help=False)
    parser.add_argument("labels", type=Path, help="the alignment label file")
    args = parser.parse_args(list(argv))
    return AnnotationPageOptions(labels=args.labels.expanduser())


def render_annotation_page(argv: Sequence[str]) -> None:
    """Draw the page for the label file ``argv`` names."""
    import streamlit as st  # function-local: `import pydocs_eval.judge` stays Streamlit-free

    st.set_page_config(page_title="Alignment annotation", layout="wide")
    options = annotation_page_options(argv)
    rows = read_alignment_labels(options.labels)
    queued = [row for row in rows if row.escalation_reasons]
    if not queued:
        st.info("No item is in the owner's queue.")
        return
    _draw_item(queued[0])
    _draw_ruling_form(queued[0], options)


def _draw_item(row: AlignmentLabelRow) -> None:
    import streamlit as st

    st.header(f"Item {row.item_id}")
    st.caption(item_identity_line(row))
    st.subheader("Why it is in the owner's queue")
    for reason in row.escalation_reasons:
        st.text(escalation_reason_line(reason))
    _draw_context(row)
    st.subheader("Code check")
    for line in code_check_lines(row.code_check):
        st.text(line)


def _draw_context(row: AlignmentLabelRow) -> None:
    """What every judge of the item was shown, verbatim."""
    import streamlit as st

    context = row.context
    st.subheader("Question")
    st.code(context.question, language=None, wrap_lines=True)
    st.subheader("Gold")
    for site in context.gold:
        st.text(gold_site_line(site))
    for code in context.shown_code:
        with st.expander(shown_code_caption(code)):
            st.code("\n".join(code.lines), language=None)
    st.subheader("Reference answer")
    st.code(context.reference_answer or "(none)", language=None, wrap_lines=True)
    st.subheader("Agent answer")
    st.code(context.agent_answer, language=None, wrap_lines=True)


def _draw_ruling_form(row: AlignmentLabelRow, options: AnnotationPageOptions) -> None:
    """Both labellers on each question beside the owner's verdict, then the note and Save."""
    import streamlit as st

    st.subheader("Labellers and the owner's ruling")
    with st.form(key=f"annotation_form:{row.item_id}"):
        for question_id in row.questions:
            _draw_question(row, question_id)
        st.text_area("Owner note", value=row.owner_note, key=note_widget_key(row.item_id))
        st.form_submit_button(
            "Save ruling",
            key=save_button_key(row.item_id),
            on_click=_save_ruling,
            args=(options.labels, row.item_id, row.questions),
        )


def _draw_question(row: AlignmentLabelRow, question_id: str) -> None:
    import streamlit as st

    st.markdown(f"**{question_id}**")
    for slot in LabellerSlot:
        st.text(labeller_answer_line(row, slot, question_id))
    choices = [_NO_RULING, *(verdict.value for verdict in owner_ruling_verdicts(question_id))]
    ruled = row.owner_decision.get(question_id)
    st.radio(
        "Owner ruling",
        choices,
        index=choices.index(ruled.value) if ruled is not None else 0,
        key=decision_widget_key(row.item_id, question_id),
        horizontal=True,
    )


def _save_ruling(labels: Path, item_id: str, questions: Sequence[str]) -> None:
    """Save's callback: the form's verdicts and note, into the item's row of ``labels``."""
    import streamlit as st

    chosen = {qid: st.session_state[decision_widget_key(item_id, qid)] for qid in questions}
    decision = {qid: LabelVerdict(value) for qid, value in chosen.items() if value != _NO_RULING}
    note = str(st.session_state[note_widget_key(item_id)]).strip()
    record_owner_ruling(labels, OwnerRuling(item_id, decision, note))


if __name__ == "__main__":
    render_annotation_page(sys.argv[1:])
