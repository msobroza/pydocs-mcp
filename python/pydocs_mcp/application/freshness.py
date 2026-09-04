"""Index-freshness probe — is the index current with the working tree? (spec §D4)

``resolve_git_head`` lives in :mod:`pydocs_mcp.git.refs` and is re-exported
here for the existing import path. It reads git plumbing files directly — no
subprocess, so it is safe to call from a TTL-cached probe on every response.
Unresolvable layouts degrade to ``None`` (the envelope then renders age-only,
never a false stale warning).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from pydocs_mcp.git.refs import resolve_git_head
from pydocs_mcp.models import NON_GIT_BRANCH_NAME
from pydocs_mcp.storage.index_metadata import IndexMetadata


@dataclass(frozen=True, slots=True)
class EnvelopeInfo:
    """Facts the envelope header renders (spec §D4). Pure value object.

    ``branch`` is meta-only (spec §6.7 / contract §2.4) — the header line and
    every card are byte-identical with or without it in P0.
    """

    indexed_commit: str
    live_commit: str
    age_days: int
    package_count: int
    stale: bool
    branch: str | None = None


@dataclass(slots=True)
class IndexFreshnessProbe:
    """TTL-cached freshness facts for one loaded database.

    NOT frozen — ``_cache`` is deliberate instance state (one probe per
    composition root; the TTL bounds re-reads, spec §D4). All injected
    callables are sync; ``envelope_info`` hops them off the event loop via
    ``asyncio.to_thread`` because they do file/SQLite I/O in production.
    """

    enabled: bool
    ttl_seconds: float
    read_metadata: Callable[[], IndexMetadata | None]
    resolve_live_head: Callable[[], str | None]
    count_packages: Callable[[], int]
    now: Callable[[], float] = time.time
    # Spec §6.7 / §6.14 item 6: one more sync closure, the default branch name
    # from the ``branches`` table (None on a pre-v16 bundle). The non-git
    # sentinel renders as null — the contract's "not a git repository" value.
    read_default_branch: Callable[[], str | None] = lambda: None
    _cache: tuple[float, EnvelopeInfo | None] | None = field(default=None, init=False)

    async def envelope_info(self) -> EnvelopeInfo | None:
        if not self.enabled:
            return None
        current = self.now()
        if self._cache is not None and current - self._cache[0] < self.ttl_seconds:
            return self._cache[1]
        info = await asyncio.to_thread(self._compute)
        self._cache = (current, info)
        return info

    def _compute(self) -> EnvelopeInfo | None:
        meta = self.read_metadata()
        if meta is None:
            return None
        live = self.resolve_live_head() or ""
        indexed = meta.git_head or ""
        age_days = max(0, int((self.now() - meta.indexed_at) / 86400.0))
        return EnvelopeInfo(
            indexed_commit=indexed,
            live_commit=live,
            age_days=age_days,
            package_count=self.count_packages(),
            # Stale ONLY when both sides resolved and differ — a missing
            # side degrades to age-only, never a false warning (spec §D4).
            stale=bool(indexed and live and indexed != live),
            branch=self._branch(),
        )

    def _branch(self) -> str | None:
        name = self.read_default_branch()
        return None if name in (None, NON_GIT_BRANCH_NAME) else name


__all__ = ("EnvelopeInfo", "IndexFreshnessProbe", "resolve_git_head")
