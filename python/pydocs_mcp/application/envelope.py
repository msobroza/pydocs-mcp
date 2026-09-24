"""ResponseEnvelope — wraps one response's production (spec §D4/§D5/§D7).

The single choke point where the three response conventions meet: it opens
the truncation-ledger scope around body production, resolves (or strips)
surface-neutral pointer tokens, prepends the freshness header, and appends
the truncation footer. Both the MCP server and the CLI route every response
through one of these, so the conventions cannot drift between surfaces.
``formatting.py`` stays pure — all I/O lives in the injected probe.

Raised errors go through the same pointer resolution as returned bodies
(:func:`resolve_error_message_pointers`): a raise unwinds past the body-side
step, and a client that is handed a literal ``[[next:…]]`` token cannot act
on it. Being the one place BOTH surfaces funnel through is exactly why the
error path belongs here rather than at the raise sites.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.pointer_grammar import resolve_pointers, strip_pointers
from pydocs_mcp.application.suggestions import CHECKOUT_NOT_INDEXED_RULE, log_suggestion_fired
from pydocs_mcp.application.tool_response import SUGGESTION_TOOLS, ToolResponse
from pydocs_mcp.application.truncation import TruncationLedger, ledger_scope
from pydocs_mcp.observability.rendered_rows import publish_rendered_rows

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_resolution import ResolvedBranch

log = logging.getLogger(__name__)

_SHORT_SHA = 7

# Cheap containment gate before running the pointer regexes over an error
# message: every pointer starts with this, so a message without it cannot change.
_POINTER_OPENER = "[[next:"

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


def resolve_error_message_pointers(
    exc: BaseException, surface: str, *, pointers_enabled: bool
) -> None:
    """Rewrite ``exc``'s message IN PLACE so a raised error carries the same
    ready-made follow-up call a returned body carries.

    A raise unwinds past :meth:`ResponseEnvelope.wrap`'s body-side resolution,
    so without this the literal ``[[next:search:…]]`` is what ``str(exc)``
    yields on both surfaces — MCP (server.py re-raises; FastMCP serializes
    ``str(exc)``) and CLI (``__main__`` prints ``Error: {exc}``). Mutating
    ``args`` rather than re-raising a new instance keeps the exception CLASS,
    traceback and ``__cause__`` intact, which is what the error envelope's
    wire shape is made of — only the message text changes.

    A message carrying no pointer token is left byte-identical, and so is any
    exception whose ``args`` are not a single string (``str(exc)`` would then
    render the whole tuple, and rebuilding it is not this function's business).

    >>> err = ValueError("gone. [[next:search:Cls]]")
    >>> resolve_error_message_pointers(err, "mcp", pointers_enabled=True)
    >>> str(err)
    'gone. → search_codebase(query="Cls")'
    """
    if len(exc.args) != 1 or not isinstance(exc.args[0], str):
        return
    message: str = exc.args[0]
    if _POINTER_OPENER not in message:
        return
    exc.args = (
        resolve_pointers(message, surface) if pointers_enabled else strip_pointers(message),
    )


@dataclass(frozen=True, slots=True)
class ResponseEnvelope:
    """One per composition root per surface; wraps every tool response."""

    probe: FreshnessProbe
    surface: Literal["mcp", "cli"]
    pointers_enabled: bool

    async def wrap(
        self,
        tool: str,
        project: str,
        produce: Callable[[], Awaitable[BodyResult]],
        *,
        branch: ResolvedBranch | None = None,
        probe: FreshnessProbe | None = None,
    ) -> ToolResponse:
        """``branch`` is the request's resolved selector (meta only, spec §6.7);
        ``probe`` the answering project's own freshness probe (O19, #311) —
        omitted, the envelope's (the legacy text-only surfaces)."""
        try:
            with ledger_scope() as ledger:
                body, items, extras = _coerce_body(await produce())
        except Exception as exc:
            resolve_error_message_pointers(
                exc, self.surface, pointers_enabled=self.pointers_enabled
            )
            raise
        # The trace's rendered-row count leaves through here: the ledger scope
        # has closed by the time the recorder writes the event line, and a row
        # the text did not render never reached the model (ADR 0010).
        publish_rendered_rows(ledger.rendered_rows)
        body = (
            resolve_pointers(body, self.surface) if self.pointers_enabled else strip_pointers(body)
        )
        info = await (probe if probe is not None else self.probe).envelope_info()
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
            branch=branch,
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
    branch: ResolvedBranch | None = None,
) -> dict[str, Any]:
    """The §2.1 ``meta`` block. Empty commit strings degrade to null (the wire
    contract's "head cannot be resolved" value); ``truncated`` ORs the ledger
    with any body-level truncation the producer reported in ``extras``.
    ``branch`` (#311) moves only ``branch``, the freshness pair and, on the
    three suggestion tools, ``suggestion`` — no new key (spec §6.7, A7)."""
    indexed_head, live_head, index_stale = _meta_freshness_pair(info, branch)
    meta: dict[str, Any] = {
        "tool": tool,
        "project": project,
        "indexed_git_head": indexed_head,
        "live_git_head": live_head,
        "index_stale": index_stale,
        "branch": _meta_branch(info, branch),
        "truncated": truncated,
    }
    for key, value in _with_branch_suggestion(tool, extras, branch).items():
        meta[key] = bool(meta["truncated"] or value) if key == "truncated" else value
    return meta


def _meta_freshness_pair(
    info: EnvelopeInfo | None, branch: ResolvedBranch | None
) -> tuple[str | None, str | None, bool]:
    """``(indexed_git_head, live_git_head, index_stale)`` from ONE pair, so the
    three never contradict contract §2.1 ("true only when both heads resolve
    and differ").

    No probe facts (envelope disabled, unstamped bundle): nulls and never
    stale. An explicit selection reads the selected branch's own pair
    (``ResolvedBranch.own_heads``; spec §6.5c, §7 item 4); the default selector
    keeps the project probe's — the working tree against the last pass — so a
    single-branch bundle answers exactly as before (#311). The text header
    stays the probe's until the per-branch header (P2.4).
    """
    if info is None:
        return None, None, False
    if branch is None or branch.is_default_selector:
        return info.indexed_commit or None, info.live_commit or None, info.stale
    indexed_head, live_head = branch.own_heads
    return indexed_head, live_head, branch.index_stale


def _meta_branch(info: EnvelopeInfo | None, branch: ResolvedBranch | None) -> str | None:
    if branch is None:
        return info.branch if info else None
    if branch.is_default_selector and info is None:
        # Contract §2.4 case 4: without freshness facts the default selector's
        # branch renders null, like both heads.
        return None
    return branch.meta_name


def _with_branch_suggestion(
    tool: str, extras: dict[str, Any], branch: ResolvedBranch | None
) -> dict[str, Any]:
    """Mirror the resolution's suggestion into ``extras`` on a tool that
    declares the field (§2.3); a suggestion the tool fired itself wins.

    The fired-rule line is logged exactly when the field is written: the eval
    trace merge requires meta.suggestion and a fired rule to agree. The one
    suggestion a resolution carries is the ``checkout_not_indexed`` rule's.
    """
    if branch is None or not branch.suggestion or extras.get("suggestion"):
        return extras
    if tool not in SUGGESTION_TOOLS:
        log.debug(json.dumps({"event": "branch_suggestion_dropped", "tool": tool}))
        return extras
    log_suggestion_fired(tool, CHECKOUT_NOT_INDEXED_RULE)
    return {**extras, "suggestion": branch.suggestion}
