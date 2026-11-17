"""Asking the two blind labellers about every alignment item, and the escalation judge's agreement pass.

Each labeller is asked independently, one batch per run, through the shared
chat client: the request is built from the item alone (``labeller_request``),
so neither labeller ever sees the other's answer. The label file is the batch
journal: the moment a batch is submitted, its rows are marked ``submitted``
with the batch id and the file is persisted, before the first poll. A later
run collects every ``submitted`` row by its batch id and never resubmits it —
a batch keeps running upstream for up to 24 h and bills whether or not anyone
waits. A row whose job failed, or whose answer was not the JSON asked for, is
asked again by the next run; a row whose submit failed may have run anyway, so
it is asked again only on request.

Example:
    >>> result = label_alignment_rows(rows, clients, jev=jev, persist=save)  # doctest: +SKIP
    >>> labelling_cost(result.rows).per_item_usd  # doctest: +SKIP
    0.18
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from pydocs_eval.judge.alignment_request import (
    AlignmentLabelError,
    labeller_request,
    parse_labels,
)
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, LabellerEntry
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelStatus
from pydocs_eval.judge.chat_wire import (
    BatchChatCompleter,
    ChatCompleter,
    ChatCompletion,
    ChatFailureKind,
    ChatOutcome,
)
from pydocs_eval.judge.config import JevConfig

#: Persists the rows as they stand: the label file's atomic rewrite.
PersistRows = Callable[[Sequence[AlignmentLabelRow]], None]

_STATUS_OF_FAILURE = {
    ChatFailureKind.STILL_RUNNING: LabelStatus.SUBMITTED,
    ChatFailureKind.NOT_SUBMITTED: LabelStatus.NOT_SUBMITTED,
    ChatFailureKind.JOB_FAILED: LabelStatus.FAILED,
    ChatFailureKind.UNUSABLE_ANSWER: LabelStatus.FAILED,
}


@dataclass(frozen=True, slots=True)
class LabellerClients:
    """The two labellers' batch completers, by slot."""

    astra: BatchChatCompleter
    opus: BatchChatCompleter

    def of(self, slot: LabellerSlot) -> BatchChatCompleter:
        """The completer of the labeller in ``slot`` (its field is named for the slot)."""
        client: BatchChatCompleter = getattr(self, slot.value)
        return client


@dataclass(frozen=True, slots=True)
class EndedBatch:
    """A labeller's batch that ended with every row stored: it may be deleted upstream."""

    slot: LabellerSlot
    batch_id: str


@dataclass(frozen=True, slots=True)
class LabellingResult:
    """The rows after a run, how many asks it made and collected, and the batches that ended."""

    rows: tuple[AlignmentLabelRow, ...]
    asked: int
    collected: int
    ended_batches: tuple[EndedBatch, ...]


@dataclass(frozen=True, slots=True)
class LabellingCost:
    """What labelling cost as OpenRouter priced it, and per item both labellers answered."""

    total_usd: float
    items: int
    unpriced_batches: int

    @property
    def per_item_usd(self) -> float | None:
        return self.total_usd / self.items if self.items else None


def label_alignment_rows(
    rows: Sequence[AlignmentLabelRow],
    clients: LabellerClients,
    *,
    jev: JevConfig,
    persist: PersistRows,
    resubmit_not_submitted: bool = False,
) -> LabellingResult:
    """Ask each labeller every row it has not answered; collect what earlier runs left running.

    Raises:
        JudgeModelMismatchError: a row was answered by a model other than its labeller's pin.
        JudgeRequestError: OpenRouter refused a request (every batch already
            submitted is in the persisted rows).
    """
    run = _LabellingRun(list(rows), persist)
    for slot in LabellerSlot:
        run.collect(slot, clients.of(slot))
        run.ask(slot, clients.of(slot), jev, resubmit_not_submitted)
    return run.result()


def label_with_escalation_judge(
    rows: Sequence[AlignmentLabelRow], client: ChatCompleter, *, model: str, jev: JevConfig
) -> tuple[AlignmentLabelRow, ...]:
    """The escalation judge's agreement pass: its answers on every row both labellers answered.

    A row it already answered is not asked again; its answers sit under
    ``escalation_judge``. This pass is not an in-band escalation: it runs
    outside the in-band spend cap, on its own ledger line.
    """
    due = [row for row in rows if row.labelled_by_both and not _judged_by_escalation(row)]
    if not due:
        return tuple(rows)
    outcomes = client.complete_all([labeller_request(row, jev=jev) for row in due])
    entries = {
        row.item_id: _entry_after(LabellerEntry(model=model), outcome, row.questions)
        for row, outcome in zip(due, outcomes, strict=True)
    }
    return tuple(
        replace(row, escalation_judge=entries[row.item_id]) if row.item_id in entries else row
        for row in rows
    )


def labelling_cost(rows: Sequence[AlignmentLabelRow]) -> LabellingCost:
    """What the labellers' batches cost, each batch counted once, over the items both answered."""
    batches = {
        (slot, entry.batch_id): entry.batch_cost_usd
        for row in rows
        for slot, entry in ((slot, row.labeller(slot)) for slot in LabellerSlot)
        if entry.batch_id and entry.status is not LabelStatus.SUBMITTED
    }
    priced = [cost for cost in batches.values() if cost is not None]
    items = sum(1 for row in rows if row.labelled_by_both)
    return LabellingCost(sum(priced), items, len(batches) - len(priced))


