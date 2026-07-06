"""Both fetcher steps require a non-None ConnectionProvider — the
composition root wires it before calling ``from_dict``. Surface a clear
error if a misconfigured ``BuildContext`` slips through.

These regression tests pin the contract that ``chunk_fetcher`` and
``member_fetcher`` ``from_dict`` decoders need a wired
:class:`ConnectionProvider` (just like they already need a wired
:class:`AppConfig`). Previously, a ``None`` provider blew up later
inside ``_fetch_sync`` with ``AttributeError``; the codified guard
turns that into a YAML-anchored ``ValueError`` at decode time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.member_fetcher import MemberFetcherStep


def test_chunk_fetcher_from_dict_rejects_none_provider() -> None:
    ctx = BuildContext(connection_provider=None, app_config=AppConfig())
    with pytest.raises(ValueError, match="ChunkFetcherStep requires.*connection_provider"):
        ChunkFetcherStep.from_dict({}, ctx)


def test_member_fetcher_from_dict_rejects_none_provider() -> None:
    ctx = BuildContext(connection_provider=None, app_config=AppConfig())
    with pytest.raises(ValueError, match="MemberFetcherStep requires.*connection_provider"):
        MemberFetcherStep.from_dict({}, ctx)


def test_chunk_fetcher_from_dict_rejects_none_filter_adapter(tmp_path: Path) -> None:
    """Post-wiring contract: the composition root MUST supply a FilterAdapter.

    Previously a None adapter silently fell back to a runtime
    ``from pydocs_mcp.storage.sqlite import SqliteFilterAdapter`` inside the
    step — a violation of the FilterAdapter rule. Now it fails loudly at
    YAML-decode time, like the app_config / connection_provider guards.
    """
    ctx = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "unused.db"),
        app_config=AppConfig(),
    )
    with pytest.raises(ValueError, match="ChunkFetcherStep requires.*filter_adapter"):
        ChunkFetcherStep.from_dict({}, ctx)


def test_member_fetcher_from_dict_rejects_none_filter_adapter(tmp_path: Path) -> None:
    """Same contract as the chunk-side guard — see that test's docstring."""
    ctx = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "unused.db"),
        app_config=AppConfig(),
    )
    with pytest.raises(ValueError, match="MemberFetcherStep requires.*filter_adapter"):
        MemberFetcherStep.from_dict({}, ctx)
