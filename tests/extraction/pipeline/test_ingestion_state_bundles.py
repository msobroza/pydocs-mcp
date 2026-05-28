"""Tests for the FileBundle / ChunkBundle / ReferenceBundle split (I7).

This test file is the canonical pin across the three I7 commits:

* Commit 1 — bundles exist alongside the flat fields; both work.
* Commit 2 — every stage reads/writes via the bundles (with mirror writes
  to the flat fields so the commit-1 assertions still hold).
* Commit 3 (current) — the flat fields are gone; only the bundles
  remain. The previous "alongside" assertion is replaced by
  :func:`test_ingestion_state_has_no_legacy_flat_fields`.

The post-commit-3 :class:`IngestionState` is a clean three-bundle
value object:

* ``files: FileBundle`` — target/target_kind/package_name/root/paths/file_contents/content_hash
* ``chunks: ChunkBundle`` — trees + the flat chunk list
* ``refs: ReferenceBundle`` — references/aliases/class-attribute-types
* ``package`` + ``existing_chunk_hashes`` — orthogonal scalars that
  don't fit any of the three bundles.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    ReferenceBundle,
    TargetKind,
)


def test_file_bundle_holds_required_fields():
    """FileBundle wraps the discovery-stage outputs together."""
    fb = FileBundle(
        target=Path("/tmp/pkg"),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
    )
    assert fb.package_name == "pkg"
    assert fb.target == Path("/tmp/pkg")
    assert fb.target_kind is TargetKind.PROJECT
    # Defaults — same shape as the legacy flat fields used to be on
    # IngestionState before I7.
    assert fb.paths == ()
    assert fb.file_contents == ()
    assert fb.root == Path()


def test_chunk_bundle_defaults():
    """ChunkBundle defaults match what the legacy flat fields used to be."""
    cb = ChunkBundle()
    assert cb.trees == ()
    assert cb.chunks == ()


def test_reference_bundle_defaults():
    """ReferenceBundle defaults match what the legacy flat fields used to be."""
    rb = ReferenceBundle()
    assert rb.references == ()
    assert rb.reference_aliases == {}
    assert rb.class_attribute_types == {}


def test_ingestion_state_carries_three_bundles():
    """Post-I7 commit 3: state carries exactly the three bundles + the
    orthogonal package + existing_chunk_hashes scalars. Target /
    target_kind / package_name moved INTO the FileBundle."""
    state = IngestionState(
        files=FileBundle(
            target=Path("/tmp/pkg"),
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
        ),
    )
    assert isinstance(state.files, FileBundle)
    assert isinstance(state.chunks, ChunkBundle)
    assert isinstance(state.refs, ReferenceBundle)
    # Entry-point fields live on FileBundle now.
    assert state.files.target == Path("/tmp/pkg")
    assert state.files.target_kind is TargetKind.PROJECT
    assert state.files.package_name == "pkg"


def test_ingestion_state_has_no_legacy_flat_fields():
    """Post-I7 commit 3: the legacy flat fields are gone from
    :class:`IngestionState`. Only the bundles + orthogonal scalars
    survive."""
    fields = {f.name for f in IngestionState.__dataclass_fields__.values()}
    # Bundles + orthogonal scalars.
    assert "files" in fields
    assert "chunks" in fields
    assert "refs" in fields
    assert "package" in fields
    assert "existing_chunk_hashes" in fields
    # Legacy flat duplicates (these were inside the bundles all along
    # post-commit-2 and now live exclusively on the bundles).
    assert "paths" not in fields
    assert "file_contents" not in fields
    assert "trees" not in fields
    assert "references" not in fields
    assert "reference_aliases" not in fields
    assert "class_attribute_types" not in fields
    # The entry-point fields moved INTO the FileBundle in commit 3 —
    # they no longer live on the top-level state.
    assert "target" not in fields
    assert "target_kind" not in fields
    assert "package_name" not in fields
    assert "root" not in fields
    assert "content_hash" not in fields
    # Bundle-rename: there is no longer a separate ``chunks_bundle`` slot —
    # the flat ``chunks`` slot was freed in commit 3 and now holds the
    # ChunkBundle.
    assert "chunks_bundle" not in fields


def test_bundles_are_frozen():
    """Bundles are frozen — mirrors the IngestionState immutability rule."""
    fb = FileBundle(target=Path("/tmp"), target_kind=TargetKind.PROJECT)
    with pytest.raises(Exception):  # FrozenInstanceError
        fb.package_name = "other"  # type: ignore[misc]
