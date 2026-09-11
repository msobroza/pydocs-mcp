"""Real-pipeline src-layout index builder for the target-resolution integration
tests (spec 2026-09-10 §8, ``tests/test_src_layout_resolution.py``).

Follows the ``probe_db`` recipe (tests/test_reference_probe_regressions.py):
the REAL ingestion pipeline + ``IndexingService.reindex_package`` over a tiny
project on disk. Kept out of the test module so that file stays a readable
list of AC-mapped cases, and so the router wiring (real lookup/source services
behind fake search/overview) has one source.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
    ProjectServices,
)
from pydocs_mcp.application.null_services import NullDecisionService
from pydocs_mcp.application.target_resolution import NullTargetResolver
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    build_ingestion_pipeline,
)
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.models import Chunk, ChunkFilterField, ModuleMember, PackageOrigin
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import (
    build_sqlite_indexing_service,
    build_sqlite_lookup_service,
    build_sqlite_symbol_source_service,
)
from tests.application._router_fakes import (
    FakeApi,
    FakeDocs,
    FakeOverview,
    make_envelope,
    make_project,
)

# A dependency indexed under the SAME name as the project's top-level package
# (spec P2 / AC10): an exact ``srcpkg.…`` target must keep resolving to it,
# while a stripped ``src.srcpkg.…`` rewrite must render the __project__ node.
SHADOW_DEPENDENCY_NAME = "srcpkg"
PROJECT_SCORER_DOC = "Project MaxSim scorer."
DEPENDENCY_SCORER_DOC = "Dependency MaxSim scorer."

_PYPROJECT = '[project]\nname = "src-layout"\nversion = "0.0.0"\ndependencies = []\n'

_INIT_PY = '''"""Src-layout fixture package."""

from .scoring import MaxSimScorer

__all__ = ["MaxSimScorer"]
'''

_SCORING_PY = f'''"""Scoring strategies for the src-layout fixture."""


class Score:
    """A ranked score value."""

    def __init__(self, value: float) -> None:
        self.value = value


class MaxSimScorer:
    """{PROJECT_SCORER_DOC}"""

    def score(self, query: str, doc: str) -> Score:
        """Score one document with MaxSim."""
        return Score(float(len(query) + len(doc)))


class CosineScorer:
    """Project cosine scorer."""

    def score(self, query: str, doc: str) -> Score:
        """Score one document with cosine similarity."""
        return Score(1.0)
'''

_CLI_PY = '''"""Command-line entry point."""

from srcpkg.scoring import MaxSimScorer


def main() -> float:
    """Run the scorer once."""
    return MaxSimScorer().score("q", "d").value
'''

_RUN_PY = '''"""Standalone runner script."""


def main() -> None:
    """Run the script."""
    print("run")
'''

_AGENTS_MD = "# Agents\n\nUse the scorer in `srcpkg.scoring`.\n"

_DEP_SCORING_PY = f'''"""Dependency scoring module."""


class MaxSimScorer:
    """{DEPENDENCY_SCORER_DOC}"""

    def score(self, query: str, doc: str) -> float:
        """Dependency MaxSim score."""
        return 0.0
'''


def write_src_layout_project(project: Path) -> Path:
    """Write the §8 src-layout tree under ``project`` and return it."""
    pkg = project / "src" / "srcpkg"
    pkg.mkdir(parents=True)
    (project / "scripts").mkdir()
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (project / "AGENTS.md").write_text(_AGENTS_MD)
    (pkg / "__init__.py").write_text(_INIT_PY)
    (pkg / "scoring.py").write_text(_SCORING_PY)
    (pkg / "cli.py").write_text(_CLI_PY)
    (project / "scripts" / "run.py").write_text(_RUN_PY)
    return project


def _write_shadow_dependency(root: Path) -> Path:
    """A flat-layout ``srcpkg`` whose module ids equal the project's stripped ones."""
    pkg = root / "srcpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""Dependency srcpkg."""\n')
    (pkg / "scoring.py").write_text(_DEP_SCORING_PY)
    return root


