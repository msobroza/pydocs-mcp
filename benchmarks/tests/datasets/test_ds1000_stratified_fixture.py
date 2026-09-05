"""Stratified 50-task DS-1000 CI fixture tests.

The fixture under test — ``fixtures/ds1000_50.json`` — is SYNTHETIC CI
data, NOT sampled from the real ``code-rag-bench/ds1000`` HF dataset
(which is network-blocked in the build sandbox). It exists purely as the
dry-run safety net for the end-to-end smoke tests, which run FAKE
retrieval systems and therefore never need real DS-1000 problem text.
Real evaluation runs use the HF loader path, not this fixture.

The rows are templated but shaped EXACTLY like ``ds1000_mini.json`` (raw
title-case ``library`` + ``prompt`` + ``perturbation_type`` +
``perturbation_origin_id`` + ``docs:[{doc_id, doc_content}]``), stratified
by the RAW title-case library field as
15 Pandas + 12 Numpy + 8 Matplotlib + 6 Sklearn + 5 Scipy +
2 Tensorflow + 2 Pytorch = 50. Every ``doc_id`` carries a ``synthetic``
token and is globally unique across the 50 rows. The assertions below
exercise the SAME loader (``Ds1000Dataset(fixture_path=...)``) the real
runs use, so any drift between this fixture's shape and the loader's
expectations fails here.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydocs_eval.datasets.ds1000 import Ds1000Dataset

# Mirror ``test_ds1000_dataset.py``'s fixture-finding: this test lives in
# ``benchmarks/tests/datasets/`` so ``Path(__file__).parents[1]`` is
# ``benchmarks/tests/`` and ``/ "fixtures"`` reaches the shared fixtures dir.
FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "ds1000_50.json"

# The post-normalization (PyPI-canonical) per-library task counts the
# loader must produce — note Sklearn -> scikit-learn and Pytorch -> torch
# via ``_normalize_library``. Derived from the raw title-case stratification
# 15 Pandas / 12 Numpy / 8 Matplotlib / 6 Sklearn / 5 Scipy /
# 2 Tensorflow / 2 Pytorch.
_EXPECTED_NORMALIZED_COUNTS = {
    "pandas": 15,
    "numpy": 12,
    "matplotlib": 8,
    "scikit-learn": 6,
    "scipy": 5,
    "tensorflow": 2,
    "torch": 2,
}


async def test_fixture_yields_fifty_tasks() -> None:
    """The synthetic CI fixture ships 50 rows -> 50 EvalTasks through the
    default (``split="all"``) loader path."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 50


async def test_per_normalized_library_counts() -> None:
    """Per-NORMALIZED-library task counts match the stratification, after
    the loader's ``_normalize_library`` remaps Sklearn -> scikit-learn and
    Pytorch -> torch."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    counts = Counter(t.metadata["library"] for t in tasks)
    assert dict(counts) == _EXPECTED_NORMALIZED_COUNTS


async def test_every_task_has_query_and_gold_doc_ids() -> None:
    """Every task exposes a non-empty stripped query and at least one gold
    ``doc_id`` (so the smoke tests' fake systems always have something to
    resolve against)."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    for task in tasks:
        assert task.query.strip(), f"empty query for {task.task_id}"
        doc_ids = task.gold.extra["doc_ids"]  # type: ignore[index]
        assert doc_ids, f"no gold doc_ids for {task.task_id}"
        assert all(d for d in doc_ids), f"empty doc_id in {task.task_id}"


async def test_gold_doc_ids_globally_unique() -> None:
    """Every gold ``doc_id`` is unique across all 50 tasks — the smoke
    tests rely on doc_ids being a stable, collision-free identity for
    exact-match resolution."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    all_doc_ids = [
        d
        for t in tasks
        for d in t.gold.extra["doc_ids"]  # type: ignore[index]
    ]
    assert len(all_doc_ids) == len(set(all_doc_ids)), "duplicate gold doc_id"
