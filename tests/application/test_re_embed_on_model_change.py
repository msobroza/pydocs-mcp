"""``reindex_package`` must round-trip ``Package.embedding_model`` into SQLite.

Nothing downstream can record which embedder produced a package's vectors if the
column does not actually persist, so this pins the storage round-trip that
``PackageBuildStage``'s stamp depends on. It is consumed by multirepo's
serve-time embedder-mismatch guard and is useful metadata in its own right.

It is NOT used to decide re-embedding. A changed embedder invalidates the cache
structurally — its identity folds into ``ingestion_pipeline_hash``, which folds
into the package content hash — because comparing the stamp against
``config.embedding.model_name`` reads two independently-derived strings that
legitimately differ under the late-interaction preset and under an airgap
side-load, making every package look permanently stale. The end-to-end model-swap
behaviour lives in
tests/integration/test_embedding_model_stamping.py::test_model_swap_actually_reindexes_and_restamps,
and the settling property in tests/integration/test_index_pass_settles.py.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
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
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
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
