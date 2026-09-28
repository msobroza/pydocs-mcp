"""The Jev request designs: what each slice asks about one answer, over which named state."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator, Mapping

import pytest

from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_questions import JevQuestionKind, kind_of
from pydocs_eval.judge.jev_requests import (
    JevRequestPlan,
    JudgedAnswer,
    JudgedDataset,
    JudgedSite,
    chat_request_plan,
    repoqa_audit_request,
    repoqa_request_plan,
    swe_qa_request_plan,
)
from pydocs_eval.judge.jev_wire import JevRequest, NoulQuestion
from pydocs_eval.judge.planted_injections import (
    PlantedInjection,
    load_planted_injections,
    planted_answer,
)

from ._judge_fakes import golden

_JEV = JevConfig()
_QUESTION = "Where are the estimator's parameters read?"
_ANSWER = "They are read by `BaseEstimator.get_params` in `sklearn/base.py`."
_REFERENCE = "`sklearn/base.py:120-140` `BaseEstimator.get_params` reads each parameter."
_NEEDLE = JudgedSite(path="sklearn/base.py", symbol="BaseEstimator.get_params")
_CHAT_SITES = (
    JudgedSite(
        "src/needle/scoring/strategies.py",
        "MaxSimStrategy",
        "src/needle/scoring/strategies.py:42-43",
    ),
    JudgedSite("README.md", "Scoring", "README.md:10-24"),
)
_GOLD_FILES = (JudgedSite("sklearn/base.py"), JudgedSite("sklearn/utils/validation.py"))


def _judged(reference: str | None = _REFERENCE, answer: str = _ANSWER) -> JudgedAnswer:
    return JudgedAnswer(question=_QUESTION, answer=answer, reference=reference)


def _sites(count: int) -> tuple[JudgedSite, ...]:
    return tuple(
        JudgedSite(f"src/pkg/m{i}.py", f"f{i}", f"src/pkg/m{i}.py:1-2") for i in range(count)
    )


_PLAN_OF_EACH_SLICE: dict[str, Callable[[JudgedAnswer], JevRequestPlan]] = {
    "repoqa-qa": lambda judged: repoqa_request_plan(judged, _NEEDLE, jev=_JEV),
    "example-needle-chat": lambda judged: chat_request_plan(judged, _CHAT_SITES, jev=_JEV),
    "swe-qa-questions": lambda judged: swe_qa_request_plan(judged, _GOLD_FILES, jev=_JEV),
}


def test_a_repoqa_request_asks_three_questions_once_its_reference_exists() -> None:
    (request,) = repoqa_request_plan(_judged(), _NEEDLE, jev=_JEV).requests

    assert list(request.questions) == [
        "needle_identified",
        "addresses_grader",
        "contradicts_reference",
    ]


def test_a_repoqa_request_asks_two_questions_before_its_reference_exists() -> None:
    (request,) = repoqa_request_plan(_judged(reference=None), _NEEDLE, jev=_JEV).requests

    assert list(request.questions) == ["needle_identified", "addresses_grader"]
    assert "reference_answer" not in request.state


def test_the_repoqa_request_is_itsgolden() -> None:
    (request,) = repoqa_request_plan(_judged(), _NEEDLE, jev=_JEV).requests

    assert request.body("jev-1.13") == golden("jev_repoqa_request.json")


def test_the_repoqa_state_names_the_gold_by_path_module_symbol_and_bare_name() -> None:
    (request,) = repoqa_request_plan(_judged(), _NEEDLE, jev=_JEV).requests

    assert list(request.state) == ["task", "gold", "reference_answer", "agent_answer"]
    assert request.state["gold"] == {
        "path": "sklearn/base.py",
        "module": "sklearn.base",
        "symbol": "BaseEstimator.get_params",
        "bare_name": "get_params",
    }
    assert request.state["task"] == {"question": _QUESTION}
    assert (request.state["reference_answer"], request.state["agent_answer"]) == (
        _REFERENCE,
        _ANSWER,
    )


@pytest.mark.parametrize("site_count", [1, 2, 7, 12])
def test_a_chat_request_asks_one_noul_per_site_plus_three(site_count: int) -> None:
    (request,) = chat_request_plan(_judged(), _sites(site_count), jev=_JEV).requests

    assert list(request.questions) == [
        *(f"site_{i}" for i in range(site_count)),
        "addresses_grader",
        "completeness",
        "contradicts_reference",
    ]


def test_the_chat_request_is_itsgolden() -> None:
    (request,) = chat_request_plan(_judged(), _CHAT_SITES, jev=_JEV).requests

    assert request.body("jev-1.13") == golden("jev_chat_request.json")


def test_a_chat_site_question_carries_its_site_and_the_state_lists_every_site() -> None:
    (request,) = chat_request_plan(_judged(), _CHAT_SITES, jev=_JEV).requests

    assert list(request.state) == ["task", "gold_sites", "reference_answer", "agent_answer"]
    assert request.state["gold_sites"] == [
        {
            "path": "src/needle/scoring/strategies.py",
            "module": "needle.scoring.strategies",
            "symbol": "MaxSimStrategy",
            "span": "src/needle/scoring/strategies.py:42-43",
        },
        {"path": "README.md", "symbol": "Scoring", "span": "README.md:10-24"},
    ]
    site = request.questions["site_1"]
    assert isinstance(site, NoulQuestion)
    assert isinstance(site.instructions, Mapping)
    assert site.instructions["gold_site"] == {"path": "README.md", "symbol": "Scoring"}


@pytest.mark.parametrize("gold_count", [1, 3, 12])
def test_a_swe_qa_request_asks_one_noul_per_gold_file_plus_three(gold_count: int) -> None:
    files = tuple(JudgedSite(f"pkg/m{i}.py") for i in range(gold_count))

    (request,) = swe_qa_request_plan(_judged(), files, jev=_JEV).requests

    assert list(request.questions) == [
        *(f"gold_file_{i}" for i in range(gold_count)),
        "addresses_grader",
        "completeness",
        "contradicts_reference",
    ]


def test_the_swe_qa_request_is_itsgolden() -> None:
    (request,) = swe_qa_request_plan(_judged(), _GOLD_FILES, jev=_JEV).requests

    assert request.body("jev-1.13") == golden("jev_swe_qa_request.json")


def test_the_swe_qa_state_carries_no_gold() -> None:
    (request,) = swe_qa_request_plan(_judged(), _GOLD_FILES, jev=_JEV).requests

    assert list(request.state) == ["task", "reference_answer", "agent_answer"]


@pytest.mark.parametrize("slice_name", ["example-needle-chat", "swe-qa-questions"])
def test_without_a_reference_only_the_located_questions_and_the_injection_flag_are_asked(
    slice_name: str,
) -> None:
    (request,) = _PLAN_OF_EACH_SLICE[slice_name](_judged(reference=None)).requests

    kinds = {kind_of(question_id) for question_id in request.questions}
    assert JevQuestionKind.COMPLETENESS not in kinds
    assert JevQuestionKind.CONTRADICTS_REFERENCE not in kinds
    assert JevQuestionKind.ADDRESSES_GRADER in kinds
    assert "reference_answer" not in request.state


@pytest.mark.parametrize(
    ("build", "located"),
    [
        (lambda judged, n: chat_request_plan(judged, _sites(n), jev=_JEV), "site"),
        (
            lambda judged, n: swe_qa_request_plan(
                judged, tuple(JudgedSite(f"pkg/m{i}.py") for i in range(n)), jev=_JEV
            ),
            "gold_file",
        ),
    ],
)
def test_more_than_twelve_locations_are_judged_in_recorded_parts(
    build: Callable[[JudgedAnswer, int], JevRequestPlan], located: str
) -> None:
    plan = build(_judged(), 15)

    first, second = plan.requests
    assert plan.split
    assert list(first.questions) == [
        *(f"{located}_{i}" for i in range(12)),
        "addresses_grader",
        "completeness",
        "contradicts_reference",
    ]
    assert list(second.questions) == [f"{located}_{i}" for i in range(12, 15)]
    assert first.state == second.state
    assert len(plan.question_ids) == 15 + 3


def test_twelve_locations_fit_one_request() -> None:
    assert not chat_request_plan(_judged(), _sites(12), jev=_JEV).split


def test_the_split_follows_the_configured_cap() -> None:
    plan = chat_request_plan(_judged(), _sites(5), jev=JevConfig(max_gold_files_per_request=2))

    assert [len(request.questions) for request in plan.requests] == [2 + 3, 2, 1]


@pytest.mark.parametrize("slice_name", sorted(_PLAN_OF_EACH_SLICE))
def test_an_answer_over_the_cap_is_flagged_and_never_sent(slice_name: str) -> None:
    over_cap = "x" * (_JEV.max_answer_chars + 1)

    plan = _PLAN_OF_EACH_SLICE[slice_name](_judged(answer=over_cap))

    assert plan.answer_over_cap
    assert plan.requests == ()


@pytest.mark.parametrize("slice_name", sorted(_PLAN_OF_EACH_SLICE))
def test_an_answer_at_the_cap_is_sent_whole(slice_name: str) -> None:
    at_cap = "y" * _JEV.max_answer_chars

    plan = _PLAN_OF_EACH_SLICE[slice_name](_judged(answer=at_cap))

    assert not plan.answer_over_cap
    assert plan.requests[0].state["agent_answer"] == at_cap


@pytest.mark.parametrize("slice_name", sorted(_PLAN_OF_EACH_SLICE))
def test_every_slice_names_its_dataset(slice_name: str) -> None:
    assert _PLAN_OF_EACH_SLICE[slice_name](_judged()).dataset == JudgedDataset(slice_name)


@pytest.mark.parametrize("slice_name", sorted(_PLAN_OF_EACH_SLICE))
@pytest.mark.parametrize("injection", load_planted_injections(), ids=lambda each: each.id)
def test_every_planted_injection_is_asked_about_on_every_slice(
    slice_name: str, injection: PlantedInjection
) -> None:
    answer = planted_answer(_ANSWER, injection)

    plan = _PLAN_OF_EACH_SLICE[slice_name](_judged(answer=answer))

    assert plan.question_ids.count("addresses_grader") == 1
    for request in plan.requests:
        assert request.state["agent_answer"] == answer
        assert injection.text not in json.dumps([q.to_wire() for q in request.questions.values()])


def test_the_planted_injections_are_the_four_vendored_ones() -> None:
    injections = load_planted_injections()

    assert [each.id for each in injections] == [
        "grader_note",
        "hidden_comment",
        "system_override",
        "prior_review_claim",
    ]
    assert planted_answer("An answer.", injections[0]).startswith("An answer.\n\n")


def test_the_audit_is_one_gold_blind_choice_over_the_names_the_answer_writes() -> None:
    answer = "It is `sklearn.base.BaseEstimator.get_params`, not `set_params` or `clone()`."

    audit = repoqa_audit_request(_judged(answer=answer), jev=_JEV)

    assert audit.request is not None
    assert list(audit.request.questions) == ["committed_function"]
    assert list(audit.request.state) == ["task", "agent_answer"]
    assert audit.request.body("jev-1.13") == golden("jev_repoqa_audit_request.json")


def test_an_over_cap_answer_is_never_audited() -> None:
    audit = repoqa_audit_request(_judged(answer="z" * (_JEV.max_answer_chars + 1)), jev=_JEV)

    assert audit.answer_over_cap
    assert audit.request is None


@pytest.mark.parametrize("slice_name", sorted(_PLAN_OF_EACH_SLICE))
@pytest.mark.parametrize("reference", [_REFERENCE, None])
def test_no_scoring_request_asks_the_audit_question(slice_name: str, reference: str | None) -> None:
    plan = _PLAN_OF_EACH_SLICE[slice_name](_judged(reference=reference))

    assert "committed_function" not in plan.question_ids


_FIELD_REFERENCE = re.compile(r"`([a-z_]+(?:\.[a-z_]+)*)`")


def _referenced_fields(value: object) -> Iterator[str]:
    """Every backticked ``field.path`` a question's text points Jev at."""
    if isinstance(value, str):
        yield from _FIELD_REFERENCE.findall(value)
    elif isinstance(value, Mapping):
        for each in value.values():
            yield from _referenced_fields(each)
    elif isinstance(value, list | tuple):
        for each in value:
            yield from _referenced_fields(each)


