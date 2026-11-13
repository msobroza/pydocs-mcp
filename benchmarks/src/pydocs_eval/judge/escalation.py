"""The escalation judge: it decides Jev's in-band answers while ladder arms are scored (spec 9d, Q52).

Outside its review band Jev's verdict stands; inside it the answer goes to
``judge.escalation.model`` (Luna), asked the same questions with the same
ground truth the labellers saw (``alignment_request.judge_request``), never
back to Jev. Luna decides a question only where its agreement pass cleared the
floor (``escalation_trust``); elsewhere every in-band answer reads
``undecided`` without a call. Answers are cached by input hash under
``judge.escalation.cache_dir``, so a second scoring pass makes zero calls.
Every call is metered from OpenRouter's reported cost into a spend ledger
beside the cache; no call starts unless one more answer at its bound still
fits under ``spend_cap_usd`` (program-wide), and beyond that in-band answers
read ``undecided``. A call that gets no usable answer reads ``undefined``,
never false, and is not cached. Only yes/no questions are escalated: a Score
below its confidence gate stays ``undefined``.

The alignment labelling never builds this judge: Luna's agreement pass labels
the alignment set through the shared client directly, outside the in-band cap.

Example:
    >>> judge = build_escalation_judge(config, label_rows, client)  # doctest: +SKIP
    >>> escalate_in_band(score, item_id="t1", context=context, judge=judge).verdicts  # doctest: +SKIP
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydocs_eval._bench_cache import cache_root
from pydocs_eval.atomic_text import write_text_atomically
from pydocs_eval.judge.alignment_report import QuestionKey
from pydocs_eval.judge.alignment_request import AlignmentLabelError, judge_request, parse_labels
from pydocs_eval.judge.alignment_rows import AlignmentItemContext, AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabelVerdict
from pydocs_eval.judge.chat_wire import ChatCompleter, ChatCompletion, ChatRequest
from pydocs_eval.judge.config import JevConfig, JudgeConfig
from pydocs_eval.judge.escalation_trust import escalation_trust
from pydocs_eval.judge.jev_questions import SCORE_KINDS, JevQuestionKind, kind_of
from pydocs_eval.judge.jev_scoring import AGREEMENT, JevScore, agreement_of
from pydocs_eval.judge.role_config import EscalationConfig
from pydocs_eval.judge.roles import escalation_role
from pydocs_eval.judge.thresholds import JevVerdict
from pydocs_eval.trajectory.blob_store import canonical_json

# Public: a deployment that persists caches persists this one with Jev's.
ESCALATION_CACHE_SUBDIR = "escalation"
_SPEND_LEDGER_FILE = "spend_ledger.jsonl"
_ENTRY_SUFFIX = ".json"
# The most one in-band answer can cost. Luna lists $0.10/M in and $0.50/M out;
# an in-band request is under ~15k tokens in, and its labels plus reasoning at
# xhigh stay under ~60k out: about $0.032. An answer OpenRouter does not price
# (or a call that failed, which may still have been billed) is booked at this
# bound, and no call starts unless the bound still fits under the cap — so the
# cap holds even when a price is missing.
_MAX_ANSWER_USD = 0.05
_CONTRADICTS_REFERENCE = JevQuestionKind.CONTRADICTS_REFERENCE.value
_VERDICT_OF_LABEL = {LabelVerdict.TRUE: JevVerdict.TRUE, LabelVerdict.FALSE: JevVerdict.FALSE}


def escalation_cache_dir(config: EscalationConfig) -> Path:
    """``judge.escalation.cache_dir``, or the derived default beside the bench caches.

    Example:
        >>> escalation_cache_dir(EscalationConfig(cache_dir="/tmp/esc"))
        PosixPath('/tmp/esc')
    """
    if config.cache_dir:
        return Path(config.cache_dir).expanduser()
    return cache_root() / ESCALATION_CACHE_SUBDIR


@dataclass(frozen=True, slots=True)
class EscalationCache:
    """The escalation judge's answers under ``root``, one file per input hash."""

    root: Path

    def get(self, key: str) -> Mapping[str, object] | None:
        """The answer stored under ``key``; ``None`` when absent or unreadable."""
        try:
            stored = json.loads((self.root / f"{key}{_ENTRY_SUFFIX}").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return stored if isinstance(stored, Mapping) else None

    def put(self, key: str, content: Mapping[str, object]) -> None:
        """Store ``content`` under ``key``: a reader sees the old entry or the whole new one."""
        write_text_atomically(self.root / f"{key}{_ENTRY_SUFFIX}", canonical_json(content))


@dataclass(frozen=True, slots=True)
class EscalationSpendLedger:
    """Every escalation call's cost, one JSON line each, appended and synced as it is booked."""

    path: Path

    def spent_usd(self) -> float:
        """What the ledger has booked, program-wide."""
        if not self.path.exists():
            return 0.0
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return sum(float(json.loads(line)["booked_usd"]) for line in lines if line)

    def record(self, key: str, cost_usd: float | None) -> None:
        """Book one call: its reported cost, or the bound when OpenRouter did not price it."""
        booked = _MAX_ANSWER_USD if cost_usd is None else cost_usd
        line = json.dumps(
            {"key": key, "cost_usd": cost_usd, "booked_usd": booked, "priced": cost_usd is not None},
            sort_keys=True,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as ledger:
            ledger.write(f"{line}\n")
            ledger.flush()
            os.fsync(ledger.fileno())


@dataclass(frozen=True, slots=True)
class EscalationJudge:
    """Luna, the questions it is trusted on, its cache, its ledger and its program-wide cap."""

    client: ChatCompleter
    model: str
    jev: JevConfig
    trusted: frozenset[QuestionKey]
    cache: EscalationCache
    ledger: EscalationSpendLedger
    spend_cap_usd: float

    def decide(
        self, item_id: str, context: AlignmentItemContext, question_ids: Sequence[str]
    ) -> dict[str, JevVerdict]:
        """Luna's verdict on each in-band question of one answer; ``undecided`` where untrusted.

        Raises:
            ValueError: a question is a Score, which is not escalated.
            JudgeModelMismatchError: another model answered.
        """
        scored = [qid for qid in question_ids if kind_of(qid) in SCORE_KINDS]
        if scored:
            raise ValueError(f"questions {scored!r} are Scores, expected yes/no questions only")
        trusted = [qid for qid in question_ids if self._trusts(context, qid)]
        verdicts = {qid: JevVerdict.UNDECIDED for qid in question_ids if qid not in trusted}
        if trusted:
            request = judge_request(item_id, context, jev=self.jev, question_ids=trusted)
            verdicts |= self._verdicts(request, trusted)
        return {qid: verdicts[qid] for qid in question_ids}

    def _trusts(self, context: AlignmentItemContext, question_id: str) -> bool:
        return (context.dataset.value, kind_of(question_id).value) in self.trusted

    def _verdicts(self, request: ChatRequest, question_ids: Sequence[str]) -> dict[str, JevVerdict]:
        """From the cache; else asked, while the cap allows one more answer at its bound."""
        key = escalation_cache_key(self.model, request)
        cached = _parsed(self.cache.get(key), question_ids)
        if cached is not None:
            return cached
        if self.ledger.spent_usd() + _MAX_ANSWER_USD > self.spend_cap_usd:
            return dict.fromkeys(question_ids, JevVerdict.UNDECIDED)
        content = self._ask(request, key)
        verdicts = _parsed(content, question_ids)
        if content is None or verdicts is None:
            return dict.fromkeys(question_ids, JevVerdict.UNDEFINED)
        self.cache.put(key, content)
        return verdicts

    def _ask(self, request: ChatRequest, key: str) -> Mapping[str, object] | None:
        (outcome,) = self.client.complete_all([request])
        if not isinstance(outcome, ChatCompletion):
            self.ledger.record(key, None)
            return None
        cost = outcome.cost_usd if outcome.cost_usd is not None else outcome.batch_cost_usd
        self.ledger.record(key, cost)
        return outcome.content


def escalation_cache_key(model: str, request: ChatRequest) -> str:
    """The input hash an answer is cached under: the pin, the messages and the schema asked.

    Example:
        >>> len(escalation_cache_key("openai/gpt-6-luna", request))  # doctest: +SKIP
        64
    """
    messages = [{"role": m.role.value, "content": m.content} for m in request.messages]
    payload = {"model": model, "messages": messages, "output": dict(request.output.schema)}
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_escalation_judge(
    config: JudgeConfig, rows: Sequence[AlignmentLabelRow], client: ChatCompleter
) -> EscalationJudge:
    """The escalation judge ``config`` pins, trusted where ``rows``' agreement pass says so.

    Raises:
        JudgeConfigError: ``judge.escalation.model`` is empty.
    """
    role = escalation_role(config)
    trust = escalation_trust(rows, config.escalation)
    root = escalation_cache_dir(config.escalation)
    return EscalationJudge(
        client=client,
        model=role.model,
        jev=config.jev,
        trusted=frozenset((row.dataset, row.question) for row in trust if row.trusted),
        cache=EscalationCache(root),
        ledger=EscalationSpendLedger(root / _SPEND_LEDGER_FILE),
        spend_cap_usd=config.escalation.spend_cap_usd,
    )


def escalate_in_band(
    score: JevScore, *, item_id: str, context: AlignmentItemContext, judge: EscalationJudge
) -> JevScore:
    """``score`` with each in-band row decided by ``judge``; every other row as Jev left it."""
    in_band = [row for row, verdict in score.verdicts.items() if verdict is JevVerdict.IN_BAND]
    if not in_band:
        return score
    decided = judge.decide(item_id, context, [_question_of(row) for row in in_band])
    verdicts = dict(score.verdicts)
    for row in in_band:
        verdict = decided[_question_of(row)]
        verdicts[row] = agreement_of(verdict) if row == AGREEMENT else verdict
    return replace(score, verdicts=verdicts)


def _question_of(row: str) -> str:
    """The question a score row reads: ``agreement`` is ``contradicts_reference`` swapped."""
    return _CONTRADICTS_REFERENCE if row == AGREEMENT else row


def _parsed(
    content: Mapping[str, object] | None, question_ids: Sequence[str]
) -> dict[str, JevVerdict] | None:
    """``content``'s verdict on each question; ``None`` when it is not the answer asked for."""
    if content is None:
        return None
    try:
        labels = parse_labels(content, question_ids)
    except AlignmentLabelError:
        return None
    return {qid: _VERDICT_OF_LABEL[label.verdict] for qid, label in labels.items()}


__all__ = (
    "ESCALATION_CACHE_SUBDIR",
    "EscalationCache",
    "EscalationJudge",
    "EscalationSpendLedger",
    "build_escalation_judge",
    "escalate_in_band",
    "escalation_cache_dir",
    "escalation_cache_key",
)
