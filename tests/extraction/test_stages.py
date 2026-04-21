"""Unit tests for ``extraction/stages.py`` (Task 20 — sub-PR #5, spec §7.2).

Pins the 6-stage behavior:
- Each stage is registered via ``@stage_registry.register("<type>")`` — all
  6 stage types are discoverable by ``stage_registry.names()``.
- Each stage returns a NEW :class:`IngestionState` (via ``dataclasses.replace``)
  — never mutates in place.
- ``FileDiscoveryStage`` / ``PackageBuildStage`` branch on ``state.target_kind``
  — one stage instance handles both PROJECT and DEPENDENCY modes.
- ``ChunkingStage`` enforces AC #27 per-file failure isolation: if one chunker
  raises, remaining files still process and a warning is logged.
- Every stage implements ``from_dict(data, context)`` + ``to_dict()`` so the
  ingestion YAML round-trips.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import ChunkingConfig, ExtractionConfig
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.extraction.pipeline import IngestionState, TargetKind
from pydocs_mcp.extraction.serialization import chunker_registry, stage_registry
from pydocs_mcp.extraction.stages import (
    ChunkingStage,
    ContentHashStage,
    FileDiscoveryStage,
    FileReadStage,
    FlattenStage,
    PackageBuildStage,
)
from pydocs_mcp.models import Package, PackageOrigin


# ── BuildContext stub ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _FakeBuildContext:
    """Minimal stand-in for retrieval.serialization.BuildContext. ``from_dict``
    decoders only read ``app_config`` for extraction stages; the other
    BuildContext fields are irrelevant here so we don't construct them."""

    app_config: object  # keep the shape open — tests pass a real AppConfig or a stub


@dataclass(frozen=True)
class _FakeAppConfig:
    """AppConfig duck for extraction stages — only exposes the ``extraction``
    sub-config those stages actually read in ``from_dict``."""

    extraction: ExtractionConfig


def _ctx() -> _FakeBuildContext:
    return _FakeBuildContext(app_config=_FakeAppConfig(extraction=ExtractionConfig()))


# ── FileDiscoveryStage ────────────────────────────────────────────────────

@dataclass
class _FakeProjectDiscoverer:
    """Records which discover() call was invoked; returns a canned (paths, root)."""

    calls: list = None
    result: tuple = ((), Path("."))

    def __post_init__(self) -> None:
        if self.calls is None:
            object.__setattr__(self, "calls", [])

    def discover(self, target: Path) -> tuple[list[str], Path]:
        self.calls.append(("project", target))
        return self.result


@dataclass
class _FakeDepDiscoverer:
    calls: list = None
    result: tuple = ((), Path("."))

    def __post_init__(self) -> None:
        if self.calls is None:
            object.__setattr__(self, "calls", [])

    def discover(self, target: str) -> tuple[list[str], Path]:
        self.calls.append(("dep", target))
        return self.result


@pytest.mark.asyncio
async def test_file_discovery_branches_on_project_target(tmp_path: Path) -> None:
    """PROJECT target_kind dispatches to project_discoverer, not dep_discoverer."""
    project_disc = _FakeProjectDiscoverer(
        result=([str(tmp_path / "a.py")], tmp_path),
    )
    dep_disc = _FakeDepDiscoverer()

    stage = FileDiscoveryStage(
        project_discoverer=project_disc, dep_discoverer=dep_disc,
    )
    state = IngestionState(target=tmp_path, target_kind=TargetKind.PROJECT)

    out = await stage.run(state)

    assert [c[0] for c in project_disc.calls] == ["project"]
    assert dep_disc.calls == []  # dep branch NOT taken
    assert out.paths == (str(tmp_path / "a.py"),)
    assert out.root == tmp_path


@pytest.mark.asyncio
async def test_file_discovery_branches_on_dependency_target() -> None:
    """DEPENDENCY target_kind dispatches to dep_discoverer, not project_discoverer."""
    project_disc = _FakeProjectDiscoverer()
    dep_disc = _FakeDepDiscoverer(result=(["/pkgs/foo/mod.py"], Path("/pkgs")))

    stage = FileDiscoveryStage(
        project_discoverer=project_disc, dep_discoverer=dep_disc,
    )
    state = IngestionState(target="foo", target_kind=TargetKind.DEPENDENCY)

    out = await stage.run(state)

    assert [c[0] for c in dep_disc.calls] == ["dep"]
    assert project_disc.calls == []
    assert out.paths == ("/pkgs/foo/mod.py",)
    assert out.root == Path("/pkgs")


