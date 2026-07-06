"""Chunk + Package value-object behavior after the dead-code sweep.

Covers:

* S2 / S25: ``Chunk.from_test_inputs(...)`` factory is the canonical
  auto-hash entry point. Production callers pass ``content_hash``
  explicitly; the factory is a test-only convenience.
* S13: ``Chunk.embedding`` is documented as ``None`` on read paths
  (vectors live in the ``.tq`` sidecar).
* Dead-code sweep: the transitional grouped value objects
  (``RetrievalEnrichment`` + ``Chunk.enrichment`` +
  ``Chunk.with_enrichment``; ``EmbeddingProvenance`` +
  ``Package.provenance``) never gained a producer or consumer outside
  their own tests and were removed. The flat fields (``relevance`` /
  ``retriever_name`` on Chunk, ``embedding_model`` on Package) are the
  single surviving form; the absence tests below make re-introducing
  the grouped form a deliberate act instead of drift.
"""

from __future__ import annotations

import dataclasses

import pydocs_mcp.models as models
from pydocs_mcp.models import Chunk, Package, PackageOrigin

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
        package="p",
        module="m",
        title="t",
        text="x",
        id=42,
    )
    assert chunk.id == 42


def test_chunk_from_test_inputs_accepts_pipeline_hash() -> None:
    """``pipeline_hash`` participates in the SHA-256 just like in
    production — bumping it changes the hash for the same chunk text."""
    chunk_a = Chunk.from_test_inputs(
        package="p",
        module="m",
        title="t",
        text="x",
        pipeline_hash="pipeline-A",
    )
    chunk_b = Chunk.from_test_inputs(
        package="p",
        module="m",
        title="t",
        text="x",
        pipeline_hash="pipeline-B",
    )
    assert chunk_a.content_hash != chunk_b.content_hash


def test_chunk_explicit_content_hash_is_respected() -> None:
    """Production callers pass ``content_hash`` explicitly; the value
    is honoured verbatim, no auto-compute on top."""
    chunk = Chunk(text="hello", content_hash="explicit-hash", metadata={"package": "p"})
    assert chunk.content_hash == "explicit-hash"


# ── S13: embedding field is documented as None on read paths ───────────


def test_chunk_embedding_documented_as_none_on_read_paths() -> None:
    """The ``Chunk.embedding`` field carries the inline embedding only
    during ingestion. On read paths it is always ``None`` because dense
    vectors live in the ``.tq`` sidecar, not on the SQLite row."""
    chunk = Chunk(text="x")
    assert chunk.embedding is None


# ── Flat retrieval / provenance fields are the single surviving form ───


def test_chunk_flat_relevance_fields() -> None:
    """``relevance`` / ``retriever_name`` are the fields every production
    retrieval step (bm25_scorer, dense_scorer, rrf_fusion, ...) writes."""
    chunk = Chunk(text="x", relevance=0.9, retriever_name="bm25")
    assert chunk.relevance == 0.9
    assert chunk.retriever_name == "bm25"


def test_package_flat_embedding_model_field() -> None:
    """``embedding_model`` drives the indexing-service
    re-embed-on-model-change path."""
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


# ── Dead grouped value objects stay dead ────────────────────────────────


def test_grouped_value_objects_removed() -> None:
    """models.py has a PEP 562 ``__getattr__`` that raises AttributeError
    for unknown names, so hasattr() is a faithful absence probe."""
    assert not hasattr(models, "RetrievalEnrichment")
    assert not hasattr(models, "EmbeddingProvenance")
    assert not hasattr(Chunk, "with_enrichment")
    assert "enrichment" not in {f.name for f in dataclasses.fields(Chunk)}
    assert "provenance" not in {f.name for f in dataclasses.fields(Package)}
