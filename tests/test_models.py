"""Tests for domain models in pydocs_mcp.models.

Per sub-PR #1 spec §5, every enum subclasses enum.StrEnum and values round-trip
through SQLite TEXT columns, YAML, and JSON without glue code.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ChunkOrigin,
    MemberKind,
    MetadataFilterFormat,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    Package,
    PackageOrigin,
    Parameter,
    PipelineResultItem,
    SearchQuery,
    SearchResponse,
    SearchScope,
)

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


@pytest.mark.parametrize("enum_cls,value", [
    (ChunkOrigin, "project_module_doc"),
    (ChunkOrigin, "project_code_section"),
    (ChunkOrigin, "dependency_code_section"),
    (ChunkOrigin, "dependency_doc_file"),
    (ChunkOrigin, "dependency_readme"),
    (ChunkOrigin, "dependency_module_doc"),
    (ChunkOrigin, "composite_output"),
    (MemberKind, "function"),
    (MemberKind, "class"),
    (MemberKind, "method"),
    (PackageOrigin, "project"),
    (PackageOrigin, "dependency"),
    (SearchScope, "project_only"),
    (SearchScope, "dependencies_only"),
    (SearchScope, "all"),
    (MetadataFilterFormat, "multifield"),
    (MetadataFilterFormat, "filter_tree"),
    (MetadataFilterFormat, "chromadb"),
    (MetadataFilterFormat, "elasticsearch"),
    (MetadataFilterFormat, "qdrant"),
    (ChunkFilterField, "package"),
    (ChunkFilterField, "title"),
    (ChunkFilterField, "origin"),
    (ChunkFilterField, "module"),
    (ChunkFilterField, "scope"),
    (ModuleMemberFilterField, "package"),
    (ModuleMemberFilterField, "module"),
    (ModuleMemberFilterField, "name"),
    (ModuleMemberFilterField, "kind"),
])
def test_enum_value_roundtrip(enum_cls, value):
    """Every enum value round-trips: str ↔ enum member."""
    member = enum_cls(value)
    assert member.value == value
    assert str(member) == value


def test_parameter_defaults():
    p = Parameter(name="prefix")
    assert p.name == "prefix"
    assert p.annotation == ""
    assert p.default == ""


def test_parameter_is_frozen():
    p = Parameter(name="prefix")
    with pytest.raises(Exception):
        p.name = "other"


def test_package_construction():
    pkg = Package(
        name="fastapi",
        version="0.104.1",
        summary="Web framework.",
        homepage="https://fastapi.tiangolo.com",
        dependencies=("starlette>=0.27",),
        content_hash="abc123",
        origin=PackageOrigin.DEPENDENCY,
    )
    assert pkg.kind == "package"
    assert pkg.dependencies == ("starlette>=0.27",)
    assert pkg.origin is PackageOrigin.DEPENDENCY


def test_package_is_frozen():
    pkg = Package(
        name="fastapi", version="0.1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )
    with pytest.raises(Exception):
        pkg.name = "other"


def test_chunk_default_metadata_empty():
    c = Chunk(text="hello")
    assert c.kind == "chunk"
    assert c.text == "hello"
    assert c.id is None
    assert c.relevance is None
    assert c.retriever_name is None
    assert c.metadata == {}


def test_chunk_with_metadata_and_retrieval_fields():
    c = Chunk(
        text="body",
        id=7,
        relevance=0.93,
        retriever_name="fts5",
        metadata={
            "package": "fastapi",
            "title": "Routing",
            "origin": ChunkOrigin.DEPENDENCY_DOC_FILE.value,
        },
    )
    assert c.id == 7
    assert c.relevance == 0.93
    assert c.retriever_name == "fts5"
    assert c.metadata["origin"] == "dependency_doc_file"


def test_chunk_is_frozen():
    c = Chunk(text="x")
    with pytest.raises(Exception):
        c.text = "y"


def test_module_member_default_metadata_empty():
    m = ModuleMember()
    assert m.kind == "module_member"
    assert m.id is None
    assert m.metadata == {}


def test_module_member_with_metadata():
    m = ModuleMember(
        id=3,
        relevance=0.7,
        retriever_name="like",
        metadata={
            "package": "fastapi",
            "module": "fastapi.routing",
            "name": "APIRouter",
            "kind": "class",
            "signature": "(prefix: str = '')",
            "docstring": "Group endpoints.",
            "return_annotation": "",
            "parameters": (),
        },
    )
    assert m.metadata["name"] == "APIRouter"
    assert m.metadata["kind"] == "class"


def test_chunk_list_carries_kind():
    cl = ChunkList(items=(Chunk(text="a"), Chunk(text="b")))
    assert cl.kind == "chunk_list"
    assert len(cl.items) == 2


def test_module_member_list_carries_kind():
    ml = ModuleMemberList(items=(ModuleMember(), ModuleMember()))
    assert ml.kind == "module_member_list"
    assert len(ml.items) == 2


def test_pipeline_result_item_is_union():
    items: list[PipelineResultItem] = [ChunkList(items=()), ModuleMemberList(items=())]
    assert len(items) == 2


def test_search_query_defaults():
    q = SearchQuery(terms="fastapi routing")
    assert q.terms == "fastapi routing"
    assert q.max_results == 8
    assert q.pre_filter is None
    assert q.post_filter is None
    assert q.pre_filter_format is MetadataFilterFormat.MULTIFIELD
    assert q.post_filter_format is MetadataFilterFormat.MULTIFIELD


def test_search_query_rejects_empty_terms():
    with pytest.raises(Exception):
        SearchQuery(terms="   ")


def test_search_query_rejects_non_positive_max_results():
    with pytest.raises(Exception):
        SearchQuery(terms="x", max_results=0)
    with pytest.raises(Exception):
        SearchQuery(terms="x", max_results=-1)


def test_search_query_carries_pre_filter_dict():
    q = SearchQuery(terms="x", pre_filter={"package": "fastapi"})
    assert q.pre_filter == {"package": "fastapi"}


def test_search_response_construction():
    q = SearchQuery(terms="x")
    cl = ChunkList(items=())
    r = SearchResponse(result=cl, query=q, duration_ms=12.5)
    assert r.result is cl
    assert r.query is q
    assert r.duration_ms == 12.5


def test_search_response_default_duration():
    r = SearchResponse(result=ModuleMemberList(items=()), query=SearchQuery(terms="x"))
    assert r.duration_ms == 0.0


def test_search_response_is_frozen():
    r = SearchResponse(result=ChunkList(items=()), query=SearchQuery(terms="x"))
    with pytest.raises(Exception):
        r.duration_ms = 1.0


def test_chunk_metadata_is_read_only():
    c = Chunk(text="x", metadata={"origin": "foo"})
    with pytest.raises(TypeError):
        c.metadata["origin"] = "bar"


def test_module_member_metadata_is_read_only():
    m = ModuleMember(metadata={"name": "APIRouter"})
    with pytest.raises(TypeError):
        m.metadata["name"] = "Router"


def test_search_query_strips_whitespace_around_terms():
    q = SearchQuery(terms="  fastapi routing  ")
    assert q.terms == "fastapi routing"


def test_schema_version_upgrade_rebuilds(tmp_path):
    """A DB created with user_version=0 and stale tables is dropped and rebuilt
    when opened by the current code."""
    import sqlite3

    db_file = tmp_path / "stale.db"
    con = sqlite3.connect(db_file)
    con.executescript("""
        PRAGMA user_version = 0;
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, stale TEXT);
        INSERT INTO symbols (stale) VALUES ('old');
    """)
    con.commit()
    con.close()

    con2 = open_index_database(db_file)
    version = con2.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        r[0]
        for r in con2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    con2.close()

    assert version == SCHEMA_VERSION
    assert "symbols" not in tables
    assert {"packages", "chunks", "module_members"}.issubset(tables)