def test_file_discovery_from_dict_builds_both_discoverers() -> None:
    """from_dict uses context.app_config.extraction.discovery to construct
    both ProjectFileDiscoverer and DependencyFileDiscoverer — neither branch
    is lazy; both are wired at build time."""
    stage = FileDiscoveryStage.from_dict({"type": "file_discovery"}, _ctx())

    # Concrete types from extraction.discovery — must have a discover() method.
    assert hasattr(stage.project_discoverer, "discover")
    assert hasattr(stage.dep_discoverer, "discover")


# ── FileReadStage ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_read_reads_file_contents(tmp_path: Path) -> None:
    """Reads each path's contents and fills state.file_contents as (path, src) tuples."""
    f1 = tmp_path / "a.py"
    f1.write_text("x = 1\n")
    f2 = tmp_path / "b.py"
    f2.write_text("y = 2\n")

    stage = FileReadStage()
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        paths=(str(f1), str(f2)),
    )

    out = await stage.run(state)

    # Normalize by path so test doesn't depend on iteration order.
    got = dict(out.file_contents)
    assert got[str(f1)] == "x = 1\n"
    assert got[str(f2)] == "y = 2\n"


@pytest.mark.asyncio
async def test_file_read_with_empty_paths_returns_empty(tmp_path: Path) -> None:
    """No paths → no file_contents; no crash on the empty-input edge case."""
    stage = FileReadStage()
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT, paths=(),
    )

    out = await stage.run(state)
    assert out.file_contents == ()


# ── ChunkingStage ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chunking_dispatches_by_extension(tmp_path: Path) -> None:
    """``.py`` file goes through AstPythonChunker (registered in chunker_registry)."""
    stage = ChunkingStage(chunking_config=ChunkingConfig())
    state = IngestionState(
        target=tmp_path,
        target_kind=TargetKind.PROJECT,
        package_name="__project__",
        root=tmp_path,
        file_contents=((str(tmp_path / "m.py"), 'x = 1\n'),),
    )

    out = await stage.run(state)

    assert len(out.trees) == 1
    tree = out.trees[0]
    assert isinstance(tree, DocumentNode)
    assert tree.kind == NodeKind.MODULE


@pytest.mark.asyncio
async def test_chunking_skips_unknown_extensions(tmp_path: Path) -> None:
    """Unknown extension (not in chunker_registry) is silently skipped — the
    pipeline produces zero trees for it but no error."""
    stage = ChunkingStage(chunking_config=ChunkingConfig())
    state = IngestionState(
        target=tmp_path,
        target_kind=TargetKind.PROJECT,
        package_name="__project__",
        root=tmp_path,
        file_contents=(("/unused/foo.unknown-ext", "anything\n"),),
    )

    out = await stage.run(state)
    assert out.trees == ()


