"""Task 21 — Chunk + Package value-object polish.

Covers:

* S2 / S25: ``Chunk.from_test_inputs(...)`` factory is the canonical
  auto-hash entry point. Production callers pass ``content_hash``
  explicitly; the factory is a test-only convenience.
* S13: ``Chunk.embedding`` is documented as ``None`` on read paths
  (vectors live in the ``.tq`` sidecar).
* S17: ``RetrievalEnrichment(relevance, retriever_name)`` is exposed as
  an optional sub-object on Chunk via ``chunk.with_enrichment(...)``.
  Added **additively** — the legacy ``relevance`` / ``retriever_name``
  fields on Chunk continue to work for backward compatibility.
* S28: ``EmbeddingProvenance(model_name, content_hash)`` is exposed as
  an optional sub-object on Package. Added **additively** — the legacy
  ``embedding_model`` / ``content_hash`` fields on Package continue to
  work for backward compatibility.

The test file is intentionally adapted to the actual Chunk / Package
schema (text + metadata payload, not a flat record), which is the
shape production callers in ``storage/sqlite.py``,
``retrieval/steps/*.py``, and ``extraction/pipeline/stages/*.py``
already use.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.models import (
    Chunk,
    EmbeddingProvenance,
    Package,
    PackageOrigin,
    RetrievalEnrichment,
)


# ── S2 / S25: Chunk.from_test_inputs auto-computes content_hash ────────


def test_chunk_from_test_inputs_auto_computes_hash() -> None:
    """``Chunk.from_test_inputs(...)`` is the canonical test factory and
    auto-fills ``content_hash`` from the chunk identity tuple (SHA-256)."""
    chunk = Chunk.from_test_inputs(
        package="p",
        module="m",
        title="t",
        text="x",
    )
    assert chunk.content_hash != ""
    assert len(chunk.content_hash) == 64  # SHA-256 hex
    # Identity fields are written into metadata so production retrieval
    # filters keep working.
    assert chunk.metadata["package"] == "p"
    assert chunk.metadata["module"] == "m"
    assert chunk.metadata["title"] == "t"
    assert chunk.text == "x"


def test_chunk_from_test_inputs_accepts_optional_id() -> None:
    chunk = Chunk.from_test_inputs(
        package="p", module="m", title="t", text="x", id=42,
    )
    assert chunk.id == 42


def test_chunk_from_test_inputs_accepts_pipeline_hash() -> None:
    """``pipeline_hash`` participates in the SHA-256 just like in
    production — bumping it changes the hash for the same chunk text."""
    chunk_a = Chunk.from_test_inputs(
        package="p", module="m", title="t", text="x",
        pipeline_hash="pipeline-A",
    )
    chunk_b = Chunk.from_test_inputs(
        package="p", module="m", title="t", text="x",
        pipeline_hash="pipeline-B",
    )
    assert chunk_a.content_hash != chunk_b.content_hash


def test_chunk_explicit_content_hash_is_respected() -> None:
    """Production callers pass ``content_hash`` explicitly; the value
    is honoured verbatim, no auto-compute on top."""
    chunk = Chunk(text="hello", content_hash="explicit-hash",
                  metadata={"package": "p"})
    assert chunk.content_hash == "explicit-hash"


# ── S13: embedding field is documented as None on read paths ───────────


def test_chunk_embedding_documented_as_none_on_read_paths() -> None:
    """The ``Chunk.embedding`` field carries the inline embedding only
    during ingestion. On read paths it is always ``None`` because dense
    vectors live in the ``.tq`` sidecar, not on the SQLite row."""
    # The docstring is attached to the field via the dataclass field
    # default; we assert here that the documented default (None) holds
    # and is reachable via the standard attribute path.
    chunk = Chunk(text="x")
    assert chunk.embedding is None


# ── S17: RetrievalEnrichment value object on Chunk ─────────────────────


def test_retrieval_enrichment_value_object() -> None:
    enr = RetrievalEnrichment(relevance=0.95, retriever_name="bm25")
    assert enr.relevance == 0.95
    assert enr.retriever_name == "bm25"


def test_retrieval_enrichment_is_frozen() -> None:
    enr = RetrievalEnrichment(relevance=0.5, retriever_name="bm25")
    with pytest.raises(Exception):
        enr.relevance = 0.9  # type: ignore[misc]


def test_chunk_enrichment_default_is_none() -> None:
    chunk = Chunk(text="x")
    assert chunk.enrichment is None


def test_chunk_with_enrichment_returns_new_instance() -> None:
    """``chunk.with_enrichment(e)`` is non-mutating; the original chunk
    is unchanged and a new Chunk carries the enrichment."""
    original = Chunk(text="x", content_hash="h")
    enr = RetrievalEnrichment(relevance=0.95, retriever_name="bm25")
    enriched = original.with_enrichment(enr)

    assert original.enrichment is None  # original is untouched
    assert enriched is not original
    assert enriched.enrichment == enr
    assert enriched.enrichment is not None
    assert enriched.enrichment.relevance == 0.95
    assert enriched.enrichment.retriever_name == "bm25"
    # Identity fields preserved.
    assert enriched.text == "x"
    assert enriched.content_hash == "h"


def test_chunk_legacy_relevance_fields_still_work() -> None:
    """S17 is ADDITIVE — the legacy flat ``relevance`` / ``retriever_name``
    fields remain on Chunk for backward compatibility with existing
    production retrieval steps (bm25_scorer, dense_scorer, etc.)."""
    chunk = Chunk(text="x", relevance=0.9, retriever_name="bm25")
    assert chunk.relevance == 0.9
    assert chunk.retriever_name == "bm25"
    assert chunk.enrichment is None  # the two paths are independent


# ── S28: EmbeddingProvenance value object on Package ───────────────────


def test_embedding_provenance_value_object() -> None:
    prov = EmbeddingProvenance(model_name="BAAI/bge-small-en-v1.5",
                                content_hash="h")
    assert prov.model_name == "BAAI/bge-small-en-v1.5"
    assert prov.content_hash == "h"


def test_embedding_provenance_is_frozen() -> None:
    prov = EmbeddingProvenance(model_name="m", content_hash="h")
    with pytest.raises(Exception):
        prov.model_name = "other"  # type: ignore[misc]


def test_package_provenance_default_is_none() -> None:
    """S28 is ADDITIVE — ``provenance`` is optional and defaults to
    None so every existing Package(...) call site keeps compiling."""
    pkg = Package(
        name="demo",
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )
    assert pkg.provenance is None


def test_package_with_explicit_provenance() -> None:
    prov = EmbeddingProvenance(
        model_name="BAAI/bge-small-en-v1.5", content_hash="pkg-h",
    )
    pkg = Package(
        name="demo",
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="pkg-h",
        origin=PackageOrigin.DEPENDENCY,
        provenance=prov,
    )
    assert pkg.provenance == prov
    assert pkg.provenance is not None
    assert pkg.provenance.model_name == "BAAI/bge-small-en-v1.5"


def test_package_legacy_embedding_model_field_still_works() -> None:
    """S28 is ADDITIVE — the legacy flat ``embedding_model`` field
    remains on Package for backward compatibility with the
    indexing-service re-embed-on-model-change path."""
    pkg = Package(
        name="demo",
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
        embedding_model="BAAI/bge-small-en-v1.5",
    )
    assert pkg.embedding_model == "BAAI/bge-small-en-v1.5"
    assert pkg.provenance is None