async def _extract(source_dir: Path) -> tuple[ExtractionResult, tuple[ModuleMember, ...]]:
    from tests._fakes import MockEmbedder, make_fake_uow_factory

    pipeline = build_ingestion_pipeline(
        AppConfig(), embedder=MockEmbedder(), uow_factory=make_fake_uow_factory()
    )
    result = await PipelineChunkExtractor(pipeline=pipeline).extract_from_project(source_dir)
    members = await AstMemberExtractor().extract_from_project(source_dir)
    return result, tuple(members)


def _retag_chunk(chunk: Chunk, package: str) -> Chunk:
    metadata = {**chunk.metadata, ChunkFilterField.PACKAGE.value: package}
    return dataclasses.replace(chunk, metadata=metadata)


def _retag_member(member: ModuleMember, package: str) -> ModuleMember:
    return dataclasses.replace(member, metadata={**member.metadata, "package": package})


def _as_dependency(
    result: ExtractionResult, members: tuple[ModuleMember, ...], package: str
) -> tuple[ExtractionResult, tuple[ModuleMember, ...]]:
    """Re-tag a project extraction as dependency ``package`` — the rows the
    indexer's dependency path writes (package name + origin on every row)."""
    retagged = dataclasses.replace(
        result,
        package=dataclasses.replace(result.package, name=package, origin=PackageOrigin.DEPENDENCY),
        chunks=tuple(_retag_chunk(c, package) for c in result.chunks),
        references=(),
    )
    return retagged, tuple(_retag_member(m, package) for m in members)


async def _reindex(db_path: Path, result: ExtractionResult, members: tuple[ModuleMember, ...]):
    await build_sqlite_indexing_service(db_path).reindex_package(
        result.package,
        result.chunks,
        members,
        trees=result.trees,
        references=result.references,
        reference_aliases=result.reference_aliases,
        class_attribute_types=result.class_attribute_types,
    )


def build_src_layout_db(root: Path, *, with_shadow_dependency: bool = False) -> Path:
    """Index the src-layout project (plus, optionally, a same-named dependency)."""
    project = write_src_layout_project(root / "project")
    db_path = root / "src_layout.db"
    open_index_database(db_path).close()

    async def _index() -> None:
        await _reindex(db_path, *await _extract(project))
        if with_shadow_dependency:
            dep = _write_shadow_dependency(root / "dependency")
            await _reindex(db_path, *_as_dependency(*await _extract(dep), SHADOW_DEPENDENCY_NAME))

    asyncio.run(_index())
    conn = open_index_database(db_path)
    rebuild_fulltext_index(conn)
    conn.close()
    return db_path


def build_router(db_path: Path, config: AppConfig, *, null_resolver: bool = False) -> ToolRouter:
    """A ``ToolRouter`` over the production lookup/source factories for ``config``.

    ``null_resolver=True`` swaps in ``NullTargetResolver`` — the origin/main
    behaviour baseline every byte-identity comparison is made against.
    """
    lookup = build_sqlite_lookup_service(db_path, config)
    if null_resolver:
        lookup = dataclasses.replace(lookup, target_resolver=NullTargetResolver())
    services = (
        ProjectServices(
            project=make_project(),
            docs=FakeDocs(),
            api=FakeApi(),
            lookup=lookup,
            symbol_source=build_sqlite_symbol_source_service(db_path, config),
            overview=FakeOverview(),
            decisions=NullDecisionService(),
        ),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(
            services=services, target_resolution=config.target_resolution
        ),
    )


def run_tool(coro) -> tuple[object, ...]:
    """Run one router call; a response → ``(text, items, meta)``, a raise →
    ``("raised", type, message)`` — one comparable shape for byte-identity."""
    try:
        response = asyncio.run(coro)
    except Exception as exc:  # NotFoundError and any other raise compare alike
        return ("raised", type(exc).__name__, str(exc))
    return (response.text, response.items, response.meta)
