"""Pin that every IngestionStage reads/writes the bundles only (I7 commit 3).

After commit 3 the :class:`IngestionState` is a clean three-bundle
value object — no legacy flat fields. These tests pin that each stage:

* reads its inputs from the correct bundle
* writes its outputs to the correct bundle
* never references the (removed) flat duplicates

The complementary high-level test for the post-commit-3 state shape
lives in ``test_ingestion_state_bundles.py``.
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
    """FileDiscoveryStage populates state.files.paths + state.files.root."""
    f1 = tmp_path / "a.py"
    project_disc = _FakeProjectDiscoverer(result=([str(f1)], tmp_path))
    dep_disc = _FakeDepDiscoverer(result=([], Path("/unused")))
    stage = FileDiscoveryStage(
        project_discoverer=project_disc,  # type: ignore[arg-type]
        dep_discoverer=dep_disc,  # type: ignore[arg-type]
    )
    state = IngestionState(
        files=FileBundle(target=tmp_path, target_kind=TargetKind.PROJECT),
    )
    out = await stage.run(state)
    assert out.files.paths == (str(f1),)
    assert out.files.root == tmp_path


# ----------------------------------------------------------------------
# FileReadStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_file_read_writes_to_files_bundle(tmp_path: Path) -> None:
    """FileReadStage reads state.files.paths and writes state.files.file_contents."""
    f1 = tmp_path / "a.py"
    f1.write_text("x = 1\n")
    stage = FileReadStage()
    state = IngestionState(
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            paths=(str(f1),),
        ),
    )
    out = await stage.run(state)
    assert dict(out.files.file_contents)[str(f1)] == "x = 1\n"


# ----------------------------------------------------------------------
# ContentHashStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_content_hash_writes_to_files_bundle(tmp_path: Path) -> None:
    """ContentHashStage writes state.files.content_hash."""
    f1 = tmp_path / "a.py"
    f1.write_text("x = 1\n")
    stage = ContentHashStage()
    state = IngestionState(
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            paths=(str(f1),),
        ),
    )
    out = await stage.run(state)
    assert out.files.content_hash != ""


# ----------------------------------------------------------------------
# ChunkingStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chunking_writes_to_chunks_bundle(tmp_path: Path) -> None:
    """ChunkingStage reads state.files.file_contents and writes state.chunks.trees."""
    src = "def foo(): pass\n"
    state = IngestionState(
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
    assert isinstance(out.chunks.trees, tuple)
    assert len(out.chunks.trees) == 1


# ----------------------------------------------------------------------
# FlattenStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flatten_writes_to_chunks_bundle(tmp_path: Path) -> None:
    """FlattenStage reads state.chunks.trees and writes state.chunks.chunks."""
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
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
        ),
        chunks=ChunkBundle(trees=(tree,)),
    )
    out = await FlattenStage().run(state)
    assert isinstance(out.chunks.chunks, tuple)


# ----------------------------------------------------------------------
# AssignChunkContentHashStage
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_chunk_content_hash_writes_to_chunks_bundle() -> None:
    """AssignChunkContentHashStage rewrites state.chunks.chunks[*].content_hash."""
    from pydocs_mcp.models import Chunk
    chunk = Chunk(
        text="text",
        metadata={"package": "pkg", "module": "m", "title": "t"},
        content_hash="initial",
    )
    state = IngestionState(
        files=FileBundle(target=Path("/p"), target_kind=TargetKind.PROJECT),
        chunks=ChunkBundle(chunks=(chunk,)),
    )
    stage = AssignChunkContentHashStage(pipeline_hash="pipehash")
    out = await stage.run(state)
    assert out.chunks.chunks[0].content_hash != "initial"


# ----------------------------------------------------------------------
# PackageBuildStage — reads from state.files; writes state.package
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_package_build_reads_from_files_bundle(tmp_path: Path) -> None:
    """PackageBuildStage reads target/target_kind/content_hash from FileBundle."""
    state = IngestionState(
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
    """ReferenceCaptureStage reads state.files.file_contents and writes state.refs.*."""
    src = "def f():\n    g()\n"
    p = str(tmp_path / "m.py")
    state = IngestionState(
        files=FileBundle(
            target=tmp_path,
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
            root=tmp_path,
            file_contents=((p, src),),
        ),
    )
    out = await ReferenceCaptureStage().run(state)
    assert isinstance(out.refs, ReferenceBundle)
    assert isinstance(out.refs.references, tuple)
    assert isinstance(out.refs.reference_aliases, dict)
    assert isinstance(out.refs.class_attribute_types, dict)
