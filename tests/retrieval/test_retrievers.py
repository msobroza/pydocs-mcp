"""Tests for concrete retrievers against fixture DB."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkList,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.retrievers import Bm25ChunkRetriever, LikeMemberRetriever


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_file = tmp_path / "seed.db"
    conn = open_index_database(db_file)
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?,?,?,?)",
        ("fastapi", "Routing", "Use APIRouter to group related endpoints.", "dependency_doc_file"),
    )
    conn.execute(
        "INSERT INTO chunks (package, title, text, origin) VALUES (?,?,?,?)",
        ("__project__", "README", "Project overview", "project_module_doc"),
    )
    conn.execute(
        "INSERT INTO module_members "
        "(package, module, name, kind, signature, return_annotation, parameters, docstring) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi.routing", "APIRouter", "class",
         "(prefix: str = '')", "", json.dumps([]), "Groups endpoints."),
    )
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    return db_file


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_returns_chunk_list(seeded_db: Path):
    provider = build_connection_provider(seeded_db)
    r = Bm25ChunkRetriever(provider=provider)
    result = await r.retrieve(SearchQuery(terms="APIRouter"))
    assert isinstance(result, ChunkList)
    assert len(result.items) >= 1
    first = result.items[0]
    assert first.metadata[ChunkFilterField.PACKAGE.value] == "fastapi"
    assert first.retriever_name == "bm25_chunk"
    assert first.relevance is not None


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_respects_package_filter(seeded_db: Path):
    provider = build_connection_provider(seeded_db)
    r = Bm25ChunkRetriever(provider=provider)
    result = await r.retrieve(SearchQuery(
        terms="Project",
        pre_filter={ChunkFilterField.PACKAGE.value: "__project__"},
    ))
    for chunk in result.items:
        assert chunk.metadata[ChunkFilterField.PACKAGE.value] == "__project__"


@pytest.mark.asyncio
async def test_like_member_retriever_returns_module_member_list(seeded_db: Path):
    provider = build_connection_provider(seeded_db)
    r = LikeMemberRetriever(provider=provider)
    result = await r.retrieve(SearchQuery(terms="APIRouter"))
    assert isinstance(result, ModuleMemberList)
    assert len(result.items) >= 1
    m = result.items[0]
    assert m.metadata[ModuleMemberFilterField.NAME.value] == "APIRouter"
    assert m.retriever_name == "like_member"
