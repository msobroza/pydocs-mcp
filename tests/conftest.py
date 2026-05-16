"""Shared pytest fixtures for pydocs-mcp tests.

Post sub-PR #5 + #6 rebase: indexer.py has been deleted; extraction logic
now lives in the ``extraction/`` subpackage. Fixtures here use the new
``PipelineChunkExtractor`` + ``AstMemberExtractor`` strategy classes.
"""
import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.db import (
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    AstPythonChunker,
    PipelineChunkExtractor,
    build_ingestion_pipeline,
    flatten_to_chunks,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import build_sqlite_indexing_service
from tests._retriever_helpers import write_package_sync


@pytest.fixture
def conn(tmp_path):
    """File-backed SQLite DB seeded with known project + dep data."""
    db_path = tmp_path / "test.db"
    c = open_index_database(db_path)
    c.close()

    write_package_sync(
        db_path,
        name="__project__", version="0.1", summary="Test project",
        content_hash="aaa", origin=PackageOrigin.PROJECT,
        chunks=(
            Chunk(
                text="Compute the fibonacci sequence for n",
                metadata={
                    ChunkFilterField.PACKAGE.value: "__project__",
                    ChunkFilterField.TITLE.value: "fibonacci",
                    ChunkFilterField.ORIGIN.value: "project_code_section",
                },
            ),
            Chunk(
                text="Project overview and fibonacci examples",
                metadata={
                    ChunkFilterField.PACKAGE.value: "__project__",
                    ChunkFilterField.TITLE.value: "README",
                    ChunkFilterField.ORIGIN.value: "project_module_doc",
                },
            ),
        ),
        module_members=(
            ModuleMember(
                metadata={
                    ModuleMemberFilterField.PACKAGE.value: "__project__",
                    ModuleMemberFilterField.MODULE.value: "myapp.utils",
                    ModuleMemberFilterField.NAME.value: "fibonacci",
                    ModuleMemberFilterField.KIND.value: "function",
                    "signature": "(n: int)",
                    "return_annotation": "int",
                    "parameters": (),
                    "docstring": "Return nth fibonacci number",
                }
            ),
        ),
    )

    write_package_sync(
        db_path,
        name="requests", version="2.28", summary="HTTP library",
        content_hash="bbb",
        chunks=(
            Chunk(
                text="Send HTTP GET request to a URL",
                metadata={
                    ChunkFilterField.PACKAGE.value: "requests",
                    ChunkFilterField.TITLE.value: "get",
                    ChunkFilterField.ORIGIN.value: "dependency_code_section",
                },
            ),
        ),
        module_members=(
            ModuleMember(
                metadata={
                    ModuleMemberFilterField.PACKAGE.value: "requests",
                    ModuleMemberFilterField.MODULE.value: "requests.api",
                    ModuleMemberFilterField.NAME.value: "get",
                    ModuleMemberFilterField.KIND.value: "function",
                    "signature": "(url, **kwargs)",
                    "return_annotation": "Response",
                    "parameters": (),
                    "docstring": "Send GET request",
                }
            ),
        ),
    )

    write_package_sync(
        db_path,
        name="sqlalchemy", version="2.0", summary="Database toolkit",
        content_hash="ccc",
        chunks=(
            Chunk(
                text="Database session for ORM queries",
                metadata={
                    ChunkFilterField.PACKAGE.value: "sqlalchemy",
                    ChunkFilterField.TITLE.value: "Session",
                    ChunkFilterField.ORIGIN.value: "dependency_doc_file",
                },
            ),
        ),
        module_members=(
            ModuleMember(
                metadata={
                    ModuleMemberFilterField.PACKAGE.value: "sqlalchemy",
                    ModuleMemberFilterField.MODULE.value: "sqlalchemy.orm",
                    ModuleMemberFilterField.NAME.value: "Session",
                    ModuleMemberFilterField.KIND.value: "class",
                    "signature": "()",
                    "return_annotation": "None",
                    "parameters": (),
                    "docstring": "ORM session class",
                }
            ),
        ),
    )

    c = open_index_database(db_path)
    rebuild_fulltext_index(c)
    yield c
    c.close()


FIXTURES_DIR = Path(__file__).parent / "fixtures"
FAKE_PROJECT = FIXTURES_DIR / "fake_project"
PACKAGES_DIR = FIXTURES_DIR / "packages"


def _extract_package_fixture(name: str, pkg_dir: Path) -> tuple[tuple[Chunk, ...], tuple[ModuleMember, ...]]:
    """Static-parse a fixture package directory into (chunks, members).

    Uses AstPythonChunker directly on every .py file under pkg_dir; mirrors
    what extraction.discovery would do for a dependency, but without going
    through importlib.metadata (fixture packages aren't installed).
    """
    chunker = AstPythonChunker()
    chunks_acc: list[Chunk] = []
    members_acc: list[ModuleMember] = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tree = chunker.build_tree(str(py_file), source, name, pkg_dir)
        chunks_acc.extend(flatten_to_chunks(tree, package=name))
        # Extract member symbols via AST walk on top-level + class methods.
        import ast
        try:
            ast_tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = py_file.relative_to(pkg_dir)
        module = ".".join(rel.with_suffix("").parts).removesuffix(".__init__")
        for stmt in ast_tree.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(stmt, ast.ClassDef) else "function"
                doc = ast.get_docstring(stmt) or ""
                members_acc.append(ModuleMember(metadata={
                    ModuleMemberFilterField.PACKAGE.value: name,
                    ModuleMemberFilterField.MODULE.value: module,
                    ModuleMemberFilterField.NAME.value: stmt.name,
                    ModuleMemberFilterField.KIND.value: kind,
                    "signature": "",
                    "return_annotation": "",
                    "parameters": (),
                    "docstring": doc,
                }))
    return tuple(chunks_acc), tuple(members_acc)


@pytest.fixture
def integration_conn(tmp_path):
    """DB seeded by running the real extraction pipeline against fixture files.

    Indexes the fake_project source + the 3 package snapshots (sklearn, vllm,
    langgraph) using PipelineChunkExtractor + AstMemberExtractor, then rebuilds
    FTS. Mirrors what ProjectIndexer does for ``__project__`` without
    resolving the fixture project's declared (uninstalled) deps.
    """
    db_path = tmp_path / "integration.db"
    open_index_database(db_path).close()

    service = build_sqlite_indexing_service(db_path)

    async def _index_project_only() -> None:
        pipeline = build_ingestion_pipeline(AppConfig())
        extractor = PipelineChunkExtractor(pipeline=pipeline)
        members_extractor = AstMemberExtractor()
        chunks, _trees, pkg = await extractor.extract_from_project(FAKE_PROJECT)
        members = await members_extractor.extract_from_project(FAKE_PROJECT)
        await service.reindex_package(pkg, chunks, members)

    asyncio.run(_index_project_only())

    # Index each package snapshot as if it were an installed dep — using
    # _extract_package_fixture so we don't need importlib.metadata on the
    # fixture dirs (they aren't installed in the test environment).
    for pkg_name in ("sklearn", "vllm", "langgraph"):
        pkg_dir = PACKAGES_DIR / pkg_name
        chunks, members = _extract_package_fixture(pkg_name, pkg_dir)
        write_package_sync(
            db_path,
            name=pkg_name,
            version="0.0.0",
            summary=f"{pkg_name} fixture",
            content_hash=f"fixture_{pkg_name}",
            chunks=chunks,
            module_members=members,
        )

    c = open_index_database(db_path)
    rebuild_fulltext_index(c)
    yield c
    c.close()
