"""ResponseEnvelope — wraps one response's production (spec §D4/§D5/§D7).

The single choke point where the three response conventions meet: it opens
the truncation-ledger scope around body production, resolves (or strips)
surface-neutral pointer tokens, prepends the freshness header, and appends
the truncation footer. Both the MCP server and the CLI route every response
through one of these, so the conventions cannot drift between surfaces.
``formatting.py`` stays pure — all I/O lives in the injected probe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydocs_mcp.application.formatting import resolve_pointers, strip_pointers
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.truncation import TruncationLedger, ledger_scope

_SHORT_SHA = 7


class FreshnessProbe(Protocol):
    """The slice of IndexFreshnessProbe the envelope consumes (ISP)."""

    async def envelope_info(self) -> EnvelopeInfo | None: ...


def render_envelope_header(info: EnvelopeInfo | None) -> str:
    """The ``[index: …]`` line, plus the stale warning when HEADs diverge."""
    if info is None:
        return ""
    indexed = info.indexed_commit[:_SHORT_SHA] or "unstamped"
    lines = [f"[index: {indexed} · {info.age_days}d old · {info.package_count} packages]"]
    if info.stale:
        lines.append(
            f"[⚠ index stale: indexed {indexed}, working tree at "
            f"{info.live_commit[:_SHORT_SHA]} — run `pydocs-mcp index .`]"
        )
    return "\n".join(lines)


def render_envelope_footer(
    ledger: TruncationLedger, surface: str, *, pointers_enabled: bool
) -> str:
    """The ``[truncated: …]`` block — one line per elision, pointer resolved.

    ``pointers_enabled`` mirrors the body-side contract in ``wrap()``: a
    deployment with pointers disabled must not leak "-> get_symbol(...)"
    syntax anywhere in the response, footer included.
    """
    if not ledger.entries:
        return ""
    n = len(ledger.entries)
    plural = "" if n == 1 else "s"
    lines = [f"[truncated: {n} section{plural} — recovery pointers inline]"]
    for entry in ledger.entries:
        if not entry.recovery:
            recovery = ""
        elif pointers_enabled:
            recovery = resolve_pointers(entry.recovery, surface)
        else:
            recovery = strip_pointers(entry.recovery).rstrip("\n")
        lines.append(f"- {entry.description} {recovery}".rstrip())
    return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    """One per composition root per surface; wraps every tool response."""

    probe: FreshnessProbe
    surface: Literal["mcp", "cli"]
    pointers_enabled: bool

    async def wrap(self, produce: Callable[[], Awaitable[str]]) -> str:
        with ledger_scope() as ledger:
            body = await produce()
        body = (
            resolve_pointers(body, self.surface) if self.pointers_enabled else strip_pointers(body)
        )
        header = render_envelope_header(await self.probe.envelope_info())
        footer = render_envelope_footer(
            ledger, self.surface, pointers_enabled=self.pointers_enabled
        )
        parts = [p for p in (header, body.rstrip("\n"), footer) if p]
        return "\n\n".join(parts) + "\n"
