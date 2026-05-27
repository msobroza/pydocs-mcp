"""Tests for the FileBundle / ChunkBundle / ReferenceBundle split (I7).

This test file is the canonical pin across the three I7 commits:

* Commit 1 — bundles exist alongside the flat fields; both work.
* Commit 2 — every stage reads/writes via the bundles (with mirror writes
  to the flat fields so this file's commit-1 assertions still hold).
* Commit 3 — the flat fields are gone; only the bundles remain.

The commit-3 assertion lives in
:func:`test_ingestion_state_has_no_legacy_flat_fields` and is added in
the third commit (it would fail today; see Task 19 of the cleanup plan).
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
    # Defaults — same shape as the legacy flat fields on IngestionState.
    assert fb.paths == ()
    assert fb.file_contents == ()
    assert fb.root == Path(".")


def test_chunk_bundle_defaults():
    """ChunkBundle defaults match the legacy flat-field defaults."""
    cb = ChunkBundle()
    assert cb.trees == ()
    assert cb.chunks == ()


def test_reference_bundle_defaults():
    """ReferenceBundle defaults match the legacy flat-field defaults."""
    rb = ReferenceBundle()
    assert rb.references == ()
    assert rb.reference_aliases == {}
    assert rb.class_attribute_types == {}


def test_ingestion_state_has_bundles_alongside_old_fields():
    """Commit 1: bundles exist; old flat fields ALSO still exist."""
    state = IngestionState(
        target=Path("/tmp/pkg"),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
    )
    # New bundles default-construct alongside the existing fields.
    assert isinstance(state.files, FileBundle)
    assert isinstance(state.chunks_bundle, ChunkBundle)
    assert isinstance(state.refs, ReferenceBundle)

    # Old flat fields still readable (the migration in commit 2 mirrors
    # writes to both; commit 3 drops the flat ones).
    assert state.paths == ()
    assert state.file_contents == ()
    assert state.trees == ()
    assert state.chunks == ()
    assert state.references == ()
    assert state.reference_aliases == {}
    assert state.class_attribute_types == {}


def test_bundles_are_frozen():
    """Bundles are frozen — mirrors the IngestionState immutability rule."""
    fb = FileBundle(target=Path("/tmp"), target_kind=TargetKind.PROJECT)
    with pytest.raises(Exception):  # FrozenInstanceError
        fb.package_name = "other"  # type: ignore[misc]
