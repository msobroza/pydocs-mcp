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
    escalation_reasons  [{reason, question}]: why the owner sees the item ("" = the whole item)
    owner_decision      {question_id: verdict}: the owner's verdicts, written by the annotation page
    owner_note          the owner's free-text note
    escalation_judge?   the escalation judge's answers from its agreement pass (an entry like
                          ``astra``), null until that pass runs

Verdicts are "true" / "false" (yes/no questions), "L0".."L3" (``completeness``) or
"undecided"; confidences "low" / "medium" / "high"; statuses "pending",
"submitted", "answered", "failed", "not_submitted".

Example:
    >>> read_alignment_labels(vendored_alignment_label_path("repoqa-qa", "jev-1.13"))  # doctest: +SKIP
    (AlignmentLabelRow(item_id='…', ...), ...)
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TypeVar

from pydocs_eval.atomic_text import write_text_atomically
from pydocs_eval.judge.alignment_rows import (
    AlignmentItemContext,
    AlignmentLabelRow,
    AnswerSource,
    CodeCheck,
    LabellerEntry,
    OwnerEscalation,
)
from pydocs_eval.judge.alignment_vocabulary import (
    AlignmentStratum,
    EscalationReason,
    LabelConfidence,
    LabelStatus,
    LabelVerdict,
    QuestionLabel,
)
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.reference_sources import ShownCode
from pydocs_eval.judge.role_config import AlignmentConfig
from pydocs_eval.trajectory.blob_store import canonical_json

#: The label file's format; a reader refuses any other, so a change is a deliberate bump.
ALIGNMENT_LABEL_REVISION = "1.0"
_VENDORED_DIR = Path(__file__).parent / "data" / "alignment"
# Separates the dataset from the Jev model in a label file's name.
_NAME_SEPARATOR = "__"
_ROW_KEYS = frozenset(
    {
        "revision",
        "item_id",
        "dataset",
        "jev_model",
        "task_id",
        "source",
        "stratum",
        "planted_injection",
        "context",
        "questions",
        "code_check",
        "astra",
        "opus",
        "agreed",
        "escalation_reasons",
        "owner_decision",
        "owner_note",
        "escalation_judge",
    }
)

_Value = TypeVar("_Value")


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
    return tuple(_row_of_line(path, number, line) for number, line in enumerate(lines, 1) if line)


def write_alignment_labels(path: Path, rows: Sequence[AlignmentLabelRow]) -> None:
    """Replace ``path`` with ``rows``, in their order, in one atomic write.

    Raises:
        ValueError: two rows share an item id.
    """
    counts = Counter(row.item_id for row in rows)
    repeated = sorted(item_id for item_id, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"item ids {repeated!r} repeated, expected one row per item")
    write_text_atomically(path, "".join(f"{canonical_json(row_json(row))}\n" for row in rows))


def row_json(row: AlignmentLabelRow) -> dict[str, object]:
    """``row`` as one label-file object (before its canonical serialization)."""
    return {
        "revision": ALIGNMENT_LABEL_REVISION,
        "item_id": row.item_id,
        "dataset": row.dataset.value,
        "jev_model": row.jev_model,
        "task_id": row.task_id,
        "source": {"arm": row.source.arm, "trajectory_id": row.source.trajectory_id},
        "stratum": row.stratum.value,
        "planted_injection": row.planted_injection,
        "context": _context_json(row.context),
        "questions": list(row.questions),
        "code_check": {
            "needle_cited": row.code_check.needle_cited,
            "gold_site_coverage": row.code_check.gold_site_coverage,
            "verdicts": dict(row.code_check.verdicts),
        },
        "astra": _entry_json(row.astra),
        "opus": _entry_json(row.opus),
        "agreed": _verdicts_json(row.agreed),
        "escalation_reasons": [
            {"reason": each.reason.value, "question": each.question}
            for each in row.escalation_reasons
        ],
        "owner_decision": _verdicts_json(row.owner_decision),
        "owner_note": row.owner_note,
        "escalation_judge": _entry_json(row.escalation_judge) if row.escalation_judge else None,
    }


def _context_json(context: AlignmentItemContext) -> dict[str, object]:
    return {
        "question": context.question,
        "gold": [{"path": s.path, "symbol": s.symbol, "span": s.span} for s in context.gold],
        "shown_code": [
            {
                "path": code.site.path,
                "symbol": code.site.symbol,
                "start": code.start,
                "end": code.end,
                "first_line": code.first_line,
                "lines": list(code.lines),
            }
            for code in context.shown_code
        ],
        "reference_answer": context.reference_answer,
        "agent_answer": context.agent_answer,
    }


