"""Pin Context7System's oracle-library mode + resolved-id exposure (Task 7).

Two axes are covered:

- Oracle mode (``oracle_library_name`` set): ``index()`` seeds
  ``_library_id`` from the operator-supplied ``/org/project`` id and NEVER
  calls ``resolve_library_id`` (the HTTP router hop). This is the
  doc-quality-vs-router separation — score retrieval against an oracle
  library so the router's accuracy doesn't confound the doc-retrieval
  measurement.
- Default mode (no oracle): ``index()`` calls ``resolve_library_id``,
  caches the id, and ``last_resolved_library_id`` surfaces it so the
  runner can capture the router's pick.

Network is BLOCKED — the Context7Client is fully mocked (no live HTTP).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.eval.serialization import system_registry
from benchmarks.eval.systems import Context7System  # noqa: F401 -- triggers registration
from pydocs_mcp.retrieval.config import AppConfig


class _RecordingClient:
    """Mocks Context7Client. Records whether ``resolve_library_id`` ran
    and returns a canonical ``/org/project`` id so the default-mode test
    can assert the cache + the exposed property."""

    def __init__(self, resolved: str = "/pandas-dev/pandas") -> None:
        self.entered = False
        self.exited = False
        self.resolve_calls: list[str] = []
        self._resolved = resolved

    async def __aenter__(self) -> "_RecordingClient":
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def resolve_library_id(self, library_name: str) -> str:
        self.resolve_calls.append(library_name)
        return self._resolved


@pytest.mark.asyncio
async def test_oracle_mode_skips_resolve_library_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    client = _RecordingClient()
    import benchmarks.eval.systems.context7 as c7

    monkeypatch.setattr(c7, "Context7Client", lambda *a, **kw: client)

    system = system_registry.build("context7")
    # Operator/config hands an explicit Context7 /org/project id.
    system.oracle_library_name = "/pandas-dev/pandas"
    # A library_name may also be set by the runner; oracle must win and
    # the resolve hop must be skipped entirely.
    system.library_name = "pandas"

    await system.index(tmp_path, AppConfig.load())

    # Oracle short-circuit: _library_id is the oracle, resolve never ran.
    assert system._library_id == "/pandas-dev/pandas"
    assert client.resolve_calls == []
    assert system.last_resolved_library_id == "/pandas-dev/pandas"
    # The client is still opened — search() needs it for query_docs.
    assert system._client is client
    assert client.entered is True

    await system.teardown()


@pytest.mark.asyncio
async def test_default_mode_calls_resolve_and_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    client = _RecordingClient(resolved="/pandas-dev/pandas")
    import benchmarks.eval.systems.context7 as c7

    monkeypatch.setattr(c7, "Context7Client", lambda *a, **kw: client)

    system = system_registry.build("context7")
    system.library_name = "pandas"  # no oracle set

    await system.index(tmp_path, AppConfig.load())

    # Default path: resolve ran exactly once, id cached + exposed.
    assert client.resolve_calls == ["pandas"]
    assert system._library_id == "/pandas-dev/pandas"
    assert system.last_resolved_library_id == "/pandas-dev/pandas"

    await system.teardown()
