"""The alignment label file: one JSON line per item, the format the annotation page reads and writes.

Labels are vendored per dataset and Jev model, under
``judge/data/alignment/<dataset>__<jev-model>.jsonl`` (``judge.alignment.label_file``
moves a dataset's file elsewhere). Each line is one item, canonical JSON (sorted
keys, no spaces), so rewriting a file that was read moves no byte, and a change
to one field moves only that field. The whole file is rewritten atomically. A
line names its format (``revision``); a reader refuses any other revision, and
refuses an unknown key rather than drop it on the next rewrite.

Revision 1.0, one object per line (``?`` marks a field that may be ``null``)::

    revision            "1.0"
    item_id             stable id of the item (the labellers' batch row id)
    dataset             "repoqa-qa" | "example-needle-chat" | "swe-qa-questions"
    jev_model           the Jev pin the labels are fitted for, e.g. "jev-1.13"
    task_id             the dataset task the answer is to
    source              {arm: "<role>@<commit>", trajectory_id}
    alignment_pass      "pilot" | "pass_1" | "pass_2": the labelling run that drew the item
    stratum             "borderline" | "code_vs_jev" | "planted_injection" | "random"
    planted_injection   the vendored injection's id, "" when none was planted
    context             what every judge is shown:
                          {question, gold: [{path, symbol, span}],
                           shown_code: [{path, symbol, start, end, first_line, lines: [..]}],
                           reference_answer?, agent_answer}
    questions           the question ids asked, in order ("needle_identified", "site_0", ...)
    code_check          {needle_cited, gold_site_coverage, verdicts: {question_id: bool}}
    astra, opus         each labeller (judge.alignment.labellers[0] and [1]):
                          {model, status, labels: {question_id: {verdict, evidence, confidence}},
                           batch_id, batch_cost_usd?, cost_usd?, reason}
    agreed              {question_id: verdict}: what the labellers' agreement settles to,
                          "undecided" where it settles nothing
    escalation_reasons  [{reason, question}]: why the owner sees the item ("" = the whole
                          item); reasons are "disagreement", "self_preference", "code_check",
                          "low_confidence", "planted_injection_passed", "audit"
    owner_decision      {question_id: verdict}: the owner's verdicts, written by the annotation page
    owner_note          the owner's free-text note
    escalation_judge?   the escalation judge's answers from its agreement pass (an entry like
                          ``astra``), null until that pass runs

Verdicts are "true" / "false" (yes/no questions), "L0".."L3" (``completeness``) or
"undecided"; confidences "low" / "medium" / "high"; statuses "pending",
"submitted", "answered", "failed", "not_submitted". A question reads its
``owner_decision`` once there is one; before that, a question a reason other
than the audit holds reads "undecided", and any other question its ``agreed``
verdict (``alignment_adjudication.effective_label``). The annotation page writes
``owner_decision`` and ``owner_note`` only.

Example:
    >>> read_alignment_labels(vendored_alignment_label_path("repoqa-qa", "jev-1.13"))  # doctest: +SKIP
    (AlignmentLabelRow(item_id='…', ...), ...)
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydocs_eval.atomic_text import write_text_atomically
from pydocs_eval.judge.alignment_label_json import (
    ALIGNMENT_LABEL_REVISION,
    label_row_from_json,
    label_row_json,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.role_config import AlignmentConfig
from pydocs_eval.trajectory.blob_store import canonical_json

_VENDORED_DIR = Path(__file__).parent / "data" / "alignment"
# Separates the dataset from the Jev model in a label file's name.
_NAME_SEPARATOR = "__"


class AlignmentLabelFileError(ValueError):
    """A label-file line outside the declared format, named by file and line number."""


def vendored_alignment_label_path(dataset: str, jev_model: str) -> Path:
    """Where ``dataset``'s labels for ``jev_model`` are vendored in this source tree.

    Example:
        >>> vendored_alignment_label_path("repoqa-qa", "jev-1.13").name
        'repoqa-qa__jev-1.13.jsonl'
    """
    safe_model = jev_model.replace("/", "_")
    return _VENDORED_DIR / f"{dataset}{_NAME_SEPARATOR}{safe_model}.jsonl"


def alignment_label_path(alignment: AlignmentConfig, dataset: str, jev_model: str) -> Path:
    """``dataset``'s label file: its ``judge.alignment.label_file`` entry, else the vendored one.

    Example:
        >>> alignment_label_path(AlignmentConfig(), "repoqa-qa", "jev-1.13").name
        'repoqa-qa__jev-1.13.jsonl'
    """
    configured = alignment.label_file.get(dataset)
    if configured:
        return Path(configured).expanduser()
    return vendored_alignment_label_path(dataset, jev_model)


def read_alignment_labels(path: Path) -> tuple[AlignmentLabelRow, ...]:
    """Every row of ``path``, in file order; a missing file holds none.

    Raises:
        AlignmentLabelFileError: a line that is not a row of this revision, named by number.
    """
    if not path.exists():
        return ()
    lines = path.read_text(encoding="utf-8").splitlines()
    return tuple(_row_on_line(path, number, line) for number, line in enumerate(lines, 1) if line)


def write_alignment_labels(path: Path, rows: Sequence[AlignmentLabelRow]) -> None:
    """Replace ``path`` with ``rows``, in their order, in one atomic write.

    Raises:
        ValueError: two rows share an item id.
    """
    counts = Counter(row.item_id for row in rows)
    repeated = sorted(item_id for item_id, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"item ids {repeated!r} repeated, expected one row per item")
    write_text_atomically(path, "".join(f"{canonical_json(label_row_json(row))}\n" for row in rows))


def _row_on_line(path: Path, number: int, line: str) -> AlignmentLabelRow:
    """The row on line ``number``, or a refusal naming the file, the line and what is wrong."""
    try:
        raw = json.loads(line)
        if not isinstance(raw, Mapping):
            raise TypeError(f"line = {raw!r}, expected a JSON object")
        return label_row_from_json(raw)
    except KeyError as exc:
        reason = (
            f"missing field {exc.args[0]!r}, expected every field of revision "
            f"{ALIGNMENT_LABEL_REVISION}"
        )
    except (TypeError, ValueError) as exc:
        reason = str(exc)
    raise AlignmentLabelFileError(f"{path.name} line {number}: {reason}")


__all__ = (
    "ALIGNMENT_LABEL_REVISION",
    "AlignmentLabelFileError",
    "alignment_label_path",
    "read_alignment_labels",
    "vendored_alignment_label_path",
    "write_alignment_labels",
)
