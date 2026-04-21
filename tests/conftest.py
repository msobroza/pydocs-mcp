"""Shared pytest fixtures for pydocs-mcp tests."""
import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.db import (
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    build_ingestion_pipeline,
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
from pydocs_mcp.storage.wiring import build_sqlite_indexing_service
from tests._retriever_helpers import write_package_sync


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


async def _extract_tree_as_project(
    root: Path,
) -> tuple[tuple[Chunk, ...], tuple[ModuleMember, ...], Package]:
    """Extract chunks + members from *root* via the production ingestion pipeline.

    Both the fake project and the vendored fixture packages live on disk
    (not installed as distributions), so we route them through the
    :class:`PipelineChunkExtractor` project path — which uses file discovery
    instead of ``importlib.metadata``. The caller overrides the returned
    :class:`Package` for fixture-dep cases.
    """
    config = AppConfig.load()
    pipeline = build_ingestion_pipeline(config)
    chunk_extractor = PipelineChunkExtractor(pipeline=pipeline)
    chunks, _trees, pkg = await chunk_extractor.extract_from_project(root)
    members = await AstMemberExtractor().extract_from_project(root)
    return chunks, members, pkg


def _rewrite_chunk_package(chunks: tuple[Chunk, ...], new_package: str) -> tuple[Chunk, ...]:
    """Clone each :class:`Chunk` with its PACKAGE metadata key rewritten.

    Used for the fixture-dep override path: chunks are extracted via the
    PROJECT target (fixture packages aren't installed) so PackageBuildStage
    stamps ``__project__``; we re-stamp the real dep name before insertion.
    """
    rewritten: list[Chunk] = []
    for c in chunks:
        md = dict(c.metadata)
        md[ChunkFilterField.PACKAGE.value] = new_package
        rewritten.append(
            Chunk(
                text=c.text,
                id=c.id,
                relevance=c.relevance,
                retriever_name=c.retriever_name,
                metadata=md,
            )
        )
    return tuple(rewritten)


def _rewrite_member_package(
    members: tuple[ModuleMember, ...], new_package: str,
) -> tuple[ModuleMember, ...]:
    """Clone each :class:`ModuleMember` with its PACKAGE metadata key rewritten.

    See :func:`_rewrite_chunk_package` for rationale.
    """
    rewritten: list[ModuleMember] = []
    for m in members:
        md = dict(m.metadata)
        md[ModuleMemberFilterField.PACKAGE.value] = new_package
        rewritten.append(
            ModuleMember(
                id=m.id,
                relevance=m.relevance,
                retriever_name=m.retriever_name,
                metadata=md,
            )
        )
    return tuple(rewritten)


@pytest.fixture
def integration_conn(tmp_path):
    """DB seeded by running the ingestion pipeline against fixture files.

    Indexes the fake_project source + the 3 vendored fixture packages
    (sklearn, vllm, langgraph) via :class:`PipelineChunkExtractor` +
    :class:`AstMemberExtractor` — the production code path. Fixture
    packages aren't installed, so they flow through the PROJECT branch
    of the pipeline and the caller override-stamps the real package
    name on chunks, members, and the ``Package`` row.
    """
    db_path = tmp_path / "integration.db"
    open_index_database(db_path).close()

    service = build_sqlite_indexing_service(db_path)

    async def _seed() -> None:
        # -- Fake project: real __project__ name, no rewrite needed. --
        chunks, members, pkg = await _extract_tree_as_project(FAKE_PROJECT)
        await service.reindex_package(pkg, chunks, members)

        # -- Vendored fixture packages: re-stamp with the dep name. --
        for pkg_name in ("sklearn", "vllm", "langgraph"):
            pkg_dir = PACKAGES_DIR / pkg_name
            raw_chunks, raw_members, _ = await _extract_tree_as_project(pkg_dir)
            chunks = _rewrite_chunk_package(raw_chunks, pkg_name)
            members = _rewrite_member_package(raw_members, pkg_name)
            override = Package(
                name=pkg_name,
                version="0.0.0",
                summary=f"{pkg_name} fixture",
                homepage="",
                dependencies=(),
                content_hash=f"fixture_{pkg_name}",
                origin=PackageOrigin.DEPENDENCY,
            )
            await service.reindex_package(override, chunks, members)

    asyncio.run(_seed())

    c = open_index_database(db_path)
    rebuild_fulltext_index(c)
    yield c
    c.close()