@pytest.mark.asyncio
async def test_chunking_per_file_failure_isolation(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    """Spec AC #27 — a chunker that raises on one file MUST NOT abort the
    pipeline; remaining files still chunk. The failing file is logged at
    WARNING level."""
    # Register a bomb chunker for a throwaway extension; mirror a real failure.
    @dataclass(frozen=True, slots=True)
    class BombChunker:
        def build_tree(self, path, content, package, root):
            raise RuntimeError("intentional test failure")

        @classmethod
        def from_config(cls, cfg):
            return cls()

    chunker_registry[".bomb"] = BombChunker
    try:
        stage = ChunkingStage(chunking_config=ChunkingConfig())
        state = IngestionState(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
            root=tmp_path,
            file_contents=(
                ("/proj/ok.py", "x = 1\n"),       # succeeds
                ("/proj/bad.bomb", "payload\n"),  # raises; must be caught
                ("/proj/also.py", "y = 2\n"),     # succeeds AFTER the failure
            ),
        )

        with caplog.at_level(logging.WARNING):
            out = await stage.run(state)

        # Two successes despite the middle file raising.
        assert len(out.trees) == 2
        # Warning mentions the failing path so operators can debug.
        assert any("bad.bomb" in rec.message for rec in caplog.records)
    finally:
        del chunker_registry[".bomb"]


@pytest.mark.asyncio
async def test_chunking_skips_empty_source_files(tmp_path: Path) -> None:
    """Empty content → no tree emitted (nothing to parse)."""
    stage = ChunkingStage(chunking_config=ChunkingConfig())
    state = IngestionState(
        target=tmp_path,
        target_kind=TargetKind.PROJECT,
        package_name="__project__",
        root=tmp_path,
        file_contents=(("/proj/empty.py", ""),),
    )

    out = await stage.run(state)
    assert out.trees == ()


# ── FlattenStage ───────────────────────────────────────────────────────────

def _leaf_node(qname: str, text: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname,
        kind=NodeKind.MODULE,
        source_path="x.py",
        start_line=1,
        end_line=1,
        text=text,
        content_hash="h",
    )


@pytest.mark.asyncio
async def test_flatten_emits_chunks_from_trees(tmp_path: Path) -> None:
    """Each non-structural node with non-empty text yields one Chunk —
    delegates to ``flatten_to_chunks`` (tested elsewhere; this stage just wires)."""
    stage = FlattenStage()
    trees = (_leaf_node("m1", "hello"), _leaf_node("m2", "world"))
    state = IngestionState(
        target=tmp_path,
        target_kind=TargetKind.PROJECT,
        package_name="__project__",
        trees=trees,
    )

    out = await stage.run(state)
    assert len(out.chunks) == 2
    assert {c.text for c in out.chunks} == {"hello", "world"}


@pytest.mark.asyncio
async def test_flatten_with_empty_trees_yields_empty_chunks(tmp_path: Path) -> None:
    stage = FlattenStage()
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT, trees=(),
    )
    out = await stage.run(state)
    assert out.chunks == ()


# ── ContentHashStage ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_content_hash_produces_stable_string(tmp_path: Path) -> None:
    """Same paths → same hash. Hash is a non-empty str (normalized whether
    the native or fallback implementation returns bytes vs str)."""
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")

    stage = ContentHashStage()
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        paths=(str(f),),
    )

    out1 = await stage.run(state)
    out2 = await stage.run(state)

    assert isinstance(out1.content_hash, str)
    assert out1.content_hash != ""
    assert out1.content_hash == out2.content_hash


# ── PackageBuildStage ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_package_build_project_branch(tmp_path: Path) -> None:
    """PROJECT target builds a Package(name='__project__', origin=PROJECT, ...) ."""
    stage = PackageBuildStage()
    state = IngestionState(
        target=tmp_path,
        target_kind=TargetKind.PROJECT,
        content_hash="abc123",
    )

    out = await stage.run(state)

    assert out.package is not None
    assert isinstance(out.package, Package)
    assert out.package.name == "__project__"
    assert out.package.origin == PackageOrigin.PROJECT
    assert out.package.content_hash == "abc123"
    assert out.package.version == "local"


@pytest.mark.asyncio
async def test_package_build_dependency_missing_raises_lookup_error() -> None:
    """DEPENDENCY target where the distribution isn't installed → LookupError.
    The IndexProjectService catches this one level up as a non-fatal skip, but
    the stage itself surfaces the failure rather than silently returning None."""
    stage = PackageBuildStage()
    state = IngestionState(
        target="definitely-not-installed-pkg-zzz-2026",
        target_kind=TargetKind.DEPENDENCY,
        content_hash="deadbeef",
    )

    with pytest.raises(LookupError):
        await stage.run(state)


