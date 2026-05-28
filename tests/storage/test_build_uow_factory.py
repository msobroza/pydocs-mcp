"""build_uow_factory dispatches on late_interaction.enabled.

Two paths to verify, mirroring the docstring contract:

* disabled (the shipped default) -> ``CompositeUnitOfWork`` over SQLite-only
  with ``multi_vectors`` falling back to :class:`NullMultiVectorStore`.
* enabled -> ``CompositeUnitOfWork`` includes :class:`FastPlaidUnitOfWork`,
  whose self-referencing ``multi_vectors`` property wins the composite's
  child-scan over the SQLite-side NullMultiVectorStore placeholder.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import build_uow_factory


@pytest.mark.asyncio
async def test_disabled_returns_composite_with_null_multi_vectors(tmp_path) -> None:
    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    factory = build_uow_factory(cfg, db_path=db)
    async with factory() as uow:
        assert isinstance(uow, CompositeUnitOfWork)
        from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore

        assert isinstance(uow.multi_vectors, NullMultiVectorStore)


@pytest.mark.asyncio
async def test_enabled_wires_fast_plaid_uow(tmp_path, monkeypatch) -> None:
    # Stub fast_plaid's PyO3 class so __aenter__'s mmap-style constructor
    # succeeds without the optional extra installed. The class is mutated
    # in-place by ``_ensure_fast_plaid_imported`` — patching the module
    # attribute is the documented test seam (see fast_plaid_uow.py
    # module-level comment).
    import pydocs_mcp.storage.fast_plaid_uow as mod

    monkeypatch.setattr(
        mod, "_FastPlaidCls", lambda *a, **kw: object(), raising=False
    )

    db = tmp_path / "x.db"
    open_index_database(db).close()
    cfg = AppConfig.load()
    # ``AppConfig`` is a frozen pydantic BaseSettings; override the
    # late_interaction sub-model via ``object.__setattr__`` per the
    # existing test convention in tests/retrieval/test_late_interaction_config.py.
    object.__setattr__(
        cfg, "late_interaction", LateInteractionConfig(enabled=True)
    )
    factory = build_uow_factory(cfg, db_path=db)
    async with factory() as uow:
        from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork
        from pydocs_mcp.storage.null_multi_vector_store import NullMultiVectorStore

        assert not isinstance(uow.multi_vectors, NullMultiVectorStore)
        # The composite's scan picks ``FastPlaidUnitOfWork`` via its
        # self-referencing ``multi_vectors`` property (mirrors the
        # TurboQuantUnitOfWork.vectors precedent for the single-vector
        # path).
        assert isinstance(uow.multi_vectors, FastPlaidUnitOfWork)
