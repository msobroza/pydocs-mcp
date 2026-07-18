"""Pin the two DS-1000 runner knobs added alongside the AppConfig overlays:

  - ``--dataset-library-filter`` -> ``Ds1000Dataset.library_filter`` (passed
    as a build kwarg ONLY when set, so RepoQA — which doesn't accept it — is
    unaffected when the flag is absent).
  - ``--corpus-dir`` -> ``run_sweep(corpus_dir=...)`` overrides each task's
    ``corpus_source()`` for the whole sweep AND skips the ``shutil.rmtree``
    teardown (an operator-supplied dir is never deleted).

The library-filter half drives ``Ds1000Dataset`` directly (the same object
``dataset_registry.build("ds1000", library_filter=...)`` constructs) plus the
``main()`` kwarg-plumbing branch. The ``--corpus-dir`` half is a hermetic
``run_sweep`` call with a FAKE system (records the path it received in
``index()``) + a FAKE 1-task dataset, both registered ad-hoc so the global
registries stay clean — mirrors ``test_runner_smoke.py``'s fakes.
"""

from __future__ import annotations

import argparse
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.ds1000 import Ds1000Dataset
from pydocs_eval.runner import _build_arg_parser, run_sweep
from pydocs_eval.registries import dataset_registry, system_registry
from pydocs_eval.systems.base_system import RetrievedItem

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "ds1000_mini.json"


# ── --dataset-library-filter -> Ds1000Dataset.library_filter ────────────────


async def test_library_filter_reduces_task_count_and_retains_only_pandas() -> None:
    """The mini fixture ships 8 rows (3 pandas + 2 numpy + 1 each of
    matplotlib/sklearn/scipy). ``library_filter=("pandas",)`` slices to the 3
    pandas rows and drops everything else — the same slice the runner's
    ``dataset_registry.build("ds1000", library_filter=("pandas",))`` produces.
    """
    unfiltered = Ds1000Dataset(fixture_path=_FIXTURE)
    all_tasks = [t async for t in unfiltered.tasks()]
    assert len(all_tasks) == 8  # fixture sanity check

    filtered = Ds1000Dataset(
        fixture_path=_FIXTURE,
        library_filter=("pandas",),
    )
    pandas_tasks = [t async for t in filtered.tasks()]
    assert len(pandas_tasks) == 3
    assert len(pandas_tasks) < len(all_tasks)
    assert all(t.metadata["library"] == "pandas" for t in pandas_tasks)


async def test_library_filter_via_registry_build_kwarg() -> None:
    """The runner reaches the filter through
    ``dataset_registry.build("ds1000", **dataset_kwargs)`` — the same path
    ``main()`` plumbs ``--dataset-library-filter`` into. Build it that way and
    confirm the kwarg lands on ``library_filter``.
    """
    dataset = dataset_registry.build(
        "ds1000",
        fixture_path=_FIXTURE,
        library_filter=("numpy",),
    )
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 2
    assert all(t.metadata["library"] == "numpy" for t in tasks)


def _parse(argv: list[str]) -> argparse.Namespace:
    return _build_arg_parser().parse_args(argv)


def test_cli_flag_absent_means_no_library_filter_kwarg() -> None:
    """REGRESSION: when ``--dataset-library-filter`` is omitted the parsed
    value is ``None`` so ``main()`` adds NO ``library_filter`` kwarg — RepoQA's
    constructor (which doesn't accept it) is unaffected. Pin the parse default
    + the conditional that gates the kwarg.
    """
    args = _parse(["--configs", "x.yaml"])
    assert args.dataset_library_filter is None
    # Mirror main()'s gating branch: a None flag contributes nothing.
    dataset_kwargs: dict[str, object] = {}
    if args.dataset_library_filter is not None:  # pragma: no cover -- None here
        dataset_kwargs["library_filter"] = args.dataset_library_filter
    assert "library_filter" not in dataset_kwargs


def test_cli_flag_present_parses_comma_list() -> None:
    args = _parse(
        ["--configs", "x.yaml", "--dataset-library-filter", "pandas,numpy"],
    )
    assert args.dataset_library_filter == "pandas,numpy"


# ── --corpus-dir resolution + fast-fail ─────────────────────────────────────


def test_corpus_dir_resolved_to_absolute_path(tmp_path: Path) -> None:
    """``--corpus-dir`` is resolved to an absolute Path at parse time, so a
    relative value isn't cwd-dependent downstream."""
    args = _parse(["--configs", "x.yaml", "--corpus-dir", str(tmp_path)])
    assert args.corpus_dir == tmp_path.resolve()
    assert args.corpus_dir.is_absolute()