def _judged_by_escalation(row: AlignmentLabelRow) -> bool:
    return row.escalation_judge is not None and row.escalation_judge.answered


class _LabellingRun:
    """One run's rows in flight, persisted at every submit."""

    def __init__(self, rows: list[AlignmentLabelRow], persist: PersistRows) -> None:
        self.rows = rows
        self.persist = persist
        self.asked = 0
        self.collected = 0
        # Every batch an outcome came from, and whether any of its rows still runs.
        self.still_running_by_batch: dict[EndedBatch, bool] = {}

    def collect(self, slot: LabellerSlot, client: BatchChatCompleter) -> None:
        """Read every batch an earlier run left running, by its id."""
        waiting: dict[str, list[str]] = {}
        for row in self.rows:
            entry = row.labeller(slot)
            if entry.status is LabelStatus.SUBMITTED and entry.batch_id:
                waiting.setdefault(entry.batch_id, []).append(row.item_id)
        for batch_id, item_ids in waiting.items():
            self._apply(slot, item_ids, client.collect(batch_id, item_ids))
            self.collected += len(item_ids)

    def ask(
        self, slot: LabellerSlot, client: BatchChatCompleter, jev: JevConfig, resubmit: bool
    ) -> None:
        """Ask every row ``slot`` has not answered and is not waiting on, as one batch."""
        askable = {LabelStatus.PENDING, LabelStatus.FAILED}
        if resubmit:
            askable.add(LabelStatus.NOT_SUBMITTED)
        due = [row for row in self.rows if row.labeller(slot).status in askable]
        if not due:
            return
        item_ids = [row.item_id for row in due]

        def journal(batch_id: str) -> None:
            self._mark_submitted(slot, item_ids, batch_id)
            self.persist(self.rows)

        requests = [labeller_request(row, jev=jev) for row in due]
        self._apply(slot, item_ids, client.complete_all(requests, on_submitted=journal))
        self.asked += len(item_ids)

    def result(self) -> LabellingResult:
        batches = self.still_running_by_batch.items()
        ended = tuple(batch for batch, running in batches if not running)
        return LabellingResult(tuple(self.rows), self.asked, self.collected, ended)

    def _mark_submitted(self, slot: LabellerSlot, item_ids: Sequence[str], batch_id: str) -> None:
        submitted = set(item_ids)
        self.rows = [
            row.with_labeller(slot, _submitted(row.labeller(slot), batch_id))
            if row.item_id in submitted
            else row
            for row in self.rows
        ]

    def _apply(
        self, slot: LabellerSlot, item_ids: Sequence[str], outcomes: Sequence[ChatOutcome]
    ) -> None:
        by_id = dict(zip(item_ids, outcomes, strict=True))
        for outcome in (each for each in outcomes if each.batch_id):
            batch = EndedBatch(slot, outcome.batch_id)
            running = self.still_running_by_batch.get(batch, False)
            self.still_running_by_batch[batch] = running or _is_running(outcome)
        self.rows = [
            row.with_labeller(
                slot, _entry_after(row.labeller(slot), by_id[row.item_id], row.questions)
            )
            if row.item_id in by_id
            else row
            for row in self.rows
        ]


def _is_running(outcome: ChatOutcome) -> bool:
    return not isinstance(outcome, ChatCompletion) and outcome.kind is ChatFailureKind.STILL_RUNNING


def _submitted(entry: LabellerEntry, batch_id: str) -> LabellerEntry:
    return replace(entry, status=LabelStatus.SUBMITTED, batch_id=batch_id, reason="")


def _entry_after(
    entry: LabellerEntry, outcome: ChatOutcome, question_ids: Sequence[str]
) -> LabellerEntry:
    """``entry`` once ``outcome`` is read: answered with its labels, or why it is not."""
    if isinstance(outcome, ChatCompletion):
        return _answered_entry(entry, outcome, question_ids)
    return replace(
        entry,
        status=_STATUS_OF_FAILURE[outcome.kind],
        batch_id=outcome.batch_id or entry.batch_id,
        batch_cost_usd=outcome.batch_cost_usd,
        reason=outcome.reason,
    )


def _answered_entry(
    entry: LabellerEntry, completion: ChatCompletion, question_ids: Sequence[str]
) -> LabellerEntry:
    paid = replace(
        entry,
        batch_id=completion.batch_id,
        batch_cost_usd=completion.batch_cost_usd,
        cost_usd=completion.cost_usd,
    )
    try:
        labels = parse_labels(completion.content, question_ids)
    except AlignmentLabelError as exc:
        return replace(paid, status=LabelStatus.FAILED, reason=str(exc))
    return replace(paid, status=LabelStatus.ANSWERED, labels=labels, reason="")


__all__ = (
    "EndedBatch",
    "LabellerClients",
    "LabellingCost",
    "LabellingResult",
    "PersistRows",
    "label_alignment_rows",
    "label_with_escalation_judge",
    "labelling_cost",
)
