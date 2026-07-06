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

import ast
import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..serialization import dataset_registry
from ._split import _DEFAULT_SMALL_TEST_SIZE, stratified_split, validate_split
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


# The perturbation fields the loader reads top-level but the real HF rows nest
# inside the ``metadata`` struct — lifted at load so every downstream
# ``row.get(<field>)`` works for BOTH shapes (see ``_lift_metadata_fields``).
# ``library`` is lifted separately via ``_resolve_library`` (which owns the
# top-level-vs-metadata precedence + the dict/str coercion); these two drive
# the perturbation filter and the canonical ``ds1000/<lib>/<pert>/<origin>``
# task id (without them every real row collapses to a colliding
# ``ds1000/<lib>//``).
_METADATA_LIFTED_FIELDS: tuple[str, ...] = (
    "perturbation_type",
    "perturbation_origin_id",
)


def _coerce_metadata(raw: object) -> dict[str, Any]:
    """Return a row's ``metadata`` as a dict.

    The live ``datasets`` loader deserializes the ``metadata`` struct column
    into a real Python dict — that is the production shape. A Python-repr dict
    STRING (e.g. ``"{'library': 'Numpy', ...}"``) is also accepted defensively
    for tooling / fixtures that surface ``metadata`` as raw repr text. Any
    other or unparseable payload yields ``{}`` so callers stay total (never
    raise)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _resolve_library(row: dict[str, Any]) -> str:
    """Resolve a row's raw (title-case) ``library`` value.

    The flat CI fixtures expose a top-level ``library`` directly; the real
    ``code-rag-bench/ds1000`` HF rows nest it inside ``metadata``. Prefer a
    present, non-empty top-level ``library`` (keeps the flat fixtures working);
    else read it out of the coerced ``metadata`` dict. Total / never raises:
    a missing field or unparseable ``metadata`` yields ``""``."""
    top_level = row.get("library", "")
    if top_level:
        return str(top_level)
    return str(_coerce_metadata(row.get("metadata")).get("library", ""))


def _lift_metadata_fields(row: dict[str, Any]) -> None:
    """Lift the nested ``metadata`` filter/split/task-id fields up to the top
    level, IN PLACE.

    Real ``code-rag-bench/ds1000`` rows expose only
    ``[code_context, docs, metadata, prompt, reference_code]`` — ``library``,
    ``perturbation_type`` and ``perturbation_origin_id`` all live inside the
    ``metadata`` struct. The loader reads those three top-level (library
    filter, stratification key, ``_row_to_task`` task id), so without this lift
    every real row reads ``""`` for all three: zero filter matches, a
    degenerate single-stratum split, and colliding ``ds1000/<lib>//`` task ids.
    A present, non-empty top-level value is PREFERRED (keeps the flat CI
    fixtures working); otherwise it is copied from ``metadata``."""
    # ``library``: top-level wins, else read from the coerced ``metadata``.
    resolved_library = _resolve_library(row)
    if resolved_library:
        row["library"] = resolved_library
    # ``perturbation_*``: copy from ``metadata`` only when the top-level value
    # is genuinely absent (``None`` / ``""``). The empty test is membership,
    # not truthiness, so a falsy-but-valid ``0`` origin id is NOT clobbered.
    meta = _coerce_metadata(row.get("metadata"))
    for key in _METADATA_LIFTED_FIELDS:
        if row.get(key) in (None, "") and meta.get(key) not in (None, ""):
            row[key] = meta[key]


def _has_gold(row: dict[str, Any]) -> bool:
    """Whether a row carries at least one gold doc citation.

    The ``docs`` field is a list of gold doc dicts (real HF:
    ``{function, text, title}``; flat fixtures: ``{doc_id, doc_content}``), but
    the HF rows may serialize it as a JSON STRING (``'[]'`` / ``'["..."]'``).
    Handle both: ``json.loads`` a string, use a list as-is. An empty list, a
    non-list payload, or a parse failure all count as no gold (gold-bearing is
    purely list-length here, so it stays schema-agnostic; never raises).
    """
    docs = row.get("docs")
    if isinstance(docs, str):
        try:
            docs = json.loads(docs)
        except (ValueError, TypeError):
            return False
    if not isinstance(docs, list):
        return False
    return len(docs) > 0


def _split_sort_key(row: dict[str, Any]) -> str:
    """A deterministic, position-independent sort key for the dev/test
    split's per-library shuffle.

    Uses the gold doc identifiers joined with ``"|"`` (stable across runs,
    unique per problem), falling back to the raw ``prompt`` when a row has
    no gold docs. Deliberately NOT the row's list index — the key must be
    stable regardless of where the row lands in the loaded list so the
    seeded shuffle yields the same partition across runs and load paths.

    Routes through ``_gold_doc_fields`` so the identifiers come from the real
    HF ``title`` key (or the fixtures' ``doc_id``) — reading the legacy
    ``doc_id`` directly would yield ``""`` for every real row, silently
    collapsing the key to the ``prompt`` for the entire corpus."""
    doc_ids, _ = _gold_doc_fields(row.get("docs", []))
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
        # through ``_normalize_library`` (title-case -> PyPI-canonical), so a
        # filter value must go through the same canonicalization or the two
        # sides can't match. DS-1000's raw library field is title-case, so an
        # operator passing the casing they SEE (``--dataset-library-filter
        # Pandas``) would otherwise silently match zero rows. Build the
        # normalized filter set once so ``"Pandas"`` / ``"pandas"`` /
        # ``"PANDAS"`` and ``"Sklearn"`` / ``"scikit-learn"`` all behave
        # identically.
        normalized_filter = (
            {_normalize_library(x) for x in self.library_filter} if self.library_filter else None
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
            stratum_of=lambda r: _normalize_library(r.get("library", "")),
            sort_key=_split_sort_key,
        )


def _gold_doc_fields(docs: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract ``(doc_ids, doc_contents)`` from a row's ``docs`` gold list,
    handling BOTH the real HF schema and the flat CI fixtures.

    Real ``code-rag-bench/ds1000`` gold entries are dicts keyed
    ``{function, text, title}``: ``title`` is the canonical doc identifier
    (e.g. ``numpy.reference.generated.numpy.cumsum``) that the exact-title gold
    resolver matches against a chunk's ``title`` metadata, and ``text`` is the
    doc prose the fuzzy resolver matches against chunk text. The flat fixtures
    use ``{doc_id, doc_content}``. Prefer the real keys, fall back to the
    fixture keys, so BOTH shapes yield non-empty, 1:1-aligned gold (reading the
    wrong keys silently emptied every gold => recall 0 by construction).

    Total / never raises: non-dict entries and a non-list ``docs`` payload are
    skipped, yielding aligned empty tuples."""
    if not isinstance(docs, list):
        return (), ()
    ids: list[str] = []
    contents: list[str] = []
    for entry in docs:
        if not isinstance(entry, dict):
            continue
        ids.append(str(entry.get("title") or entry.get("doc_id") or ""))
        contents.append(str(entry.get("text") or entry.get("doc_content") or ""))
    return tuple(ids), tuple(contents)


def _row_to_task(row: dict[str, Any], *, strip_query: bool = True) -> EvalTask | None:
    raw_prompt = row.get("prompt", "")
    if not raw_prompt:
        return None
    # WHY: ``strip_query=False`` feeds the verbatim prompt (NL problem + code
    # stub) — the canonical CodeRAG-Bench query. The default strips the
    # canonical solution so retrieval sees only the NL question.
    query = _strip_query(raw_prompt) if strip_query else raw_prompt
    library_raw = row.get("library", "")
    library = _normalize_library(library_raw)
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
