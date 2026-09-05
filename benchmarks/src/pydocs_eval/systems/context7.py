"""Context7 adapter (spec §4.10).

Hosts both the async HTTP client (``Context7Client`` /
``Context7Error``) and the ``Context7System`` adapter in one file. The
client has exactly one consumer — this System — so the previous
``benchmarks/context7_client.py`` module was a ceremonial split. They
live together here because they're one cohesive concern: a thin wrapper
around the Context7 MCP service.

The remote service indexes its own corpus, so ``index`` resolves the
library ID once and caches it — ``search`` then issues ``query-docs``
with the cached ID. We surface the returned doc blob as a single rank-1
``RetrievedItem`` because Context7 returns one concatenated text body
per query rather than ranked chunks.

Context7 exposes an MCP server at ``https://mcp.context7.com/mcp`` with
two tools:
  - ``resolve-library-id(libraryName, query)`` → canonical library ID
  - ``query-docs(libraryId, query)`` → doc text

We communicate via MCP Streamable HTTP transport (JSON-RPC POST with
``Accept: application/json, text/event-stream`` header).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..gold_resolver import DEFAULT_FUZZ_THRESHOLD, LazyFuzzyGoldResolver
from ..registries import system_registry
from ._mcp_http import _DEFAULT_TIMEOUT, McpHttpClient
from .base_system import RetrievedItem, single_blob_items

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig

    from ..gold_resolver import GoldResolver

CONTEXT7_BASE_URL = "https://mcp.context7.com/mcp"
_DEFAULT_TOKENS = 5000


class Context7Error(Exception):
    """Raised when Context7 returns an error or unexpected response."""


class Context7Client(McpHttpClient):
    """Async context-manager client for Context7 MCP tools.

    Usage::

        async with Context7Client() as client:
            lib_id = await client.resolve_library_id("requests")
            docs = await client.get_library_docs(lib_id, query="GET request")
    """

    error_cls = Context7Error
    http_error_format = "HTTP {status} from Context7"
    network_error_format = "Network error contacting Context7: {exc}"
    tool_error_format = "Context7 tool error: {msg}"

    def __init__(
        self,
        base_url: str = CONTEXT7_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(base_url, timeout)

    def _extract_text(self, result: dict) -> str:
        # Strict single-block read: Context7 returns exactly one text
        # block; anything else is an unexpected-shape error.
        try:
            return result["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise Context7Error(f"Unexpected Context7 response shape: {result!r}") from exc

    async def resolve_library_id(self, library_name: str, query: str = "") -> str:
        """Call resolve-library-id and return the canonical library ID.

        Args:
            library_name: Human name like 'requests' or 'pandas'.
            query: Context about what the user is trying to do.

        Returns:
            Canonical ID string like '/psf/requests'.

        Raises:
            Context7Error: On network failure or unexpected response.
        """
        text = await self.call_tool(
            "resolve-library-id",
            {
                "libraryName": library_name,
                "query": query or f"How to use {library_name}",
            },
        )
        # Response format: "- Context7-compatible library ID: /org/project"
        for line in text.splitlines():
            stripped = line.strip()
            if "Context7-compatible library ID:" in stripped:
                lib_id = stripped.split("Context7-compatible library ID:")[-1].strip()
                if lib_id.startswith("/"):
                    return lib_id
            # Fallback: line starting with /org/project pattern
            elif stripped.startswith("/") and "/" in stripped[1:]:
                return stripped.split()[0]
        raise Context7Error(f"Could not parse library ID from response: {text[:300]}")

    async def query_docs(
        self,
        library_id: str,
        query: str,
    ) -> str:
        """Call query-docs and return documentation text.

        Args:
            library_id: Canonical ID from resolve_library_id.
            query: Search query to focus the returned docs.

        Returns:
            Documentation text string.

        Raises:
            Context7Error: On network failure or unexpected response.
        """
        return await self.call_tool(
            "query-docs",
            {
                "libraryId": library_id,
                "query": query,
            },
        )

    async def get_library_docs(
        self,
        library_id: str,
        query: str,
        topic: str = "",
        tokens: int = _DEFAULT_TOKENS,
    ) -> str:
        """Alias for query_docs (backward compatibility)."""
        return await self.query_docs(library_id, query)


@system_registry.register("context7")
@dataclass
class Context7System:
    """Adapter for the hosted Context7 MCP service."""

    name: str = "context7"
    library_name: str = ""  # WHY: set per task via EvalTask.metadata["package"]
    # WHY: doc-quality-vs-router axis (methodology §5.4). When set to a
    # Context7 ``/org/project`` id by config, ``index()`` seeds
    # ``_library_id`` directly and SKIPS the ``resolve-library-id`` HTTP
    # hop — so end-to-end doc retrieval is measured against an oracle
    # library, isolating it from the router's accuracy. Not auto-seeded
    # from metadata: the oracle value is a Context7 id, not a DS-1000
    # library name (a name→id map is out of scope; configs set it).
    oracle_library_name: str = ""
    _client: Context7Client | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _library_id: str | None = field(default=None, init=False, repr=False)

    async def index(self, corpus_dir: Path, config: AppConfig) -> None:
        if self._client is None:
            self._client = Context7Client()
            await self._client.__aenter__()
        # WHY: oracle mode short-circuits the router. ``search()`` still
        # needs the open client for ``query_docs``, so we open it above
        # then seed the id from the oracle and return BEFORE the
        # ``resolve_library_id`` hop — that hop is never called here.
        if self.oracle_library_name:
            self._library_id = self.oracle_library_name
            return
        # WHY: resolve-library-id is rate-limited and idempotent — cache
        # the lookup so a per-task harness can call ``index`` repeatedly
        # for the same library without burning quota. Failure-atomicity:
        # if ``resolve_library_id`` raises after we opened the HTTP client,
        # close the client before re-raising so callers that don't wrap
        # in finally: teardown() still don't leak the httpx session.
        if self.library_name:
            try:
                self._library_id = await self._client.resolve_library_id(
                    self.library_name,
                )
            except BaseException:
                await self._client.__aexit__(None, None, None)
                self._client = None
                raise

    async def search(
        self,
        query: str,
        limit: int,
    ) -> tuple[RetrievedItem, ...]:
        if self._client is None or self._library_id is None:
            raise RuntimeError(
                "Context7System.search called before index — runner contract",
            )
        text = await self._client.query_docs(self._library_id, query)
        return single_blob_items(
            text,
            source_path=self._library_id,
            qualified_name=self.library_name or None,
        )

    @property
    def gold_resolver(self) -> GoldResolver:
        # WHY: Context7 returns a single concatenated blob from a
        # non-enumerable remote store — there's no chunk-id store to scan,
        # so ground-truth is decided by fuzzy-matching the retrieved blob
        # against gold ``doc_contents`` (lazy), same as Neuledge.
        return LazyFuzzyGoldResolver(DEFAULT_FUZZ_THRESHOLD)

    @property
    def last_resolved_library_id(self) -> str | None:
        # WHY: surfaces the id ``index()`` settled on (the router's pick,
        # or the oracle) so the runner's ``_capture_library_resolution``
        # can record it for the ``library_resolution@1`` metric and the
        # ``coverage_signal`` side channel. Read-only view — Context7 is
        # mutable, so no wrapper is needed; this is just the cached field.
        return self._library_id

    async def teardown(self) -> None:
        client = self._client
        self._client = None
        self._library_id = None
        if client is not None:
            await client.__aexit__(None, None, None)
