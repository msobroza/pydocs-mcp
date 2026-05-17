"""Tests for ProjectIndexer — write-side bootstrap orchestrator (spec §5.1).

ProjectIndexer depends only on Protocols:
- ``IndexingService`` (already tested with Protocol fakes in test_indexing_service.py)
- ``DependencyResolver`` / ``ChunkExtractor`` / ``MemberExtractor`` (from
  ``application.protocols``, ``@runtime_checkable``)

These tests use in-memory fakes that structurally satisfy each Protocol — no
real extraction imports, no SQLite, no network. The concrete strategy classes
in ``extraction/strategies/`` and ``extraction/pipeline/`` wire to the
``extract_*`` Protocols; the service itself is backend and adapter agnostic.
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
    IndexingStats,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
)
from tests._fakes import InMemoryPackageStore, make_fake_uow_factory


# ── Helpers ────────────────────────────────────────────────────────────────


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="1.0.0",
        summary=f"{name} summary",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=(
            PackageOrigin.PROJECT if name == "__project__" else PackageOrigin.DEPENDENCY
        ),
    )


def _chunk(package: str, title: str) -> Chunk:
    return Chunk(
        text=f"{title} body",
        metadata={
            ChunkFilterField.PACKAGE.value: package,
            ChunkFilterField.TITLE.value: title,
        },
    )


def _member(package: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.MODULE.value: f"{package}.core",
            ModuleMemberFilterField.KIND.value: "function",
        },
    )


# ── Protocol fakes ─────────────────────────────────────────────────────────


@dataclass
class FakeIndexingService:
    """Stands in for application.IndexingService — records the call sequence."""

    cleared: bool = False
    clear_call_order: int | None = None
    reindex_calls: list[
        tuple[
            Package,
            tuple[Chunk, ...],
            tuple[ModuleMember, ...],
            tuple[DocumentNode, ...],
        ]
    ] = field(default_factory=list)
    _call_counter: int = 0

    async def clear_all(self) -> None:
        self._call_counter += 1
        self.cleared = True
        self.clear_call_order = self._call_counter

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: tuple[DocumentNode, ...] = (),
        references: tuple = (),  # spec §3.1 — accept the #5b seam
    ) -> None:
        self._call_counter += 1
        self.reindex_calls.append((package, chunks, module_members, tuple(trees)))


@dataclass
class FakeDependencyResolver:
    """Protocol fake for application.protocols.DependencyResolver."""

    deps: tuple[str, ...] = ()
    resolve_calls: list[Path] = field(default_factory=list)

    async def resolve(self, project_dir: Path) -> tuple[str, ...]:
        self.resolve_calls.append(project_dir)
        return self.deps


@dataclass
class FakeChunkExtractor:
    """Protocol fake for application.protocols.ChunkExtractor.

    Spec §5 amendment: the Protocol returns a 3-tuple
    ``(chunks, trees, package)``. Test fixtures pass ``(chunks, pkg)`` and
    the fake wraps them with an empty ``trees=()``; real strategies emit
    actual trees.

    ``dep_returns`` maps dep-name → either the extractor return value
    (2-tuple OR 3-tuple; both accepted for test-terseness) or an
    ``Exception`` instance; when the entry is an exception we raise it so
    tests can exercise the _index_one_dependency failure path.
    """

    project_chunks: tuple[Chunk, ...] = ()
    project_package: Package | None = None
    project_trees: tuple[DocumentNode, ...] = ()
    dep_returns: dict[str, Any] = field(default_factory=dict)
    project_calls: list[Path] = field(default_factory=list)
    dep_calls: list[str] = field(default_factory=list)

    async def extract_from_project(
        self, project_dir: Path,
    ) -> ExtractionResult:
        self.project_calls.append(project_dir)
        assert self.project_package is not None, "Configure project_package first"
        return ExtractionResult(
            chunks=self.project_chunks,
            trees=self.project_trees,
            package=self.project_package,
        )

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> ExtractionResult:
        self.dep_calls.append(dep_name)
        entry = self.dep_returns.get(dep_name)
        if isinstance(entry, BaseException):
            raise entry
        assert entry is not None, f"No chunk-extractor result configured for {dep_name}"
        # Tests can configure dep_returns with any of three shapes:
        # - an ExtractionResult (preferred for new tests);
        # - a 3-tuple ``(chunks, trees, pkg)`` (matches the older
        #   destructuring convention);
        # - a bare 2-tuple ``(chunks, pkg)`` (no trees needed).
        # Widen the tuples here so the test surface stays terse.
        if isinstance(entry, ExtractionResult):
            return entry
        if len(entry) == 3:
            chunks, trees, pkg = entry
            return ExtractionResult(chunks=chunks, trees=trees, package=pkg)
        chunks, pkg = entry
        return ExtractionResult(chunks=chunks, trees=(), package=pkg)


@dataclass
class FakeMemberExtractor:
    """Protocol fake for application.protocols.MemberExtractor."""

    project_members: tuple[ModuleMember, ...] = ()
    dep_returns: dict[str, Any] = field(default_factory=dict)
    project_calls: list[Path] = field(default_factory=list)
    dep_calls: list[str] = field(default_factory=list)

    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]:
        self.project_calls.append(project_dir)
        return self.project_members

    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]:
        self.dep_calls.append(dep_name)
        entry = self.dep_returns.get(dep_name)
        if isinstance(entry, BaseException):
            raise entry
        # Missing entry for a dep is fine — return empty so the chunk side's
        # exception (if any) is the only error path exercised in isolation.
        return entry if entry is not None else ()


def _make_service(
    *,
    deps: tuple[str, ...] = (),
    project_pkg: Package | None = None,
    project_chunks: tuple[Chunk, ...] = (),
    project_members: tuple[ModuleMember, ...] = (),
    dep_chunk_returns: dict[str, Any] | None = None,
    dep_member_returns: dict[str, Any] | None = None,
    cached_packages: dict[str, Package] | None = None,
) -> tuple[
    ProjectIndexer,
    FakeIndexingService,
    FakeDependencyResolver,
    FakeChunkExtractor,
    FakeMemberExtractor,
    InMemoryPackageStore,  # NEW: returned so tests can assert on get() calls
]:
    idx = FakeIndexingService()
    resolver = FakeDependencyResolver(deps=deps)
    chunks_ex = FakeChunkExtractor(
        project_chunks=project_chunks,
        project_package=project_pkg,
        dep_returns=dep_chunk_returns or {},
    )
    members_ex = FakeMemberExtractor(
        project_members=project_members,
        dep_returns=dep_member_returns or {},
    )
    pkg_store = InMemoryPackageStore(items=dict(cached_packages or {}))
    uow_factory = make_fake_uow_factory(packages=pkg_store)
    service = ProjectIndexer(
        indexing_service=idx,
        dependency_resolver=resolver,
        chunk_extractor=chunks_ex,
        member_extractor=members_ex,
        uow_factory=uow_factory,
    )
    return service, idx, resolver, chunks_ex, members_ex, pkg_store


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_project_force_clears_first(tmp_path: Path) -> None:
    """``force=True`` wipes everything before the first extraction call."""
    project_pkg = _pkg("__project__")
    service, idx, _resolver, chunks_ex, _members_ex, _pkg_store = _make_service(
        project_pkg=project_pkg,
    )

    stats = await service.index_project(tmp_path, force=True)

    assert idx.cleared is True
    # clear_all is the very first call to IndexingService (call_order == 1),
    # extraction happens afterwards — and only then reindex_package fires.
    assert idx.clear_call_order == 1
    assert len(idx.reindex_calls) == 1
    # Project extraction still happens after the clear.
    assert chunks_ex.project_calls == [tmp_path]
    assert stats.project_indexed is True


@pytest.mark.asyncio
async def test_index_project_skips_source_when_include_false(tmp_path: Path) -> None:
    """``include_project_source=False`` skips project extraction; deps still run."""
    dep_pkg = _pkg("fastapi")
    dep_chunks = (_chunk("fastapi", "Routing"),)
    dep_members = (_member("fastapi", "APIRouter"),)

    service, idx, resolver, chunks_ex, members_ex, _pkg_store = _make_service(
        deps=("fastapi",),
        dep_chunk_returns={"fastapi": (dep_chunks, dep_pkg)},
        dep_member_returns={"fastapi": dep_members},
    )

    stats = await service.index_project(tmp_path, include_project_source=False)

    # No project extraction at all.
    assert chunks_ex.project_calls == []
    assert members_ex.project_calls == []
    assert stats.project_indexed is False
    # But dep extraction and reindex still happen.
    assert chunks_ex.dep_calls == ["fastapi"]
    assert members_ex.dep_calls == ["fastapi"]
    assert resolver.resolve_calls == [tmp_path]
    assert len(idx.reindex_calls) == 1
    assert idx.reindex_calls[0][0] is dep_pkg
    assert stats.indexed == 1


@pytest.mark.asyncio
async def test_index_project_resolves_and_indexes_each_dep(tmp_path: Path) -> None:
    """Resolver returns ("a", "b"); each dep is extracted once and reindexed."""
    pkg_a, pkg_b = _pkg("a"), _pkg("b")
    project_pkg = _pkg("__project__")

    service, idx, resolver, chunks_ex, members_ex, _pkg_store = _make_service(
        deps=("a", "b"),
        project_pkg=project_pkg,
        dep_chunk_returns={
            "a": ((_chunk("a", "T"),), pkg_a),
            "b": ((_chunk("b", "T"),), pkg_b),
        },
        dep_member_returns={
            "a": (_member("a", "F"),),
            "b": (_member("b", "F"),),
        },
    )

    stats = await service.index_project(tmp_path)

    # Resolver called exactly once with project_dir.
    assert resolver.resolve_calls == [tmp_path]
    # Chunk + member extractors each called for both deps, in order.
    assert chunks_ex.dep_calls == ["a", "b"]
    assert members_ex.dep_calls == ["a", "b"]
    # Three reindexes total: project + each dep.
    assert len(idx.reindex_calls) == 3
    reindexed_names = [pkg.name for pkg, _, _, _ in idx.reindex_calls]
    assert reindexed_names == ["__project__", "a", "b"]
    # IndexingStats reflects the two successful deps + the project flag.
    assert stats.project_indexed is True
    assert stats.indexed == 2
    assert stats.failed == 0
    assert isinstance(stats, IndexingStats)


@pytest.mark.asyncio
async def test_index_one_dependency_increments_failed_on_exception(
    tmp_path: Path,
) -> None:
    """A raising chunk-extractor must NOT abort the pass — stats.failed += 1
    and subsequent deps still process (spec §7)."""
    pkg_good = _pkg("good")
    service, idx, _resolver, chunks_ex, _members_ex, _pkg_store = _make_service(
        deps=("bad-dep", "good"),
        dep_chunk_returns={
            "bad-dep": RuntimeError("simulated pypi metadata corruption"),
            "good": ((_chunk("good", "T"),), pkg_good),
        },
        dep_member_returns={"good": ()},
    )
    # No project source so we isolate the dep loop.
    stats = await service.index_project(tmp_path, include_project_source=False)

    # Failure was caught, not re-raised.
    assert stats.failed == 1
    assert stats.indexed == 1
    # Both deps were attempted.
    assert chunks_ex.dep_calls == ["bad-dep", "good"]
    # Only the good dep made it to reindex_package.
    assert len(idx.reindex_calls) == 1
    assert idx.reindex_calls[0][0] is pkg_good


@pytest.mark.asyncio
async def test_index_one_dependency_failure_logs_warning(
    tmp_path: Path, caplog
) -> None:
    """On failure the service logs a warning that includes the dep name."""
    import logging

    service, _idx, _resolver, _chunks, _members, _pkg_store = _make_service(
        deps=("explode",),
        dep_chunk_returns={"explode": RuntimeError("boom")},
    )
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        stats = await service.index_project(tmp_path, include_project_source=False)

    assert stats.failed == 1
    # Name of the failing dep must appear in the log so operators can grep.
    assert any("explode" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_index_one_dependency_success_increments_indexed(
    tmp_path: Path,
) -> None:
    """Happy-path single dep: stats.indexed += 1; reindex_package called once."""
    pkg = _pkg("httpx")
    service, idx, _resolver, _chunks, _members, _pkg_store = _make_service(
        deps=("httpx",),
        dep_chunk_returns={"httpx": ((_chunk("httpx", "Client"),), pkg)},
        dep_member_returns={"httpx": (_member("httpx", "Client"),)},
    )

    stats = await service.index_project(tmp_path, include_project_source=False)

    assert stats.indexed == 1
    assert stats.failed == 0
    assert len(idx.reindex_calls) == 1
    reindexed_pkg, reindexed_chunks, reindexed_members, _trees = idx.reindex_calls[0]
    assert reindexed_pkg is pkg
    assert len(reindexed_chunks) == 1
    assert len(reindexed_members) == 1


@pytest.mark.asyncio
async def test_index_project_without_force_does_not_clear(tmp_path: Path) -> None:
    """Default (``force=False``) leaves the index intact before reindexing."""
    project_pkg = _pkg("__project__")
    service, idx, _resolver, _chunks, _members, _pkg_store = _make_service(
        project_pkg=project_pkg,
    )

    await service.index_project(tmp_path)

    assert idx.cleared is False
    assert idx.clear_call_order is None


@pytest.mark.asyncio
async def test_index_project_returns_fresh_stats_each_call(tmp_path: Path) -> None:
    """Each ``index_project`` call returns an independent IndexingStats
    accumulator — no leaked mutable state between invocations."""
    pkg = _pkg("httpx")
    service, _idx, _resolver, _chunks, _members, _pkg_store = _make_service(
        deps=("httpx",),
        dep_chunk_returns={"httpx": ((_chunk("httpx", "T"),), pkg)},
        dep_member_returns={"httpx": ()},
    )

    a = await service.index_project(tmp_path, include_project_source=False)
    b = await service.index_project(tmp_path, include_project_source=False)

    assert a is not b
    assert a.indexed == 1
    assert b.indexed == 1


def test_project_indexer_is_frozen_and_slotted() -> None:
    """Frozen + slots — matches the SOLID pattern shared by the other
    application services (spec §5.1). Prevents silent attribute typos
    and silent field rebinds.

    ``dataclasses.FrozenInstanceError`` subclasses ``AttributeError`` so we
    still catch it via the wider ``Exception`` type to stay decoupled from
    its exact class name. ``slots=True`` rejects unknown attrs with a
    ``TypeError`` on a frozen class (setattr fails through ``object.__setattr__``
    before dataclass notices the frozen guard).
    """
    import dataclasses

    service, _idx, _resolver, _chunks, _members, _pkg_store = _make_service(
        project_pkg=_pkg("__project__"),
    )
    # Frozen: can't rebind a declared field.
    with pytest.raises(dataclasses.FrozenInstanceError):
        service.indexing_service = None  # type: ignore[misc]
    # Slots: unknown attribute not present in __slots__ → TypeError.
    with pytest.raises((AttributeError, TypeError)):
        service.bogus = 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_index_project_forwards_trees_to_reindex_package(
    tmp_path: Path,
) -> None:
    """Spec §5 + §13.3: ``ChunkExtractor`` returns 3-tuple
    ``(chunks, trees, package)``; :class:`ProjectIndexer` forwards
    ``trees`` to :meth:`IndexingService.reindex_package` so the wired
    ``DocumentTreeStore`` persists them. Both project + dep branches must
    forward.

    This test exercises the forwarding path for both the project branch
    (uses ``project_trees`` on the fake) and the dep branch (uses a
    raw 3-tuple in ``dep_returns``).
    """
    # Build a DocumentNode stub so the tree input is non-empty and we
    # prove the service really is passing it through.
    from pydocs_mcp.extraction.model import NodeKind

    tree = DocumentNode(
        node_id="pkg.mod",
        qualified_name="pkg.mod",
        title="mod",
        kind=NodeKind.MODULE,
        source_path="pkg/mod.py",
        start_line=1,
        end_line=10,
        text="...",
        content_hash="h",
    )
    project_pkg = _pkg("__project__")
    dep_pkg = _pkg("fastapi")
    service, idx, _resolver, chunks_ex, _members, _pkg_store = _make_service(
        deps=("fastapi",),
        project_pkg=project_pkg,
        dep_chunk_returns={
            "fastapi": ((_chunk("fastapi", "T"),), (tree,), dep_pkg),
        },
        dep_member_returns={"fastapi": ()},
    )
    chunks_ex.project_trees = (tree,)

    stats = await service.index_project(tmp_path)

    # Both extractions ran.
    assert chunks_ex.project_calls == [tmp_path]
    assert chunks_ex.dep_calls == ["fastapi"]
    # Each produced a reindex_package call AND trees rode through to the
    # store — end-to-end invariant. The FakeIndexingService
    # records (pkg, chunks, members, trees); all four assertions hold.
    assert len(idx.reindex_calls) == 2
    for _pkg_arg, chunks_arg, members_arg, trees_arg in idx.reindex_calls:
        assert all(isinstance(c, Chunk) for c in chunks_arg)
        assert all(isinstance(m, ModuleMember) for m in members_arg)
        assert trees_arg == (tree,), (
            "ProjectIndexer must forward the extractor's trees to "
            "IndexingService.reindex_package — the DocumentTreeStore "
            "round-trip depends on this wiring."
        )
    assert stats.project_indexed is True
    assert stats.indexed == 1


@pytest.mark.asyncio
async def test_index_project_workers_1_is_serial(tmp_path: Path) -> None:
    """With ``workers=1`` deps run one-at-a-time in resolver order — the
    deterministic path that tests and byte-parity depend on.
    """
    pkg_a, pkg_b = _pkg("a"), _pkg("b")
    service, idx, _resolver, chunks_ex, _members, _pkg_store = _make_service(
        deps=("a", "b"),
        dep_chunk_returns={
            "a": ((_chunk("a", "T"),), pkg_a),
            "b": ((_chunk("b", "T"),), pkg_b),
        },
        dep_member_returns={"a": (), "b": ()},
    )

    stats = await service.index_project(
        tmp_path, include_project_source=False, workers=1,
    )

    # Order preserved because the serial branch iterates the resolver tuple.
    assert chunks_ex.dep_calls == ["a", "b"]
    assert stats.indexed == 2
    assert [pkg.name for pkg, _, _, _ in idx.reindex_calls] == ["a", "b"]


@pytest.mark.asyncio
async def test_index_project_workers_N_allows_concurrent(tmp_path: Path) -> None:
    """With ``workers>1`` multiple deps can enter extraction at the same
    time. We prove it by having each extractor bump an observable
    "in-flight" counter and assert that the max observed value is > 1.
    """
    import asyncio

    pkg_a, pkg_b, pkg_c = _pkg("a"), _pkg("b"), _pkg("c")

    # Custom chunk-extractor that blocks until released so we can force
    # multiple tasks to be in extract_from_dependency simultaneously.
    @dataclass
    class ConcurrencyProbeExtractor:
        in_flight: int = 0
        max_in_flight: int = 0
        gate: asyncio.Event = field(default_factory=asyncio.Event)
        dep_returns: dict[str, Any] = field(default_factory=dict)
        dep_calls: list[str] = field(default_factory=list)

        async def extract_from_project(self, project_dir: Path):  # pragma: no cover
            raise AssertionError("project extraction should be skipped in this test")

        async def extract_from_dependency(
            self, dep_name: str,
        ) -> ExtractionResult:
            self.dep_calls.append(dep_name)
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            # Yield once so all workers get a chance to enter before the
            # first returns — otherwise a single fast path wins and we can't
            # observe concurrency on a fast machine.
            await asyncio.sleep(0)
            try:
                chunks, pkg = self.dep_returns[dep_name]
                return ExtractionResult(chunks=chunks, trees=(), package=pkg)
            finally:
                self.in_flight -= 1

    probe = ConcurrencyProbeExtractor(
        dep_returns={
            "a": ((_chunk("a", "T"),), pkg_a),
            "b": ((_chunk("b", "T"),), pkg_b),
            "c": ((_chunk("c", "T"),), pkg_c),
        },
    )

    service, idx, _resolver, _chunks, _members, _pkg_store = _make_service(
        deps=("a", "b", "c"),
        dep_member_returns={"a": (), "b": (), "c": ()},
    )
    # Swap in the concurrency-tracking chunk extractor.
    service = ProjectIndexer(
        indexing_service=service.indexing_service,
        dependency_resolver=service.dependency_resolver,
        chunk_extractor=probe,
        member_extractor=service.member_extractor,
        uow_factory=service.uow_factory,
    )

    stats = await service.index_project(
        tmp_path, include_project_source=False, workers=3,
    )

    assert stats.indexed == 3
    assert stats.failed == 0
    # With a semaphore of 3, all three extractors should co-exist briefly.
    assert probe.max_in_flight > 1, (
        f"gather path did not run concurrently: max_in_flight={probe.max_in_flight}"
    )
    # All three reindex_package calls landed (order is non-deterministic
    # under gather, so sort by name before asserting equality).
    assert sorted(pkg.name for pkg, _, _, _ in idx.reindex_calls) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_project_indexer_uses_own_uow_factory_for_cache_check(tmp_path: Path) -> None:
    """spec §3.1 — ProjectIndexer reads cached package row via uow.packages.get,
    not via reach-through to indexing_service.package_store."""
    cached = _pkg("__project__")
    cached_pkg_with_hash = Package(
        name=cached.name, version=cached.version, summary=cached.summary,
        homepage=cached.homepage, dependencies=cached.dependencies,
        content_hash="h", origin=cached.origin,
    )
    project_pkg_same_hash = Package(
        name="__project__", version="0", summary="", homepage="",
        dependencies=(), content_hash="h",  # identical hash → cached
        origin=PackageOrigin.PROJECT,
    )

    service, idx, _resolver, _chunks_ex, _members_ex, pkg_store = _make_service(
        project_pkg=project_pkg_same_hash,
        cached_packages={"__project__": cached_pkg_with_hash},
    )

    stats = await service.index_project(tmp_path)

    # Cached → reindex_package NOT called; only the get on packages.
    assert len(idx.reindex_calls) == 0
    assert stats.project_indexed is False
    # Reach-through proof: pkg_store.calls shows the get.
    assert any(c.method == "get" and c.payload == "__project__" for c in pkg_store.calls)
