"""Reference answers from ground truth, in rounds of batches (judge 9c).

Every source is asked of the primary writer (``reference_writer.model``). Each
answer passes its dataset's code check or is regenerated on the same model,
told what the rejected draft got wrong, at most ``retries`` times; a row still
failing is listed, never kept. The fallback (``reference_writer.fallback_model``)
is asked only for a row whose primary job failed (``JOB_FAILED``: the Batch API
answered it with an error, or its batch ended ``failed``, ``expired`` or
``cancelled``), and the row keeps the reason. A row whose batch is still running
is listed with the batch's id and never asked again: it is collected by id
(``collected``), so a slow job is not paid for twice.

Example:
    >>> result = write_reference_answers(sources, clients, retries=2, extensions=(".py",))  # doctest: +SKIP
    >>> [row.reference.model_id for row in result.rows]  # doctest: +SKIP
    ['anthropic/claude-opus-5.5-20260921', ...]
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from pydocs_eval.datasets.base_dataset import ReferenceAnswer
from pydocs_eval.datasets.reference_answers import ReferenceRow
from pydocs_eval.judge.chat_wire import (
    BatchChatCompleter,
    ChatCompletion,
    ChatFailure,
    ChatFailureKind,
    ChatOutcome,
)
from pydocs_eval.judge.reference_checks import check_reference
from pydocs_eval.judge.reference_prompt import ReferencePrompt, reference_prompt, reference_text_of
from pydocs_eval.judge.reference_sources import ReferenceSource
from pydocs_eval.judge.role_config import WRITER_FALLBACK_MODEL_KEY, WRITER_MODEL_KEY

_NO_ANSWER_STRING = "wrote no non-empty 'answer' string"


class WriterRole(StrEnum):
    """Which writer asks a row, named by its YAML key."""

    PRIMARY = WRITER_MODEL_KEY
    FALLBACK = WRITER_FALLBACK_MODEL_KEY


class ReferenceGapKind(StrEnum):
    """Why a task has no reference yet, and so what the owner does about it."""

    #: Still failing its code check after every regeneration.
    FAILED_CHECK = "failed_check"
    #: The primary's job failed, and then the fallback's.
    BOTH_MODELS_FAILED = "both_models_failed"
    #: The batch submit failed; it may have run anyway, so it is not resubmitted.
    NOT_SUBMITTED = "not_submitted"
    #: Its batch still runs upstream: collect it by ``batch_id``.
    STILL_RUNNING = "still_running"


@dataclass(frozen=True, slots=True)
class ReferenceGap:
    """A task left without a reference, listed for the owner; ``batch_id`` when it still runs."""

    task_id: str
    kind: ReferenceGapKind
    reason: str
    batch_id: str = ""


@dataclass(frozen=True, slots=True)
class AskedRow:
    """One row as it was asked: the hash of its prompt, and why the fallback asked it."""

    task_id: str
    prompt_hash: str
    fallback_reason: str = ""


@dataclass(frozen=True, slots=True)
class AskedPrompt:
    """One prompt as it is about to be asked, and why the fallback asks it (else empty)."""

    prompt: ReferencePrompt
    fallback_reason: str = ""

    @property
    def row(self) -> AskedRow:
        """What a journal keeps of it: the task, the prompt's hash, the fallback's reason."""
        request = self.prompt.request
        return AskedRow(request.custom_id, self.prompt.prompt_hash, self.fallback_reason)


@dataclass(frozen=True, slots=True)
class SubmittedBatch:
    """A batch as submitted — what a caller records to collect it later by id.

    ``submitted_at`` is the Unix time a journal recorded it at; 0 when unknown.
    """

    batch_id: str
    role: WriterRole
    rows: tuple[AskedRow, ...]
    submitted_at: float = 0.0

    @classmethod
    def of(cls, batch_id: str, role: WriterRole, asked: Sequence[AskedPrompt]) -> SubmittedBatch:
        """The batch ``batch_id`` that asked each of ``asked``.

        Example:
            >>> SubmittedBatch.of("batch_1", WriterRole.PRIMARY, []).rows
            ()
        """
        return cls(batch_id, role, tuple(each.row for each in asked))


@dataclass(frozen=True, slots=True)
class CollectedBatch:
    """A batch submitted by an earlier run, and its rows' outcomes as collected now."""

    batch: SubmittedBatch
    outcomes: tuple[ChatOutcome, ...]


@dataclass(frozen=True, slots=True)
class WriterClients:
    """The primary writer and its fallback, each a batch completer."""

    primary: BatchChatCompleter
    fallback: BatchChatCompleter

    def of(self, role: WriterRole) -> BatchChatCompleter:
        """The completer that asks for ``role``."""
        return self.primary if role is WriterRole.PRIMARY else self.fallback


