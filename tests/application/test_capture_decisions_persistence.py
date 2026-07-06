"""IndexingService decision persistence + chunk backlink stamping (spec §D8-§D10).

``reindex_package`` gains a decisions step BEFORE chunk upsert: build
:class:`DecisionRecord`\\s from the merged :class:`RawDecision`\\s (staleness
computed here with the real ``project_root``), ``reconcile`` against the
persisted rows, upsert + delete vanished, then rewrite each decision chunk's
``decision_id`` from the ``decision_key`` → id map before the normal chunk
persistence path. Dependency packages skip the whole step (``decisions=()``).

Fake-UoW throughout — the decision store is :class:`InMemoryDecisionStore`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.extraction.decisions._types import RawDecision
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import Chunk, ChunkOrigin, Package, PackageOrigin
from pydocs_mcp.storage.decision_record import DecisionEvidence
from tests._fakes import InMemoryDecisionStore, make_fake_uow_factory

PROJECT = "__project__"


def _pkg(name: str = PROJECT, *, origin: PackageOrigin = PackageOrigin.PROJECT) -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=origin,
    )


def _raw(
    *,
    title: str = "Use sidecar for vectors",
    text: str = "# DECISION: sidecar file for vectors",
    confidence: float = 0.95,
) -> RawDecision:
    return RawDecision(
        title=title,
        status="active",
        source="inline_markers",
        confidence=confidence,
        evidence=(DecisionEvidence(source="inline_markers", locator="pkg/mod.py:10", text=text),),
        affected_files=("pkg/mod.py",),
        affected_qnames=("pkg.mod",),
    )


def _decision_chunk(title: str = "Use sidecar for vectors", text: str = "body") -> Chunk:
    """A decision-as-chunk carrying the ``decision_key`` the stage stamps."""
    return Chunk(
        text=text,
        metadata={
            "package": PROJECT,
            "module": "",
            "title": title,
            "origin": ChunkOrigin.DECISION_RECORD.value,
            "decision_key": decision_key(title),
        },
    )


async def test_decision_persisted_and_chunk_backlinked(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    decisions_store = InMemoryDecisionStore()
    factory = make_fake_uow_factory(decisions=decisions_store)
    svc = IndexingService(uow_factory=factory)

    raw = _raw()
    chunk = _decision_chunk()
    await svc.reindex_package(
        _pkg(),
        (chunk,),
        (),
        decisions=(raw,),
        project_root=tmp_path,
    )

    # One decision record persisted for the project package.
    records = tuple(decisions_store.by_id.values())
    assert len(records) == 1
    record = records[0]
    assert record.title == "Use sidecar for vectors"
    assert record.package == PROJECT

    # The decision chunk was stamped with the record's id in its metadata.
    async with factory() as uow:
        stored = await uow.chunks.list(filter={"package": PROJECT})
    decision_chunks = [
        c for c in stored if c.metadata.get("origin") == ChunkOrigin.DECISION_RECORD.value
    ]
    assert len(decision_chunks) == 1
    assert decision_chunks[0].metadata.get("decision_id") == record.id


async def test_reconcile_preserves_id_across_reindex(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    decisions_store = InMemoryDecisionStore()
    factory = make_fake_uow_factory(decisions=decisions_store)
    svc = IndexingService(uow_factory=factory)

    raw = _raw()
    await svc.reindex_package(
        _pkg(), (_decision_chunk(),), (), decisions=(raw,), project_root=tmp_path
    )
    first_id = next(iter(decisions_store.by_id))

    # Second reindex, same decision title → same row id preserved.
    await svc.reindex_package(
        _pkg(), (_decision_chunk(),), (), decisions=(raw,), project_root=tmp_path
    )
    assert len(decisions_store.by_id) == 1
    assert next(iter(decisions_store.by_id)) == first_id


async def test_vanished_decision_deleted_on_reindex(tmp_path: Path) -> None:
    decisions_store = InMemoryDecisionStore()
    factory = make_fake_uow_factory(decisions=decisions_store)
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(
        _pkg(), (_decision_chunk(),), (), decisions=(_raw(),), project_root=tmp_path
    )
    assert len(decisions_store.by_id) == 1

    # Reindex with NO decisions → the previously mined row is deleted.
    await svc.reindex_package(_pkg(), (), (), decisions=(), project_root=tmp_path)
    assert decisions_store.by_id == {}


async def test_dependency_package_skips_decisions(tmp_path: Path) -> None:
    decisions_store = InMemoryDecisionStore()
    factory = make_fake_uow_factory(decisions=decisions_store)
    svc = IndexingService(uow_factory=factory)

    # Even if decisions are (accidentally) passed, a dependency package must
    # not persist them — the caller (ProjectIndexer) passes decisions=() for
    # deps, and reindex_package leaves the decision store untouched.
    dep = _pkg("fastapi", origin=PackageOrigin.DEPENDENCY)
    await svc.reindex_package(dep, (), (), decisions=(), project_root=tmp_path)
    assert decisions_store.by_id == {}
