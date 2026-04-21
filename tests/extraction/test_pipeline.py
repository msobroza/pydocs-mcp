"""Tests for IngestionPipeline + IngestionState + TargetKind (spec §7.1)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline import (
    IngestionPipeline,
    IngestionStage,
    IngestionState,
    TargetKind,
)


def test_target_kind_values():
    """PROJECT/DEPENDENCY are the only kinds and their string values are stable."""
    assert TargetKind.PROJECT.value == "project"
    assert TargetKind.DEPENDENCY.value == "dependency"
    # StrEnum identity vs string
    assert TargetKind.PROJECT == "project"
    assert set(TargetKind) == {TargetKind.PROJECT, TargetKind.DEPENDENCY}


def test_ingestion_state_frozen_rejects_mutation():
    """Frozen dataclass → attribute assignment must raise."""
    state = IngestionState(target=Path("/tmp/x"), target_kind=TargetKind.PROJECT)
    with pytest.raises(FrozenInstanceError):
        state.paths = ("other.py",)  # type: ignore[misc]


def test_ingestion_state_slots_blocks_extra_attrs():
    """slots=True → assigning an unknown attribute must raise AttributeError."""
    state = IngestionState(target=Path("/tmp/x"), target_kind=TargetKind.PROJECT)
    with pytest.raises(AttributeError):
        object.__setattr__(state, "bogus_attr", 123)


def test_ingestion_state_defaults():
    """Bare-minimum construction leaves everything at sensible empty defaults."""
    state = IngestionState(target=Path("/tmp/x"), target_kind=TargetKind.PROJECT)
    assert state.package_name == ""
    assert state.root == Path(".")
    assert state.paths == ()
    assert state.file_contents == ()
    assert state.trees == ()
    assert state.chunks == ()
    assert state.content_hash == ""
    assert state.package is None


def test_ingestion_state_references_defaults_to_empty_tuple():
    """Sub-PR #5b seam — ``references`` is reserved but empty in #5."""
    state = IngestionState(target="requests", target_kind=TargetKind.DEPENDENCY)
    assert state.references == ()
    assert isinstance(state.references, tuple)


async def test_empty_pipeline_returns_input_unchanged():
    """Identity pipeline — no stages → state passes through untouched."""
    pipeline = IngestionPipeline(stages=())
    initial = IngestionState(target=Path("/tmp/x"), target_kind=TargetKind.PROJECT)
    out = await pipeline.run(initial)
    assert out is initial


async def test_pipeline_threads_state_forward_through_stages():
    """Two fake stages — each adds a path; final state carries both additions."""

    class AppendPathStage:
        def __init__(self, added: str) -> None:
            self._added = added

        async def run(self, state: IngestionState) -> IngestionState:
            return replace(state, paths=state.paths + (self._added,))

    s1 = AppendPathStage("a.py")
    s2 = AppendPathStage("b.py")
    pipeline = IngestionPipeline(stages=(s1, s2))

    initial = IngestionState(target=Path("/tmp/x"), target_kind=TargetKind.PROJECT)
    out = await pipeline.run(initial)
    assert out.paths == ("a.py", "b.py")


def test_ingestion_stage_protocol_is_runtime_checkable():
    """A duck-typed class with an async ``run(state)`` satisfies the Protocol."""

    class FakeStage:
        async def run(self, state: IngestionState) -> IngestionState:
            return state

    assert isinstance(FakeStage(), IngestionStage)


def test_ingestion_stage_protocol_rejects_non_conforming():
    """A class missing ``run`` must NOT satisfy ``isinstance(_, IngestionStage)``."""

    class NotAStage:
        async def go(self, state: IngestionState) -> IngestionState:
            return state

    assert not isinstance(NotAStage(), IngestionStage)


def test_ingestion_state_accepts_dependency_string_target():
    """target_kind=DEPENDENCY pairs with a str target (PyPI distribution name)."""
    state = IngestionState(target="requests", target_kind=TargetKind.DEPENDENCY)
    assert state.target == "requests"
    assert state.target_kind is TargetKind.DEPENDENCY


def test_ingestion_pipeline_is_frozen():
    """IngestionPipeline itself is a frozen dataclass (reusable / safe)."""
    pipeline = IngestionPipeline(stages=())
    with pytest.raises(FrozenInstanceError):
        pipeline.stages = ()  # type: ignore[misc]