async def test_corpus_dir_missing_dir_fast_fails(tmp_path: Path) -> None:
    """A non-existent ``corpus_dir`` makes ``run_sweep`` raise
    ``NotADirectoryError`` BEFORE launching any leg — the check lives in
    ONE place (the sweep) so programmatic callers get the same fast-fail
    the CLI used to implement separately via ``parser.error``."""
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    bad_dir = tmp_path / "does-not-exist"
    with pytest.raises(NotADirectoryError, match="does-not-exist"):
        await run_sweep(
            systems=(),
            config_paths=(overlay,),
            dataset_name="ds1000",
            dataset_kwargs={"fixture_path": _FIXTURE},
            corpus_dir=bad_dir,
        )


# ── --corpus-dir override + rmtree guard ────────────────────────────────────


@dataclass
class _CorpusRecordingSystem:
    """Fake system that records the corpus path ``index()`` is handed and
    returns one fixed retrieved item so the scorer has something to score.
    Registered ad-hoc; never reaches the global registry namespace.
    """

    name: str = "corpus-recorder"
    received_dirs: list[Path] = field(default_factory=list, init=False)

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        self.received_dirs.append(corpus_dir)

    async def search(
        self,
        query: str,
        limit: int,
    ) -> tuple[RetrievedItem, ...]:
        return (RetrievedItem(rank=1, text="x", source_path="s"),)

    async def teardown(self) -> None:
        return None


@dataclass
class _OneTaskDataset:
    """Fake 1-task dataset whose ``corpus_source`` would hand back a path the
    runner must NOT use when ``--corpus-dir`` is set (and must NOT delete).
    """

    name: str = "fake-one-task"
    revision: str = "v0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        yield EvalTask(
            task_id="t0",
            query="q",
            gold=GoldAnswer(ast_body="body"),
            # WHY: a sentinel the test would notice if the runner used it
            # instead of the operator's --corpus-dir.
            corpus_source=lambda: Path("/nonexistent-per-task-source"),
            metadata={},
        )


async def test_corpus_dir_overrides_source_and_survives_rmtree(
    tmp_path: Path,
) -> None:
    """With ``corpus_dir`` set, ``run_sweep`` (a) hands that exact path to
    ``system.index`` instead of ``task.corpus_source()``, and (b) leaves the
    dir intact after the sweep — an operator-supplied dir is never rmtree'd.
    """
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")  # empty overlay -> shipped defaults underneath
    jsonl_dir = tmp_path / "jsonl"

    # WHY: a REAL dir (mkdtemp, not a sentinel) so the rmtree-survival
    # assertion is meaningful — if the guard were missing the runner would
    # actually delete it.
    operator_dir = Path(tempfile.mkdtemp())
    recorder = _CorpusRecordingSystem()

    system_registry._items["corpus-recorder"] = lambda: recorder  # type: ignore[assignment]
    dataset_registry._items["fake-one-task"] = _OneTaskDataset  # type: ignore[assignment]
    try:
        _results, tasks_ran = await run_sweep(
            systems=("corpus-recorder",),
            config_paths=(overlay,),
            dataset_name="fake-one-task",
            tracker_names=("jsonl",),
            tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
            metric_specs=("recall@1",),
            corpus_dir=operator_dir,
        )
    finally:
        system_registry._items.pop("corpus-recorder", None)
        dataset_registry._items.pop("fake-one-task", None)

    try:
        assert tasks_ran == 1
        # (a) the override path reached index() — NOT the task's source.
        assert recorder.received_dirs == [operator_dir]
        # (b) operator-supplied dir is untouched after the sweep.
        assert operator_dir.exists()
    finally:
        operator_dir.rmdir()


async def test_no_corpus_dir_uses_per_task_source(tmp_path: Path) -> None:
    """REGRESSION: omitting ``corpus_dir`` keeps the per-task
    ``corpus_source()`` behavior — the fake's sentinel path reaches
    ``index()``. The runner rmtrees that per-task dir (ignore_errors), so a
    nonexistent sentinel is harmless. Pins the backward-compatible default
    so RepoQA / existing sweeps are unaffected by the new param.
    """
    overlay = tmp_path / "baseline.yaml"
    overlay.write_text("")
    jsonl_dir = tmp_path / "jsonl"
    recorder = _CorpusRecordingSystem()

    system_registry._items["corpus-recorder"] = lambda: recorder  # type: ignore[assignment]
    dataset_registry._items["fake-one-task"] = _OneTaskDataset  # type: ignore[assignment]
    try:
        await run_sweep(
            systems=("corpus-recorder",),
            config_paths=(overlay,),
            dataset_name="fake-one-task",
            tracker_names=("jsonl",),
            tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
            metric_specs=("recall@1",),
        )
    finally:
        system_registry._items.pop("corpus-recorder", None)
        dataset_registry._items.pop("fake-one-task", None)

    assert recorder.received_dirs == [Path("/nonexistent-per-task-source")]
