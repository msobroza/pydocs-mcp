"""Adapter validity firewall — the zero-cost, serve-parity gate (ADR 0019 §5-6).

ONE view-parameterized engine now backs both validity firewalls that used to
live apart (ADR 0019 §Amendment 2026-07-20). ``firewall_violations`` runs the
~96 µs check every proposed candidate must clear BEFORE it may cost a rollout,
parameterized by the SECTION UNIVERSE it validates against:

- :data:`CANDIDATE_UNIVERSE` — the FULL eleven-header product grammar
  (``SERVER_INSTRUCTIONS`` + nine ``TOOL: <name>`` + ``SESSION_START_PREAMBLE``).
  The default; GEPA candidates carry the preamble as a mutable component.
- :data:`OVERLAY_UNIVERSE` — the ten-header subset the arm-B ``tool_docs``
  overlay optimizes. ``SESSION_START_PREAMBLE`` is ABSENT-and-legal there because
  the overlay bridge injects the live preamble downstream (``_overlay_server.
  _as_product_document``); flagging its absence would reject a valid overlay.
  ``ToolDocsArtifact.validate`` (``optimize/artifacts/tool_docs``) now delegates
  here with this universe — the old hand-rolled ten-header firewall is gone.

THE PARITY RULE (ADR 0019 §Decision 6): **firewall-accepts ⇒ product-accepts**,
for BOTH universes. The engine's accept path IS the product's strict
``parse_sections`` + ``validate_sections`` raising nothing, so the implication
holds BY CONSTRUCTION on every shared dimension — headers, required markers,
budgets, stray content, and duplicate sections. The overlay universe reaches the
SAME unconditional eleven-header ``validate_sections`` by completing the parsed
sections with an inert placeholder preamble (mirroring the runtime bridge), so
its budget/marker verdict is byte-identical to the candidate universe's. The
extra section-ORDER check only makes the firewall STRICTER (it rejects a
reordered document the product would still serve — the safe direction that
shrinks the search space, never wastes a rollout). A parity test pins the
implication over a battery of mutated candidates, for both universes.

BUDGET RECONCILIATION (ADR 0019 §Decision 6a, chosen direction): budgets are the
product's own — the nine TOOL sections ONLY (``_check_token_budgets``);
``SERVER_INSTRUCTIONS`` and ``SESSION_START_PREAMBLE`` are budget-exempt. This is
EXACT product parity with zero over-rejection. The pre-unification
``ToolDocsArtifact`` counted ``SERVER_INSTRUCTIONS`` into both the per-tool cap
and the surface total, making it stricter than the product and needlessly
shrinking the search space; delegating budgets to ``validate_sections`` WIDENS
what the overlay firewall accepts to exactly what a real serve accepts, while the
``firewall-accepts ⇒ product-accepts`` implication stays guaranteed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: the firewall's whole job is serve
# parity, so it must run the product's own strict validator — there is no
# library-free way to define it. Base install without [retrieval] gets the hint.
try:
    from pydocs_mcp.application.description_source import (
        CANONICAL_HEADERS,
        SESSION_START_PREAMBLE_HEADER,
        DescriptionSourceError,
        parse_sections,
        validate_sections,
    )
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.candidates.candidate import Candidate

__all__ = [
    "CANDIDATE_UNIVERSE",
    "OVERLAY_UNIVERSE",
    "ValidityVerdict",
    "firewall_violations",
    "screen_candidate",
]

# The two section universes the one engine validates against (see module
# docstring). CANDIDATE_UNIVERSE is the product's full canonical order;
# OVERLAY_UNIVERSE drops SESSION_START_PREAMBLE (the overlay bridge injects the
# live preamble downstream, so its absence from an overlay document is legal).
CANDIDATE_UNIVERSE: tuple[str, ...] = CANONICAL_HEADERS
OVERLAY_UNIVERSE: tuple[str, ...] = tuple(
    header for header in CANONICAL_HEADERS if header != SESSION_START_PREAMBLE_HEADER
)

# WHY: the overlay universe omits SESSION_START_PREAMBLE, but the product's
# validator is unconditionally eleven-header. We complete the parsed sections
# with this inert placeholder to run that SAME validator — the preamble is
# presence-only and budget-exempt, so its value never changes the verdict, and
# budget/marker parity is byte-identical to the candidate universe.
_OVERLAY_PREAMBLE_PLACEHOLDER = ""


@dataclass(frozen=True, slots=True)
class ValidityVerdict:
    """The firewall's verdict for one candidate — ``valid`` iff no violations."""

    valid: bool
    violations: tuple[str, ...]


