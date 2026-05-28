"""ingestion.yaml includes embed_chunks between flatten and content_hash.

AC-23 wiring: once :class:`FlattenStage` has materialised every
per-tree :class:`Chunk`, :class:`EmbedChunksStage` must run *before*
:class:`ContentHashStage` so that the per-package content hash isn't
recomputed without the embeddings having been attached. The hash-skip
behaviour upstream (``ProjectIndexer``) is unaffected: unchanged packages
bypass the whole pipeline including embedding.

The shipped pipeline uses the ``stages:`` key with inline-flow ``{type:...}``
entries (no ``name:`` field); these assertions match that schema.
"""

from __future__ import annotations

from pathlib import Path

import yaml

INGESTION_YAML = (
    Path(__file__).resolve().parents[3] / "python" / "pydocs_mcp" / "pipelines" / "ingestion.yaml"
)


def test_embed_chunks_is_between_flatten_and_content_hash() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text(encoding="utf-8"))
    stage_types = [s["type"] for s in cfg["stages"]]
    flatten_idx = stage_types.index("flatten")
    embed_idx = stage_types.index("embed_chunks")
    hash_idx = stage_types.index("content_hash")
    assert flatten_idx < embed_idx < hash_idx


def test_embed_chunks_stage_type_is_embed_chunks() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text(encoding="utf-8"))
    embed_stage = next(s for s in cfg["stages"] if s["type"] == "embed_chunks")
    assert embed_stage["type"] == "embed_chunks"


def test_assign_chunk_content_hash_is_between_flatten_and_embed_chunks() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text(encoding="utf-8"))
    types = [s["type"] for s in cfg["stages"]]
    flat = types.index("flatten")
    assign = types.index("assign_chunk_content_hash")
    embed = types.index("embed_chunks")
    assert flat < assign < embed


def test_load_existing_chunk_hashes_is_between_assign_and_embed_chunks() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text(encoding="utf-8"))
    types = [s["type"] for s in cfg["stages"]]
    assign = types.index("assign_chunk_content_hash")
    load = types.index("load_existing_chunk_hashes")
    embed = types.index("embed_chunks")
    assert assign < load < embed
