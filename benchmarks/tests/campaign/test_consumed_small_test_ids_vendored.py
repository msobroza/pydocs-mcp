"""The committed exclusion file: the repoqa-qa ``small_test`` ids an earlier run consumed.

Its bytes are pinned: the fresh test draw is only as clean as this file is
unchanged, and every run that excludes it records this digest. A deliberate
edit re-pins the digest here AND re-records the draw in the README ledger.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from pydocs_eval.campaign.before_after_fresh_draw import FRESH_DRAW_SIZE, load_excluded_task_ids

_CONSUMED = (
    Path(__file__).parents[2] / "baselines" / "repoqa_qa_small_test_consumed_2026-09-15.json"
)
_PINNED_SHA256 = "47db1055eae8fdbb6b2dd6abb8720124c1c994abf054cb948e79f92fbd9488f2"
_FRAMED_PREFIX = "repoqa-qa/repo_qa/"
# The pinned RepoQA release has 10 Python repos, so small_test takes 3 from each.
_REPOS = 10


def test_the_consumed_ids_file_is_byte_pinned() -> None:
    assert hashlib.sha256(_CONSUMED.read_bytes()).hexdigest() == _PINNED_SHA256


def test_it_loads_as_an_exclusion_file_carrying_its_digest() -> None:
    assert load_excluded_task_ids(_CONSUMED).sha256 == _PINNED_SHA256


def test_it_holds_one_small_test_worth_of_framed_repoqa_qa_ids() -> None:
    task_ids = load_excluded_task_ids(_CONSUMED).task_ids

    assert len(task_ids) == FRESH_DRAW_SIZE == len(set(task_ids))
    assert all(task_id.startswith(_FRAMED_PREFIX) for task_id in task_ids)


def test_it_is_stratified_by_repo_like_small_test() -> None:
    repos = Counter(
        task_id.removeprefix(_FRAMED_PREFIX).split("@", 1)[0]
        for task_id in load_excluded_task_ids(_CONSUMED).task_ids
    )

    assert len(repos) == _REPOS and set(repos.values()) == {FRESH_DRAW_SIZE // _REPOS}


def test_its_provenance_names_the_run_the_release_and_the_seed() -> None:
    provenance = load_excluded_task_ids(_CONSUMED).provenance

    assert "2026-09-15" in provenance
    assert "2024-06-23" in provenance
    assert "split_seed 0" in provenance
