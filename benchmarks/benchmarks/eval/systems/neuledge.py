"""Neuledge Context adapter (spec §4.10).

Thin wrapper around the existing ``NeuledgeClient``. The server is
expected to be running at ``base_url`` (default
``http://localhost:8080/mcp``) and already has the library indexed —
``index`` only establishes the MCP session, and ``search`` issues
``get_docs(library, topic)``. Like Context7, Neuledge returns a single
concatenated doc blob per query, so we emit one rank-1
``RetrievedItem``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..protocols import RetrievedItem
from ..serialization import system_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from benchmarks.neuledge_client import NeuledgeClient


@system_registry.register("neuledge")
@dataclass
class NeuledgeSystem:
    """Adapter for the local Neuledge Context MCP server."""

    name: str = "neuledge"
    base_url: str = "http://localhost:8080/mcp"
    # WHY: set per task — the runner is expected to seed ``library`` to the
    # ``name@version`` identifier returned by ``context list``. Empty
    # default keeps construction cheap (and registry-buildable).
    library: str = ""
    _client: "NeuledgeClient | None" = field(
        default=None, init=False, repr=False,
    )

    async def index(self, corpus_dir: Path, config: "AppConfig") -> None:  # noqa: ARG002
        # WHY: imports deferred so a bare registry build() doesn't drag in
        # httpx for callers that only need to enumerate names.
        from benchmarks.neuledge_client import NeuledgeClient

        if self._client is None:
            self._client = NeuledgeClient(base_url=self.base_url)
            await self._client.__aenter__()

    async def search(
        self, query: str, limit: int,  # noqa: ARG002 -- Neuledge returns one blob
    ) -> tuple[RetrievedItem, ...]:
        if self._client is None or not self.library:
            raise RuntimeError(
                "NeuledgeSystem.search called before index, or library unset",
            )
        text = await self._client.get_docs(library=self.library, topic=query)
        if not text:
            return ()
        return (
            RetrievedItem(
                rank=1,
                text=text,
                source_path=self.library,
                qualified_name=self.library or None,
            ),
        )

    async def teardown(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.__aexit__(None, None, None)
