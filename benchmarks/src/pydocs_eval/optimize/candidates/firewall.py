"""Adapter validity firewall — the zero-cost, serve-parity gate (ADR 0019 §5-6).

Every proposed candidate runs this ~96 µs check BEFORE it may cost a rollout.
The firewall validates the FULL eleven-header product grammar
(``SERVER_INSTRUCTIONS`` + nine ``TOOL: <name>`` + ``SESSION_START_PREAMBLE``) —
NOT the ten-header overlay ``ToolDocsArtifact`` (``optimize/artifacts/tool_docs``),
which omits ``SESSION_START_PREAMBLE`` and would flag a valid full-document
candidate for a phantom header collision (ADR 0019 §Evidence). ``tool_docs``
stays untouched for its existing overlay consumers; this is the v2 firewall
built directly on the product's strict ``parse_sections`` + ``validate_sections``
(catching the typed errors into a violations tuple) plus the extra section-ORDER
invariant the product omits.

THE PARITY RULE (ADR 0019 §Decision 6): **firewall-accepts ⇒ product-accepts**.
Because the firewall's accept path IS the product's strict parse + validate
raising nothing, the implication holds BY CONSTRUCTION on every shared
dimension — headers, required markers, budgets, stray content, and duplicate
sections. The extra order check only makes the firewall STRICTER (it rejects a
reordered document the product would still serve — the safe direction that
shrinks the search space, never wastes a rollout). A parity test pins the
implication over a battery of mutated candidates.

BUDGET RECONCILIATION (ADR 0019 §Decision 6a, chosen direction): the old
``ToolDocsArtifact`` counts ``SERVER_INSTRUCTIONS`` into both the per-tool cap
and the surface total, making it stricter than the product — which budgets the
nine TOOL sections ONLY (``_check_token_budgets``) — and needlessly shrinking
the search space. This firewall delegates budgets to the product's own
``validate_sections``, so budget-accept ⇔ product-budget-accept: EXACT parity,
zero over-rejection, and the ``firewall-accepts ⇒ product-accepts`` implication
still guaranteed. Exact parity is preferred over inherited over-strictness
because it maximizes the reachable candidate space while keeping the implication
intact.
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
        DescriptionSourceError,
        parse_sections,
        validate_sections,
    )
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.candidates.candidate import Candidate

__all__ = ["ValidityVerdict", "firewall_violations", "screen_candidate"]


@dataclass(frozen=True, slots=True)
class ValidityVerdict:
    """The firewall's verdict for one candidate — ``valid`` iff no violations."""

    valid: bool
    violations: tuple[str, ...]


def screen_candidate(candidate: Candidate) -> ValidityVerdict:
    """Screen a candidate through the full firewall; ``valid`` iff violations empty."""
    violations = firewall_violations(candidate.render())
    return ValidityVerdict(valid=not violations, violations=violations)


def firewall_violations(document: str) -> tuple[str, ...]:
    """Return every product-grammar + ordering violation; empty tuple == valid.

    Never raises (the reflector feeds arbitrary text): the product's strict
    parse / validate errors are caught into violation strings, and the extra
    order invariant is appended. A firewall-accepted (empty-tuple) document is
    guaranteed to pass the product ``apply_source`` path — the parity rule.
    """
    try:
        sections = parse_sections(document, allowed=CANONICAL_HEADERS)
    except DescriptionSourceError as exc:
        # Parse-stage rejection — stray content, a duplicate header, or a
        # smuggled/renamed header outside the eleven canonical keys. The
        # document is already firewall-rejected before any structural check,
        # which keeps firewall-accepts ⇒ product-accepts (the product's
        # apply_source runs this exact strict parse and would raise too).
        return (str(exc),)
    return (*_validate_violations(sections), *_order_violations(sections))


def _validate_violations(sections: Mapping[str, str]) -> tuple[str, ...]:
    # Delegates presence + required markers + token budgets to the product's own
    # validator, so every shared dimension is byte-identical to serve time.
    try:
        validate_sections(sections)
    except DescriptionSourceError as exc:
        return (str(exc),)
    return ()


def _order_violations(sections: Mapping[str, str]) -> tuple[str, ...]:
    # The product checks section PRESENCE, never ORDER; the firewall keeps order
    # as an invariant (ADR 0019 §Decision 5) so the mutation tree relies on a
    # stable canonical layout. Strictly stronger than the product (rejects a
    # reordered doc it would serve) — the safe, search-space-shrinking direction.
    present = [key for key in sections if key in CANONICAL_HEADERS]
    expected = [key for key in CANONICAL_HEADERS if key in sections]
    if present != expected:
        return (f"section order {present} != canonical {expected}",)
    return ()
