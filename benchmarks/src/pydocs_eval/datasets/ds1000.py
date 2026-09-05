"""DS-1000 (CodeRAG-Bench flavor) dataset loader.

DS-1000 (Lai et al., ICML 2023; arXiv:2211.11501) packaged inside the
CodeRAG-Bench benchmark (Wang et al., 2024; arXiv:2406.14497). 1,000
StackOverflow-derived data-science problems across 7 Python libraries,
each annotated with the canonical documentation chunks (``docs`` field of
``{function, text, title}`` on the real HF rows; the flat CI fixtures use the
legacy ``{doc_id, doc_content}``) that answer the problem.

The raw HF row exposes:
- ``prompt``: NL problem statement, then the literal ``A:``, then the
  canonical solution wrapped in ``<code>...</code>`` (sometimes with
  ``BEGIN SOLUTION`` / ``END SOLUTION`` markers).
- ``library``: title-case library name (``"Pandas"``, ``"Numpy"``,
  ``"Matplotlib"``, ``"Sklearn"``, ``"Scipy"``, ``"Tensorflow"``,
  ``"Pytorch"``). On the real CodeRAG-Bench rows this is nested inside
  the ``metadata`` repr-dict string rather than exposed top-level, so
  the loader resolves it via ``_resolve_library`` (top-level wins, else
  parse ``metadata``).
- ``perturbation_type``: one of ``"Origin"`` / ``"Surface"`` /
  ``"Semantic"`` / ``"Difficult-Rewrite"`` (DS-1000 paper §3.2). Like
  ``library``, it is nested inside ``metadata`` on the real rows and lifted
  by ``_lift_metadata_fields``.
- ``docs``: list of the manually-verified gold doc citations. The real HF
  entries are dicts keyed ``{function, text, title}`` (``title`` = canonical
  doc identifier, ``text`` = doc prose); the flat CI fixtures use the legacy
  ``{doc_id, doc_content}``. ``_gold_doc_fields`` reads either shape.

This loader strips the canonical solution off the prompt (so retrieval
systems see only the NL question), preserves the raw prompt under
``metadata["_raw_query"]`` for secondary runs, normalizes the
title-case library name to PyPI canonical (so downstream pydocs lookups
agree on package names), and exposes both ``doc_ids`` and
``doc_contents`` on the gold so exact-match (oracle) and fuzzy-match
(native) ground-truth resolvers can each pick the form they need.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ._split import _DEFAULT_SMALL_TEST_SIZE, stratified_split, validate_split
from .base_dataset import EvalTask, GoldAnswer
from .ds1000_schema import (
    PINNED_DS1000_REVISION,
    _gold_doc_fields,
    _has_gold,
    _lift_metadata_fields,
    _split_sort_key,
    _strip_query,
    to_pypi_canonical,
)


@dataset_registry.register("ds1000")
@dataclass
class Ds1000Dataset:
    """DS-1000 / CodeRAG-Bench (Apache-2.0)."""

    name: str = "ds1000"
    revision: str = PINNED_DS1000_REVISION
    fixture_path: Path | None = None
    library_filter: tuple[str, ...] = ()
    perturbation_filter: tuple[str, ...] = ()
    # WHY: CodeRAG-Bench queries DS-1000 retrieval with the FULL ``prompt`` (NL
    # problem + code stub), unstripped. Our default strips the canonical
    # solution so retrieval sees only the NL question. Opt out via
    # ``strip_query=False`` to feed the verbatim prompt (the canonical
    # CodeRAG-Bench query). Default ``True`` PRESERVES the existing behavior.
    strip_query: bool = True
    # WHY: stratified-by-library dev/test split so each slice keeps the
    # 7-library proportions of the full corpus; seeded so the partition is
    # reproducible across runs. Default ``"all"`` is the whole filtered
    # corpus (no partition) — strict backward-compat for existing usage and
    # the CI fixture, which rely on getting every task.
    split: str = "all"
    dev_fraction: float = 0.2
    split_seed: int = 0
    # Target size for BOTH small splits: ``small_test`` (fixed-size
    # stratified subsample of the held-out ``test`` tail) and ``small_dev``
    # (its mirror on the ``dev`` head — the burn-free iteration slice; see
    # benchmarks/README.md §"Sweep protocol"). Default from the shared
    # split helper (single source of truth).
    small_test_size: int = _DEFAULT_SMALL_TEST_SIZE
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/ds1000").expanduser(),
    )
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        # A bad ``split`` is a caller bug — fail loud at construction rather
        # than silently yielding the wrong slice deep in the async loop.
        validate_split(self.split)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            if self.fixture_path is not None:
                self._rows_cache = self._load_from_fixture()
            else:
                # WHY: `datasets.load_dataset` is sync + does network I/O.
                # The runner loop is async (CLAUDE.md §"Async Patterns") —
                # offload to a worker thread so the multi-MB download
                # doesn't block the event loop.
                self._rows_cache = await asyncio.to_thread(self._load_from_hf)
        for row in self._rows_cache:
            task = _row_to_task(row, strip_query=self.strip_query)
            if task is None:
                continue
            yield task

    def _load_from_fixture(self) -> list[dict[str, Any]]:
        assert self.fixture_path is not None
        with self.fixture_path.open() as fh:
            data = json.load(fh)
        return self._apply_filters(data)

    def _load_from_hf(self) -> list[dict[str, Any]]:
        # WHY: `datasets` is a heavy optional dep that the fixture path
        # doesn't need. Import lazily so tests / hermetic runs that pass
        # `fixture_path=` never trip on a missing wheel.
        import datasets as hf_datasets

        ds = hf_datasets.load_dataset(
            "code-rag-bench/ds1000",
            revision=self.revision,
            split="train",
            cache_dir=str(self.cache_dir) if self.cache_dir else None,
        )
        rows = [dict(row) for row in ds]
        return self._apply_filters(rows)

    def _apply_filters(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Lift the nested ``metadata`` fields at load (single normalization
        # point): the real HF rows nest ``library`` / ``perturbation_type`` /
        # ``perturbation_origin_id`` inside the ``metadata`` struct with NO
        # top-level field, so the library + perturbation filters, the split
        # stratification, and the ``_row_to_task`` task id (all keyed off the
        # top-level reads) would otherwise see ``""`` for every HF row — zero
        # filter matches, a degenerate single-stratum split, and colliding
        # task ids. The lift prefers a present top-level value (flat CI
        # fixtures) and copies from ``metadata`` otherwise, so the downstream
        # ``row.get(...)`` reads work UNCHANGED for both shapes.
        for row in rows:
            _lift_metadata_fields(row)
        # Filters compare against the NORMALIZED (lowercase canonical)
        # library name, so callers don't have to know the upstream
        # title-case form. Empty filter = keep all.
        #
        # WHY normalize the FILTER too: the row library is already run
        # through ``to_pypi_canonical`` (title-case -> PyPI-canonical), so a
        # filter value must go through the same canonicalization or the two
        # sides can't match. DS-1000's raw library field is title-case, so an
        # operator passing the casing they SEE (``--dataset-library-filter
        # Pandas``) would otherwise silently match zero rows. Build the
        # normalized filter set once so ``"Pandas"`` / ``"pandas"`` /
        # ``"PANDAS"`` and ``"Sklearn"`` / ``"scikit-learn"`` all behave
        # identically.
        normalized_filter = (
            {to_pypi_canonical(x) for x in self.library_filter} if self.library_filter else None
        )
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if normalized_filter is not None:
                normalized = to_pypi_canonical(row.get("library", ""))
                if normalized not in normalized_filter:
                    continue
            if self.perturbation_filter:
                if row.get("perturbation_type") not in self.perturbation_filter:
                    continue
            # Gold-bearing restriction: DS-1000 has many doc-less pure-codegen
            # problems with no retrieval target. A retrieval-recall benchmark
            # can only score rows that HAVE a gold citation — a doc-less row
            # is recall-0 by construction and would silently drag the metric
            # down. Drop empty-gold rows here so the split (below) stratifies
            # over scoreable rows only.
            if not _has_gold(row):
                continue
            filtered.append(row)
        # Partition AFTER the library/perturbation/gold filters so the split
        # slices the already-narrowed corpus (the split composes with the
        # filters, never the raw HF rows). Stratify by the normalized
        # (PyPI-canonical) library so each slice keeps the corpus's
        # per-library proportions; the shared helper owns the determinism
        # contract (see ``_split.stratified_split``).
        return stratified_split(
            filtered,
            split=self.split,
            dev_fraction=self.dev_fraction,
            seed=self.split_seed,
            small_test_size=self.small_test_size,
            stratum_of=lambda r: to_pypi_canonical(r.get("library", "")),
            sort_key=_split_sort_key,
        )


def _row_to_task(row: dict[str, Any], *, strip_query: bool = True) -> EvalTask | None:
    raw_prompt = row.get("prompt", "")
    if not raw_prompt:
        return None
    # WHY: ``strip_query=False`` feeds the verbatim prompt (NL problem + code
    # stub) — the canonical CodeRAG-Bench query. The default strips the
    # canonical solution so retrieval sees only the NL question.
    query = _strip_query(raw_prompt) if strip_query else raw_prompt
    library_raw = row.get("library", "")
    library = to_pypi_canonical(library_raw)
    perturbation = row.get("perturbation_type", "")
    origin_id = row.get("perturbation_origin_id", "")
    doc_ids, doc_contents = _gold_doc_fields(row.get("docs", []))
    # WHY: `<library>:<perturbation>:<origin_id>` is the canonical
    # DS-1000 task identifier — same problem under different
    # perturbations shares an origin_id but differs on perturbation_type.
    task_id = f"ds1000/{library}/{perturbation}/{origin_id}"
    return EvalTask(
        task_id=task_id,
        query=query,
        gold=GoldAnswer(
            extra={
                "doc_ids": doc_ids,
                "doc_contents": doc_contents,
            },
        ),
        # WHY: no per-task corpus materialization — DS-1000 evaluates
        # against the operator-prepared reference project (Task 6) or
        # against the library-documentation HF dataset (oracle mode).
        # The corpus lives outside the task. A no-op factory keeps the
        # `EvalTask` shape stable; the runner ignores it for DS-1000.
        corpus_source=_noop_corpus_source,
        metadata={
            "library": library,
            "library_raw": library_raw,
            "perturbation_type": perturbation,
            "perturbation_origin_id": str(origin_id),
            "_raw_query": raw_prompt,
        },
    )


def _noop_corpus_source() -> Path:
    # WHY: DS-1000's corpus is operator-prepared (reference project /
    # library-documentation HF dataset), not per-task. The `EvalTask`
    # Protocol requires a `corpus_source` callable; this stub satisfies
    # the type at zero cost — the runner skips it for DS-1000.
    return Path("/dev/null")
