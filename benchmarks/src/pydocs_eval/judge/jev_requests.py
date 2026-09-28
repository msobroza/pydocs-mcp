"""The Jev request designs: everything one slice asks about one answer, in as few requests as fit.

Each design puts every question about an answer into one request over
named-JSON state (``task``, the gold, ``reference_answer``, ``agent_answer``).
The gold questions come first — ``needle_identified`` on repoqa-qa, one
``site_<i>`` per gold site on the chat slice, one ``gold_file_<i>`` per gold
file on swe-qa-questions — then ``addresses_grader`` (injection) on every slice,
then the questions that need a reference, asked only once the answer has one:
``completeness`` (not on repoqa-qa's single needle) and
``contradicts_reference``. An answer over ``judge.jev.max_answer_chars`` is
flagged and never sent; past ``judge.jev.max_gold_files_per_request``
locations, the per-location questions continue in further requests.

The gold-blind ``committed_function`` Choice is the calibration audit of
alignment items: :func:`repoqa_audit_request` builds it apart, and no scoring
plan ever carries it.

Example:
    >>> plan = repoqa_request_plan(  # doctest: +SKIP
    ...     JudgedAnswer("Where are parameters read?", "In `get_params`."),
    ...     JudgedSite("sklearn/base.py", "BaseEstimator.get_params"),
    ...     jev=JevConfig(),
    ... )
    >>> plan.question_ids  # doctest: +SKIP
    ('needle_identified', 'addresses_grader')
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_questions import (
    ADDRESSES_GRADER,
    COMPLETENESS,
    CONTRADICTS_REFERENCE,
    NEEDLE_IDENTIFIED,
    JevQuestionKind,
    committed_function_question,
    gold_file_question,
    located_question_id,
    site_question,
)
from pydocs_eval.judge.jev_wire import JevQuestion, JevRequest
from pydocs_eval.judge.needle_citation import extract_dotted_names, module_of

# The API accepts up to 255 options per Choice; one is the no-commitment option.
_MAX_AUDIT_CANDIDATES = 254


class JudgedDataset(StrEnum):
    """The slices a Jev design exists for, by their dataset names."""

    REPOQA_QA = "repoqa-qa"
    EXAMPLE_NEEDLE_CHAT = "example-needle-chat"
    SWE_QA_QUESTIONS = "swe-qa-questions"


@dataclass(frozen=True, slots=True)
class JudgedAnswer:
    """One answer to judge: its question, its text, and its reference's text once one exists."""

    question: str
    answer: str
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class JudgedSite:
    """A gold location as a request states it: a path, and its symbol and span when known."""

    path: str
    symbol: str = ""
    span: str = ""


@dataclass(frozen=True, slots=True)
class JevRequestPlan:
    """The requests that judge one answer on one slice, and every question they ask.

    Over the cap ``requests`` is empty — the answer is never sent — while
    ``question_ids`` still lists what would have been asked, so each of its rows
    can read ``undefined``.
    """

    dataset: JudgedDataset
    requests: tuple[JevRequest, ...]
    question_ids: tuple[str, ...]
    answer_over_cap: bool = False

    @property
    def split(self) -> bool:
        """Whether the answer's questions did not fit one request."""
        return len(self.requests) > 1


@dataclass(frozen=True, slots=True)
class JevAuditRequest:
    """The gold-blind audit of one alignment item; ``request`` is ``None`` over the cap."""

    request: JevRequest | None
    answer_over_cap: bool = False


def repoqa_request_plan(
    judged: JudgedAnswer, needle: JudgedSite, *, jev: JevConfig
) -> JevRequestPlan:
    """repoqa-qa: whether the answer commits to the one gold function (``needle``).

    Example:
        >>> repoqa_request_plan(JudgedAnswer("Where?", "In `run`."), JudgedSite("src/pkg/mod.py", "run"), jev=JevConfig()).question_ids
        ('needle_identified', 'addresses_grader')
    """
    gold = {
        "path": needle.path,
        "module": module_of(needle.path),
        "symbol": needle.symbol,
        "bare_name": needle.symbol.rpartition(".")[2],
    }
    questions: dict[str, JevQuestion] = {JevQuestionKind.NEEDLE_IDENTIFIED.value: NEEDLE_IDENTIFIED}
    questions |= _answer_questions(judged, with_completeness=False)
    state = _state(judged, {"gold": _non_empty(gold)})
    return _plan(JudgedDataset.REPOQA_QA, judged, (JevRequest(state, questions),), jev)


def chat_request_plan(
    judged: JudgedAnswer, sites: Sequence[JudgedSite], *, jev: JevConfig
) -> JevRequestPlan:
    """example-needle-chat: which of the gold ``sites`` the answer points to, and how completely.

    Example:
        >>> chat_request_plan(JudgedAnswer("Where?", "In `run`.", "ref"), [JudgedSite("README.md")], jev=JevConfig()).question_ids
        ('site_0', 'addresses_grader', 'completeness', 'contradicts_reference')
    """
    stated = [_non_empty(_site_state(site)) for site in sites]
    located = [
        (located_question_id(JevQuestionKind.SITE, i), site_question(_without_span(site)))
        for i, site in enumerate(stated)
    ]
    state = _state(judged, {"gold_sites": stated})
    return _located_plan(JudgedDataset.EXAMPLE_NEEDLE_CHAT, judged, state, located, jev)


