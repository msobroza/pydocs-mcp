"""run_sweep as a complete programmatic surface: dataset-kwarg gating,
bench-cache toggling, and corpus-dir fail-fast — no argparse mimicry, no
hidden global state a caller must know about."""

from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.eval import _bench_cache
from benchmarks.eval.runner import build_dataset_kwargs
from benchmarks.eval.sweep import run_sweep, run_sweep_detailed

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


# ── build_dataset_kwargs: the RepoQA-crashes-on-unknown-kwargs contract ──


def test_default_kwargs_are_empty() -> None:
    # EVERY kwarg must be absent by default — RepoQA's constructor rejects
    # unknown kwargs, so the gating (not the values) is the contract.
    assert build_dataset_kwargs() == {}


def test_fixture_path_added_only_when_set(tmp_path: Path) -> None:
    fixture = tmp_path / "f.json"
    assert build_dataset_kwargs(fixture_path=fixture) == {"fixture_path": fixture}


def test_library_filter_added_only_when_not_none() -> None:
    assert build_dataset_kwargs(library_filter=("pandas", "numpy")) == {
        "library_filter": ("pandas", "numpy")
    }
    assert "library_filter" not in build_dataset_kwargs()


def test_full_prompt_maps_to_strip_query_false() -> None:
    assert build_dataset_kwargs(full_prompt=True) == {"strip_query": False}
    assert "strip_query" not in build_dataset_kwargs()


def test_split_added_only_when_not_all() -> None:
    assert build_dataset_kwargs(split="dev") == {"split": "dev"}
    assert build_dataset_kwargs(split="all") == {}


# ── bench_cache parameter on run_sweep_detailed ──────────────────────────


def _overlay(tmp_path: Path) -> Path:
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    return overlay


async def test_bench_cache_param_toggles_module_state(tmp_path: Path) -> None:
    # ``systems=()`` → zero legs: the sweep returns immediately after its
    # setup phase, which is exactly where the toggle must fire.
    overlay = _overlay(tmp_path)
    prior = _bench_cache.is_enabled()
    common = dict(
        systems=(),
        config_paths=(overlay,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": _FIXTURE},
    )
    try:
        await run_sweep_detailed(**common, bench_cache=False)
        assert _bench_cache.is_enabled() is False
        await run_sweep_detailed(**common, bench_cache=True)
        assert _bench_cache.is_enabled() is True
        # None (the default) leaves the process-global untouched — the
        # contract existing direct callers and toggle-and-restore test
        # fixtures rely on.
        _bench_cache.set_enabled(False)
        await run_sweep_detailed(**common)
        assert _bench_cache.is_enabled() is False
    finally:
        _bench_cache.set_enabled(prior)


# ── corpus-dir fail-fast lives in the sweep, not argparse ────────────────


async def test_missing_corpus_dir_raises_not_a_directory(tmp_path: Path) -> None:
    overlay = _overlay(tmp_path)
    bad_dir = tmp_path / "does-not-exist"
    with pytest.raises(NotADirectoryError, match="does-not-exist"):
        await run_sweep(
            systems=(),
            config_paths=(overlay,),
            dataset_name="repoqa",
            dataset_kwargs={"fixture_path": _FIXTURE},
            corpus_dir=bad_dir,
        )