def _entry_json(entry: LabellerEntry) -> dict[str, object]:
    return {
        "model": entry.model,
        "status": entry.status.value,
        "labels": {
            question_id: {
                "verdict": each.verdict.value,
                "evidence": each.evidence,
                "confidence": each.confidence.value,
            }
            for question_id, each in entry.labels.items()
        },
        "batch_id": entry.batch_id,
        "batch_cost_usd": entry.batch_cost_usd,
        "cost_usd": entry.cost_usd,
        "reason": entry.reason,
    }


def _verdicts_json(verdicts: Mapping[str, LabelVerdict]) -> dict[str, str]:
    return {question_id: verdict.value for question_id, verdict in verdicts.items()}


def _row_of_line(path: Path, number: int, line: str) -> AlignmentLabelRow:
    where = f"{path.name} line {number}"
    try:
        return _row_of(_LineReader(_json_object(line)))
    except (KeyError, TypeError, ValueError) as exc:
        raise AlignmentLabelFileError(f"{where}: {_reason(exc)}") from None


def _reason(exc: Exception) -> str:
    if isinstance(exc, KeyError):
        return f"missing field {exc.args[0]!r}, expected every field of revision {ALIGNMENT_LABEL_REVISION}"
    return str(exc)


def _json_object(line: str) -> Mapping[str, object]:
    raw = json.loads(line)
    if not isinstance(raw, Mapping):
        raise TypeError(f"line = {raw!r}, expected a JSON object")
    return raw


def _row_of(raw: _LineReader) -> AlignmentLabelRow:
    raw.refuse_unknown_keys()
    revision = raw.text("revision")
    if revision != ALIGNMENT_LABEL_REVISION:
        raise ValueError(f"revision {revision!r}, expected {ALIGNMENT_LABEL_REVISION!r}")
    dataset = JudgedDataset(raw.text("dataset"))
    escalation_judge = raw.raw("escalation_judge")
    return AlignmentLabelRow(
        item_id=raw.text("item_id"),
        dataset=dataset,
        jev_model=raw.text("jev_model"),
        task_id=raw.text("task_id"),
        source=_source_of(raw.mapping("source")),
        stratum=_enum(AlignmentStratum, raw.text("stratum"), "stratum"),
        context=_context_of(dataset, raw.mapping("context")),
        questions=tuple(_texts(raw.raw("questions"), "questions")),
        code_check=_code_check_of(raw.mapping("code_check")),
        astra=_entry_of(raw.mapping("astra"), "astra"),
        opus=_entry_of(raw.mapping("opus"), "opus"),
        planted_injection=raw.text("planted_injection"),
        agreed=_verdicts_of(raw.mapping("agreed"), "agreed"),
        escalation_reasons=tuple(
            _escalation_of(each) for each in _list(raw.raw("escalation_reasons"), "reasons")
        ),
        owner_decision=_verdicts_of(raw.mapping("owner_decision"), "owner_decision"),
        owner_note=raw.text("owner_note"),
        escalation_judge=None
        if escalation_judge is None
        else _entry_of(_as_mapping(escalation_judge, "escalation_judge"), "escalation_judge"),
    )


class _LineReader:
    """Typed reads of one line's fields; a wrong type raises naming the field."""

    def __init__(self, raw: Mapping[str, object]) -> None:
        self._raw = raw

    def refuse_unknown_keys(self) -> None:
        unknown = sorted(set(self._raw) - _ROW_KEYS)
        if unknown:
            raise ValueError(f"unknown fields {unknown!r}, expected only {sorted(_ROW_KEYS)}")

    def raw(self, name: str) -> object:
        return self._raw[name]

    def text(self, name: str) -> str:
        return _as_text(self._raw[name], name)

    def mapping(self, name: str) -> Mapping[str, object]:
        return _as_mapping(self._raw[name], name)


def _source_of(raw: Mapping[str, object]) -> AnswerSource:
    return AnswerSource(
        arm=_as_text(raw["arm"], "source.arm"),
        trajectory_id=_as_text(raw["trajectory_id"], "source.trajectory_id"),
    )


def _context_of(dataset: JudgedDataset, raw: Mapping[str, object]) -> AlignmentItemContext:
    reference = raw["reference_answer"]
    return AlignmentItemContext(
        dataset=dataset,
        question=_as_text(raw["question"], "context.question"),
        gold=tuple(_site_of(_as_mapping(each, "gold")) for each in _list(raw["gold"], "gold")),
        shown_code=tuple(
            _shown_code_of(_as_mapping(each, "shown_code"))
            for each in _list(raw["shown_code"], "shown_code")
        ),
        reference_answer=None if reference is None else _as_text(reference, "reference_answer"),
        agent_answer=_as_text(raw["agent_answer"], "context.agent_answer"),
    )


