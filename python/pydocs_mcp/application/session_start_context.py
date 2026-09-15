"""Session-start context pack builder (ADR 0008; the phase spec calls this
'turn-0 context injection').

Composes the deterministic block a harness may inject at agent-session start:
a fixed harness-injected marker line, the optimizable ``SESSION_START_PREAMBLE``
prose from the description source (ADR 0005), the same §D17 overview card
``get_overview`` serves (same snapshot, same renderer), and the
installed-package version inventory (``name version`` per row). Injection is
default-off (``serve.session_start_context.enabled``); the budget is enforced in
REAL tokens via :func:`~pydocs_mcp.retrieval.llm_clients.model_budget.count_tokens`
and, under pressure, the card is trimmed before the inventory — the
inventory is the distinctive cheap part (the wrong-library-version failure
mode this project exists for).

Usage::

    pack = await build_session_start_context(
        uow_factory=uow_factory,
        overview=overview_service,
        budget_tokens=2000,
        pointers_enabled=config.output.next_pointers.enabled,
        pointers=config.output.pointers,
    )
"""

from __future__ import annotations

from collections.abc import Callable

from pydocs_mcp.application import tool_docs
from pydocs_mcp.application.formatting import format_overview_card
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.pointer_grammar import resolve_pointers, strip_pointers
from pydocs_mcp.pointer_table import PointerTableConfig
from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens
from pydocs_mcp.storage.protocols import UnitOfWork

# Wire-format constant (ADR 0008 §Decision 6): the Phase 2 attribution
# matcher excludes injected content by EXACT match on this first line —
# rewording it is a cross-phase breaking change. Machinery, not optimizable
# text; pinned byte-for-byte by test.
INJECTED_CONTEXT_MARKER = (
    "[pydocs-mcp session-start-context: harness-injected at session start; not model-retrieved]"
)

# Truncation is NOTED, never silent (ADR 0008 §Decision 2) — the reader must
# know the map/inventory it sees is a prefix, not the whole corpus.
CARD_TRUNCATED_NOTE = "[session-start-context: overview card truncated to fit the token budget]"
INVENTORY_TRUNCATED_NOTE = (
    "[session-start-context: version inventory truncated to fit the token budget]"
)

_INVENTORY_HEADING = "## Installed packages"

# The pack is harness-injected at turn 0 (ADR 0008) on BOTH of its channels —
# the ask-your-docs agent prompt and the ``session-start-context`` CLI verb,
# whose own help says its output is what a harness injects. Both feed an agent
# that issues MCP calls, so the card's follow-ups render in MCP form even on
# the CLI channel. WHICH of the two renderings runs is the deployment's call,
# not this module's: ``output.next_pointers.enabled`` arrives as
# ``pointers_enabled`` exactly as it does at ``ResponseEnvelope``, so the pack
# cannot advertise follow-ups a deployment has suppressed on every other channel.
_PACK_SURFACE = "mcp"

# count_tokens falls back to the o200k_base encoding for a model name
# tiktoken doesn't know — the same encoding ADR 0008's budget measurements
# used, so the enforced cap matches the recorded evidence.
_BUDGET_MODEL_NAME = ""


async def build_session_start_context(
    *,
    uow_factory: Callable[[], UnitOfWork],
    overview: OverviewService,
    budget_tokens: int,
    pointers_enabled: bool,
    pointers: PointerTableConfig,
    package: str = "",
) -> str:
    """Build the session-start pack: marker + preamble + overview card + inventory.

    ``pointers_enabled`` is the deployment's ``output.next_pointers.enabled``,
    passed in by the composition root that owns the config — the same flag,
    from the same place, that ``ResponseEnvelope`` applies to tool responses.
    There is no default here on purpose: a second one would let this channel
    disagree with every other one.

    ``pointers`` is the deployment's pointer table, threaded from the same
    composition root: the pack embeds the card ``get_overview`` serves, so its
    module rows must offer the follow-ups the tool would offer one call later —
    the two channels cannot advertise different next calls.

    Example::

        pack = await build_session_start_context(
            uow_factory=factory,
            overview=service,
            budget_tokens=2000,
            pointers_enabled=True,
        )
        assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER
    """
    # The card's rows come from the table, and its pointers are rendered BEFORE
    # the budget fit, so the enforced cap is counted on the exact bytes the
    # harness injects.
    card = _rendered_card(
        format_overview_card(await overview.build(package), pointers=pointers),
        pointers_enabled=pointers_enabled,
    )
    # Read path — no commit needed (CLAUDE.md UoW contract); __aexit__'s
    # safety-net rollback is a no-op.
    async with uow_factory() as uow:
        packages = await uow.packages.list()
    rows = tuple(f"{pkg.name} {pkg.version}" for pkg in sorted(packages, key=lambda p: p.name))
    # Module-attribute read (never a from-import snapshot) so an
    # ``apply_source`` override rebinding the preamble (ADR 0006) reaches
    # every later pack build.
    head = f"{INJECTED_CONTEXT_MARKER}\n{tool_docs.SESSION_START_PREAMBLE}"
    return _fit_to_budget(head, card, rows, budget_tokens)


def _rendered_card(card: str, *, pointers_enabled: bool) -> str:
    """The overview card with its pointers resolved to the pack's call form, or
    removed — the same either/or ``ResponseEnvelope.wrap`` applies to a body.

    Trailing newline stripped so section joins stay exactly one blank line.
    """
    rendered = resolve_pointers(card, _PACK_SURFACE) if pointers_enabled else strip_pointers(card)
    return rendered.rstrip("\n")


def _tokens(text: str) -> int:
    return count_tokens(text, _BUDGET_MODEL_NAME)


def _join_sections(*sections: str) -> str:
    return "\n\n".join(section for section in sections if section)


def _fit_to_budget(head: str, card: str, rows: tuple[str, ...], budget: int) -> str:
    """Deterministic budget enforcement with the ADR 0008 trim order.

    Card lines are dropped (from the end) before inventory rows; the head
    (marker + preamble) is machinery the attribution phase needs and is
    never trimmed — a budget below that floor returns the floor rather than
    an unmarked fragment.
    """
    inventory = "\n".join((_INVENTORY_HEADING, *rows))
    full = _join_sections(head, card, inventory)
    if _tokens(full) <= budget:
        return full

    card_lines = card.splitlines()

    def _with_card_prefix(kept: int) -> str:
        trimmed = "\n".join((*card_lines[:kept], CARD_TRUNCATED_NOTE))
        return _join_sections(head, trimmed, inventory)

    kept = _largest_fitting(len(card_lines), _with_card_prefix, budget)
    if kept is not None:
        return _with_card_prefix(kept)

    def _with_row_prefix(kept: int) -> str:
        trimmed = "\n".join((_INVENTORY_HEADING, *rows[:kept], INVENTORY_TRUNCATED_NOTE))
        return _join_sections(head, CARD_TRUNCATED_NOTE, trimmed)

    kept = _largest_fitting(len(rows), _with_row_prefix, budget)
    if kept is not None:
        return _with_row_prefix(kept)
    return _with_row_prefix(0)  # the floor — see docstring


def _largest_fitting(n_max: int, render: Callable[[int], str], budget: int) -> int | None:
    """Largest ``k`` in ``[0, n_max]`` whose render fits ``budget``, else None.

    Binary search — token count is monotone in the kept-line count (BPE
    counts grow with appended text for line-structured input), so the fit
    predicate flips exactly once.
    """
    if _tokens(render(0)) > budget:
        return None
    lo, hi = 0, n_max
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _tokens(render(mid)) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo
