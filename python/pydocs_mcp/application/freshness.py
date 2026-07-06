"""Index-freshness probe — is the index current with the working tree? (spec §D4)

``resolve_git_head`` reads git plumbing files directly (``.git`` dir or
worktree gitfile → ``HEAD`` → loose ref / ``commondir`` / ``packed-refs``) —
no subprocess, so it is safe to call from a TTL-cached probe on every
response. Unresolvable layouts degrade to ``None`` (the envelope then
renders age-only, never a false stale warning).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.storage.index_metadata import IndexMetadata


def _read_packed_refs(packed: Path, ref: str) -> str | None:
    for line in packed.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # '#' = header, '^' = peeled-tag annotation for the line above.
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def resolve_git_head(project_root: Path) -> str | None:
    """Return the commit sha ``HEAD`` points at, or ``None`` when unresolvable.

    Handles: regular ``.git`` directories, detached HEAD (raw sha), loose
    refs, worktree gitfiles (``gitdir:`` pointer + ``commondir`` delegation),
    and ``packed-refs``. Any I/O error or unrecognized layout → ``None``.
    """
    git = project_root / ".git"
    try:
        if git.is_file():
            content = git.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            gitdir = Path(content.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (project_root / gitdir).resolve()
        elif git.is_dir():
            gitdir = git
        else:
            return None

        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None  # detached HEAD stores the raw sha
        ref = head.split(":", 1)[1].strip()

        loose = gitdir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None

        # Worktree gitdirs keep only HEAD locally; refs + packed-refs live in
        # the main repo's gitdir, reachable via the ``commondir`` pointer.
        commondir_file = gitdir / "commondir"
        if commondir_file.is_file():
            common = Path(commondir_file.read_text(encoding="utf-8").strip())
            if not common.is_absolute():
                common = (gitdir / common).resolve()
            loose = common / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip() or None
            packed = common / "packed-refs"
        else:
            packed = gitdir / "packed-refs"

        if packed.is_file():
            return _read_packed_refs(packed, ref)
        return None
    except OSError:
        return None


@dataclass(frozen=True, slots=True)
class EnvelopeInfo:
    """Facts the envelope header renders (spec §D4). Pure value object."""

    indexed_commit: str
    live_commit: str
    age_days: int
    package_count: int
    stale: bool


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
        )
