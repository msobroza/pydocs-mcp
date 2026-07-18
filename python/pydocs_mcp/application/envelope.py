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
from typing import Any, Literal, Protocol

from pydocs_mcp.application.formatting import resolve_pointers, strip_pointers
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.tool_response import ToolResponse
from pydocs_mcp.application.truncation import TruncationLedger, ledger_scope

_SHORT_SHA = 7

# What a body producer may return: a bare markdown string (no structured
# rows) or ``(markdown, items, meta_extras)`` once a tool emits items[].
BodyResult = str | tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]


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

    async def wrap(
        self, tool: str, project: str, produce: Callable[[], Awaitable[BodyResult]]
    ) -> ToolResponse:
        with ledger_scope() as ledger:
            body, items, extras = _coerce_body(await produce())
        body = (
            resolve_pointers(body, self.surface) if self.pointers_enabled else strip_pointers(body)
        )
        info = await self.probe.envelope_info()
        header = render_envelope_header(info)
        footer = render_envelope_footer(
            ledger, self.surface, pointers_enabled=self.pointers_enabled
        )
        parts = [p for p in (header, body.rstrip("\n"), footer) if p]
        meta = _assemble_meta(
            tool=tool,
            project=project,
            info=info,
            truncated=bool(ledger.entries),
            extras=extras,
        )
        return ToolResponse(text="\n\n".join(parts) + "\n", items=items, meta=meta)


def _coerce_body(result: BodyResult) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
    """A bare string body means "no structured rows" (contract §2.1)."""
    if isinstance(result, str):
        return result, (), {}
    return result


def _assemble_meta(
    *,
    tool: str,
    project: str,
    info: EnvelopeInfo | None,
    truncated: bool,
    extras: dict[str, Any],
) -> dict[str, Any]:
    """The §2.1 ``meta`` block. Empty commit strings degrade to null (the wire
    contract's "head cannot be resolved" value); ``truncated`` ORs the ledger
    with any body-level truncation the producer reported in ``extras``."""
    meta: dict[str, Any] = {
        "tool": tool,
        "project": project,
        "indexed_git_head": (info.indexed_commit or None) if info else None,
        "live_git_head": (info.live_commit or None) if info else None,
        "index_stale": info.stale if info else False,
        "truncated": truncated,
    }
    for key, value in extras.items():
        meta[key] = bool(meta["truncated"] or value) if key == "truncated" else value
    return meta
