"""The two ``bug_loc`` loaders — hermetic via ``fixture_path`` + a fake RepoCache.

No network, no git, no parquet wheels: every row comes from a committed JSONL
miniature of the real release schema, and the corpus comes from a committed
fixture dir returned by a named fake cache.

The fixtures deliberately carry the shapes the derivation must survive — a
single-file gold, a multi-file gold, a patch that also touches tests, a gold
that is ONLY tests, an empty patch, a ``changed_files`` path with spaces, and a
``changed_files`` value that decodes under no spelling. The LCA rows carry the
release's REAL stringified-Python-list spelling (single quotes) alongside one
JSON-spelled row, because a fixture that only ever used JSON is what let a
``json.loads``-only reader pass its tests while minting zero real tasks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.datasets.bug_localization import (
    BUG_LOC_TASK_NAME,
    CORPUS_GLOBS,
    LCA_BUG_LOC_INSTANCES,
    LCA_BUG_LOC_PIN,
    SWE_BENCH_VERIFIED_INSTANCES,
    SWE_BENCH_VERIFIED_PIN,
    LcaBugLocDataset,
    SweBenchVerifiedLocDataset,
)
from pydocs_eval.registries import dataset_registry

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_SWE_FIXTURE = _FIXTURES / "swe_bench_verified_loc_mini.jsonl"
_LCA_FIXTURE = _FIXTURES / "lca_bug_loc_mini.jsonl"
_CORPUS_DIR = _FIXTURES / "bug_loc_corpus"


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network.

    Records every ``(url, sha)`` it was asked for so the pin-plumbing can be
    asserted, and always returns the shared fixture corpus dir.
    """

    corpus_dir: Path = field(default=_CORPUS_DIR)
    requested: list[tuple[str, str]] = field(default_factory=list)

    def checkout(self, url: str, sha: str) -> Path:
        self.requested.append((url, sha))
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


def _swe(cache: _FakeRepoCache | None = None) -> SweBenchVerifiedLocDataset:
    return SweBenchVerifiedLocDataset(
        fixture_path=_SWE_FIXTURE, repo_cache=cache or _FakeRepoCache()
    )


def _lca(cache: _FakeRepoCache | None = None) -> LcaBugLocDataset:
    return LcaBugLocDataset(fixture_path=_LCA_FIXTURE, repo_cache=cache or _FakeRepoCache())


# --- Registration + identity ---------------------------------------------


@pytest.mark.parametrize("name", ["swe-bench-verified-loc", "lca-bug-loc"])
def test_both_datasets_are_registered_under_their_arm_facing_names(name: str) -> None:
    # An arm's ``dataset:`` names a REGISTERED dataset, checked at config load,
    # so registration is what makes the arms below constructible at all.
    built = dataset_registry.build(name)
    assert isinstance(built, Dataset)
    assert built.name == name


def test_revisions_are_the_pinned_hf_commits() -> None:
    # A loader's revision is the corpus pin itself (not a ``<framing>+<pin>``
    # composite): these mint from raw release rows, they do not wrap another
    # registered dataset.
    assert SweBenchVerifiedLocDataset().revision == SWE_BENCH_VERIFIED_PIN.revision
    assert LcaBugLocDataset().revision == LCA_BUG_LOC_PIN.revision
    assert len(SWE_BENCH_VERIFIED_PIN.revision) == 40
    assert len(LCA_BUG_LOC_PIN.revision) == 40


def test_the_lca_pin_selects_the_python_slice_by_file_path() -> None:
    # The language filter IS the parquet path — the Python slice is a separate
    # file, so there is no row-level filter that could be forgotten. Java and
    # Kotlin are deliberately absent (ADR 0021 T1 gates their extensions).
    assert LCA_BUG_LOC_PIN.files == ("py/test-00000-of-00001.parquet",)
    assert not any("java" in name or "/kt" in name for name in LCA_BUG_LOC_PIN.files)


def test_the_recorded_row_counts_are_the_enforced_pin_field() -> None:
    # Asserting the constants against their own literals is a tautology — it
    # cannot fail for the reason its name gives. What makes the count a GUARD is
    # that it rides on the pin, where ``read_parquet_rows`` compares it against
    # the shard it just read (pinned in tests/datasets/test_parquet_edge.py).
    assert SWE_BENCH_VERIFIED_PIN.expected_rows == SWE_BENCH_VERIFIED_INSTANCES == 500
    assert LCA_BUG_LOC_PIN.expected_rows == LCA_BUG_LOC_INSTANCES == 50


