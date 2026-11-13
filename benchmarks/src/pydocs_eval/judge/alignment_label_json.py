"""One alignment label row as one JSON object, and back: the label file's line codec.

The format itself (every field, its meaning, its revision) is documented in
``alignment_label_file``. Reading is strict: a field of the wrong type, a value
outside its vocabulary or a key the revision does not have raises naming the
field, so a hand edit cannot be dropped silently by the next rewrite.

Example:
    >>> label_row_from_json(label_row_json(row)) == row  # doctest: +SKIP
    True
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TypedDict, TypeVar

from pydocs_eval.judge.alignment_rows import (
    AlignmentItemContext,
    AlignmentLabelRow,
    AnswerSource,
    CodeCheck,
    LabellerEntry,
    OwnerEscalation,
)
from pydocs_eval.judge.alignment_vocabulary import (
    AlignmentPass,
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

#: The label file's format; a reader refuses any other, so a change is a deliberate bump.
ALIGNMENT_LABEL_REVISION = "1.0"
_ROW_KEYS = frozenset(
    {
        "revision",
        "item_id",
        "dataset",
        "jev_model",
        "task_id",
        "source",
        "alignment_pass",
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

_Enum = TypeVar("_Enum", bound=StrEnum)


def label_row_json(row: AlignmentLabelRow) -> dict[str, object]:
    """``row`` as one label-file object, before its canonical serialization."""
    return {
        "revision": ALIGNMENT_LABEL_REVISION,
        **_identity_json(row),
        "context": _context_json(row.context),
        "questions": list(row.questions),
        "code_check": _code_check_json(row.code_check),
        "astra": _entry_json(row.astra),
        "opus": _entry_json(row.opus),
        **_settlement_json(row),
    }


def label_row_from_json(raw: Mapping[str, object]) -> AlignmentLabelRow:
    """The row one label-file object holds.

    Raises:
        KeyError: a field of the revision is missing.
        TypeError: a field has the wrong JSON type, named.
        ValueError: an unknown field, another revision, or a value outside its vocabulary.
    """
    _refuse_unknown_keys(raw)
    _refuse_other_revision(raw)
    dataset = _enum_of(JudgedDataset, raw["dataset"], "dataset")
    return AlignmentLabelRow(
        item_id=_text(raw["item_id"], "item_id"),
        dataset=dataset,
        jev_model=_text(raw["jev_model"], "jev_model"),
        task_id=_text(raw["task_id"], "task_id"),
        source=_source_from_json(_mapping(raw["source"], "source")),
        alignment_pass=_enum_of(AlignmentPass, raw["alignment_pass"], "alignment_pass"),
        stratum=_enum_of(AlignmentStratum, raw["stratum"], "stratum"),
        context=_context_from_json(dataset, _mapping(raw["context"], "context")),
        questions=tuple(_texts(raw["questions"], "questions")),
        code_check=_code_check_from_json(_mapping(raw["code_check"], "code_check")),
        astra=_entry_from_json(_mapping(raw["astra"], "astra"), "astra"),
        opus=_entry_from_json(_mapping(raw["opus"], "opus"), "opus"),
        **_settlement_from_json(raw),
    )


def _identity_json(row: AlignmentLabelRow) -> dict[str, object]:
    return {
        "item_id": row.item_id,
        "dataset": row.dataset.value,
        "jev_model": row.jev_model,
        "task_id": row.task_id,
        "source": {"arm": row.source.arm, "trajectory_id": row.source.trajectory_id},
        "alignment_pass": row.alignment_pass.value,
        "stratum": row.stratum.value,
        "planted_injection": row.planted_injection,
    }


def _settlement_json(row: AlignmentLabelRow) -> dict[str, object]:
    """What the item settled to: the agreement, the owner's queue, the owner, the escalation judge."""
    judge = row.escalation_judge
    return {
        "agreed": _verdicts_json(row.agreed),
        "escalation_reasons": [
            {"reason": each.reason.value, "question": each.question}
            for each in row.escalation_reasons
        ],
        "owner_decision": _verdicts_json(row.owner_decision),
        "owner_note": row.owner_note,
        "escalation_judge": _entry_json(judge) if judge is not None else None,
    }


