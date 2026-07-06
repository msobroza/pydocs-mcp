"""Tests for ``ProjectIndexer.index_project(include_dependencies=...)``.

The RepoQA benchmark indexes per-task repositories whose needles live in the
repo source — its declared dependencies are pure noise AND the dominant
ingestion cost. ``include_dependencies=False`` lets the runner skip the whole
dependency-resolution + dependency-indexing block for those datasets, while
reference-project datasets (DS-1000) keep the default ``True`` so their
declared libraries — which ARE the search target — still get indexed.

These tests use the same in-memory Protocol fakes as ``test_project_indexer``:
a spy ``DependencyResolver`` records whether ``resolve()`` was called, which is
the single observable that proves the dependency block ran (or was skipped).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.application.project_indexer import ProjectIndexer
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    Package,
    PackageOrigin,
)
from tests._fakes import InMemoryPackageStore, make_fake_uow_factory


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="1.0.0",
        summary=f"{name} summary",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=(PackageOrigin.PROJECT if name == "__project__" else PackageOrigin.DEPENDENCY),
    )


def _chunk(package: str, title: str) -> Chunk:
    return Chunk(
        text=f"{title} body",
        metadata={
            ChunkFilterField.PACKAGE.value: package,
            ChunkFilterField.TITLE.value: title,
        },
    )


@dataclass
class SpyDependencyResolver:
    """Records whether ``resolve()`` was invoked — the gating observable."""

    deps: tuple[str, ...] = ()
    resolve_calls: list[Path] = field(default_factory=list)

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        self.resolve_calls.append(project_dir)
        return self.deps


@dataclass
class FakeIndexingService:
    cleared: bool = False
    reindex_calls: list[Package] = field(default_factory=list)

    async def clear_all(self) -> None:
        self.cleared = True

    async def recompute_node_scores(self) -> None:
        self.node_scores_recomputed = True

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: tuple[DocumentNode, ...] = (),
        references: tuple = (),
        reference_aliases: dict[str, dict[str, str]] | None = None,
        class_attribute_types: dict[str, dict[str, str]] | None = None,
        decisions: tuple = (),  # spec §D8 — decision capture seam
        project_root=None,  # staleness scorer root (project path)
    ) -> None:
        self.reindex_calls.append(package)


@dataclass
class FakeChunkExtractor:
    project_package: Package | None = None
    dep_returns: dict[str, Any] = field(default_factory=dict)

    async def extract_from_project(self, project_dir: Path) -> ExtractionResult:
        assert self.project_package is not None
        return ExtractionResult(chunks=(), trees=(), package=self.project_package)

    async def extract_from_dependency(self, dep_name: str) -> ExtractionResult:
        chunks, pkg = self.dep_returns[dep_name]
        return ExtractionResult(chunks=chunks, trees=(), package=pkg)


@dataclass
class FakeMemberExtractor:
    async def extract_from_project(self, project_dir: Path) -> tuple[ModuleMember, ...]:
        return ()

    async def extract_from_dependency(self, dep_name: str) -> tuple[ModuleMember, ...]:
        return ()


def _make_indexer(
    *,
    deps: tuple[str, ...],
    dep_returns: dict[str, Any],
) -> tuple[ProjectIndexer, SpyDependencyResolver]:
    resolver = SpyDependencyResolver(deps=deps)
    indexer = ProjectIndexer(
        indexing_service=FakeIndexingService(),
        dependency_resolver=resolver,
        chunk_extractor=FakeChunkExtractor(
            project_package=_pkg("__project__"),
            dep_returns=dep_returns,
        ),
        member_extractor=FakeMemberExtractor(),
        uow_factory=make_fake_uow_factory(packages=InMemoryPackageStore(items={})),
    )
    return indexer, resolver


@pytest.mark.asyncio
async def test_include_dependencies_false_skips_resolution(tmp_path: Path) -> None:
    """RepoQA path: ``include_dependencies=False`` never calls the resolver."""
    indexer, resolver = _make_indexer(
        deps=("fastapi",),
        dep_returns={"fastapi": ((_chunk("fastapi", "T"),), _pkg("fastapi"))},
    )

    await indexer.index_project(tmp_path, include_dependencies=False)

    assert resolver.resolve_calls == [], (
        "include_dependencies=False must skip the dependency block entirely — "
        "resolve() should not be called"
    )


@pytest.mark.asyncio
async def test_include_dependencies_default_resolves(tmp_path: Path) -> None:
    """Default (DS-1000 + production): the resolver IS called."""
    indexer, resolver = _make_indexer(
        deps=("fastapi",),
        dep_returns={"fastapi": ((_chunk("fastapi", "T"),), _pkg("fastapi"))},
    )

    await indexer.index_project(tmp_path)

    assert resolver.resolve_calls == [tmp_path], (
        "default include_dependencies=True must resolve + index deps "
        "(production + DS-1000 behavior)"
    )
