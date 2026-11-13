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
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from pydocs_eval.judge.alignment_annotation_text import (
    awaits_owner_ruling,
    code_check_lines,
    escalation_reason_line,
    gold_site_line,
    item_identity_line,
    item_option_label,
    labeller_answer_line,
    queue_summary_line,
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

# Named, not __name__: run as a page this module is __main__.
log = logging.getLogger("pydocs_eval.judge.annotate_app")

# The choice that gives a question no verdict: the owner has not ruled on it.
_NO_RULING = "no ruling"

#: The session-state key of the item picker (the item on screen).
ITEM_PICKER_KEY = "annotation_item"
#: The session-state key of the box that lists every item, not only the owner's queue.
EVERY_ITEM_KEY = "annotation_every_item"
# Where Save leaves its outcome for the run it triggers, which shows it once. Plain
# strings: Streamlit re-executes this script in a fresh module every run, so a class
# defined here would not be the class the next run compares against.
_SAVED_KEY = "annotation_saved"
_NOT_SAVED_KEY = "annotation_not_saved"


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
    """What the page was started on: the one label file it reads, and whether it may write it."""

    labels: Path
    read_only: bool = False


class AnnotationPageUsageError(ValueError):
    """The page's script arguments are not ``LABELS [--read-only]``."""


class _PageArgumentParser(argparse.ArgumentParser):
    """An argparse that raises: argparse's own exit would end the page's run, not show the error."""

    def error(self, message: str) -> NoReturn:
        raise AnnotationPageUsageError(f"{message}; {self.format_usage().strip()}")


def annotation_page_options(argv: Sequence[str]) -> AnnotationPageOptions:
    """The options in the page's script arguments (what follows ``--`` on ``streamlit run``).

    Raises:
        AnnotationPageUsageError: the arguments are not ``LABELS [--read-only]``.
    """
    parser = _PageArgumentParser(prog="annotate_app.py", add_help=False)
    parser.add_argument("labels", type=Path, help="the alignment label file")
    parser.add_argument("--read-only", action="store_true", help="review only: write nothing")
    args = parser.parse_args(list(argv))
    return AnnotationPageOptions(labels=args.labels.expanduser(), read_only=args.read_only)


def render_annotation_page(argv: Sequence[str]) -> None:
    """Draw the page for the label file ``argv`` names."""
    import streamlit as st  # function-local: `import pydocs_eval.judge` stays Streamlit-free

    st.set_page_config(page_title="Alignment annotation", layout="wide")
    try:
        options = annotation_page_options(argv)
        rows = _read_label_file(options.labels)
    except (ValueError, OSError) as exc:
        st.error(f"Cannot open the annotation page: {exc}")
        return
    if options.read_only:
        st.info(f"Read-only: this page writes nothing to {options.labels.name}.")
    _draw_notice()
    row = _picked_row(rows, options)
    if row is None:
        return
    _draw_item(row)
    _draw_ruling_form(row, options)


def _read_label_file(path: Path) -> tuple[AlignmentLabelRow, ...]:
    """The file's rows; a missing file is refused here, where the reader would read it as empty."""
    if not path.is_file():
        raise ValueError(f"{path} does not exist, expected an alignment label file")
    return read_alignment_labels(path)


def _draw_notice() -> None:
    """Show, once, what the last Save did."""
    import streamlit as st

    saved = st.session_state.pop(_SAVED_KEY, None)
    if saved is not None:
        st.success(saved)
    not_saved = st.session_state.pop(_NOT_SAVED_KEY, None)
    if not_saved is not None:
        st.error(not_saved)


def _picked_row(
    rows: Sequence[AlignmentLabelRow], options: AnnotationPageOptions
) -> AlignmentLabelRow | None:
    """The sidebar: the file, its queue, and the item the owner picked (first awaiting by default)."""
    import streamlit as st

    with st.sidebar:
        st.caption(str(options.labels))
        st.caption(queue_summary_line(rows))
        every = st.checkbox("Show every item", key=EVERY_ITEM_KEY)
        listed = {row.item_id: row for row in rows if every or row.escalation_reasons}
        if not listed:
            st.info("No item is in the owner's queue: tick 'Show every item' to browse the rest.")
            return None
        picked = st.selectbox(
            "Item",
            list(listed),
            index=_first_awaiting(list(listed.values())),
            format_func=lambda item_id: item_option_label(listed[item_id]),
            key=ITEM_PICKER_KEY,
        )
    return listed[picked]


def _first_awaiting(rows: Sequence[AlignmentLabelRow]) -> int:
    """Where the picker starts: the first item still awaiting a ruling, else the first item."""
    return next((index for index, row in enumerate(rows) if awaits_owner_ruling(row)), 0)


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
    """Both labellers on each question beside the owner's verdict, then the note and Save.

    Read-only, every control is disabled and Save has no callback: nothing can write.
    """
    import streamlit as st

    read_only = options.read_only
    st.subheader("Labellers and the owner's ruling")
    with st.form(key=f"annotation_form:{row.item_id}"):
        for question_id in row.questions:
            _draw_question(row, question_id, disabled=read_only)
        st.text_area(
            "Owner note",
            value=row.owner_note,
            key=note_widget_key(row.item_id),
            disabled=read_only,
        )
        st.form_submit_button(
            "Save ruling",
            key=save_button_key(row.item_id),
            on_click=None if read_only else _save_ruling,
            args=(options.labels, row.item_id, row.questions),
            disabled=read_only,
        )


def _draw_question(row: AlignmentLabelRow, question_id: str, *, disabled: bool) -> None:
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
        disabled=disabled,
    )


def _save_ruling(labels: Path, item_id: str, questions: Sequence[str]) -> None:
    """Save's callback: the form's verdicts and note, into the item's row of ``labels``."""
    import streamlit as st

    chosen = {qid: st.session_state[decision_widget_key(item_id, qid)] for qid in questions}
    decision = {qid: LabelVerdict(value) for qid, value in chosen.items() if value != _NO_RULING}
    note = str(st.session_state[note_widget_key(item_id)]).strip()
    try:
        record_owner_ruling(labels, OwnerRuling(item_id, decision, note))
    except (OSError, ValueError) as exc:
        # The rewrite is atomic, so a failed save leaves every byte of the file as it was.
        st.session_state[_NOT_SAVED_KEY] = (
            f"Not saved: the ruling on {item_id} did not reach {labels.name} ({exc}). "
            "The file is unchanged."
        )
        event = {"event": "owner_ruling_not_saved", "item": item_id, "labels": str(labels)}
        log.warning(json.dumps({**event, "error": str(exc)}, sort_keys=True))
        return
    st.session_state[_SAVED_KEY] = f"Saved the ruling on {item_id} to {labels.name}."


if __name__ == "__main__":
    render_annotation_page(sys.argv[1:])