def _context_json(context: AlignmentItemContext) -> dict[str, object]:
    return {
        "question": context.question,
        "gold": [{"path": s.path, "symbol": s.symbol, "span": s.span} for s in context.gold],
        "shown_code": [_shown_code_json(code) for code in context.shown_code],
        "reference_answer": context.reference_answer,
        "agent_answer": context.agent_answer,
    }


def _shown_code_json(code: ShownCode) -> dict[str, object]:
    return {
        "path": code.site.path,
        "symbol": code.site.symbol,
        "start": code.start,
        "end": code.end,
        "first_line": code.first_line,
        "lines": list(code.lines),
    }


def _code_check_json(check: CodeCheck) -> dict[str, object]:
    return {
        "needle_cited": check.needle_cited,
        "gold_site_coverage": check.gold_site_coverage,
        "verdicts": dict(check.verdicts),
    }


def _entry_json(entry: LabellerEntry) -> dict[str, object]:
    labels = {
        question_id: {
            "verdict": each.verdict.value,
            "evidence": each.evidence,
            "confidence": each.confidence.value,
        }
        for question_id, each in entry.labels.items()
    }
    return {
        "model": entry.model,
        "status": entry.status.value,
        "labels": labels,
        "batch_id": entry.batch_id,
        "batch_cost_usd": entry.batch_cost_usd,
        "cost_usd": entry.cost_usd,
        "reason": entry.reason,
    }


def _verdicts_json(verdicts: Mapping[str, LabelVerdict]) -> dict[str, str]:
    return {question_id: verdict.value for question_id, verdict in verdicts.items()}


def _refuse_unknown_keys(raw: Mapping[str, object]) -> None:
    unknown = sorted(set(raw) - _ROW_KEYS)
    if unknown:
        raise ValueError(f"unknown fields {unknown!r}, expected only {sorted(_ROW_KEYS)}")


def _refuse_other_revision(raw: Mapping[str, object]) -> None:
    revision = raw["revision"]
    if revision != ALIGNMENT_LABEL_REVISION:
        raise ValueError(f"revision {revision!r}, expected {ALIGNMENT_LABEL_REVISION!r}")


class _Settlement(TypedDict):
    """The row's fields after its two labellers: what the item settled to, and the owner's word."""

    planted_injection: str
    agreed: dict[str, LabelVerdict]
    escalation_reasons: tuple[OwnerEscalation, ...]
    owner_decision: dict[str, LabelVerdict]
    owner_note: str
    escalation_judge: LabellerEntry | None


def _settlement_from_json(raw: Mapping[str, object]) -> _Settlement:
    judge = raw["escalation_judge"]
    reasons = _list(raw["escalation_reasons"], "escalation_reasons")
    return {
        "planted_injection": _text(raw["planted_injection"], "planted_injection"),
        "agreed": _verdicts_from_json(_mapping(raw["agreed"], "agreed"), "agreed"),
        "escalation_reasons": tuple(_escalation_from_json(each) for each in reasons),
        "owner_decision": _verdicts_from_json(
            _mapping(raw["owner_decision"], "owner_decision"), "owner_decision"
        ),
        "owner_note": _text(raw["owner_note"], "owner_note"),
        "escalation_judge": None
        if judge is None
        else _entry_from_json(_mapping(judge, "escalation_judge"), "escalation_judge"),
    }


def _source_from_json(raw: Mapping[str, object]) -> AnswerSource:
    return AnswerSource(
        arm=_text(raw["arm"], "source.arm"),
        trajectory_id=_text(raw["trajectory_id"], "source.trajectory_id"),
    )


def _context_from_json(dataset: JudgedDataset, raw: Mapping[str, object]) -> AlignmentItemContext:
    reference = raw["reference_answer"]
    gold = _list(raw["gold"], "context.gold")
    shown = _list(raw["shown_code"], "context.shown_code")
    return AlignmentItemContext(
        dataset=dataset,
        question=_text(raw["question"], "context.question"),
        gold=tuple(_site_from_json(_mapping(each, "context.gold")) for each in gold),
        shown_code=tuple(_shown_code_from_json(_mapping(each, "shown_code")) for each in shown),
        reference_answer=None if reference is None else _text(reference, "reference_answer"),
        agent_answer=_text(raw["agent_answer"], "context.agent_answer"),
    )


