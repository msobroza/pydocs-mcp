"""DS-1000 (CodeRAG-Bench flavor) dataset loader.

DS-1000 (Lai et al., ICML 2023; arXiv:2211.11501) packaged inside the
CodeRAG-Bench benchmark (Wang et al., 2024; arXiv:2406.14497). 1,000
StackOverflow-derived data-science problems across 7 Python libraries,
each annotated with the canonical documentation chunks (``docs`` field
of ``{doc_id, doc_content}``) that answer the problem.

The raw HF row exposes:
- ``prompt``: NL problem statement, then the literal ``A:``, then the
  canonical solution wrapped in ``<code>...</code>`` (sometimes with
  ``BEGIN SOLUTION`` / ``END SOLUTION`` markers).
- ``library``: title-case library name (``"Pandas"``, ``"Numpy"``,
  ``"Matplotlib"``, ``"Sklearn"``, ``"Scipy"``, ``"Tensorflow"``,
  ``"Pytorch"``).
- ``perturbation_type``: one of ``"Origin"`` / ``"Surface"`` /
  ``"Semantic"`` / ``"Difficult-Rewrite"`` (DS-1000 paper §3.2).
- ``docs``: list of ``{"doc_id": str, "doc_content": str}`` — the
  manually-verified gold doc citations.

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
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..serialization import dataset_registry
from ._split import stratified_split, validate_split
from .base_dataset import EvalTask, GoldAnswer

# TODO: pin to SHA once HF network access verified. The HF API + the
# `datasets` loader were both 403/blocked at write time; downstream tests
# use the fixture path so this is non-blocking for green. Bump these by
# running `huggingface-cli download code-rag-bench/<repo> --revision main
# --repo-type dataset` and reading the resolved SHA from the output.
_PINNED_DS1000_REVISION = "main"
_PINNED_LIBDOCS_REVISION = "main"

# DS-1000's ``library`` field is title-case; pydocs / PyPI use lowercase
# canonical names. The map covers every value observed in the 7-library
# DS-1000 corpus plus a couple of defensive aliases (``"PyTorch"`` /
# ``"Scikit-learn"``) in case the upstream casing drifts. Picks PyPI
# names (NOT import names) so the result matches ``Package.name`` in
# pydocs's store: ``"scikit-learn"`` (not ``"sklearn"``) and ``"torch"``
# (not ``"pytorch"``).
_LIBRARY_NORMALIZATION: dict[str, str] = {
    "Pandas": "pandas",
    "Numpy": "numpy",
    "Matplotlib": "matplotlib",
    "Sklearn": "scikit-learn",
    "Scikit-learn": "scikit-learn",
    "Scipy": "scipy",
    "Tensorflow": "tensorflow",
    "TensorFlow": "tensorflow",
    "Pytorch": "torch",
    "PyTorch": "torch",
}


def _normalize_library(raw: str) -> str:
    """Map a DS-1000 title-case ``library`` value to its PyPI-canonical
    lowercase name. Unknown values fall back to ``raw.lower()`` so the
    normalization is total (never raises) and the fallback semantics live
    in exactly one place."""
    return _LIBRARY_NORMALIZATION.get(raw, raw.lower())


def _split_sort_key(row: dict[str, Any]) -> str:
    """A deterministic, position-independent sort key for the dev/test
    split's per-library shuffle.

    Uses the gold ``doc_id``\\ s joined with ``"|"`` (stable across runs,
    unique per problem), falling back to the raw ``prompt`` when a row has
    no gold docs. Deliberately NOT the row's list index — the key must be
    stable regardless of where the row lands in the loaded list so the
    seeded shuffle yields the same partition across runs and load paths."""
    docs = row.get("docs", []) or []
    doc_ids = [str(d.get("doc_id", "")) for d in docs]
    joined = "|".join(doc_ids)
    return joined if joined else str(row.get("prompt", ""))

# Markers inside the canonical-solution block to strip from the NL
# question after the ``A:`` split.
_SOLUTION_MARKERS: tuple[str, ...] = (
    "<code>",
    "</code>",
    "BEGIN SOLUTION",
    "END SOLUTION",
)

# DS-1000's answer delimiter is a LINE-LEADING ``A:`` (prompt format is
# ``Problem:\n<NL question>\nA:\n<code>``). Anchor on the line-leading
# marker only — a bare ``A:`` substring also matches in-body NL labels
# like ``"DataFrame A:"`` / ``"matrix A:"`` and would amputate the
# question. ``(?m)`` makes ``^``/``$`` match per-line.
_ANSWER_DELIMITER = re.compile(r"(?m)^A:\s*$")


@dataset_registry.register("ds1000")
@dataclass
class Ds1000Dataset:
    """DS-1000 / CodeRAG-Bench (Apache-2.0)."""

    name: str = "ds1000"
    revision: str = _PINNED_DS1000_REVISION
    fixture_path: Path | None = None
    library_filter: tuple[str, ...] = ()
    perturbation_filter: tuple[str, ...] = ()
    # WHY: stratified-by-library dev/test split so each slice keeps the
    # 7-library proportions of the full corpus; seeded so the partition is
    # reproducible across runs. Default ``"all"`` is the whole filtered
    # corpus (no partition) — strict backward-compat for existing usage and
    # the CI fixture, which rely on getting every task.
    split: str = "all"
    dev_fraction: float = 0.2
    split_seed: int = 0
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/ds1000").expanduser(),
    )
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None, init=False, repr=False,
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
            task = _row_to_task(row)
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
        # Filters compare against the NORMALIZED (lowercase canonical)
        # library name, so callers don't have to know the upstream
        # title-case form. Empty filter = keep all.
        #
        # WHY normalize the FILTER too: the row library is already run
        # through ``_normalize_library`` (title-case -> PyPI-canonical), so a
        # filter value must go through the same canonicalization or the two
        # sides can't match. DS-1000's raw library field is title-case, so an
        # operator passing the casing they SEE (``--dataset-library-filter
        # Pandas``) would otherwise silently match zero rows. Build the
        # normalized filter set once so ``"Pandas"`` / ``"pandas"`` /
        # ``"PANDAS"`` and ``"Sklearn"`` / ``"scikit-learn"`` all behave
        # identically.
        normalized_filter = (
            {_normalize_library(x) for x in self.library_filter}
            if self.library_filter
            else None
        )
        filtered: list[dict[str, Any]] = []
        for row in rows:
            if normalized_filter is not None:
                normalized = _normalize_library(row.get("library", ""))
                if normalized not in normalized_filter:
                    continue
            if self.perturbation_filter:
                if row.get("perturbation_type") not in self.perturbation_filter:
                    continue
            filtered.append(row)
        # Partition AFTER the library/perturbation filters so the split
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
            stratum_of=lambda r: _normalize_library(r.get("library", "")),
            sort_key=_split_sort_key,
        )


def _row_to_task(row: dict[str, Any]) -> EvalTask | None:
    raw_prompt = row.get("prompt", "")
    if not raw_prompt:
        return None
    query = _strip_query(raw_prompt)
    library_raw = row.get("library", "")
    library = _normalize_library(library_raw)
    perturbation = row.get("perturbation_type", "")
    origin_id = row.get("perturbation_origin_id", "")
    docs = row.get("docs", []) or []
    doc_ids = tuple(str(d.get("doc_id", "")) for d in docs)
    doc_contents = tuple(str(d.get("doc_content", "")) for d in docs)
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


def _strip_query(prompt: str) -> str:
    """Drop the canonical-solution block from a DS-1000 prompt.

    DS-1000's prompts embed the gold answer after a LINE-LEADING ``A:``
    delimiter (the format is ``Problem:\\n<NL question>\\nA:\\n<code>``),
    typically wrapped in ``<code>...</code>`` and sometimes flanked by
    ``BEGIN SOLUTION`` / ``END SOLUTION`` markers. Retrieval systems must
    NOT see the solution — they're scored on whether they can surface the
    relevant docs from the NL question alone. We split at the FIRST
    line-leading ``A:`` (``_ANSWER_DELIMITER``), then remove any leftover
    code/solution markers and collapse the surrounding whitespace.

    WHY line-leading, not a bare substring: real DS-1000 question bodies
    reference in-body labels like ``"DataFrame A:"`` / ``"matrix A:"`` /
    ``"column A:"``. A bare ``prompt.split("A:")`` would cut at the first
    such in-body mention and amputate the actual question. Anchoring on
    ``^A:\\s*$`` matches only the real answer delimiter; an in-body
    ``A:`` never triggers the cut. If no line-leading ``A:`` exists,
    ``split`` returns the whole prompt unchanged — correct, since then the
    entire prompt (minus markers) is the query.
    """
    body = _ANSWER_DELIMITER.split(prompt, maxsplit=1)[0]
    for marker in _SOLUTION_MARKERS:
        body = body.replace(marker, "")
    # Collapse runs of blank lines / trailing whitespace introduced by
    # the marker removal. `\s+` is over-broad (would collapse internal
    # spacing); restrict to vertical-whitespace runs of 2+.
    body = re.sub(r"\n{2,}", "\n\n", body)
    return body.strip()


def _noop_corpus_source() -> Path:
    # WHY: DS-1000's corpus is operator-prepared (reference project /
    # library-documentation HF dataset), not per-task. The `EvalTask`
    # Protocol requires a `corpus_source` callable; this stub satisfies
    # the type at zero cost — the runner skips it for DS-1000.
    return Path("/dev/null")
