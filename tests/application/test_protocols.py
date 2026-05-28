"""Smoke test for application-layer Protocols (sub-PR #&).

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
    ExtractionResult,
    MemberExtractor,
)
from pydocs_mcp.models import ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.node_reference import NodeReference


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
    """Protocol amendment (spec §5, AC #19): an :class:`ExtractionResult`
    return shape conforms; ``trees`` may be empty."""

    class Fake:
        async def extract_from_project(self, project_dir: Path) -> ExtractionResult:
            return ExtractionResult(chunks=(), trees=(), package=_pkg("__project__"))

        async def extract_from_dependency(self, dep_name: str) -> ExtractionResult:
            return ExtractionResult(chunks=(), trees=(), package=_pkg(dep_name))

    assert isinstance(Fake(), ChunkExtractor)


def test_member_extractor_runtime_checkable() -> None:
    class Fake:
        async def extract_from_project(
            self,
            project_dir: Path,
        ) -> tuple[ModuleMember, ...]:
            return ()

        async def extract_from_dependency(
            self,
            dep_name: str,
        ) -> tuple[ModuleMember, ...]:
            return ()

    assert isinstance(Fake(), MemberExtractor)


def test_extraction_result_references_defaults_to_empty_tuple() -> None:
    """Spec §4.2, AC #21: ``references`` is optional.

    Existing extractors (sub-PR #5 chunkers, the AST/Inspect member
    extractors) construct :class:`ExtractionResult` with positional
    ``(chunks, trees, package)`` — adding a fourth field must not break
    them, so the default is an empty tuple.
    """
    result = ExtractionResult(chunks=(), trees=(), package=_pkg())
    assert result.references == ()


def test_extraction_result_accepts_node_references() -> None:
    """A ``ReferenceExtractionStage`` (later task) will emit real edges;
    the dataclass must accept them in the ``references`` slot."""
    from pydocs_mcp.extraction.reference_kind import ReferenceKind

    ref = NodeReference(
        from_package="pkg",
        from_node_id="pkg.mod.caller",
        to_name="pkg.mod.callee",
        to_node_id="pkg.mod.callee",
        kind=ReferenceKind.CALLS,
    )
    result = ExtractionResult(
        chunks=(),
        trees=(),
        package=_pkg("pkg"),
        references=(ref,),
    )
    assert result.references == (ref,)
