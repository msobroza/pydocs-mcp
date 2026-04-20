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


@pytest.mark.asyncio
async def test_pipeline_chunk_retriever_forwards_to_inner_pipeline(tmp_path):
    """Adapter runs the inner pipeline and returns the ChunkList at state.result."""
    from dataclasses import dataclass
    from pydocs_mcp.models import Chunk
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
    from pydocs_mcp.retrieval.retrievers import PipelineChunkRetriever

    @dataclass(frozen=True, slots=True)
    class _ReturnOneChunk:
        name: str = "return_one"
        async def run(self, state: PipelineState) -> PipelineState:
            return PipelineState(
                query=state.query,
                result=ChunkList(items=(Chunk(text="payload"),)),
            )

    inner = CodeRetrieverPipeline(name="inner", stages=(_ReturnOneChunk(),))
    adapter = PipelineChunkRetriever(pipeline=inner)
    out = await adapter.retrieve(SearchQuery(terms="x"))
    assert isinstance(out, ChunkList)
    assert len(out.items) == 1
    assert out.items[0].text == "payload"


@pytest.mark.asyncio
async def test_pipeline_module_member_retriever_forwards():
    from dataclasses import dataclass
    from pydocs_mcp.models import ModuleMember
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
    from pydocs_mcp.retrieval.retrievers import PipelineModuleMemberRetriever

    @dataclass(frozen=True, slots=True)
    class _ReturnOneMember:
        name: str = "return_one"
        async def run(self, state: PipelineState) -> PipelineState:
            return PipelineState(
                query=state.query,
                result=ModuleMemberList(items=(ModuleMember(metadata={"name": "f"}),)),
            )

    inner = CodeRetrieverPipeline(name="inner", stages=(_ReturnOneMember(),))
    adapter = PipelineModuleMemberRetriever(pipeline=inner)
    out = await adapter.retrieve(SearchQuery(terms="x"))
    assert isinstance(out, ModuleMemberList)
    assert len(out.items) == 1