@pytest.mark.asyncio
async def test_package_build_dependency_branch_with_fake_dist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEPENDENCY target with an installed distribution → builds a Package
    from the dist metadata with origin=DEPENDENCY."""
    class _FakeMeta:
        def __init__(self, data):
            self._data = data

        def __getitem__(self, k):
            return self._data.get(k)

    class _FakeDist:
        def __init__(self):
            self.metadata = _FakeMeta({
                "Name": "Foo-Bar",
                "Version": "1.2.3",
                "Summary": "A test package.",
                "Home-page": "https://example.com",
            })
            self.requires = ("baz>=1.0",)

    monkeypatch.setattr(
        "pydocs_mcp.extraction._dep_helpers.find_installed_distribution",
        lambda name: _FakeDist(),
    )

    stage = PackageBuildStage()
    state = IngestionState(
        target="foo-bar",
        target_kind=TargetKind.DEPENDENCY,
        content_hash="cafef00d",
    )

    out = await stage.run(state)

    assert out.package is not None
    assert out.package.origin == PackageOrigin.DEPENDENCY
    assert out.package.name == "foo_bar"  # normalize_package_name lower + dashes→underscores
    assert out.package.version == "1.2.3"
    assert out.package.summary == "A test package."
    assert out.package.homepage == "https://example.com"
    assert out.package.content_hash == "cafef00d"
    assert "baz>=1.0" in out.package.dependencies


# ── Registration + round-trip ──────────────────────────────────────────────

_EXPECTED_STAGE_NAMES = (
    "chunking",
    "content_hash",
    "file_discovery",
    "file_read",
    "flatten",
    "package_build",
)


def test_all_six_stages_are_registered() -> None:
    """``stage_registry.names()`` lists exactly the 6 Task-20 stage types
    (plus whatever dummy stages an earlier test may have registered). No
    Task-20 stage is missing from the registry."""
    names = set(stage_registry.names())
    for expected in _EXPECTED_STAGE_NAMES:
        assert expected in names, f"stage {expected!r} not registered"


@pytest.mark.parametrize("stage_cls,type_name", [
    (ChunkingStage, "chunking"),
    (ContentHashStage, "content_hash"),
    (FileDiscoveryStage, "file_discovery"),
    (FileReadStage, "file_read"),
    (FlattenStage, "flatten"),
    (PackageBuildStage, "package_build"),
])
def test_stage_name_field_matches_registered_type(stage_cls, type_name) -> None:
    """Every stage carries ``name == "<registered_type>"`` for introspection —
    lets operators identify a stage without reaching for its class."""
    # FileDiscoveryStage needs constructor args; build via from_dict.
    stage = stage_cls.from_dict({"type": type_name}, _ctx())
    assert stage.name == type_name


@pytest.mark.parametrize("stage_cls,type_name", [
    (ChunkingStage, "chunking"),
    (ContentHashStage, "content_hash"),
    (FileDiscoveryStage, "file_discovery"),
    (FileReadStage, "file_read"),
    (FlattenStage, "flatten"),
    (PackageBuildStage, "package_build"),
])
def test_stage_to_dict_returns_type_header(stage_cls, type_name) -> None:
    """``to_dict`` always returns at minimum ``{"type": "<registered>"}`` —
    this is the YAML round-trip contract."""
    stage = stage_cls.from_dict({"type": type_name}, _ctx())
    data = stage.to_dict()
    assert data["type"] == type_name


@pytest.mark.parametrize("stage_cls,type_name", [
    (ChunkingStage, "chunking"),
    (ContentHashStage, "content_hash"),
    (FileDiscoveryStage, "file_discovery"),
    (FileReadStage, "file_read"),
    (FlattenStage, "flatten"),
    (PackageBuildStage, "package_build"),
])
def test_stage_from_dict_accepts_minimal_data(stage_cls, type_name) -> None:
    """Each stage's ``from_dict`` accepts the minimum-viable ``{"type": "..."}``
    payload plus a BuildContext carrying an ExtractionConfig. No stage
    requires additional YAML fields today."""
    stage = stage_cls.from_dict({"type": type_name}, _ctx())
    assert stage is not None


# ── Immutability contract ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stages_return_new_state_without_mutating_input(
    tmp_path: Path,
) -> None:
    """Stages use ``dataclasses.replace`` to build a new state — the input
    state is NOT mutated. Regression guard against a stage accidentally
    doing ``state.paths = ...`` (frozen → AttributeError today, but
    tomorrow's contributor shouldn't have to re-discover why)."""
    stage = FileReadStage()
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    before = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT, paths=(str(f),),
    )
    after = await stage.run(before)

    # Input untouched
    assert before.file_contents == ()
    # Output has the new field
    assert after.file_contents != ()
    # Distinct instance
    assert before is not after
