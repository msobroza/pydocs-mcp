"""Dependency-census cap in ``build_sqlite_file_tools_service``.

The filesystem tools walk the indexed dependency packages under
``scope="deps"``/``"all"``. The census read is capped; hitting the cap must
emit a WARNING (logger ``pydocs-mcp``) instead of silently dropping roots.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import pytest

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage import factories


@dataclass(frozen=True, slots=True)
class _FakePackage:
    name: str


class FakeCensusPackageStore:
    """Named fake — serves ``list(limit=...)`` over a fixed package census."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self._names = names

    async def list(self, *, limit: int | None = None) -> tuple[_FakePackage, ...]:
        packages = tuple(_FakePackage(name) for name in self._names)
        return packages if limit is None else packages[:limit]


class FakeCensusUow:
    """Minimal async-context UoW exposing only ``packages`` (census read)."""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.packages = FakeCensusPackageStore(names)

    async def __aenter__(self) -> FakeCensusUow:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _service_over_census(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, names: tuple[str, ...]
) -> object:
    monkeypatch.setattr(
        factories,
        "build_sqlite_uow_factory",
        lambda db_path, **_kwargs: lambda: FakeCensusUow(names),
    )
    return factories.build_sqlite_file_tools_service(
        tmp_path / "census.db", project_root=None, config=AppConfig()
    )


async def test_census_at_cap_logs_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cap = factories._FILE_TOOLS_PACKAGE_LIST_LIMIT
    assert cap == 10000
    names = tuple(f"pkg{i}" for i in range(cap + 5))
    svc = _service_over_census(monkeypatch, tmp_path, names)
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        listed = await svc.list_dependency_packages()  # type: ignore[attr-defined]
    assert len(listed) == cap
    warnings = [r for r in caplog.records if r.name == "pydocs-mcp"]
    assert len(warnings) == 1
    assert str(cap) in warnings[0].getMessage()
    assert "dependency" in warnings[0].getMessage()


async def test_census_below_cap_is_silent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    svc = _service_over_census(monkeypatch, tmp_path, ("alpha", "beta"))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        listed = await svc.list_dependency_packages()  # type: ignore[attr-defined]
    assert listed == ("alpha", "beta")
    assert [r for r in caplog.records if r.name == "pydocs-mcp"] == []