def swe_qa_request_plan(
    judged: JudgedAnswer, gold_files: Sequence[JudgedSite], *, jev: JevConfig
) -> JevRequestPlan:
    """swe-qa-questions: which gold files the answer points to; the gold rides the questions only.

    Example:
        >>> swe_qa_request_plan(JudgedAnswer("Where?", "In `a.py`."), [JudgedSite("a.py")], jev=JevConfig()).question_ids
        ('gold_file_0', 'addresses_grader')
    """
    located = [
        (
            located_question_id(JevQuestionKind.GOLD_FILE, i),
            gold_file_question(_non_empty({"path": gold.path, "module": module_of(gold.path)})),
        )
        for i, gold in enumerate(gold_files)
    ]
    return _located_plan(JudgedDataset.SWE_QA_QUESTIONS, judged, _state(judged, {}), located, jev)


def repoqa_audit_request(judged: JudgedAnswer, *, jev: JevConfig) -> JevAuditRequest:
    """The calibration audit: which function the answer commits to, asked without the gold.

    Its options are the names the answer writes as code; code, not Jev, then
    compares the chosen one with the gold. Only an alignment item is audited.

    Example:
        >>> audit = repoqa_audit_request(JudgedAnswer("Where?", "It is `run`."), jev=JevConfig())
        >>> audit.request.questions['committed_function'].options
        {'run': None, 'no_single_function': '`agent_answer` names no function as the answer, lists several without committing to one, or says the function was not found.'}

    Raises:
        ValueError: the answer writes more names than one Choice can offer.
    """
    if _over_cap(judged, jev):
        return JevAuditRequest(request=None, answer_over_cap=True)
    candidates = _committed_candidates(judged.answer)
    if len(candidates) > _MAX_AUDIT_CANDIDATES:
        raise ValueError(
            f"answer writes {len(candidates)} names, expected at most {_MAX_AUDIT_CANDIDATES} "
            "for one committed_function Choice"
        )
    question = {JevQuestionKind.COMMITTED_FUNCTION.value: committed_function_question(candidates)}
    state = {"task": {"question": judged.question}, "agent_answer": judged.answer}
    return JevAuditRequest(request=JevRequest(state, question))


def _located_plan(
    dataset: JudgedDataset,
    judged: JudgedAnswer,
    state: dict[str, object],
    located: Sequence[tuple[str, JevQuestion]],
    jev: JevConfig,
) -> JevRequestPlan:
    """The per-location questions in parts of at most the cap; the first part asks the rest."""
    size = jev.max_gold_files_per_request
    parts = [dict(located[start : start + size]) for start in range(0, len(located), size)] or [{}]
    parts[0] |= _answer_questions(judged, with_completeness=True)
    return _plan(dataset, judged, tuple(JevRequest(state, part) for part in parts), jev)


def _plan(
    dataset: JudgedDataset, judged: JudgedAnswer, requests: tuple[JevRequest, ...], jev: JevConfig
) -> JevRequestPlan:
    """``requests`` as a plan, or none of them when the answer is over the cap."""
    question_ids = tuple(question_id for request in requests for question_id in request.questions)
    if _over_cap(judged, jev):
        return JevRequestPlan(dataset, (), question_ids, answer_over_cap=True)
    return JevRequestPlan(dataset, requests, question_ids)


def _answer_questions(judged: JudgedAnswer, *, with_completeness: bool) -> dict[str, JevQuestion]:
    """The questions about the answer as a whole: the injection flag, then the reference ones."""
    questions: dict[str, JevQuestion] = {JevQuestionKind.ADDRESSES_GRADER.value: ADDRESSES_GRADER}
    if judged.reference is None:
        return questions
    if with_completeness:
        questions[JevQuestionKind.COMPLETENESS.value] = COMPLETENESS
    questions[JevQuestionKind.CONTRADICTS_REFERENCE.value] = CONTRADICTS_REFERENCE
    return questions


def _state(judged: JudgedAnswer, gold: dict[str, object]) -> dict[str, object]:
    """The named state, in reading order: the task, the gold, the reference, the answer."""
    state: dict[str, object] = {"task": {"question": judged.question}, **gold}
    if judged.reference is not None:
        state["reference_answer"] = judged.reference
    state["agent_answer"] = judged.answer
    return state


def _site_state(site: JudgedSite) -> dict[str, str]:
    return {
        "path": site.path,
        "module": module_of(site.path),
        "symbol": site.symbol,
        "span": site.span,
    }


def _without_span(site: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in site.items() if key != "span"}


def _non_empty(fields: dict[str, str]) -> dict[str, str]:
    """``fields`` without its empty values: an empty field is irrelevant state, and costs accuracy."""
    return {key: value for key, value in fields.items() if value}


def _over_cap(judged: JudgedAnswer, jev: JevConfig) -> bool:
    return len(judged.answer) > jev.max_answer_chars


def _committed_candidates(answer: str) -> tuple[str, ...]:
    """The longest names the answer writes as code, sorted: each part of a longer one is dropped."""
    names = extract_dotted_names(answer)
    return tuple(sorted(name for name in names if not _is_part_of_longer(name, names)))


def _is_part_of_longer(name: str, names: frozenset[str]) -> bool:
    return any(other != name and f".{name}." in f".{other}." for other in names)


__all__ = (
    "JevAuditRequest",
    "JevRequestPlan",
    "JudgedAnswer",
    "JudgedDataset",
    "JudgedSite",
    "chat_request_plan",
    "repoqa_audit_request",
    "repoqa_request_plan",
    "swe_qa_request_plan",
)
