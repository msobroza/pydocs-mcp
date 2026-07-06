"""table_crud helpers — the shared filter→SQL→thread-offload CRUD (DRY extraction).

Pins the exact semantics the three filter-driven repositories delegate to:
mapper application, LIMIT bind ordering, the explicit-filter ValueError on
delete, COUNT with/without filter, and the unconditional sweep.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.factories import build_connection_provider
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.sqlite.filter_adapter import (
    _PACKAGE_COLUMNS,
    _SqliteFilterTranslator,
)
from pydocs_mcp.storage.sqlite.row_mappers import _row_to_package
from pydocs_mcp.storage.sqlite.table_crud import (
    count_rows,
    delete_all_rows,
    delete_rows,
    list_rows,
)

_TRANSLATOR = _SqliteFilterTranslator(safe_columns=_PACKAGE_COLUMNS)


@pytest.fixture
def provider(tmp_path):
    f = tmp_path / "crud.db"
    open_index_database(f).close()
    return build_connection_provider(f)


async def _seed(provider) -> None:
    from pydocs_mcp.storage.sqlite import SqlitePackageRepository

    repo = SqlitePackageRepository(provider=provider)
    for name, origin in (("alpha", PackageOrigin.DEPENDENCY), ("beta", PackageOrigin.PROJECT)):
        await repo.upsert(
            Package(
                name=name,
                version="1.0",
                summary="",
                homepage="",
                dependencies=(),
                content_hash="",
                origin=origin,
            )
        )


async def test_list_rows_maps_and_limits(provider) -> None:
    await _seed(provider)
    rows = await list_rows(
        provider, _TRANSLATOR, table="packages", mapper=_row_to_package, filter=None, limit=None
    )
    assert {p.name for p in rows} == {"alpha", "beta"}
    limited = await list_rows(
        provider, _TRANSLATOR, table="packages", mapper=_row_to_package, filter=None, limit=1
    )
    assert len(limited) == 1


async def test_list_rows_applies_mapping_filter(provider) -> None:
    await _seed(provider)
    rows = await list_rows(
        provider,
        _TRANSLATOR,
        table="packages",
        mapper=_row_to_package,
        filter={"name": "alpha"},
        limit=None,
    )
    assert [p.name for p in rows] == ["alpha"]


async def test_delete_rows_requires_explicit_filter(provider) -> None:
    with pytest.raises(ValueError, match="delete requires an explicit filter"):
        await delete_rows(provider, _TRANSLATOR, table="packages", filter=None)


async def test_delete_and_count_roundtrip(provider) -> None:
    await _seed(provider)
    assert await count_rows(provider, _TRANSLATOR, table="packages", filter=None) == 2
    deleted = await delete_rows(provider, _TRANSLATOR, table="packages", filter={"name": "alpha"})
    assert deleted == 1
    assert await count_rows(provider, _TRANSLATOR, table="packages", filter={"name": "alpha"}) == 0


async def test_delete_all_rows_sweeps_table(provider) -> None:
    await _seed(provider)
    await delete_all_rows(provider, table="packages")
    assert await count_rows(provider, _TRANSLATOR, table="packages", filter=None) == 0
