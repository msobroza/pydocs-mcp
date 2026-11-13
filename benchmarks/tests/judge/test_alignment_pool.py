"""The alignment draw: real answers in four strata, the reserved questions never, pass 2 only once unlocked."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from pydocs_eval.judge.alignment_pool import (
    AlignmentCandidate,
    AlignmentPassLockedError,
    AlignmentPool,
    alignment_row,
    code_check_of,
    refuse_locked_pass,
    select_alignment_items,
    stratum_of,
    swe_qa_questions_unlocked,
)
from pydocs_eval.judge.alignment_request import labeller_request
from pydocs_eval.judge.alignment_rows import AnswerSource, CodeCheck
from pydocs_eval.judge.alignment_vocabulary import AlignmentPass, AlignmentStratum
from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.planted_injections import load_planted_injections
from pydocs_eval.judge.role_config import AlignmentConfig

from ._alignment_fakes import ASTRA_MODEL, JEV_MODEL, OPUS_MODEL, repoqa_context

_CONFIG = AlignmentConfig()
_JEV = JevConfig(model=JEV_MODEL)
_INJECTIONS = load_planted_injections()


def _candidate(
    task_id: str,
    *,
    probabilities: dict[str, float] | None = None,
    cited: bool = True,
    dataset: JudgedDataset = JudgedDataset.REPOQA_QA,
) -> AlignmentCandidate:
    return AlignmentCandidate(
        dataset=dataset,
        task_id=task_id,
        source=AnswerSource(arm="baseline@60d5a00a", trajectory_id=f"traj-{task_id}"),
        answer=f"The answer to {task_id} is `get_params`.",
        code_check=CodeCheck(
            needle_cited=cited, gold_site_coverage=float(cited), verdicts={"needle_identified": cited}
        ),
        jev_probabilities=probabilities or {},
    )


def _pool_of_forty() -> AlignmentPool:
    borderline = [
        _candidate(f"border_{i}", probabilities={"needle_identified": 0.5}) for i in range(10)
    ]
    disagreeing = [
        _candidate(f"against_{i}", probabilities={"needle_identified": 0.05}) for i in range(6)
    ]
    plain = [_candidate(f"plain_{i}", probabilities={"needle_identified": 0.95}) for i in range(24)]
    return AlignmentPool.of([*borderline, *disagreeing, *plain], reserved=frozenset())


@pytest.mark.parametrize(
    ("probability", "cited", "stratum"),
    [
        (0.5, True, AlignmentStratum.BORDERLINE),
        (0.2, True, AlignmentStratum.BORDERLINE),
        (0.8, False, AlignmentStratum.BORDERLINE),
        (0.1, True, AlignmentStratum.CODE_VS_JEV),
        (0.95, False, AlignmentStratum.CODE_VS_JEV),
        (0.95, True, AlignmentStratum.RANDOM),
        (0.1, False, AlignmentStratum.RANDOM),
        (None, True, AlignmentStratum.RANDOM),
    ],
)
def test_an_answer_is_borderline_in_jevs_window_and_a_disagreement_when_jev_decides_against_code(
    probability: float | None, cited: bool, stratum: AlignmentStratum
) -> None:
    probabilities = {} if probability is None else {"needle_identified": probability}

    assert stratum_of(_candidate("t", probabilities=probabilities, cited=cited), _CONFIG) is stratum


def test_a_reserved_question_never_enters_the_pool() -> None:
    reserved = _candidate(
        "example-needle-chat/q12",
        probabilities={"site_0": 0.5},
        dataset=JudgedDataset.EXAMPLE_NEEDLE_CHAT,
    )
    others = [_candidate(f"plain_{i}") for i in range(3)]

    pool = AlignmentPool.of([reserved, *others], reserved=frozenset({"example-needle-chat/q12"}))
    drawn = select_alignment_items(pool, size=50, config=_CONFIG, injections=_INJECTIONS, seed=0)

    assert pool.excluded_reserved == 1
    assert "example-needle-chat/q12" not in {item.candidate.task_id for item in drawn}
    assert "example-needle-chat/q12" not in {candidate.task_id for candidate in pool.candidates}


def test_each_stratum_fills_its_share_with_the_planted_injections_inside_the_count() -> None:
    drawn = select_alignment_items(
        _pool_of_forty(), size=20, config=_CONFIG, injections=_INJECTIONS, seed=7
    )

    assert len(drawn) == 20
    assert Counter(item.stratum for item in drawn) == {
        AlignmentStratum.PLANTED_INJECTION: 4,
        AlignmentStratum.BORDERLINE: 6,
        AlignmentStratum.CODE_VS_JEV: 5,
        AlignmentStratum.RANDOM: 5,
    }
    planted = [item for item in drawn if item.stratum is AlignmentStratum.PLANTED_INJECTION]
    assert {item.injection.id for item in planted if item.injection} == {i.id for i in _INJECTIONS}
    assert all(item.candidate.task_id.startswith("plain_") for item in planted)


def test_active_strata_short_of_their_share_are_filled_at_random() -> None:
    pool = AlignmentPool.of([_candidate(f"plain_{i}") for i in range(30)], reserved=frozenset())

    drawn = select_alignment_items(pool, size=10, config=_CONFIG, injections=_INJECTIONS, seed=0)

    assert Counter(item.stratum for item in drawn) == {
        AlignmentStratum.PLANTED_INJECTION: 4,
        AlignmentStratum.RANDOM: 6,
    }


def test_the_same_seed_draws_the_same_items_and_another_seed_others() -> None:
    def ids(seed: int) -> list[str]:
        drawn = select_alignment_items(
            _pool_of_forty(), size=12, config=_CONFIG, injections=_INJECTIONS, seed=seed
        )
        return [item.item_id for item in drawn]

    assert ids(3) == ids(3)
    assert ids(3) != ids(4)


def test_a_pool_smaller_than_the_draw_gives_every_answer_it_has() -> None:
    pool = AlignmentPool.of([_candidate(f"plain_{i}") for i in range(3)], reserved=frozenset())

    drawn = select_alignment_items(pool, size=10, config=_CONFIG, injections=_INJECTIONS, seed=0)

    assert len({item.candidate.task_id for item in drawn}) == 3
    assert Counter(item.stratum for item in drawn)[AlignmentStratum.PLANTED_INJECTION] == 3


def test_items_already_in_the_label_file_are_never_drawn_again() -> None:
    pool = _pool_of_forty()
    first = select_alignment_items(pool, size=10, config=_CONFIG, injections=_INJECTIONS, seed=1)
    rows = [_row(item) for item in first]

    more = select_alignment_items(
        pool, size=10, config=_CONFIG, injections=_INJECTIONS, seed=1, existing=rows
    )

    assert not {item.item_id for item in more} & {row.item_id for row in rows}
    assert AlignmentStratum.PLANTED_INJECTION not in {item.stratum for item in more}
    used = {(item.candidate.task_id, item.injection is None) for item in first}
    assert not {(item.candidate.task_id, item.injection is None) for item in more} & used


def _row(item):
    return alignment_row(
        item,
        context=repoqa_context(answer=item.candidate.answer),
        jev_model=JEV_MODEL,
        labeller_models=(ASTRA_MODEL, OPUS_MODEL),
        jev=_JEV,
    )


def test_a_labeller_never_sees_jevs_first_pass_probability() -> None:
    candidate = _candidate("border", probabilities={"needle_identified": 0.4137})
    pool = AlignmentPool.of([candidate], reserved=frozenset())
    (item,) = select_alignment_items(
        pool, size=1, config=_CONFIG.model_copy(update={"planted_injections": 0}),
        injections=_INJECTIONS, seed=0,
    )

    request = labeller_request(_row(item), jev=_JEV)
    text = json.dumps([message.content for message in request.messages])

    assert item.stratum is AlignmentStratum.BORDERLINE
    assert "0.4137" not in text
    assert "borderline" not in text


def test_a_planted_item_carries_its_injection_in_the_answer_and_its_own_id() -> None:
    pool = AlignmentPool.of([_candidate("plain_0")], reserved=frozenset())
    (item,) = select_alignment_items(pool, size=1, config=_CONFIG, injections=_INJECTIONS, seed=0)

    row = _row(item)

    assert row.stratum is AlignmentStratum.PLANTED_INJECTION
    assert row.planted_injection == _INJECTIONS[0].id
    assert row.context.agent_answer.endswith(_INJECTIONS[0].text)
    assert row.questions == ("needle_identified", "addresses_grader", "contradicts_reference")
    assert (row.astra.model, row.opus.model) == (ASTRA_MODEL, OPUS_MODEL)


def test_the_code_check_gives_codes_verdict_on_each_gold_question() -> None:
    sites = (
        JudgedSite("src/needle/scoring/strategies.py", "MaxSimStrategy", "x:1-2"),
        JudgedSite("README.md", "Scoring", "README.md:1-2"),
    )

    chat = code_check_of(
        JudgedDataset.EXAMPLE_NEEDLE_CHAT,
        sites,
        "Scoring is `MaxSimStrategy` in `strategies.py`.",
        extensions=_JEV.citation_extensions,
    )
    repoqa = code_check_of(
        JudgedDataset.REPOQA_QA,
        (JudgedSite("sklearn/base.py", "BaseEstimator.get_params"),),
        "It is `sklearn/base.py`, but no function.",
        extensions=_JEV.citation_extensions,
    )

    assert chat.verdicts == {"site_0": True, "site_1": False}
    assert (chat.needle_cited, chat.gold_site_coverage) == (False, 0.5)
    assert repoqa.verdicts == {"needle_identified": False}


@pytest.mark.parametrize(
    ("alignment_pass", "datasets", "unlocked", "refused"),
    [
        (AlignmentPass.PASS_2, (JudgedDataset.SWE_QA_QUESTIONS,), False, True),
        (AlignmentPass.PASS_2, (JudgedDataset.SWE_QA_QUESTIONS,), True, False),
        (AlignmentPass.PASS_1, (JudgedDataset.SWE_QA_QUESTIONS,), False, True),
        (AlignmentPass.PASS_1, (JudgedDataset.REPOQA_QA,), False, False),
        (AlignmentPass.PILOT, (JudgedDataset.REPOQA_QA,), False, False),
    ],
)
def test_pass_2_refuses_to_run_before_the_swe_qa_questions_unlock(
    alignment_pass: AlignmentPass,
    datasets: tuple[JudgedDataset, ...],
    unlocked: bool,
    refused: bool,
) -> None:
    if not refused:
        refuse_locked_pass(alignment_pass, datasets, unlocked=unlocked)
        return
    with pytest.raises(AlignmentPassLockedError, match="SWE_QA_QUESTION_SLICES"):
        refuse_locked_pass(alignment_pass, datasets, unlocked=unlocked)


def test_the_unlock_is_read_from_the_swe_qa_questions_slices_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "landed_slices.py").write_text("SWE_QA_QUESTION_SLICES = ('1',)\n")
    (tmp_path / "other_module.py").write_text("NOTHING = ()\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    assert swe_qa_questions_unlocked("landed_slices")
    assert not swe_qa_questions_unlocked("other_module")
    assert not swe_qa_questions_unlocked("no_such_module_anywhere")
