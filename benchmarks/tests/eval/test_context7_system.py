"""Pin Context7System: failure-atomicity contract on ``index()``.

If ``resolve_library_id`` raises after the HTTP client has been opened,
``index()`` must close the client and clear the field before
re-raising — otherwise a caller that omits ``teardown()`` in a finally
block (or one whose finally is preempted by another error) leaks the
httpx session.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from benchmarks.eval.serialization import system_registry
from benchmarks.eval.systems import Context7System  # noqa: F401 -- triggers registration
from pydocs_mcp.retrieval.config import AppConfig


class _StubClient:
    """Mimics Context7Client's async-context-manager surface.

    ``__aexit__`` records the call so the test can assert it ran exactly
    once even though resolve_library_id raised.
    """

    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> "_StubClient":
        self.entered = True
        return self

    async def __aexit__(self, *_: object) -> None:
        self.exited = True

    async def resolve_library_id(self, library_name: str) -> str:  # noqa: ARG002
        raise RuntimeError("resolve boom")


@pytest.mark.asyncio
async def test_context7_index_closes_client_when_resolve_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    stub = _StubClient()
    # WHY: the system's index() does ``from benchmarks.context7_client
    # import Context7Client``, so patch the symbol on that module.
    import benchmarks.context7_client as c7

    monkeypatch.setattr(c7, "Context7Client", lambda *a, **kw: stub)

    system = system_registry.build("context7")
    system.library_name = "anything"

    with pytest.raises(RuntimeError, match="resolve boom"):
        await system.index(tmp_path, AppConfig.load())

    # WHY: post-condition — index() must be failure-atomic. Either it
    # succeeds and the client is held, or it raises and no resources
    # are held. A leaked stub here means a real httpx session would
    # leak the same way.
    assert system._client is None
    assert stub.entered is True
    assert stub.exited is True
