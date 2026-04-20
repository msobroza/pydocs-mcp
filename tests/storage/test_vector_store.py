"""Tests for SqliteChunkRepository + SqliteVectorStore (spec §5.3, AC #9)."""
from __future__ import annotations

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.storage.sqlite import SqliteChunkRepository, SqliteVectorStore


@pytest.fixture
def db_file(tmp_path):
    f = tmp_path / "vector.db"
    open_index_database(f).close()
    return f


def _chunk(package: str, title: str, text: str, origin: str = "project_code_section") -> Chunk:
    return Chunk(
        text=text,
        metadata={
            ChunkFilterField.PACKAGE.value: package,
            ChunkFilterField.TITLE.value: title,
            ChunkFilterField.ORIGIN.value: origin,
        },
    )


# ── ChunkRepository ──────────────────────────────────────────────────────


async def test_chunk_repository_upsert_and_list(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert([
        _chunk("fastapi", "routing", "Path operations and dependencies"),
        _chunk("fastapi", "security", "OAuth2 with password flow"),
        _chunk("requests", "get", "Send HTTP GET request"),
    ])

    all_chunks = await repo.list()
    assert len(all_chunks) == 3

    fastapi_only = await repo.list(filter={"package": "fastapi"})
    assert len(fastapi_only) == 2
    titles = {c.metadata["title"] for c in fastapi_only}
    assert titles == {"routing", "security"}


async def test_chunk_repository_delete(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert([
        _chunk("fastapi", "routing", "x"),
        _chunk("fastapi", "security", "y"),
        _chunk("requests", "get", "z"),
    ])

    assert await repo.count() == 3

    deleted = await repo.delete({"package": "fastapi"})
    assert deleted == 2
    assert await repo.count() == 1
    remaining = await repo.list()
    assert remaining[0].metadata["package"] == "requests"


async def test_chunk_repository_rebuild_index(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert([
        _chunk("fastapi", "routing", "Path operations and dependencies tutorial"),
    ])
    # Before rebuild, chunks_fts is empty (content=chunks means it's a contentless view).
    # After rebuild, FTS becomes queryable.
    await repo.rebuild_index()

    store = SqliteVectorStore(provider=provider)
    results = await store.text_search("routing", limit=5)
    assert len(results) >= 1
    assert results[0].metadata["package"] == "fastapi"


# ── VectorStore ──────────────────────────────────────────────────────────


async def test_vector_store_text_search_basic(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert([
        _chunk("fastapi", "routing", "Path operations and dependencies"),
        _chunk("requests", "get", "Send HTTP GET request to a URL"),
    ])
    await repo.rebuild_index()

    store = SqliteVectorStore(provider=provider)
    results = await store.text_search("routing", limit=5)
    assert len(results) == 1
    got = results[0]
    assert got.metadata["package"] == "fastapi"
    assert got.relevance is not None
    # Name follows plan's guidance: "bm25_chunk" (or "sqlite_fts5")
    assert got.retriever_name in ("bm25_chunk", "sqlite_fts5")


async def test_vector_store_text_search_with_filter_pushdown(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteChunkRepository(provider=provider)
    await repo.upsert([
        _chunk("fastapi", "routing", "Path operations and dependencies tutorial"),
        _chunk("requests", "get", "Send HTTP GET request tutorial"),
    ])
    await repo.rebuild_index()

    store = SqliteVectorStore(provider=provider)
    # Both rows match "tutorial" — filter narrows to one.
    results = await store.text_search(
        "tutorial", limit=5, filter={"package": "requests"},
    )
    assert len(results) == 1
    assert results[0].metadata["package"] == "requests"


async def test_vector_store_text_search_invalid_column(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteVectorStore(provider=provider)
    # No rebuild needed; validation gates before SQL executes.
    with pytest.raises(ValueError, match="not in safe_columns"):
        await store.text_search(
            "anything", limit=5, filter={"language": "python"},
        )