def _site_from_json(raw: Mapping[str, object]) -> JudgedSite:
    return JudgedSite(
        _text(raw["path"], "gold.path"),
        _text(raw["symbol"], "gold.symbol"),
        _text(raw["span"], "gold.span"),
    )


def _shown_code_from_json(raw: Mapping[str, object]) -> ShownCode:
    site = NeedleSite(
        _text(raw["path"], "shown_code.path"), _text(raw["symbol"], "shown_code.symbol")
    )
    return ShownCode(
        site,
        start=_integer(raw["start"], "shown_code.start"),
        end=_integer(raw["end"], "shown_code.end"),
        first_line=_integer(raw["first_line"], "shown_code.first_line"),
        lines=tuple(_texts(raw["lines"], "shown_code.lines")),
    )


def _code_check_from_json(raw: Mapping[str, object]) -> CodeCheck:
    verdicts = _mapping(raw["verdicts"], "code_check.verdicts")
    return CodeCheck(
        needle_cited=_flag(raw["needle_cited"], "code_check.needle_cited"),
        gold_site_coverage=_number(raw["gold_site_coverage"], "code_check.gold_site_coverage"),
        verdicts={
            qid: _flag(value, f"code_check.verdicts.{qid}") for qid, value in verdicts.items()
        },
    )


def _entry_from_json(raw: Mapping[str, object], name: str) -> LabellerEntry:
    labels = _mapping(raw["labels"], f"{name}.labels")
    return LabellerEntry(
        model=_text(raw["model"], f"{name}.model"),
        status=_enum_of(LabelStatus, raw["status"], f"{name}.status"),
        labels={
            qid: _label_from_json(_mapping(each, f"{name}.labels.{qid}"), f"{name}.labels.{qid}")
            for qid, each in labels.items()
        },
        batch_id=_text(raw["batch_id"], f"{name}.batch_id"),
        batch_cost_usd=_optional_number(raw["batch_cost_usd"], f"{name}.batch_cost_usd"),
        cost_usd=_optional_number(raw["cost_usd"], f"{name}.cost_usd"),
        reason=_text(raw["reason"], f"{name}.reason"),
    )


def _label_from_json(raw: Mapping[str, object], where: str) -> QuestionLabel:
    return QuestionLabel(
        verdict=_enum_of(LabelVerdict, raw["verdict"], f"{where}.verdict"),
        evidence=_text(raw["evidence"], f"{where}.evidence"),
        confidence=_enum_of(LabelConfidence, raw["confidence"], f"{where}.confidence"),
    )


def _escalation_from_json(raw: object) -> OwnerEscalation:
    fields = _mapping(raw, "escalation_reasons")
    return OwnerEscalation(
        _enum_of(EscalationReason, fields["reason"], "escalation_reasons.reason"),
        _text(fields["question"], "escalation_reasons.question"),
    )


def _verdicts_from_json(raw: Mapping[str, object], name: str) -> dict[str, LabelVerdict]:
    return {qid: _enum_of(LabelVerdict, value, f"{name}.{qid}") for qid, value in raw.items()}


def _enum_of(kind: type[_Enum], value: object, name: str) -> _Enum:
    """``value`` as a member of ``kind``, or a refusal listing the vocabulary."""
    if value not in tuple(kind):
        allowed = [member.value for member in kind]
        raise ValueError(f"{name} = {value!r}, expected one of {allowed}")
    return kind(str(value))


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} = {value!r}, expected a string")
    return value


def _texts(value: object, name: str) -> list[str]:
    return [_text(each, name) for each in _list(value, name)]


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{name} = {value!r}, expected a list")
    return value


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} = {value!r}, expected an object")
    return value


def _flag(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} = {value!r}, expected true or false")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} = {value!r}, expected an integer")
    return value


def _number(value: object, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError(f"{name} = {value!r}, expected a number")
    return float(value)


def _optional_number(value: object, name: str) -> float | None:
    return None if value is None else _number(value, name)


__all__ = ("ALIGNMENT_LABEL_REVISION", "label_row_from_json", "label_row_json")
