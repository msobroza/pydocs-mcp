"""Shared pytest fixtures for pydocs-mcp tests."""
import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.db import (
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.indexer import (
    _extract_from_source_files,
    clear_extraction_cache,
    extract_project_chunks,
    extract_project_members,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
)
from pydocs_mcp.storage.wiring import build_sqlite_indexing_service
from tests._retriever_helpers import write_package_sync


@pytest.fixture(autouse=True)
def _clear_extractor_cache():
    """Prevent indexer module-level extraction cache from leaking between tests.

    The :mod:`pydocs_mcp.indexer` module keeps two dicts (``_project_cache``
    and ``_dependency_cache``) that :class:`IndexProjectService` flushes at
    the end of each ``index_project`` call. Tests that exercise
    ``indexer.extract_*`` directly bypass that flush, so without this fixture
    a previous test's extraction could satisfy the next test's cache lookup.
    """
    clear_extraction_cache()
    yield
    clear_extraction_cache()


@pytest.fixture
def conn(tmp_path):
    """File-backed SQLite DB seeded with known project + dep data."""
    db_path = tmp_path / "test.db"
    # open_index_database materialises the schema; close it afterwards so
    # the IndexingService below can attach via its own connection factory.
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


@pytest.fixture
def integration_conn(tmp_path):
    """DB seeded by running the real indexer against fixture files.

    Indexes the fake_project source + the 3 package snapshots (sklearn, vllm,
    langgraph) using the static parser (_extract_from_source_files), then rebuilds FTS.
    """
    db_path = tmp_path / "integration.db"
    open_index_database(db_path).close()

    service = build_sqlite_indexing_service(db_path)

    # Index the fake project directly via the extraction functions so the
    # fixture doesn't also try to resolve the fake_project's declared
    # (not-installed) deps — matches what :class:`IndexProjectService`
    # does for the ``__project__`` package without the dep loop.
    async def _index_project_only() -> None:
        clear_extraction_cache()
        chunks, pkg = await extract_project_chunks(FAKE_PROJECT)
        members = await extract_project_members(FAKE_PROJECT)
        await service.reindex_package(pkg, chunks, members)

    asyncio.run(_index_project_only())

    # Index each package snapshot as if it were an installed dep.
    for pkg_name in ("sklearn", "vllm", "langgraph"):
        pkg_dir = PACKAGES_DIR / pkg_name
        py_files = sorted(str(p) for p in pkg_dir.rglob("*.py"))
        chunks, syms = _extract_from_source_files(
            pkg_name, py_files, str(pkg_dir), kind_prefix="dep",
        )
        write_package_sync(
            db_path,
            name=pkg_name,
            version="0.0.0",
            summary=f"{pkg_name} fixture",
            content_hash=f"fixture_{pkg_name}",
            chunks=tuple(chunks),
            module_members=tuple(syms),
        )

    c = open_index_database(db_path)
    rebuild_fulltext_index(c)
    yield c
    c.close()
