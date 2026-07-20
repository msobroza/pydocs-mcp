"""Split construction: determinism, repo-disjointness, the dev cap (ADR 0013 deliverable 3)."""

from __future__ import annotations

from pydocs_eval.datasets_swe.records import LiveRecord
from pydocs_eval.datasets_swe.splits import SplitConfig, build_splits


def _repo_records(repo: str, n: int, files: int, year: int) -> list[LiveRecord]:
    return [LiveRecord(f"{repo.replace('/', '__')}-{i}", repo, files, year) for i in range(n)]


def _balanced_corpus() -> list[LiveRecord]:
    records: list[LiveRecord] = []
    for i in range(60):
        files = 1 if i % 2 == 0 else 3
        year = 2024 if i % 3 == 0 else 2025
        records.extend(_repo_records(f"org{i}/repo{i}", 3, files, year))
    return records


def _heavy_corpus() -> list[LiveRecord]:
    return _balanced_corpus() + _repo_records("heavy/repo", 200, 1, 2024)


def test_build_is_byte_identical_across_runs():
    corpus = _heavy_corpus()
    a = build_splits(corpus, [])
    b = build_splits(corpus, [])
    assert a.dev_instances == b.dev_instances
    assert a.val_instances == b.val_instances


def test_repos_are_disjoint_between_dev_and_val():
    result = build_splits(_heavy_corpus(), [])
    dev_repos = {info.repo for info in result.dev_repos}
    val_repos = {info.repo for info in result.val_repos}
    assert dev_repos.isdisjoint(val_repos)


def test_dev_and_val_instance_sets_are_disjoint():
    result = build_splits(_heavy_corpus(), [])
    assert set(result.dev_instances).isdisjoint(result.val_instances)


def test_no_dev_repo_exceeds_ten_percent_of_realized_dev():
    result = build_splits(_heavy_corpus(), [])
    dev_total = len(result.dev_instances)
    contributed = [c for _assigned, c in result.dev_contribution.values()]
    assert sum(contributed) == dev_total  # contributions reconcile with the dev list
    assert max(contributed) <= 0.10 * dev_total + 1e-9


def test_over_cap_repo_leaves_excess_unused_not_spilled_to_val():
    result = build_splits(_heavy_corpus(), [])
    heavy_in_dev = any(info.repo == "heavy/repo" for info in result.dev_repos)
    if not heavy_in_dev:
        return  # heavy landed in val this seed — cap not exercised, nothing to assert
    assigned, contributed = result.dev_contribution["heavy/repo"]
    assert assigned == 200
    assert contributed < assigned  # capped
    # Excess is NOT in val (disjointness) — val carries none of heavy/repo.
    assert not any(iid.startswith("heavy__repo-") for iid in result.val_instances)


def test_dev_val_ratio_targets_roughly_two_to_one_without_a_heavy_repo():
    result = build_splits(_balanced_corpus(), [])
    assert 1.5 <= result.dev_val_ratio <= 2.5


def test_org_exclusion_applied_before_assignment():
    corpus = _balanced_corpus() + _repo_records("ansible/ansible-lint", 4, 1, 2024)
    result = build_splits(corpus, ["ansible/ansible"])
    all_instances = set(result.dev_instances) | set(result.val_instances)
    assert not any(iid.startswith("ansible__ansible-lint-") for iid in all_instances)
    assert len(result.excluded_instances) == 4


def test_seed_change_changes_the_partition():
    corpus = _balanced_corpus()
    a = build_splits(corpus, [], SplitConfig(seed=1))
    b = build_splits(corpus, [], SplitConfig(seed=2))
    assert a.dev_instances != b.dev_instances