@dataclass(frozen=True, slots=True)
class ReferenceWriteResult:
    """The kept rows and the listed gaps, in source order, and what the run touched.

    ``ended_batches`` are the batches that ended, none of their rows still
    running and every row absorbed — deleted once the rows are stored.
    ``kept_open`` are ended batches that also answered a task outside this run,
    left for the run that covers it, since their answers were paid for.
    ``cost_usd`` sums the answers OpenRouter priced; ``uncosted_answers``
    counts the rest.
    """

    rows: tuple[ReferenceRow, ...]
    gaps: tuple[ReferenceGap, ...]
    ended_batches: tuple[str, ...]
    kept_open: tuple[str, ...]
    cost_usd: float
    uncosted_answers: int


def write_reference_answers(
    sources: Sequence[ReferenceSource],
    clients: WriterClients,
    *,
    retries: int,
    extensions: Sequence[str],
    on_submitted: Callable[[SubmittedBatch], None] | None = None,
    collected: Sequence[CollectedBatch] = (),
    already_written: frozenset[str] = frozenset(),
) -> ReferenceWriteResult:
    """Every source's reference, or the reason it has none; ``collected`` is absorbed first.

    A collected row for a task in ``already_written`` is consumed without a
    second look; one for a task neither there nor among ``sources`` keeps its
    batch open (``kept_open``) rather than let a paid answer be deleted unread.

    Example:
        >>> from pydocs_eval.judge.openrouter_chat import FakeOpenRouterChatClient
        >>> clients = WriterClients(FakeOpenRouterChatClient({}), FakeOpenRouterChatClient({}))
        >>> write_reference_answers([], clients, retries=2, extensions=(".py",)).rows
        ()

    Raises:
        ValueError: a negative ``retries``, or two sources sharing a task id.
    """
    run = _WriterRun.start(
        sources, retries=retries, extensions=extensions, already_written=already_written
    )
    for batch in collected:
        run.absorb(batch.batch, batch.outcomes)
    while run.pending:
        for role in WriterRole:
            _ask_round(run, clients.of(role), role, on_submitted)
    return run.result([source.task_id for source in sources])


def _ask_round(
    run: _WriterRun,
    client: BatchChatCompleter,
    role: WriterRole,
    on_submitted: Callable[[SubmittedBatch], None] | None,
) -> None:
    """Ask ``role``'s client every row it holds, as one batch, and absorb the outcomes."""
    asked = run.prompts_for(role)
    if not asked:
        return

    def announce(batch_id: str) -> None:
        if on_submitted is not None:
            on_submitted(SubmittedBatch.of(batch_id, role, asked))

    requests = [each.prompt.request for each in asked]
    outcomes = client.complete_all(requests, on_submitted=announce)
    run.absorb(SubmittedBatch.of("", role, asked), outcomes)


@dataclass(frozen=True, slots=True)
class _Pending:
    """A row still to write: who asks it next, what it has been told, what it may still try."""

    source: ReferenceSource
    regenerations_left: int
    role: WriterRole = WriterRole.PRIMARY
    fallback_reason: str = ""
    rejected: tuple[str, ...] = ()


