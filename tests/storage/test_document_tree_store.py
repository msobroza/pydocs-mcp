"""Tests for SqliteDocumentTreeStore (spec §12.2, Task 10)."""
from __future__ import annotations

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.storage.protocols import DocumentTreeStore
from pydocs_mcp.storage.sqlite import SqliteDocumentTreeStore


@pytest.fixture
def db_file(tmp_path):
    f = tmp_path / "trees.db"
    open_index_database(f).close()
    return f


def _leaf(
    node_id: str = "mod.func",
    qualified_name: str | None = None,
    kind: NodeKind = NodeKind.FUNCTION,
    text: str = "body",
    content_hash: str = "h1",
    summary: str = "",
    extra: dict | None = None,
    parent_id: str | None = None,
) -> DocumentNode:
    return DocumentNode(
        node_id=node_id,
        qualified_name=qualified_name or node_id,
        title=node_id.rsplit(".", 1)[-1],
        kind=kind,
        source_path="mod.py",
        start_line=1,
        end_line=5,
        text=text,
        content_hash=content_hash,
        summary=summary,
        extra_metadata=extra or {},
        parent_id=parent_id,
        children=(),
    )


def _module_tree(module: str = "pkg.mod", *children: DocumentNode) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=f"{module.replace('.', '/')}.py",
        start_line=1,
        end_line=100,
        text="# module docstring\n",
        content_hash=f"mh-{module}",
        children=tuple(children),
    )


def test_implements_protocol():
    """Structural check — an instance satisfies the runtime-checkable Protocol."""
    store = SqliteDocumentTreeStore(provider=None)  # type: ignore[arg-type]
    assert isinstance(store, DocumentTreeStore)


async def test_save_many_and_load_roundtrip_preserves_tree(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    child = _leaf(node_id="pkg.mod.foo", parent_id="pkg.mod", text="def foo(): ...")
    tree = _module_tree("pkg.mod", child)

    await store.save_many([tree], package="pkg")
    loaded = await store.load("pkg", "pkg.mod")

    assert loaded is not None
    assert loaded.qualified_name == "pkg.mod"
    assert loaded.kind is NodeKind.MODULE
    assert loaded.content_hash == "mh-pkg.mod"
    assert len(loaded.children) == 1
    assert loaded.children[0].qualified_name == "pkg.mod.foo"
    assert loaded.children[0].kind is NodeKind.FUNCTION
    assert loaded.children[0].parent_id == "pkg.mod"


async def test_load_unknown_module_returns_none(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    assert await store.load("nopkg", "nomod") is None


async def test_load_all_in_package_returns_dict_keyed_by_module(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    t1 = _module_tree("pkg.a")
    t2 = _module_tree("pkg.b")
    t3 = _module_tree("other.c")
    await store.save_many([t1, t2], package="pkg")
    await store.save_many([t3], package="other")

    all_pkg = await store.load_all_in_package("pkg")
    assert set(all_pkg) == {"pkg.a", "pkg.b"}
    assert all_pkg["pkg.a"].qualified_name == "pkg.a"
    assert all_pkg["pkg.b"].qualified_name == "pkg.b"

    other = await store.load_all_in_package("other")
    assert set(other) == {"other.c"}


async def test_load_all_in_package_unknown_returns_empty_dict(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    assert await store.load_all_in_package("missing") == {}


async def test_delete_for_package_purges_only_that_package(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    await store.save_many([_module_tree("pkg.a"), _module_tree("pkg.b")], package="pkg")
    await store.save_many([_module_tree("other.c")], package="other")

    await store.delete_for_package("pkg")

    assert await store.load_all_in_package("pkg") == {}
    remaining = await store.load_all_in_package("other")
    assert set(remaining) == {"other.c"}


async def test_save_many_empty_list_is_noop(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    # Must not raise, must not create a row.
    await store.save_many([], package="pkg")
    assert await store.load_all_in_package("pkg") == {}


async def test_nested_grandchildren_preserved_through_roundtrip(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    grandchild = _leaf(
        node_id="pkg.mod.Cls.method",
        kind=NodeKind.METHOD,
        parent_id="pkg.mod.Cls",
        text="def method(self): ...",
    )
    cls = DocumentNode(
        node_id="pkg.mod.Cls",
        qualified_name="pkg.mod.Cls",
        title="Cls",
        kind=NodeKind.CLASS,
        source_path="pkg/mod.py",
        start_line=10,
        end_line=30,
        text="class Cls:\n    ...",
        content_hash="ch-cls",
        parent_id="pkg.mod",
        children=(grandchild,),
    )
    tree = _module_tree("pkg.mod", cls)

    await store.save_many([tree], package="pkg")
    loaded = await store.load("pkg", "pkg.mod")

    assert loaded is not None
    assert len(loaded.children) == 1
    cls_loaded = loaded.children[0]
    assert cls_loaded.kind is NodeKind.CLASS
    assert len(cls_loaded.children) == 1
    method_loaded = cls_loaded.children[0]
    assert method_loaded.kind is NodeKind.METHOD
    assert method_loaded.qualified_name == "pkg.mod.Cls.method"
    assert method_loaded.parent_id == "pkg.mod.Cls"


async def test_extra_metadata_preserved_through_roundtrip(db_file):
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    node = _leaf(
        node_id="pkg.mod.thing",
        extra={"decorators": ["classmethod"], "is_async": True, "count": 3},
    )
    tree = _module_tree("pkg.mod", node)

    await store.save_many([tree], package="pkg")
    loaded = await store.load("pkg", "pkg.mod")

    assert loaded is not None
    assert dict(loaded.children[0].extra_metadata) == {
        "decorators": ["classmethod"],
        "is_async": True,
        "count": 3,
    }


async def test_save_many_upserts_on_conflict(db_file):
    """Second write for the same (package, module) overwrites tree + hash."""
    provider = build_connection_provider(db_file)
    store = SqliteDocumentTreeStore(provider=provider)

    tree_v1 = _module_tree("pkg.mod", _leaf(node_id="pkg.mod.old"))
    await store.save_many([tree_v1], package="pkg")

    tree_v2 = DocumentNode(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="mod",
        kind=NodeKind.MODULE,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=100,
        text="# updated\n",
        content_hash="updated-hash",
        children=(_leaf(node_id="pkg.mod.new"),),
    )
    await store.save_many([tree_v2], package="pkg")

    loaded = await store.load("pkg", "pkg.mod")
    assert loaded is not None
    assert loaded.content_hash == "updated-hash"
    assert len(loaded.children) == 1
    assert loaded.children[0].qualified_name == "pkg.mod.new"
