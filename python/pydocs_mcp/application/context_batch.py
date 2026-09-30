"""``get_context`` batches — each target resolves on its own (ADR 0023 (h)).

A batch used to fail as a whole on its first unresolvable target, so an agent
that asked for several symbols and misspelled one got nothing back and spent a
turn re-issuing the call (the turn-efficiency repro, q05 and q07). Now each
target resolves on its own: a miss renders first, as the sentence the
single-target call raises, the cards follow at the ONE shared budget split
over the targets that resolved, and the call raises only when every target
misses — the first miss, exactly as before.

Kept out of ``tool_router`` so that module stays one method per tool.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pydocs_mcp.application.formatting import render_context_miss
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.reference_service import ContextNode

# One resolved target, as ``MultiProjectLookup.resolve_context`` returns it:
# ``(display_target, closure_nodes, focus_row)`` — the row is the §3.4 item.
ResolvedContext = tuple[str, tuple[ContextNode, ...], dict[str, Any]]

# Floor share every context card is guaranteed regardless of closure-size skew,
# so a tiny closure batched beside a huge one still renders its focus block
# (spec §D1 batched-context contract). Single source of truth for the split.
_MIN_SHARE_RATIO = 0.10


class ContextCardRenderer(Protocol):
    """``LookupService.render_context_card``'s shape: one card at one budget."""

    def __call__(self, target: str, nodes: tuple[ContextNode, ...], *, token_budget: int) -> str:
        """Render ``target``'s card from ``nodes`` within ``token_budget`` tokens."""
        ...


@dataclass(frozen=True, slots=True)
class ContextBatch:
    """A batch after resolution: the targets that resolved and the ones that missed.

    Both keep the client's ``targets`` order; ``resolved`` is never empty.
    """

    resolved: tuple[ResolvedContext, ...]
    misses: tuple[tuple[str, NotFoundError], ...]


async def resolve_context_batch(
    targets: Sequence[str],
    resolve: Callable[[str], Awaitable[ResolvedContext]],
) -> ContextBatch:
    """Resolve every target on its own, catching ``NotFoundError`` only.

    Any other error (a disabled reference graph, an invalid argument) still
    fails the whole call. When nothing resolves, the FIRST miss is re-raised —
    the same exception object the batch raised before partial resolution, so
    the error a client sees is unchanged.

    >>> batch = await resolve_context_batch(["pkg.A", "pkg.Gone"], lookup_router_resolve)  # doctest: +SKIP
    """
    resolved: list[ResolvedContext] = []
    misses: list[tuple[str, NotFoundError]] = []
    for target in targets:
        try:
            resolved.append(await resolve(target))
        except NotFoundError as exc:
            misses.append((target, exc))
    if not resolved:
        raise misses[0][1]
    return ContextBatch(resolved=tuple(resolved), misses=tuple(misses))


def render_context_batch(
    batch: ContextBatch,
    *,
    token_budget: int,
    render_card: ContextCardRenderer,
) -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
    """The body triple: miss blocks first, then one card per resolved target.

    A miss block quotes the lookup's own message verbatim, pointer token and
    all: the envelope resolves the token per surface (or strips it when
    pointers are off) exactly as it does for any body. ``items[]`` carries one
    §3.4 row per resolved target, in the client's order; a miss has no row.
    """
    shares = split_context_budget(token_budget, [len(nodes) for _, nodes, _ in batch.resolved])
    misses = [render_context_miss(target, str(error)) for target, error in batch.misses]
    cards = [
        render_card(target, nodes, token_budget=share)
        for (target, nodes, _), share in zip(batch.resolved, shares, strict=True)
    ]
    items = tuple(focus_row for _, _, focus_row in batch.resolved)
    return "\n\n".join([*misses, *cards]), items, {}


def split_context_budget(total: int, sizes: list[int]) -> list[int]:
    """Split ``total`` tokens across cards — ONE shared budget, never exceeded.

    Reserve the per-card floor (``int(total * _MIN_SHARE_RATIO)``) for every
    card, then distribute the REMAINING budget proportionally to closure
    ``sizes`` (empty closures share the remainder evenly). This guarantees the
    invariant ``sum(shares) <= total`` while still giving a tiny closure
    batched beside a huge one its guaranteed floor.

    WHY not ``max(floor, proportional)``: that layered the floor ON TOP of an
    already-full proportional split, so any floor-bound card pushed the total
    over budget — up to ~2x with 20 equal cards (``ContextInput.targets`` caps
    at 20), and past budget for any skewed batch with a small closure. The
    floor is only affordable while ``len(sizes) * floor <= total`` (i.e. up to
    ``1/_MIN_SHARE_RATIO`` = 10 cards); beyond that the floor guarantee is
    structurally impossible, so it degrades to a strict even split of ``total``.

    >>> split_context_budget(1000, [9, 1])
    [820, 180]
    """
    n = len(sizes)
    floor = int(total * _MIN_SHARE_RATIO)
    # Floor unaffordable (> 1/ratio cards): can't honor it without overshooting,
    # so split the whole budget evenly instead.
    if n * floor > total:
        even = total // n
        return [even for _ in sizes]
    # Floor affordable: reserve it for every card, hand out the remainder
    # proportionally (empty closures -> even remainder). ``floor + remainder``
    # per card sums to at most ``total`` because the proportional parts sum to
    # at most ``remainder`` under floor division.
    remainder = total - n * floor
    denom = sum(sizes)
    if denom == 0:
        extra = remainder // n
        return [floor + extra for _ in sizes]
    return [floor + remainder * size // denom for size in sizes]


__all__ = (
    "ContextBatch",
    "ContextCardRenderer",
    "ResolvedContext",
    "render_context_batch",
    "resolve_context_batch",
    "split_context_budget",
)
