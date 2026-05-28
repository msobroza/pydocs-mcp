"""Composite UoW factories (Task 11 + spec §5.7)."""

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import (
    build_composite_uow_factory,
    build_sqlite_plus_turboquant_uow_factory,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


def _setup_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    return db_path


@pytest.mark.asyncio
async def test_build_composite_uow_factory_returns_callable_that_makes_composite(
    tmp_path: Path,
) -> None:
    db_path = _setup_db(tmp_path)
    tq_path = tmp_path / "cache.tq"
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=8,
        bit_width=4,
    )
    uow = factory()
    assert isinstance(uow, CompositeUnitOfWork)


@pytest.mark.asyncio
async def test_composite_factory_exposes_sqlite_repos_AND_vectors_attr(
    tmp_path: Path,
) -> None:
    db_path = _setup_db(tmp_path)
    tq_path = tmp_path / "cache.tq"
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=8,
        bit_width=4,
    )
    async with factory() as uow:
        # SQLite-owned attributes proxy through.
        assert hasattr(uow, "packages")
        assert hasattr(uow, "chunks")
        assert hasattr(uow, "module_members")
        # TurboQuant-owned attribute proxies through.
        assert isinstance(uow.vectors, TurboQuantUnitOfWork)


@pytest.mark.asyncio
async def test_build_composite_uow_factory_with_arbitrary_children(
    tmp_path: Path,
) -> None:
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    db_path = _setup_db(tmp_path)
    sqlite_factory = build_sqlite_uow_factory(db_path)
    composite_factory = build_composite_uow_factory([sqlite_factory])
    async with composite_factory() as uow:
        assert hasattr(uow, "packages")
