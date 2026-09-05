"""get_symbol depth=source — verbatim bytes + path + line-cap (spec §D1/§D7)."""

import asyncio

import pytest

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.symbol_source import SymbolSourceService
from pydocs_mcp.models import Chunk
from tests._fakes import InMemoryChunkStore, make_fake_uow_factory


def _store(*chunks: Chunk) -> InMemoryChunkStore:
    # make_fake_uow_factory(chunks=...) expects an InMemoryChunkStore, not a
    # raw tuple — build + seed one here so each test states only its chunks.
    store = InMemoryChunkStore()
    asyncio.run(store.upsert(chunks))
    return store


def _chunk(*, qualified_name: str, source_path: str, text: str) -> Chunk:
    # Mirrors this suite's chunk-construction convention (see
    # tests/application/test_reference_service.py): metadata carries
    # ``qualified_name`` + ``source_path`` so SymbolSourceService can find the
    # symbol and render its file path.
    return Chunk(
        text=text,
        metadata={
            "package": "pkg",
            "qualified_name": qualified_name,
            "source_path": source_path,
        },
    )


def _service(store: InMemoryChunkStore) -> SymbolSourceService:
    return SymbolSourceService(
        uow_factory=make_fake_uow_factory(chunks=store),
        max_lines=5,
    )


def test_returns_source_block_with_path() -> None:
    store = _store(
        _chunk(
            qualified_name="pkg.mod.f",
            source_path="pkg/mod.py",
            text="def f():\n    return 1\n",
        )
    )
    out = asyncio.run(_service(store).source_for("pkg.mod.f"))
    assert "```python" in out and "def f():" in out
    assert "pkg/mod.py" in out


def test_line_cap_truncates_with_recovery_note() -> None:
    body = "\n".join(f"line{i}" for i in range(20))
    store = _store(_chunk(qualified_name="pkg.mod.big", source_path="pkg/mod.py", text=body))
    out = asyncio.run(_service(store).source_for("pkg.mod.big"))
    assert "line4" in out and "line5" not in out
    assert "pkg/mod.py" in out  # the file path is the terminal recovery step


def test_unknown_symbol_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        asyncio.run(_service(InMemoryChunkStore()).source_for("nope.missing"))


def test_source_with_items_emits_one_span_row() -> None:
    # contract §3.3 (Task 6): depth="source" emits ONE row for the rendered
    # span, read from the schema-v15 chunk metadata keys.
    store = _store(
        Chunk(
            text="def f():\n    return 1\n",
            metadata={
                "package": "pkg",
                "qualified_name": "pkg.mod.f",
                "source_path": "pkg/mod.py",
                "start_line": 3,
                "end_line": 4,
                "kind": "function",
            },
        )
    )
    body, items, extras = asyncio.run(_service(store).source_with_items("pkg.mod.f"))
    assert "```python" in body and "def f():" in body
    assert extras == {}
    assert items == (
        {
            "node_id": "pkg.mod.f",
            "kind": "function",
            "qualified_name": "pkg.mod.f",
            "path": "pkg/mod.py",
            "start_line": 3,
            "end_line": 4,
        },
    )


def test_source_item_kind_resolved_from_document_tree() -> None:
    # Chunk rows persist no node kind — depth="source" must recover it from
    # the document tree so the SAME target yields the SAME items[].kind as
    # depth="summary"/"tree" (which render ``str(node.kind)`` off the tree).
    from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
    from tests._fakes import InMemoryDocumentTreeStore

    func_node = DocumentNode(
        node_id="pkg.mod.f",
        qualified_name="pkg.mod.f",
        title="f",
        kind=NodeKind.FUNCTION,
        source_path="pkg/mod.py",
        start_line=3,
        end_line=4,
        text="def f():\n    return 1\n",
        content_hash="h-f",
    )
    tree = DocumentNode(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="mod",
        kind=NodeKind.MODULE,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        text="",
        content_hash="h-mod",
        children=(func_node,),
    )
    tree_store = InMemoryDocumentTreeStore()
    asyncio.run(tree_store.save_many((tree,), package="pkg"))
    chunk_store = _store(
        Chunk(
            text="def f():\n    return 1\n",
            metadata={
                "package": "pkg",
                "module": "pkg.mod",
                "qualified_name": "pkg.mod.f",
                "source_path": "pkg/mod.py",
                "start_line": 3,
                "end_line": 4,
            },
        )
    )
    svc = SymbolSourceService(
        uow_factory=make_fake_uow_factory(chunks=chunk_store, trees=tree_store),
        max_lines=5,
    )
    _body, items, _extras = asyncio.run(svc.source_with_items("pkg.mod.f"))
    # Identical to what summary/tree depths emit for this node.
    assert items[0]["kind"] == str(func_node.kind) == "function"


def test_source_with_items_degrades_missing_span_to_null() -> None:
    # Legacy rows (pre-v15) carry no span keys and chunks never persist a node
    # kind — the row degrades instead of failing the tool.
    store = _store(_chunk(qualified_name="pkg.mod.f", source_path="", text="def f(): ...\n"))
    _body, items, _extras = asyncio.run(_service(store).source_with_items("pkg.mod.f"))
    assert items == (
        {
            "node_id": "pkg.mod.f",
            "kind": "",
            "qualified_name": "pkg.mod.f",
            "path": None,
            "start_line": None,
            "end_line": None,
        },
    )