@dataclass(slots=True)
class _WriterRun:
    """One call's rows in flight: what is pending, kept, listed, and which batches it saw."""

    extensions: Sequence[str]
    pending: dict[str, _Pending]
    already_written: frozenset[str] = frozenset()
    rows: list[ReferenceRow] = field(default_factory=list)
    gaps: list[ReferenceGap] = field(default_factory=list)
    # Every batch an outcome came from, in first-seen order; those still running;
    # and those holding an answer for a task outside this run.
    batches: dict[str, None] = field(default_factory=dict)
    running: set[str] = field(default_factory=set)
    foreign: set[str] = field(default_factory=set)
    cost_usd: float = 0.0
    uncosted: int = 0

    @classmethod
    def start(
        cls,
        sources: Sequence[ReferenceSource],
        *,
        retries: int,
        extensions: Sequence[str],
        already_written: frozenset[str],
    ) -> _WriterRun:
        if retries < 0:
            raise ValueError(f"retries = {retries}, expected 0 or more")
        counts = Counter(source.task_id for source in sources)
        repeated = sorted(task_id for task_id, count in counts.items() if count > 1)
        if repeated:
            raise ValueError(f"task ids {repeated!r} repeated, expected one source per task")
        pending = {source.task_id: _Pending(source, retries) for source in sources}
        return cls(extensions, pending, already_written=already_written)

    def prompts_for(self, role: WriterRole) -> list[AskedPrompt]:
        return [
            AskedPrompt(
                reference_prompt(state.source, rejected=state.rejected), state.fallback_reason
            )
            for state in self.pending.values()
            if state.role is role
        ]

    def absorb(self, batch: SubmittedBatch, outcomes: Sequence[ChatOutcome]) -> None:
        asked = {row.task_id: row for row in batch.rows}
        for outcome in outcomes:
            # Noted even for a row stored already: a batch whose rows were all
            # stored before a crash must still end up settled and deleted.
            self._note_batch(outcome)
            state = self.pending.pop(outcome.custom_id, None)
            if state is None:
                self._note_unasked(outcome)
                continue
            row = asked[outcome.custom_id]
            state = replace(state, role=batch.role, fallback_reason=row.fallback_reason)
            after = self._settle(state, row, outcome)
            if after is not None:
                self.pending[outcome.custom_id] = after

    def result(self, order: Sequence[str]) -> ReferenceWriteResult:
        rank = {task_id: i for i, task_id in enumerate(order)}
        return ReferenceWriteResult(
            rows=tuple(sorted(self.rows, key=lambda row: rank[row.task_id])),
            gaps=tuple(sorted(self.gaps, key=lambda gap: rank[gap.task_id])),
            ended_batches=tuple(
                b for b in self.batches if b not in self.running and b not in self.foreign
            ),
            kept_open=tuple(b for b in self.batches if b in self.foreign),
            cost_usd=self.cost_usd,
            uncosted_answers=self.uncosted,
        )

    def _settle(self, state: _Pending, asked: AskedRow, outcome: ChatOutcome) -> _Pending | None:
        if isinstance(outcome, ChatCompletion):
            return self._settle_answer(state, asked, outcome)
        return self._settle_failure(state, outcome)

    def _settle_answer(
        self, state: _Pending, asked: AskedRow, completion: ChatCompletion
    ) -> _Pending | None:
        self._count_cost(completion)
        text = reference_text_of(completion)
        if text is None:
            return self._regenerate(state, (_NO_ANSWER_STRING,))
        problems = check_reference(state.source, text, extensions=self.extensions).problems
        if problems:
            return self._regenerate(state, problems)
        reference = ReferenceAnswer(text, completion.served_model, asked.prompt_hash)
        self.rows.append(ReferenceRow(asked.task_id, reference, state.fallback_reason))
        return None

    def _settle_failure(self, state: _Pending, failure: ChatFailure) -> _Pending | None:
        if failure.kind is ChatFailureKind.UNUSABLE_ANSWER:
            return self._regenerate(state, (failure.reason,))
        if failure.kind is ChatFailureKind.JOB_FAILED and state.role is WriterRole.PRIMARY:
            return replace(state, role=WriterRole.FALLBACK, fallback_reason=failure.reason)
        self.gaps.append(_gap_of(state, failure))
        return None

    def _regenerate(self, state: _Pending, problems: Sequence[str]) -> _Pending | None:
        if state.regenerations_left > 0:
            left = state.regenerations_left - 1
            return replace(state, regenerations_left=left, rejected=tuple(problems))
        reason = "; ".join(problems)
        self.gaps.append(ReferenceGap(state.source.task_id, ReferenceGapKind.FAILED_CHECK, reason))
        return None

    def _note_batch(self, outcome: ChatOutcome) -> None:
        """Remember ``outcome``'s batch; one row still running keeps the whole batch open."""
        if not outcome.batch_id:
            return
        self.batches[outcome.batch_id] = None
        if isinstance(outcome, ChatFailure) and outcome.kind is ChatFailureKind.STILL_RUNNING:
            self.running.add(outcome.batch_id)

    def _note_unasked(self, outcome: ChatOutcome) -> None:
        """An outcome for a row this run holds no source for: stored, or someone else's."""
        if outcome.batch_id and outcome.custom_id not in self.already_written:
            self.foreign.add(outcome.batch_id)

    def _count_cost(self, completion: ChatCompletion) -> None:
        if completion.cost_usd is None:
            self.uncosted += 1
        else:
            self.cost_usd += completion.cost_usd


_GAP_OF_FAILURE = {
    ChatFailureKind.JOB_FAILED: ReferenceGapKind.BOTH_MODELS_FAILED,
    ChatFailureKind.NOT_SUBMITTED: ReferenceGapKind.NOT_SUBMITTED,
    ChatFailureKind.STILL_RUNNING: ReferenceGapKind.STILL_RUNNING,
}


def _gap_of(state: _Pending, failure: ChatFailure) -> ReferenceGap:
    kind = _GAP_OF_FAILURE[failure.kind]
    reason = failure.reason
    if kind is ReferenceGapKind.BOTH_MODELS_FAILED:
        reason = f"primary: {state.fallback_reason}; fallback: {failure.reason}"
    return ReferenceGap(state.source.task_id, kind, reason, failure.batch_id)


__all__ = (
    "AskedPrompt",
    "AskedRow",
    "CollectedBatch",
    "ReferenceGap",
    "ReferenceGapKind",
    "ReferenceWriteResult",
    "SubmittedBatch",
    "WriterClients",
    "WriterRole",
    "write_reference_answers",
)
