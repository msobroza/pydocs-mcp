"""Decision read-side service + its dashboard value object (spec §D9/§D11).

``DecisionDashboard`` is the frozen view-model the ``dashboard()`` mode of the
``get_why`` surface renders: counts by status and by source, the stalest active
records, the ``proposed`` records awaiting review, and the high-centrality
modules with zero decision coverage. It lives next to the renderer's consumer
(the future ``DecisionService``) so ``application/formatting.py`` can stay a
pure rendering module and import it only under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydocs_mcp.storage.decision_record import DecisionRecord


@dataclass(frozen=True, slots=True)
class DecisionDashboard:
    """Governance view-model for ``get_why`` dashboard mode (spec §D11).

    Fields are already sliced/ranked by the service — the renderer only lays
    them out. ``stalest`` / ``awaiting_review`` are capped at 5 by the service;
    ``ungoverned_modules`` are the top-centrality module qnames with no
    decision coverage (up to 5).
    """

    by_status: Mapping[str, int]
    by_source: Mapping[str, int]
    stalest: tuple[DecisionRecord, ...]
    awaiting_review: tuple[DecisionRecord, ...]
    ungoverned_modules: tuple[str, ...]