async def test_a_total_wipeout_is_reported_loudly_rather_than_at_info(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    # An upstream schema change makes every row undecodable. At the default
    # WARNING level an INFO summary is invisible, so the sweep would look like
    # it merely found no tasks — the exact way a json-only changed_files reader
    # scored an empty lca-bug-loc corpus in silence.
    fixture = tmp_path / "all_broken.jsonl"
    fixture.write_text(
        '{"text_id": "a/b/1/1", "repo_owner": "a", "repo_name": "b", "base_sha": "0", '
        '"changed_files": "<not a list>"}\n',
        encoding="utf-8",
    )
    dataset = LcaBugLocDataset(fixture_path=fixture, repo_cache=_FakeRepoCache())
    with caplog.at_level(logging.INFO):
        tasks = [task async for task in dataset.tasks()]
    assert tasks == []
    wipeout = [r for r in caplog.records if "yielded NO tasks" in r.getMessage()]
    assert [r.levelno for r in wipeout] == [logging.ERROR]


# --- Task minting ---------------------------------------------------------


async def test_swe_bench_mints_three_part_ids_with_file_set_gold() -> None:
    tasks = [t async for t in _swe().tasks()]
    # 5 fixture rows; the tests-only patch and the empty patch drop out.
    assert len(tasks) == 3
    first = tasks[0]
    assert first.task_id == "swe-bench-verified-loc/bug_loc/astropy__astropy-12907"
    assert first.record_id == "astropy__astropy-12907"
    assert first.gold.file_set == ("astropy/modeling/separable.py",)
    assert first.metadata["repo"] == "astropy/astropy"
    assert first.metadata["difficulty"] == "15 min - 1 hour"


async def test_lca_mints_three_part_ids_over_a_slash_bearing_record_id() -> None:
    tasks = [t async for t in _lca().tasks()]
    assert len(tasks) == 3  # 4 fixture rows, 1 with malformed changed_files
    first = tasks[0]
    # record_id carries separators; only the LAST segment may, and it does.
    assert first.record_id == "thealgorithms/python/295/289"
    assert first.task_id == "lca-bug-loc/bug_loc/thealgorithms/python/295/289"
    # A gold path WITH SPACES survives the decode untouched.
    assert first.gold.file_set == ("Project Euler/Problem 01/sol2.py",)
    assert first.metadata["repo_license"] == "MIT License"


@pytest.mark.parametrize("build", [_swe, _lca])
async def test_every_task_id_carries_the_product_enumerated_task_name(build) -> None:
    from pydocs_mcp.harness.platform.skill_artifact import TASK_NAMES

    from pydocs_eval.datasets.task_ids import parse_framed_task_id

    assert BUG_LOC_TASK_NAME in TASK_NAMES
    async for task in build().tasks():
        parsed = parse_framed_task_id(task.task_id, task_names=TASK_NAMES)
        assert parsed is not None
        assert parsed.task_name == BUG_LOC_TASK_NAME


@pytest.mark.parametrize("build", [_swe, _lca])
async def test_gold_never_carries_an_ast_body_or_a_resolved_set(build) -> None:
    # ``metrics/_relevance`` dispatches on ast_body first and a resolved chunk
    # set second; either riding along would score a FILE-level corpus as if its
    # gold were a function body or a chunk-id set.
    async for task in build().tasks():
        assert task.gold.ast_body is None
        assert "resolved_chunk_ids" not in task.gold.extra
        assert task.gold.file_set


async def test_the_swe_bench_query_is_the_issue_text_only() -> None:
    # ``test_patch`` names the gold file's own test module and ``hints_text``
    # is PR-review commentary: both are direct answer leaks. They are never
    # even READ (see _SWE_BENCH_COLUMNS), so they cannot reach the query.
    tasks = [t async for t in _swe().tasks()]
    assert tasks[0].query.startswith("Modeling's `separability_matrix`")
    for task in tasks:
        assert "test_patch" not in task.query
        assert "diff --git" not in task.query


async def test_the_lca_query_concatenates_title_and_body() -> None:
    tasks = [t async for t in _lca().tasks()]
    assert tasks[0].query == (
        "sol2.py in Problem 01 returns the wrong sum\n\n"
        "The Project Euler problem 1 solution over-counts multiples of 15."
    )


# --- Gold filtering -------------------------------------------------------


async def test_swe_bench_multi_file_gold_keeps_every_touched_file() -> None:
    tasks = {t.record_id: t async for t in _swe().tasks()}
    assert tasks["django__django-11099"].gold.file_set == (
        "django/contrib/auth/validators.py",
        "docs/releases/2.2.1.txt",
    )
    assert tasks["django__django-11099"].metadata["gold_file_count"] == "2"


async def test_a_patch_touching_tests_drops_only_the_test_file() -> None:
    tasks = {t.record_id: t async for t in _swe().tasks()}
    assert tasks["pytest-dev__pytest-5227"].gold.file_set == ("src/_pytest/logging.py",)


async def test_lca_changed_files_drops_the_test_module() -> None:
    tasks = {t.record_id: t async for t in _lca().tasks()}
    assert tasks["streamlit/streamlit/501/498"].gold.file_set == ("lib/streamlit/caching.py",)


@pytest.mark.parametrize(
    ("build", "dropped_ids"),
    [
        (_swe, {"fixture__tests-only-1", "fixture__empty-patch-1"}),
        (_lca, {"example/broken/1/1"}),
    ],
)
async def test_records_with_underivable_gold_are_dropped_and_counted(
    build, dropped_ids: set[str], caplog: pytest.LogCaptureFixture
) -> None:
    # No-silent-caps: an unusable record never becomes a vacuous-gold task, and
    # the operator sees BOTH counts plus a per-record reason.
    with caplog.at_level(logging.INFO):
        record_ids = {task.record_id async for task in build().tasks()}
    assert record_ids.isdisjoint(dropped_ids)
    messages = [record.getMessage() for record in caplog.records]
    assert any("dropped" in message and "yielded" in message for message in messages)
    for dropped in dropped_ids:
        assert any(dropped in message for message in messages)


# --- Corpus materialization ----------------------------------------------


async def test_corpus_source_checks_out_the_pinned_commit_per_task() -> None:
    cache = _FakeRepoCache()
    tasks = [t async for t in _swe(cache).tasks()]
    corpus_dir = tasks[0].corpus_source()
    assert cache.requested == [
        (
            "https://github.com/astropy/astropy.git",
            "d16bfe05a744909de4b27f5875fe0d4ed41ce607",
        )
    ]
    assert (corpus_dir / "astropy/modeling/separable.py").exists()


async def test_the_materialized_corpus_reaches_non_python_gold_files() -> None:
    # THE reason bug_loc widens the corpus scope: ``docs/releases/2.2.1.txt`` is
    # a real gold path in the multi-file fixture row, and a ``.py``-only corpus
    # would make it unretrievable — a guaranteed miss measuring the corpus
    # builder rather than the retriever.
    tasks = [t async for t in _lca().tasks()]
    corpus_dir = tasks[0].corpus_source()
    assert (corpus_dir / "docs/releases/2.2.1.txt").exists()
    assert (corpus_dir / "Project Euler/Problem 01/sol2.py").exists()
    # Still scoped: an extension outside the set does not ride along.
    assert not (corpus_dir / "logo.svg").exists()


def test_the_corpus_scope_mirrors_the_products_default_indexable_set() -> None:
    # Pinned rather than imported: this package must stay importable without
    # the product installed, so the mirroring is asserted where pydocs_mcp IS
    # available instead of creating a hard dependency in the loader.
    from pydocs_mcp.extraction.config import DiscoveryScopeConfig

    product_default = set(DiscoveryScopeConfig().include_extensions)
    assert {glob.removeprefix("*") for glob in CORPUS_GLOBS} == product_default


def test_the_default_corpus_scope_is_unchanged_for_every_other_loader() -> None:
    # The widened scope is opt-in per dataset: swe-qa / swe-qa-pro /
    # crosscommitvuln keep materializing byte-identical Python-only corpora, so
    # their recorded baselines stay comparable.
    from pydocs_eval.datasets._repo_cache import DEFAULT_CORPUS_GLOBS

    assert DEFAULT_CORPUS_GLOBS == ("*.py",)
