"""I7 commit 2 — pin that every IngestionStage reads/writes the bundles.

The commit-2 contract: every stage's output ``IngestionState`` carries
the new fields on its bundles AND mirrors the same values onto the
legacy flat fields. The mirror keeps commit 1's external API intact for
this transition window — commit 3 drops both the legacy flat fields and
the mirror writes.

These tests assert the BUNDLE side of the contract. The legacy-flat
side is already pinned by the pre-existing test suite
(``test_stages.py``, ``test_reference_capture_stage.py``, etc.) so the
fact that those still pass through commit 2 is the mirror-write
guarantee.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    ReferenceBundle,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import (
    AssignChunkContentHashStage,
    ChunkingStage,
    ContentHashStage,
    FileDiscoveryStage,
    FileReadStage,
    FlattenStage,
    PackageBuildStage,
    ReferenceCaptureStage,
)


# ----------------------------------------------------------------------
# FileDiscoveryStage
# ----------------------------------------------------------------------

class _FakeProjectDiscoverer:
    def __init__(self, result):
        self.result = result

    def discover(self, target):
        return self.result


class _FakeDepDiscoverer:
    def __init__(self, result):
        self.result = result

    def discover(self, target):
        return self.result


@pytest.mark.asyncio
async def test_file_discovery_writes_to_files_bundle(tmp_path: Path) -> None:
    """FileDiscoveryStage populates state.files.paths AND state.paths."""
    f1 = tmp_path / "a.py"
    project_disc = _FakeProjectDiscoverer(result=([str(f1)], tmp_path))
    dep_disc = _FakeDepDiscoverer(result=([], Path("/unused")))
    stage = FileDiscoveryStage(
        project_discoverer=project_disc,  # type: ignore[arg-type]
        dep_discoverer=dep_disc,  # type: ignore[arg-type]
    )
    state = IngestionState(target=tmp_path, target_kind=TargetKind.PROJECT)
    out = await stage.run(state)
    # Bundle side
    assert out.files.paths == (str(f1),)
    assert out.files.root == tmp_path
    # Legacy mirror still set
    assert out.paths == (str(f1),)
    assert out.root == tmp_path


# ----------------------------------------------------------------------
# FileReadStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_read_writes_to_files_bundle(tmp_path: Path) -> None:
    """FileReadStage populates state.files.file_contents AND state.file_contents."""
    f1 = tmp_path / "a.py"
    f1.write_text("x = 1\n")
    stage = FileReadStage()
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        paths=(str(f1),),
        files=FileBundle(paths=(str(f1),)),
    )
    out = await stage.run(state)
    # Bundle side
    assert dict(out.files.file_contents)[str(f1)] == "x = 1\n"
    # Legacy mirror still set
    assert dict(out.file_contents)[str(f1)] == "x = 1\n"


# ----------------------------------------------------------------------
# ContentHashStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_content_hash_writes_to_files_bundle(tmp_path: Path) -> None:
    """ContentHashStage populates state.files.content_hash AND state.content_hash."""
    f1 = tmp_path / "a.py"
    f1.write_text("x = 1\n")
    stage = ContentHashStage()
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        paths=(str(f1),),
        files=FileBundle(paths=(str(f1),)),
    )
    out = await stage.run(state)
    # Bundle side
    assert out.files.content_hash != ""
    # Legacy mirror still set, consistent with bundle
    assert out.content_hash == out.files.content_hash


# ----------------------------------------------------------------------
# ChunkingStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chunking_writes_to_chunks_bundle(tmp_path: Path) -> None:
    """ChunkingStage populates state.chunks_bundle.trees AND state.trees."""
    src = "def foo(): pass\n"
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        package_name="__project__",
        root=tmp_path,
        file_contents=((str(tmp_path / "m.py"), src),),
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name="__project__",
            root=tmp_path,
            file_contents=((str(tmp_path / "m.py"), src),),
        ),
    )
    stage = ChunkingStage(chunking_config=ChunkingConfig())
    out = await stage.run(state)
    # Bundle side
    assert isinstance(out.chunks_bundle.trees, tuple)
    assert len(out.chunks_bundle.trees) == 1
    # Legacy mirror — same tuple of trees
    assert out.trees == out.chunks_bundle.trees


# ----------------------------------------------------------------------
# FlattenStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flatten_writes_to_chunks_bundle(tmp_path: Path) -> None:
    """FlattenStage populates state.chunks_bundle.chunks AND state.chunks."""
    tree = DocumentNode(
        node_id="pkg.m",
        qualified_name="pkg.m",
        title="m",
        kind=NodeKind.MODULE,
        source_path="/p/m.py",
        start_line=1,
        end_line=10,
        text="some content",
        content_hash="h",
        children=(),
    )
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        package_name="pkg",
        trees=(tree,),
        chunks_bundle=ChunkBundle(trees=(tree,)),
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
        ),
    )
    out = await FlattenStage().run(state)
    # Bundle side
    assert isinstance(out.chunks_bundle.chunks, tuple)
    # Legacy mirror — same chunks
    assert out.chunks == out.chunks_bundle.chunks


# ----------------------------------------------------------------------
# AssignChunkContentHashStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_chunk_content_hash_writes_to_chunks_bundle() -> None:
    """AssignChunkContentHashStage keeps state.chunks and state.chunks_bundle.chunks
    in sync after the rehash."""
    from pydocs_mcp.models import Chunk
    chunk = Chunk(
        text="text",
        metadata={"package": "pkg", "module": "m", "title": "t"},
        content_hash="initial",
    )
    state = IngestionState(
        target=Path("/p"), target_kind=TargetKind.PROJECT,
        chunks=(chunk,),
        chunks_bundle=ChunkBundle(chunks=(chunk,)),
        files=FileBundle(target=Path("/p"), target_kind=TargetKind.PROJECT),
    )
    stage = AssignChunkContentHashStage(pipeline_hash="pipehash")
    out = await stage.run(state)
    assert out.chunks_bundle.chunks[0].content_hash != "initial"
    # Legacy mirror still in sync with bundle
    assert out.chunks[0].content_hash == out.chunks_bundle.chunks[0].content_hash


# ----------------------------------------------------------------------
# PackageBuildStage — reads from state; state.package is NOT in a bundle
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_package_build_reads_from_state(tmp_path: Path) -> None:
    """PackageBuildStage produces a Package using target / target_kind /
    content_hash. The flat fields are still the canonical read source in
    commit 2; commit 3 will switch them to the bundle."""
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        content_hash="abc",
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            content_hash="abc",
        ),
    )
    out = await PackageBuildStage().run(state)
    assert out.package is not None
    assert out.package.content_hash == "abc"
    assert out.package.name == "__project__"


# ----------------------------------------------------------------------
# ReferenceCaptureStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reference_capture_writes_to_refs_bundle(tmp_path: Path) -> None:
    """ReferenceCaptureStage populates state.refs AND state.references/aliases/attrs."""
    src = "def f():\n    g()\n"
    p = str(tmp_path / "m.py")
    state = IngestionState(
        target=tmp_path, target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=tmp_path,
        file_contents=((p, src),),
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
            root=tmp_path,
            file_contents=((p, src),),
        ),
    )
    out = await ReferenceCaptureStage().run(state)
    # Bundle side
    assert isinstance(out.refs, ReferenceBundle)
    assert isinstance(out.refs.references, tuple)
    assert isinstance(out.refs.reference_aliases, dict)
    assert isinstance(out.refs.class_attribute_types, dict)
    # Legacy mirror still set
    assert out.references == out.refs.references
    assert out.reference_aliases == out.refs.reference_aliases
    assert out.class_attribute_types == out.refs.class_attribute_types