def _site_of(raw: Mapping[str, object]) -> JudgedSite:
    return JudgedSite(
        _as_text(raw["path"], "gold.path"),
        _as_text(raw["symbol"], "gold.symbol"),
        _as_text(raw["span"], "gold.span"),
    )


def _shown_code_of(raw: Mapping[str, object]) -> ShownCode:
    site = NeedleSite(_as_text(raw["path"], "shown_code.path"), _as_text(raw["symbol"], "symbol"))
    return ShownCode(
        site,
        start=_as_int(raw["start"], "shown_code.start"),
        end=_as_int(raw["end"], "shown_code.end"),
        first_line=_as_int(raw["first_line"], "shown_code.first_line"),
        lines=tuple(_texts(raw["lines"], "shown_code.lines")),
    )


def _code_check_of(raw: Mapping[str, object]) -> CodeCheck:
    verdicts = _as_mapping(raw["verdicts"], "code_check.verdicts")
    return CodeCheck(
        needle_cited=_as_bool(raw["needle_cited"], "code_check.needle_cited"),
        gold_site_coverage=_as_number(raw["gold_site_coverage"], "code_check.gold_site_coverage"),
        verdicts={qid: _as_bool(value, f"code_check.{qid}") for qid, value in verdicts.items()},
    )


def _entry_of(raw: Mapping[str, object], name: str) -> LabellerEntry:
    labels = _as_mapping(raw["labels"], f"{name}.labels")
    return LabellerEntry(
        model=_as_text(raw["model"], f"{name}.model"),
        status=_enum(LabelStatus, _as_text(raw["status"], f"{name}.status"), f"{name}.status"),
        labels={qid: _label_of(_as_mapping(each, qid), f"{name}.{qid}") for qid, each in labels.items()},
        batch_id=_as_text(raw["batch_id"], f"{name}.batch_id"),
        batch_cost_usd=_optional_number(raw["batch_cost_usd"], f"{name}.batch_cost_usd"),
        cost_usd=_optional_number(raw["cost_usd"], f"{name}.cost_usd"),
        reason=_as_text(raw["reason"], f"{name}.reason"),
    )


def _label_of(raw: Mapping[str, object], where: str) -> QuestionLabel:
    return QuestionLabel(
        verdict=_enum(
            LabelVerdict, _as_text(raw["verdict"], f"{where}.verdict"), f"{where}.verdict"
        ),
        evidence=_as_text(raw["evidence"], f"{where}.evidence"),
        confidence=_enum(
            LabelConfidence,
            _as_text(raw["confidence"], f"{where}.confidence"),
            f"{where}.confidence",
        ),
    )


def _escalation_of(raw: object) -> OwnerEscalation:
    fields = _as_mapping(raw, "escalation_reasons")
    reason = _as_text(fields["reason"], "escalation_reasons.reason")
    return OwnerEscalation(
        _enum(EscalationReason, reason, "escalation_reasons.reason"),
        _as_text(fields["question"], "escalation_reasons.question"),
    )


def _verdicts_of(raw: Mapping[str, object], name: str) -> dict[str, LabelVerdict]:
    return {
        qid: _enum(LabelVerdict, _as_text(value, f"{name}.{qid}"), f"{name}.{qid}")
        for qid, value in raw.items()
    }


def _enum(kind: Callable[[str], _Value], value: str, name: str) -> _Value:
    try:
        return kind(value)
    except ValueError:
        raise ValueError(f"{name} = {value!r}, expected one of its vocabulary") from None


def _as_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} = {value!r}, expected a string")
    return value


def _texts(value: object, name: str) -> list[str]:
    return [_as_text(each, name) for each in _list(value, name)]


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{name} = {value!r}, expected a list")
    return value


def _as_mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} = {value!r}, expected an object")
    return value


def _as_bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} = {value!r}, expected true or false")
    return value


def _as_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} = {value!r}, expected an integer")
    return value


def _as_number(value: object, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"{name} = {value!r}, expected a number")
    return float(value)


def _optional_number(value: object, name: str) -> float | None:
    return None if value is None else _as_number(value, name)


__all__ = (
    "ALIGNMENT_LABEL_REVISION",
    "AlignmentLabelFileError",
    "alignment_label_path",
    "read_alignment_labels",
    "row_json",
    "vendored_alignment_label_path",
    "write_alignment_labels",
)