def screen_candidate(candidate: Candidate) -> ValidityVerdict:
    """Screen a candidate through the full firewall; ``valid`` iff violations empty."""
    violations = firewall_violations(candidate.render())
    return ValidityVerdict(valid=not violations, violations=violations)


def firewall_violations(
    document: str, *, universe: tuple[str, ...] = CANDIDATE_UNIVERSE
) -> tuple[str, ...]:
    """Return every product-grammar + ordering violation; empty tuple == valid.

    Never raises (the reflector feeds arbitrary text): the product's strict
    parse / validate errors are caught into violation strings, and the extra
    order invariant is appended. A firewall-accepted (empty-tuple) document is
    guaranteed to pass the product ``apply_source`` path — the parity rule.

    Args:
        document: the delimited candidate/overlay text to screen.
        universe: the ordered canonical headers this view validates against —
            :data:`CANDIDATE_UNIVERSE` (default, full grammar) or
            :data:`OVERLAY_UNIVERSE` (the ``tool_docs`` overlay's ten-header
            subset). The universe drives BOTH the parse allowed-set and the
            presence/order checks.
    """
    try:
        sections = parse_sections(document, allowed=universe)
    except DescriptionSourceError as exc:
        # Parse-stage rejection — stray content, a duplicate header, or a
        # smuggled/renamed header outside this view's universe. The document is
        # firewall-rejected before any structural check, keeping firewall-accepts
        # ⇒ product-accepts (the product's apply_source runs this exact strict
        # parse for the candidate universe and would raise too).
        return (str(exc),)
    return (
        *_validate_violations(sections, universe),
        *_order_violations(sections, universe),
    )


def _validate_violations(sections: Mapping[str, str], universe: tuple[str, ...]) -> tuple[str, ...]:
    # Delegates presence + required markers + token budgets to the product's own
    # validator, so every shared dimension is byte-identical to serve time. The
    # overlay universe is completed to the full eleven-header shape first (see
    # ``_completed_for_product``) so the ONE unconditional validator serves both.
    try:
        validate_sections(_completed_for_product(sections, universe))
    except DescriptionSourceError as exc:
        return (str(exc),)
    return ()


def _completed_for_product(
    sections: Mapping[str, str], universe: tuple[str, ...]
) -> Mapping[str, str]:
    # The candidate universe already carries SESSION_START_PREAMBLE; the overlay
    # universe omits it, so inject the placeholder to satisfy the product's
    # eleven-header validator (the overlay's strict parse already rejected a
    # smuggled preamble as a collision, so ``sections`` never contains one here).
    if SESSION_START_PREAMBLE_HEADER in universe:
        return sections
    return {**sections, SESSION_START_PREAMBLE_HEADER: _OVERLAY_PREAMBLE_PLACEHOLDER}


def _order_violations(sections: Mapping[str, str], universe: tuple[str, ...]) -> tuple[str, ...]:
    # The product checks section PRESENCE, never ORDER; the firewall keeps order
    # as an invariant (ADR 0019 §Decision 5) so the mutation tree relies on a
    # stable canonical layout. Strictly stronger than the product (rejects a
    # reordered doc it would serve) — the safe, search-space-shrinking direction.
    present = [key for key in sections if key in universe]
    expected = [key for key in universe if key in sections]
    if present != expected:
        return (f"section order {present} != canonical {expected}",)
    return ()
