"""End-to-end integration: IngestionPipeline → IndexProjectService →
DocumentTreeStore → get_document_tree (spec §16 AC #12).

This file proves the full sub-PR #5 write path actually roundtrips: a real
``IngestionPipeline`` (built from the shipped ``presets/ingestion.yaml``)
runs against a tiny on-disk fixture project, the resulting 3-tuple
``(chunks, trees, package)`` flows through :class:`IndexProjectService`
into :class:`IndexingService.reindex_package`, which persists trees via
:class:`SqliteDocumentTreeStore` (wired now by
:func:`build_sqlite_indexing_service`). We then re-open the DB and use
:class:`DocumentTreeService` — the read side used by the
``get_document_tree`` MCP handler — to pull the same trees back out.

Difference from ``tests/application/test_end_to_end.py`` (service
composition over a pre-seeded DB): this file drives the WRITE path
end-to-end with zero mocks — only the fixture files on disk. A regression
anywhere on ``extraction → application → storage`` would surface here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application.document_tree_service import DocumentTreeService
from pydocs_mcp.application.index_project_service import IndexProjectService
from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    StaticDependencyResolver,
    build_ingestion_pipeline,
)
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import ChunkFilterField, ModuleMemberFilterField
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteDocumentTreeStore,
    SqliteModuleMemberRepository,
)
from pydocs_mcp.storage.wiring import build_sqlite_indexing_service


# ── Fixtures ──────────────────────────────────────────────────────────────

_APP_PY = '''\
"""Tiny app module for e2e extraction tests."""


def hi():
    """Say hi."""
    return "hi"


class Foo:
    """A toy class."""

    def bar(self):
        """Bar method."""
        return 1
'''


_README_MD = """\
# Title

Intro paragraph.

## Section

Details here.
"""

_PYPROJECT = """\
[project]
name = "e2e-test"
version = "0.1.0"
"""


@pytest.fixture
def fixture_project(tmp_path: Path) -> Path:
    """Create a minimal project tree: pyproject.toml + src/app.py + README.md.

    Small and real — the ingestion pipeline walks ``tmp_path``, finds .py /
    .md files, and passes them through ``AstPythonChunker`` /
    ``HeadingMarkdownChunker`` just like a user's project would.
    """
    (tmp_path / "pyproject.toml").write_text(_PYPROJECT, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(_APP_PY, encoding="utf-8")
    (tmp_path / "README.md").write_text(_README_MD, encoding="utf-8")
    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh SQLite DB file — schema materialised up front."""
    p = tmp_path / "e2e.db"
    open_index_database(p).close()
    return p


def _build_service(db_path: Path) -> IndexProjectService:
    """Wire the production-shape ``IndexProjectService`` over *db_path*.

    Uses :func:`build_sqlite_indexing_service` (which now wires
    ``tree_store=SqliteDocumentTreeStore``) + the real
    :class:`PipelineChunkExtractor` / :class:`AstMemberExtractor` /
    :class:`StaticDependencyResolver` strategies.
    """
    indexing = build_sqlite_indexing_service(db_path)
    pipeline = build_ingestion_pipeline(AppConfig.load())
    return IndexProjectService(
        indexing_service=indexing,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=PipelineChunkExtractor(pipeline=pipeline),
        member_extractor=AstMemberExtractor(),
    )


async def _run_indexing(fixture_project: Path, db_path: Path) -> None:
    """Run the real project indexing — project only, no deps."""
    service = _build_service(db_path)
    # include_project_source=True (default) but we skip deps to keep the
    # test hermetic: we never want the test's result to depend on which
    # site-packages dists happen to be installed.
    await service.index_project(
        fixture_project,
        force=True,
        include_project_source=True,
        # StaticDependencyResolver will resolve pyproject's (empty) deps; a
        # real dep list would have crossed into site-packages. By writing
        # pyproject.toml without declared deps we keep the test fully
        # hermetic without touching IndexProjectService's public API.
        workers=1,
    )


# ── Tests ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_project_indexing_persists_trees(
    fixture_project: Path, db_path: Path,
) -> None:
    """Full write path: pipeline → reindex_package → document_trees rows.

    After indexing, ``SqliteDocumentTreeStore.load_all_in_package`` for
    ``__project__`` must return at least the two modules we fed the
    pipeline (the .py module and the .md file).
    """
    await _run_indexing(fixture_project, db_path)

    # Open a FRESH store against the DB — no leftover connection from the
    # write side; proves the rows really persisted to disk.
    store = SqliteDocumentTreeStore(provider=build_connection_provider(db_path))
    trees = await store.load_all_in_package("__project__")

    assert trees, "no DocumentNode trees were persisted for __project__"
    # Every tree is a valid DocumentNode rooted at MODULE.
    for module_name, root in trees.items():
        assert isinstance(root, DocumentNode)
        assert root.kind is NodeKind.MODULE, (
            f"tree for {module_name!r} should be a MODULE root, got {root.kind}"
        )
        assert root.content_hash, (
            f"tree for {module_name!r} must carry a non-empty content_hash"
        )