def _resolves(path: str, *roots: Mapping[str, object]) -> bool:
    for root in roots:
        node: object = root
        for part in path.split("."):
            node = node.get(part) if isinstance(node, Mapping) else None
        if node is not None:
            return True
    return False


def _every_request() -> Iterator[JevRequest]:
    for build in _PLAN_OF_EACH_SLICE.values():
        for reference in (_REFERENCE, None):
            yield from build(_judged(reference=reference)).requests
    audit = repoqa_audit_request(_judged(), jev=_JEV).request
    assert audit is not None
    yield audit


@pytest.mark.parametrize("request_index", range(7))
def test_every_field_a_question_names_is_in_its_state(request_index: int) -> None:
    """Jev reads questions literally: a backticked field it cannot find is a question it cannot answer."""
    request = list(_every_request())[request_index]

    for question in request.questions.values():
        wire = question.to_wire()
        instructions = wire["instructions"]
        own_fields = instructions if isinstance(instructions, Mapping) else {}
        for field in _referenced_fields(wire):
            assert _resolves(field, request.state, own_fields), field


@pytest.mark.parametrize(
    "runaway",
    [
        "`" + ".".join(f"seg{i}" for i in range(1600)) + "`",
        " ".join(f"`name_{i}`" for i in range(1300)),
    ],
    ids=["one-long-chain", "many-names"],
)
def test_a_runaway_answer_is_refused_by_the_audit_in_bounded_time(runaway: str) -> None:
    """A pairwise part check took 18 s on a 12 KB runaway chain; it must stay well under 5 s."""
    judged = _judged(answer=runaway[: _JEV.max_answer_chars])
    started = time.perf_counter()

    with pytest.raises(ValueError, match="names, expected at most 254"):
        repoqa_audit_request(judged, jev=_JEV)

    assert time.perf_counter() - started < 5.0


def test_the_audit_keeps_only_the_longest_names() -> None:
    answer = "Not `a.b`, but `a.b.c`; see also `c.d` and `x`."

    audit = repoqa_audit_request(_judged(answer=answer), jev=_JEV)

    assert audit.request is not None
    options = audit.request.questions["committed_function"].to_wire()["criteria"]
    assert list(options) == ["a.b.c", "c.d", "x", "no_single_function"]
