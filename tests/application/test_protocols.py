"""Smoke test for application-layer Protocols (Task 1 — sub-PR #4).

Covers:
- Imports resolve.
- Each Protocol is ``runtime_checkable`` (usable with ``isinstance``).
- A minimal conforming class passes ``isinstance`` — the duck-typing
  contract subsequent tasks (2–9) will rely on when they register
  concrete adapters on top of these Protocols.
"""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)
from pydocs_mcp.extraction.document_node import DocumentNode
from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin


def _pkg(name: str = "x") -> Package:
    # Package is frozen + slotted with every field required; build a
    # minimal but valid instance so the Fake extractors below can return
    # real model objects (not Mock stand-ins) if a future caller ever
    # exercises the attribute.
    return Package(
        name=name,
        version="0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="",
        origin=PackageOrigin.DEPENDENCY,
    )


def test_dependency_resolver_runtime_checkable() -> None:
    class Fake:
        async def resolve(self, project_dir: Path) -> tuple[str, ...]:
            return ()

    assert isinstance(Fake(), DependencyResolver)


def test_chunk_extractor_runtime_checkable() -> None:
    """Protocol amendment (spec §5, AC #19): the 3-tuple return shape
    ``(chunks, trees, package)`` conforms; ``trees`` may be empty."""
    class Fake:
        async def extract_from_project(
            self, project_dir: Path,
        ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
            return ((), (), _pkg("__project__"))

        async def extract_from_dependency(
            self, dep_name: str,
        ) -> tuple[tuple[Chunk, ...], tuple[DocumentNode, ...], Package]:
            return ((), (), _pkg(dep_name))

    assert isinstance(Fake(), ChunkExtractor)


def test_member_extractor_runtime_checkable() -> None:
    class Fake:
        async def extract_from_project(
            self, project_dir: Path,
        ) -> tuple[ModuleMember, ...]:
            return ()

        async def extract_from_dependency(
            self, dep_name: str,
        ) -> tuple[ModuleMember, ...]:
            return ()

    assert isinstance(Fake(), MemberExtractor)
