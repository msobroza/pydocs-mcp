"""Changing YAML's embedding.model_name forces re-embed of every package (AC-26).

When the indexer starts with an embedding model different from the one
recorded against each package, the composition root must clear
``content_hash`` on the affected rows so the next hash-skip check treats
them as stale and re-extracts (re-embedding the chunks in the process).
The reusable check is exposed as
:func:`pydocs_mcp.application.indexing_service.find_packages_with_stale_embeddings`.

Two coverage points:

1. ``IndexingService.reindex_package`` round-trips
   ``package.embedding_model`` through ``uow.packages.upsert`` so the
   field actually lands in the SQLite cache (the helper has nothing to
   match on otherwise).
2. ``find_packages_with_stale_embeddings`` returns the right set of
   package names for both the "model changed" and "model unchanged"
   cases. Packages with ``embedding_model=None`` (legacy / pre-embedding
   caches) stay out of the stale list — flipping them on a model rename
   would re-extract every previously-unindexed dependency unnecessarily.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import (
    IndexingService,
    find_packages_with_stale_embeddings,
)
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
)


# turbovec requires dim multiple of 8 and bit_width in {2,3,4} — see
# tests/application/test_indexing_writes_vectors.py.
_DIM = 8
_BW = 4


def _pkg(name: str, embedding_model: str | None = None) -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
        embedding_model=embedding_model,
    )


def _vec(*values: float) -> np.ndarray:
    """Pad/truncate ``values`` to a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


@pytest.mark.asyncio
async def test_indexed_package_records_embedding_model(tmp_path: Path) -> None:
    """``reindex_package`` persists ``embedding_model`` end-to-end.

    Without this round-trip the staleness check has nothing to read; the
    field would always come back ``None`` and a model rename would never
    be detected.
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    pkg = _pkg("demo", embedding_model="model-A")
    chunk = Chunk(
        text="alpha",
        embedding=_vec(0.1, 0.2, 0.3, 0.4),
        metadata={"package": "demo", "title": "alpha"},
    )
    await svc.reindex_package(pkg, (chunk,), module_members=())

    async with factory() as uow:
        pkgs = await uow.packages.list(filter={"name": "demo"})
    assert len(pkgs) == 1
    assert pkgs[0].embedding_model == "model-A"


@pytest.mark.asyncio
async def test_model_change_detected_via_stored_embedding_model(
    tmp_path: Path,
) -> None:
    """The helper returns names where stored model != current model.

    Same model → empty list. Different model → every affected package.
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(
        _pkg("pkg-a", embedding_model="model-A"),
        (Chunk(
            text="a",
            embedding=_vec(0.1, 0.2),
            metadata={"package": "pkg-a", "title": "a"},
        ),),
        module_members=(),
    )
    await svc.reindex_package(
        _pkg("pkg-b", embedding_model="model-A"),
        (Chunk(
            text="b",
            embedding=_vec(0.3, 0.4),
            metadata={"package": "pkg-b", "title": "b"},
        ),),
        module_members=(),
    )

    # Model changed → both packages flagged stale.
    stale = await find_packages_with_stale_embeddings(
        uow_factory=factory, current_model="model-B",
    )
    assert set(stale) == {"pkg-a", "pkg-b"}

    # Same model → empty list (no spurious re-embeds).
    stale_no_change = await find_packages_with_stale_embeddings(
        uow_factory=factory, current_model="model-A",
    )
    assert stale_no_change == []


@pytest.mark.asyncio
async def test_legacy_packages_with_no_embedding_model_are_not_stale(
    tmp_path: Path,
) -> None:
    """Packages with ``embedding_model=None`` predate the embedding feature.

    They were never embedded (no vectors in the .tq sidecar to mismatch)
    and re-embedding them on a model rename would burn cycles re-indexing
    every legacy dependency. The helper deliberately skips them — they'll
    pick up an embedding model on their next natural reindex.
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(
        _pkg("legacy", embedding_model=None),
        (),
        module_members=(),
    )

    stale = await find_packages_with_stale_embeddings(
        uow_factory=factory, current_model="model-X",
    )
    assert stale == []