@pytest.mark.asyncio
async def test_e2e_chunks_and_trees_share_package_name(
    fixture_project: Path, db_path: Path,
) -> None:
    """Chunks + trees + members all get tagged with the same package id.

    Canonical invariant — cross-table joins in query-side services depend
    on consistent ``package`` values. If the pipeline or the indexing
    service stamped e2e-test vs __project__ on different surfaces, this
    would fail.
    """
    await _run_indexing(fixture_project, db_path)

    provider = build_connection_provider(db_path)
    chunk_store = SqliteChunkRepository(provider=provider)
    member_store = SqliteModuleMemberRepository(provider=provider)
    tree_store = SqliteDocumentTreeStore(provider=provider)

    chunks = await chunk_store.list(
        filter={ChunkFilterField.PACKAGE.value: "__project__"}
    )
    members = await member_store.list(
        filter={ModuleMemberFilterField.PACKAGE.value: "__project__"}
    )
    trees = await tree_store.load_all_in_package("__project__")

    assert chunks, "no chunks were persisted for __project__"
    assert members, "no module-members were persisted for __project__"
    assert trees, "no trees were persisted for __project__"

    # No rows leaked under a different package label.
    for c in chunks:
        assert c.metadata[ChunkFilterField.PACKAGE.value] == "__project__"
    for m in members:
        assert m.metadata[ModuleMemberFilterField.PACKAGE.value] == "__project__"


@pytest.mark.asyncio
async def test_e2e_python_module_produces_module_tree_with_function_child(
    fixture_project: Path, db_path: Path,
) -> None:
    """The app.py source lands as a MODULE tree with a FUNCTION child.

    Verifies the write-side invariant: the ingestion pipeline emits a
    tree rooted at MODULE whose ``qualified_name`` matches the persisted
    module id, and ``hi`` shows up as a FUNCTION child with the expected
    dotted qualified_name.
    """
    await _run_indexing(fixture_project, db_path)

    store = SqliteDocumentTreeStore(provider=build_connection_provider(db_path))
    trees = await store.load_all_in_package("__project__")

    # find the tree for src/app.py — its module id is "src.app".
    app_tree = trees.get("src.app")
    assert app_tree is not None, (
        f"expected 'src.app' module tree in persisted trees; got keys {list(trees)}"
    )
    assert app_tree.kind is NodeKind.MODULE
    assert app_tree.qualified_name == "src.app"

    # FUNCTION child for hi() is present with correct qualified_name.
    functions = [c for c in app_tree.children if c.kind is NodeKind.FUNCTION]
    names = {c.qualified_name for c in functions}
    assert "src.app.hi" in names, (
        f"expected src.app.hi in FUNCTION children; got {names}"
    )
    # Every child should carry its own content_hash — used by incremental reindex.
    for child in app_tree.children:
        assert child.content_hash, (
            f"child {child.qualified_name!r} missing content_hash"
        )

    # CLASS child for Foo is present too (sanity — pins the tree shape).
    classes = [c for c in app_tree.children if c.kind is NodeKind.CLASS]
    class_names = {c.qualified_name for c in classes}
    assert "src.app.Foo" in class_names


@pytest.mark.asyncio
async def test_e2e_markdown_file_produces_module_tree_with_heading_children(
    fixture_project: Path, db_path: Path,
) -> None:
    """README.md lands as a MODULE tree with MARKDOWN_HEADING children.

    Exercises :class:`HeadingMarkdownChunker` through the full pipeline —
    a regression where .md ever stopped being routed to a chunker would
    produce an empty-children tree here.
    """
    await _run_indexing(fixture_project, db_path)

    store = SqliteDocumentTreeStore(provider=build_connection_provider(db_path))
    trees = await store.load_all_in_package("__project__")

    # README.md has no directory prefix — module id is just "README".
    readme = trees.get("README")
    assert readme is not None, (
        f"expected 'README' module tree in persisted trees; got keys {list(trees)}"
    )
    assert readme.kind is NodeKind.MODULE

    heading_kinds = [c.kind for c in readme.children]
    assert NodeKind.MARKDOWN_HEADING in heading_kinds, (
        f"expected MARKDOWN_HEADING children, got kinds={heading_kinds}"
    )
    # Title + Section — two in-range headings per the fixture markdown.
    headings = [c for c in readme.children if c.kind is NodeKind.MARKDOWN_HEADING]
    titles = {c.title for c in headings}
    assert "Title" in titles
    assert "Section" in titles


@pytest.mark.asyncio
async def test_e2e_get_document_tree_service_returns_saved_tree(
    fixture_project: Path, db_path: Path,
) -> None:
    """DocumentTreeService.get_tree — the read path used by get_document_tree MCP.

    Saves via the pipeline, retrieves via the service that backs the MCP
    handler, and verifies the retrieved tree is a DocumentNode with the
    expected root identity. This is the full write-then-read round trip
    the spec's AC #12 calls for.
    """
    await _run_indexing(fixture_project, db_path)

    tree_store = SqliteDocumentTreeStore(provider=build_connection_provider(db_path))
    service = DocumentTreeService(tree_store=tree_store)

    tree = await service.get_tree("__project__", "src.app")
    assert isinstance(tree, DocumentNode)
    assert tree.kind is NodeKind.MODULE
    assert tree.qualified_name == "src.app"
    # Children readable + non-empty (we wrote hi() + Foo into the fixture).
    assert tree.children, "expected child nodes (FUNCTION/CLASS) under MODULE"
