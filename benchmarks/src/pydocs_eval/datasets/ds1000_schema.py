"""DS-1000 row-shape logic, shared by the loader AND the oracle indexer.

Pure functions over raw HF / fixture rows: metadata coercion and lifting
(the real ``code-rag-bench/ds1000`` rows nest ``library`` /
``perturbation_*`` inside a ``metadata`` struct; the flat CI fixtures
expose them top-level), gold-doc field extraction for both schemas,
query stripping, the pinned HF revisions, and the ONE library-name
canonicalization both sides must agree on — a divergence here directly
corrupts package-filtered gold resolution, i.e. benchmark scores.

stdlib-only (ast / json / re): importable by ``systems/pydocs_oracle.py``
without dragging in the loader's dataclass or the registry.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

# TODO: pin to SHA once HF network access verified. The HF API + the
# `datasets` loader were both 403/blocked at write time; downstream tests
# use the fixture path so this is non-blocking for green. Bump these by
# running `huggingface-cli download code-rag-bench/<repo> --revision main
# --repo-type dataset` and reading the resolved SHA from the output.
PINNED_DS1000_REVISION = "main"
PINNED_LIBDOCS_REVISION = "main"

# Keyed by LOWERCASE spelling so one map serves both the DS-1000
# title-case ``library`` values ("Sklearn", "Pytorch") and the lowercase
# ``doc_id`` prefixes the oracle derives ("sklearn.", "pytorch." — never
# observed, but symmetric). Only non-identity remaps are listed: every
# other library (numpy / pandas / matplotlib / scipy / tensorflow) IS its
# own PyPI-canonical lowercase name, so the ``.lower()`` fallback covers
# it. Picks PyPI names (NOT import names) so the result matches
# ``Package.name`` in pydocs's store: ``scikit-learn`` (not ``sklearn``)
# and ``torch`` (not ``pytorch``).
_LIBRARY_NORMALIZATION: dict[str, str] = {
    "sklearn": "scikit-learn",
    "pytorch": "torch",
}


def to_pypi_canonical(name: str) -> str:
    """Map any DS-1000 / doc_id spelling of a library to its PyPI-canonical
    lowercase name. Total (never raises); unknown values fall back to
    ``name.strip().lower()``.

    Example::

        to_pypi_canonical("Sklearn")  # -> "scikit-learn"
        to_pypi_canonical("pytorch")  # -> "torch"
    """
    key = name.strip().lower()
    return _LIBRARY_NORMALIZATION.get(key, key)


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


__all__ = (
    "PINNED_DS1000_REVISION",
    "PINNED_LIBDOCS_REVISION",
    "to_pypi_canonical",
)
